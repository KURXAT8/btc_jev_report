from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
import pytest
from tests.factories import snapshot,article,prediction,settlement
from tests.test_pipeline import environment
from tests.test_mail import FakeSMTP


@pytest.mark.parametrize('minutes,seconds,eligible',[(29,59,False),(30,0,True),(30,59,True),(31,0,False),(35,0,True),(45,0,True),(60,1,True),(61,0,False)])
def test_delivery_slot_boundaries(tmp_path,minutes,seconds,eligible):
    from btc_jev.pipeline import prepare_daily,deliver_due
    store,settings,market,cross,jev,clock,calls=environment(tmp_path)
    prepare_daily(store,settings,market,cross,jev,lambda:clock[0])
    clock[0]=snapshot().as_of+timedelta(minutes=minutes,seconds=seconds)
    result=deliver_due(store,settings,lambda:clock[0],FakeSMTP)
    assert (result is not None)==eligible


def test_smtp_unknown_never_automatically_retries(tmp_path):
    from btc_jev.pipeline import prepare_daily,deliver_due
    store,settings,market,cross,jev,clock,calls=environment(tmp_path)
    jobid=prepare_daily(store,settings,market,cross,jev,lambda:clock[0])
    factory=type('LostReply',(FakeSMTP,),{'phase':'data','instances':[]})
    clock[0]=snapshot().as_of+timedelta(minutes=30)
    assert deliver_due(store,settings,lambda:clock[0],factory).status=='unknown'
    for minute in (35,45,60):
        clock[0]=snapshot().as_of+timedelta(minutes=minute)
        assert deliver_due(store,settings,lambda:clock[0],factory) is None
    assert len(factory.instances)==1 and store.get_job(jobid).state=='unknown'


def test_prepare_deadline_skips_model_and_preserves_ready(tmp_path):
    from btc_jev.pipeline import prepare_daily
    store,settings,market,cross,jev,clock,calls=environment(tmp_path)
    clock[0]=snapshot().as_of+timedelta(minutes=25)
    jobid=prepare_daily(store,settings,market,cross,jev,lambda:clock[0])
    assert store.get_job(jobid).state=='ready' and not calls
    original=store.get_job(jobid).mime_bytes
    prepare_daily(store,settings,market,cross,jev,lambda:clock[0])
    assert store.get_job(jobid).mime_bytes==original


def test_both_news_sources_missing_stops_jev_keeps_anchor(tmp_path):
    from btc_jev.pipeline import prepare_daily
    store,settings,market,cross,jev,clock,calls=environment(tmp_path)
    with store.transaction(): store.connection.execute('DELETE FROM source_runs WHERE kind="news"')
    jobid=prepare_daily(store,settings,market,cross,jev,lambda:clock[0])
    assert not calls and not store.predictions() and store.get_job(jobid).state=='ready'
    assert store.connection.execute('SELECT COUNT(*) FROM price_anchors').fetchone()[0]==1


def test_resuming_saved_news_stage_does_not_recall_it(tmp_path):
    from btc_jev.forecast import forecast
    from btc_jev.store import Store
    from btc_jev.jev import JevClient
    from tests.test_jev import answer_questions
    import json,httpx
    s=snapshot(articles=[article()]); store=Store(tmp_path/'db'); clock=lambda:s.as_of+timedelta(minutes=5)
    day=s.as_of.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Taipei')).date()
    settings=__import__('btc_jev.domain',fromlist=['Settings']).Settings(data_dir=tmp_path,typesafe_api_key='dummy')
    stages=[]
    def handler(req):
        payload=json.loads(req.content); stages.append('trend' if 'trend_3d' in payload['questions'] else 'news')
        return httpx.Response(200,json=answer_questions(payload))
    c=JevClient(httpx.Client(transport=httpx.MockTransport(handler)),settings,now=clock,reserve_attempt=lambda stage,key,tokens:store.reserve_attempt(day,stage,key,tokens))
    class SimulatedCrash(BaseException): pass
    def save(stage):
        store.save_stage(stage)
        if stage.stage=='forecast-v2:news-1': raise SimulatedCrash()
    with pytest.raises(SimulatedCrash): forecast(s,c,clock,store.get_stage,save)
    assert len(forecast(s,c,clock,store.get_stage,store.save_stage))==3
    assert stages==['news','trend'] and store.attempt_count(day)==2


def test_request_size_trims_news_and_preserves_rules(tmp_path):
    import json,httpx
    from btc_jev.forecast import forecast
    from btc_jev.jev import JevClient
    from btc_jev.domain import Settings
    from tests.test_jev import answer_questions
    s=snapshot(articles=[article(i,title='測試'*150,summary='文'*600) for i in range(20)])
    calls=[]
    def handler(req):
        assert len(req.content)<=24*1024
        payload=json.loads(req.content); calls.append(payload)
        return httpx.Response(200,json=answer_questions(payload))
    c=JevClient(httpx.Client(transport=httpx.MockTransport(handler)),Settings(data_dir=tmp_path,typesafe_api_key='dummy'),now=lambda:s.as_of+timedelta(minutes=5),reserve_attempt=lambda *a:True)
    assert len(forecast(s,c,c.now,lambda *a:None,lambda r:None))==3
    assert len(calls)<=3 and calls[-1]['state']['trimmed_news']>0
    assert '+2 percent' in calls[-1]['questions']['trend_3d']['criteria']['sideways']


def test_calendar_schedule_replacement_removes_old_dates_at_new_cutoff(tmp_path):
    from btc_jev.store import Store
    from btc_jev.domain import CalendarEvent
    store=Store(tmp_path/'db'); s=snapshot(); before=s.as_of-timedelta(minutes=5); after=s.as_of+timedelta(minutes=1)
    old=CalendarEvent(id='old-date',source='cpi',url='https://www.bls.gov/',title='CPI August',content_hash='old',
        scheduled_start=s.as_of+timedelta(days=1),scheduled_end=s.as_of+timedelta(days=1),known_at=before,time_precision='exact',source_timezone='America/New_York')
    new=old.model_copy(update={'id':'new-date','content_hash':'new','known_at':after,'scheduled_start':s.as_of+timedelta(days=2),'scheduled_end':s.as_of+timedelta(days=2)})
    store.save_calendar([old],{'cpi':s.calendar_health['cpi']},before)
    store.save_calendar([new],{'cpi':s.calendar_health['cpi'].model_copy(update={'last_success_at':after,'last_attempt_at':after})},after)
    assert store.cached_calendar(s.as_of)[0]==[old]
    assert store.cached_calendar(after)[0]==[new]


def test_reverted_news_revision_uses_latest_observation(tmp_path):
    from btc_jev.store import Store
    store=Store(tmp_path/'db'); s=snapshot(); a=article(); at=a.fetched_at
    store.save_articles([a],s.source_health,at)
    b=a.model_copy(update={'id':'revision-b','title':'Bitcoin updated B','content_hash':'B','first_seen_at':at+timedelta(minutes=1),'fetched_at':at+timedelta(minutes=1)})
    store.save_articles([b],s.source_health,b.fetched_at)
    reverted=a.model_copy(update={'fetched_at':at+timedelta(minutes=2)})
    store.save_articles([reverted],s.source_health,reverted.fetched_at)
    chosen,_=store.cached_news(reverted.fetched_at)
    assert [item.title for item in chosen]==[a.title]
    assert chosen[0].first_seen_at==a.first_seen_at
