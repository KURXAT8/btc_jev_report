from datetime import timedelta
from decimal import Decimal
import json
from email import policy
from email.parser import BytesParser
import httpx
from tests.factories import snapshot
from tests.test_jev import answer_questions
from tests.test_mail import FakeSMTP,settings as mail_settings


def test_public_transport_to_mail_settle_and_following_week(tmp_path):
    from btc_jev.sources.market import MarketClient,millis
    from btc_jev.sources.crosscheck import CrosscheckClient
    from btc_jev.sources.news import NewsClient
    from btc_jev.sources.calendars import CalendarClient
    from btc_jev.jev import JevClient
    from btc_jev.store import Store
    from btc_jev.pipeline import collect_context,prepare_daily,deliver_due,evaluate
    from pathlib import Path
    from pydantic import SecretStr
    fixtures=Path(__file__).parent/'fixtures'
    initial=snapshot().as_of; clock=[initial-timedelta(minutes=5)]
    store=Store(tmp_path/'db'); settings=mail_settings(tmp_path).model_copy(update={'typesafe_api_key':SecretStr('synthetic')})
    model_calls=[]
    def handler(req):
        url=str(req.url)
        if req.method=='POST':
            payload=json.loads(req.content); model_calls.append(payload)
            return httpx.Response(200,json=answer_questions(payload))
        if 'klines' in url:
            widths={'1m':60000,'1h':3600000,'4h':14400000,'1d':86400000}
            width=widths[req.url.params['interval']]
            end=int(req.url.params['endTime']); limit=int(req.url.params['limit'])
            last_open=(end+1)//width*width-width
            rows=[[last_open-i*width,'100','100','100','100','2',last_open-(i-1)*width-1] for i in range(limit-1,-1,-1)]
            return httpx.Response(200,json=rows)
        if 'fundingRate' in url:
            end=int(req.url.params['endTime'])
            return httpx.Response(200,json=[{'fundingTime':end-i*3600000,'fundingRate':'.0001'} for i in range(168,0,-8)])
        if 'openInterestHist' in url:
            end=int(req.url.params['endTime'])//3600000*3600000
            return httpx.Response(200,json=[{'timestamp':end-i*3600000,'sumOpenInterest':'100','sumOpenInterestValue':'10000'} for i in range(192,-1,-1)])
        if 'okx' in url:
            return httpx.Response(200,json={'code':'0','data':[[int(req.url.params['after'])-60000,'100','100','100','100','2','2','2','1']]})
        if 'fomccalendars' in url: return httpx.Response(200,text=(fixtures/'fomc.html').read_text(),headers={'content-type':'text/html'})
        if '/schedule/' in url: return httpx.Response(200,text=(fixtures/'cpi.html').read_text(),headers={'content-type':'text/html'})
        return httpx.Response(200,text='<rss version="2.0"><channel><title>empty healthy feed</title></channel></rss>',headers={'content-type':'application/rss+xml'})
    http=httpx.Client(transport=httpx.MockTransport(handler))
    market=MarketClient(http,now=lambda:clock[0],sleep=lambda _:None)
    cross=CrosscheckClient(http,now=lambda:clock[0],sleep=lambda _:None)
    client=JevClient(http,settings,now=lambda:clock[0],sleep=lambda _:None,reserve_attempt=lambda stage,key,tokens:store.reserve_attempt(clock[0].astimezone(__import__('zoneinfo').ZoneInfo('Asia/Taipei')).date(),stage,key,tokens))
    news=NewsClient(http,now=lambda:clock[0]); calendars=CalendarClient(http,now=lambda:clock[0])
    factory=type('SMTP',(FakeSMTP,),{'instances':[]})
    for week in (0,1):
        clock[0]=initial+timedelta(days=week*7,minutes=-5)
        collect_context(store,news,calendars,lambda:clock[0])
        clock[0]+=timedelta(minutes=6)
        jobid=prepare_daily(store,settings,market,cross,client,lambda:clock[0])
        job=store.get_job(jobid)
        msg=BytesParser(policy=policy.default).parsebytes(job.mime_bytes)
        assert 'Jev Weekly Report' in msg['Subject'] and '分析未完成' not in msg['Subject']
        clock[0]=initial+timedelta(days=week*7,minutes=30)
        assert deliver_due(store,settings,lambda:clock[0],factory).status=='sent'
        assert deliver_due(store,settings,lambda:clock[0],factory) is None
    assert len(factory.instances)==2 and len(model_calls)==6
    assert len(store.evaluated_records(clock[0]))==3
    assert len(store.weekly_reports())==2
    http.close()
