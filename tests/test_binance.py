import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl,urlsplit

import pytest


def test_signed_announcement_url_uses_hmac_sha256_and_sorted_parameters():
    from btc_jev.sources.binance import signed_announcement_url
    url=signed_announcement_url('api-key','secret-key',timestamp_ms=1770000000000,random_value='random')
    parsed=urlsplit(url)
    params=dict(parse_qsl(parsed.query))
    assert parsed.scheme=='wss' and parsed.netloc=='api.binance.com'
    assert parsed.path=='/sapi/wss'
    assert params['topic']=='com_announcement_en'
    assert params['timestamp']=='1770000000000'
    unsigned='&'.join(f'{key}={params[key]}' for key in sorted(params) if key!='signature')
    expected=hmac.new(b'secret-key',unsigned.encode(),hashlib.sha256).hexdigest()
    assert params['signature']==expected


def test_parse_announcement_produces_bounded_article_with_binance_category_link():
    from btc_jev.sources.binance import parse_announcement
    seen=datetime(2026,9,25,tzinfo=timezone.utc)
    published=datetime(2026,9,24,12,tzinfo=timezone.utc)
    message={'type':'DATA','topic':'com_announcement_en','data':json.dumps({
        'catalogId':161,'catalogName':'Listings','publishDate':int(published.timestamp()*1000),
        'title':'Bitcoin announcement','body':'<p>BTC news</p><script>ignore()</script>'})}
    article=parse_announcement(message,seen)
    assert article.source=='binance'
    assert article.title=='Bitcoin announcement'
    assert article.summary=='BTC news'
    assert article.url=='https://www.binance.com/en/support/announcement/list/161'
    assert article.published_at==published
    assert article.id


def test_parse_announcement_ignores_ack_and_rejects_bad_payload():
    from btc_jev.sources.binance import parse_announcement
    seen=datetime(2026,9,24,tzinfo=timezone.utc)
    assert parse_announcement({'type':'COMMAND','data':'SUCCESS'},seen) is None
    with pytest.raises(ValueError,match='invalid_binance_announcement'):
        parse_announcement({'type':'DATA','topic':'com_announcement_en','data':'not-json'},seen)


def test_binance_articles_in_same_category_are_not_collapsed_by_shared_url():
    from btc_jev.sources.binance import parse_announcement
    from btc_jev.sources.news import select_articles
    now=datetime(2026,9,25,tzinfo=timezone.utc)
    messages=[]
    for title,hour in [('Bitcoin announcement',12),('Exchange maintenance',13)]:
        stamp=int(datetime(2026,9,24,hour,tzinfo=timezone.utc).timestamp()*1000)
        messages.append({'type':'DATA','topic':'com_announcement_en','data':json.dumps({
            'catalogId':161,'publishDate':stamp,'title':title,'body':'BTC update'})})
    articles=[parse_announcement(m,now) for m in messages]
    chosen,counts=select_articles(articles,now)
    assert {a.title for a in chosen}=={'Bitcoin announcement','Exchange maintenance'}
    assert counts['selected']==2


def test_binance_event_is_persisted_and_hidden_before_first_observation(tmp_path):
    from btc_jev.sources.binance import parse_announcement,_health
    from btc_jev.store import Store
    seen=datetime(2026,9,25,tzinfo=timezone.utc)
    published=datetime(2026,9,24,12,tzinfo=timezone.utc)
    message={'type':'DATA','topic':'com_announcement_en','data':json.dumps({
        'catalogId':161,'publishDate':int(published.timestamp()*1000),
        'title':'Bitcoin listing update','body':'BTC update'})}
    article=parse_announcement(message,seen)
    store=Store(tmp_path/'db')
    store.save_articles([article],{'binance':_health(seen)},seen)
    assert store.cached_news(seen-timedelta(seconds=1))[0]==[]
    selected,health=store.cached_news(seen)
    assert [a.id for a in selected]==[article.id]
    assert health['binance'].status=='ok'


def test_capture_subscribes_with_hmac_key_header_and_stores_push_without_live_network(tmp_path):
    import asyncio
    from btc_jev.domain import Settings
    from btc_jev.sources.binance import capture_forever

    now=datetime(2026,9,25,tzinfo=timezone.utc)
    stop=asyncio.Event(); received=[]; connected={}
    settings=Settings(data_dir=tmp_path,binance_api_key='synthetic-key',binance_api_secret='synthetic-secret')
    class WebSocket:
        async def __aenter__(self): return self
        async def __aexit__(self,*args): return None
        async def send(self,message): connected['subscription']=json.loads(message)
        async def recv(self):
            published=datetime(2026,9,24,12,tzinfo=timezone.utc)
            message={'type':'DATA','topic':'com_announcement_en','data':json.dumps({
                'catalogId':161,'publishDate':int(published.timestamp()*1000),
                'title':'Bitcoin listing update','body':'BTC update'})}
            return json.dumps(message)
    def connect(url,**kwargs):
        connected.update(url=url,kwargs=kwargs)
        return WebSocket()
    class Store:
        def save_articles(self,articles,health,at):
            received.extend(articles)
            if articles: stop.set()
    asyncio.run(capture_forever(Store(),settings,connect=connect,now=lambda:now,stop=stop))
    assert connected['kwargs']['additional_headers']=={'X-MBX-APIKEY':'synthetic-key'}
    assert connected['kwargs']['ping_interval']==30
    assert connected['subscription']=={'command':'SUBSCRIBE','value':'com_announcement_en'}
    assert received[0].title=='Bitcoin listing update'
