"""Only AI boundary. No remote response bodies or secrets in errors."""
import json
import logging
import math
import time
import httpx
from btc_jev.sources.common import retry_delay
from btc_jev.util import dumps,digest,time_limit,utcnow,json_default

MAX_BYTES=24*1024
logger=logging.getLogger(__name__)


class JevRequestError(ValueError):
    """Safe API failure metadata; deliberately excludes response content."""
    def __init__(self,code,stage,http_status=None,attempts=0):
        super().__init__(code)
        self.code,self.stage,self.http_status,self.attempts=code,stage,http_status,attempts


def probability(value):
    if type(value) not in (int,float) or not math.isfinite(value) or not 0<=value<=1:
        raise ValueError('invalid_probability')


def validate_distribution(answer, options=('bullish','sideways','bearish')):
    try:
        if answer['type']!='choice' or set(answer['probabilities'])!=set(options):
            raise ValueError('invalid_choice')
        p=answer['probabilities']
        for value in p.values(): probability(value)
        probability(answer['confidence'])
        if abs(math.fsum(p.values())-1)>1e-4 or p[answer['choice']]!=max(p.values()):
            raise ValueError('invalid_distribution')
    except (KeyError,TypeError):
        raise ValueError('invalid_distribution') from None
    return answer


def reject_secrets(value):
    if isinstance(value,dict):
        for key,nested in value.items():
            if any(token in str(key).lower() for token in ('password','api_key','gmail','recipient_email','gemini','authorization')):
                raise ValueError('forbidden_state_field')
            reject_secrets(nested)
    elif isinstance(value,list):
        for nested in value: reject_secrets(nested)


class JevClient:
    def __init__(self,http,settings,*,reserve_attempt,now=utcnow,sleep=time.sleep):
        self.http,self.settings,self.reserve_attempt,self.now,self.sleep=http,settings,reserve_attempt,now,sleep

    def payload(self,state,questions):
        reject_secrets(state)
        return dict(state=state,model=self.settings.jev_model,questions=questions)

    def ask(self,state,questions,stage,deadline):
        payload=self.payload(state,questions)
        encoded=json.dumps(payload,default=json_default,ensure_ascii=False,allow_nan=False,separators=(",", ":")).encode()
        if len(encoded)>MAX_BYTES: raise ValueError('request_too_large')
        key=digest(payload)
        secret=self.settings.typesafe_api_key.get_secret_value()
        if not secret: raise ValueError('missing_typesafe_api_key')
        last_code='jev_attempts_exhausted'; last_status=None; sent_attempts=0
        for attempt in range(3):
            remaining=(deadline-self.now()).total_seconds()
            if remaining<=0: raise JevRequestError('model_deadline',stage,last_status,sent_attempts)
            # Reserve four input tokens per UTF-8 byte before each call. This
            # intentionally overestimates tokenization and includes headroom.
            if not self.reserve_attempt(stage,key,4*len(encoded)):
                raise JevRequestError('model_budget_exhausted',stage,last_status,sent_attempts)
            sent_attempts+=1
            response=None
            try:
                with time_limit(min(45,remaining)):
                    response=self.http.post('https://api.typesafe.ai/v1/systemone',content=encoded,
                        headers={'Authorization':'Bearer '+secret,'Content-Type':'application/json'},
                        follow_redirects=False,timeout=min(45,remaining))
                if self.now()>=deadline: raise JevRequestError('model_deadline',stage,response.status_code,sent_attempts)
            except JevRequestError:
                raise
            except (httpx.HTTPError,TimeoutError) as error:
                last_code='jev_attempts_exhausted'
                logger.debug('jev attempt stage=%s attempt=%d payload_bytes=%d outcome=transport_error error_class=%s',
                    stage,sent_attempts,len(encoded),type(error).__name__)
            if response is not None and response.status_code==200:
                try:
                    body=response.json()
                    if body['model']!=self.settings.jev_model or set(body['answers'])!=set(questions): raise ValueError
                    for name,q in questions.items():
                        answer=body['answers'][name]
                        if q['type']=='choice': validate_distribution(answer,q['criteria'])
                        else:
                            if answer['type']!='noul': raise ValueError
                            probability(answer['noul'])
                    if not isinstance(body['usage'],dict) or not {'input_tokens','output_tokens'}<=body['usage'].keys(): raise ValueError
                    if any(type(v) is not int or v<0 for v in body['usage'].values()): raise ValueError
                    logger.debug('jev attempt stage=%s attempt=%d payload_bytes=%d http_status=200 outcome=accepted',
                        stage,sent_attempts,len(encoded))
                    return body
                except (KeyError,TypeError,ValueError):
                    last_code='invalid_jev_response'; last_status=200
                    logger.debug('jev attempt stage=%s attempt=%d payload_bytes=%d http_status=200 outcome=invalid_jev_response',
                        stage,sent_attempts,len(encoded))
            elif response is not None and response.status_code!=429 and response.status_code<500:
                logger.debug('jev attempt stage=%s attempt=%d payload_bytes=%d http_status=%d outcome=rejected',
                    stage,sent_attempts,len(encoded),response.status_code)
                raise JevRequestError('jev_request_rejected',stage,response.status_code,sent_attempts)
            elif response is not None:
                last_code='jev_attempts_exhausted'; last_status=response.status_code
                logger.debug('jev attempt stage=%s attempt=%d payload_bytes=%d http_status=%d outcome=retryable_http_error',
                    stage,sent_attempts,len(encoded),response.status_code)
            if attempt==2: break
            delay=retry_delay(response.headers.get('Retry-After') if response is not None else None,attempt,self.now())
            if delay>60 or self.now().timestamp()+delay>=deadline.timestamp():
                raise JevRequestError('model_retry_deadline',stage,last_status,sent_attempts)
            self.sleep(delay)
        raise JevRequestError(last_code,stage,last_status,sent_attempts)
