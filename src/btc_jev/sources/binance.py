"""Authenticated Binance public-announcement WebSocket collector."""
import asyncio
from datetime import datetime,timezone
import hashlib
import hmac
import json
import secrets
from urllib.parse import urlencode

from btc_jev.domain import Article,SourceHealth
from btc_jev.sources.news import plain
from btc_jev.util import digest,utcnow

BASE_URL="wss://api.binance.com/sapi/wss"
TOPIC="com_announcement_en"
MAX_BODY_CHARS=600


def signed_announcement_url(api_key,api_secret,*,timestamp_ms=None,random_value=None):
    if not api_key or not api_secret:
        raise ValueError('missing_binance_credentials')
    params={"random":random_value or secrets.token_hex(16),"topic":TOPIC,
        "recvWindow":"30000","timestamp":str(timestamp_ms if timestamp_ms is not None else int(utcnow().timestamp()*1000))}
    unsigned=urlencode(sorted(params.items()))
    signature=hmac.new(api_secret.encode(),unsigned.encode(),hashlib.sha256).hexdigest()
    return f"{BASE_URL}?{unsigned}&signature={signature}"


def parse_announcement(message,seen_at):
    try:
        if isinstance(message,str): message=json.loads(message)
        if not isinstance(message,dict): raise ValueError
        if message.get('type')!='DATA' or message.get('topic')!=TOPIC: return None
        data=message.get('data')
        if isinstance(data,str): data=json.loads(data)
        if not isinstance(data,dict): raise ValueError
        catalog=data['catalogId']; published_ms=data['publishDate']
        title=plain(data['title']); body=plain(data['body'])
        if type(published_ms) is not int or published_ms<=0: raise ValueError
        if not str(catalog).isdigit() or not title or not body: raise ValueError
        published=datetime.fromtimestamp(published_ms/1000,timezone.utc)
        seen=seen_at.astimezone(timezone.utc)
        summary=body[:MAX_BODY_CHARS]
        event_id=digest(['binance-announcement',str(catalog),published_ms])
        content_hash=digest([title,body,published_ms])
        return Article(id=event_id,source='binance',
            url=f'https://www.binance.com/en/support/announcement/list/{catalog}',
            title=title,summary=summary,content_hash=content_hash,published_at=published,
            first_seen_at=seen,fetched_at=seen,truncated=len(body)>MAX_BODY_CHARS)
    except (KeyError,TypeError,ValueError,OverflowError):
        raise ValueError('invalid_binance_announcement') from None


def _health(at,status='ok'):
    return SourceHealth(status=status,last_success_at=at if status=='ok' else None,
        last_attempt_at=at,reason=None if status=='ok' else 'binance_stream_unavailable',item_count=0)


async def capture_forever(store,settings,*,connect=None,now=utcnow,sleep=asyncio.sleep,stop=None):
    """Subscribe, archive events in the shared SQLite store, and reconnect safely."""
    if connect is None:
        from websockets.asyncio.client import connect as websocket_connect
        connect=websocket_connect
    key=settings.binance_api_key.get_secret_value()
    secret=settings.binance_api_secret.get_secret_value()
    if not key or not secret: raise ValueError('missing_binance_credentials')
    retry=1
    while stop is None or not stop.is_set():
        at=now()
        url=signed_announcement_url(key,secret,timestamp_ms=int(at.timestamp()*1000))
        try:
            async with connect(url,additional_headers={'X-MBX-APIKEY':key},ping_interval=30,
                    ping_timeout=20,open_timeout=15,close_timeout=10,max_size=128*1024) as ws:
                connected=now(); store.save_articles([],{"binance":_health(connected)},connected)
                await ws.send(json.dumps({'command':'SUBSCRIBE','value':TOPIC},separators=(',',':')))
                deadline=asyncio.get_running_loop().time()+23*60*60
                while stop is None or not stop.is_set():
                    remaining=deadline-asyncio.get_running_loop().time()
                    if remaining<=0: break
                    raw=await asyncio.wait_for(ws.recv(),timeout=remaining)
                    seen=now()
                    try: article=parse_announcement(raw,seen)
                    except ValueError: continue
                    if article:
                        store.save_articles([article],{"binance":_health(seen)},seen)
                retry=1
        except asyncio.CancelledError:
            raise
        except Exception:
            failed=now(); store.save_articles([],{"binance":_health(failed,'error')},failed)
            if stop is not None and stop.is_set(): return
            await sleep(retry)
            retry=min(retry*2,60)
