from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
import httpx
from tests.factories import snapshot
from tests.test_jev import answer_questions
from tests.test_mail import FakeSMTP,settings as mail_settings


def environment(tmp_path,failed=False):
    from btc_jev.store import Store
    from btc_jev.jev import JevClient
    import json
    s=snapshot(); clock=[s.as_of+timedelta(minutes=1)]; store=Store(tmp_path/'db')
    settings=mail_settings(tmp_path).model_copy(update={'typesafe_api_key':__import__('pydantic').SecretStr('synthetic')})
    store.save_articles([],s.source_health,s.as_of)
    store.save_calendar([],s.calendar_health,s.as_of)
    calls=[]
    def handler(req):
        body=json.loads(req.content); calls.append(body)
        return httpx.Response(529) if failed else httpx.Response(200,json=answer_questions(body))
    jev=JevClient(httpx.Client(transport=httpx.MockTransport(handler)),settings,now=lambda:clock[0],sleep=lambda _:None,reserve_attempt=lambda stage,key,tokens:store.reserve_attempt(s.as_of.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Taipei')).date(),stage,key,tokens))
    market=SimpleNamespace(collect=lambda cutoff:{k:getattr(s,k) for k in ('reference_price','daily','hourly','four_hour','funding','open_interest')}|{'fetched_at':clock[0]},price_at=lambda boundary,**kw:Decimal(100))
    cross=SimpleNamespace(price_at=lambda boundary:Decimal(100))
    return store,settings,market,cross,jev,clock,calls


def test_monday_prepare_send_once_and_failure_keeps_weekly(tmp_path):
    from btc_jev.pipeline import prepare_daily,deliver_due
    for failed in (False,True):
        env=environment(tmp_path/str(failed),failed)
        store,settings,market,cross,jev,clock,calls=env
        job_id=prepare_daily(store,settings,market,cross,jev,lambda:clock[0])
        job=store.get_job(job_id)
        assert job.state=='ready' and job.weekly_snapshot_id
        assert len(store.predictions())==(0 if failed else 3)
        assert len(store.baselines_for(snapshot().as_of,3))==(5 if not failed else 4)
        assert prepare_daily(store,settings,market,cross,jev,lambda:clock[0])==job_id
        assert len(calls)==3
        clock[0]=snapshot().as_of+timedelta(minutes=30)
        factory=type('Delivery',(FakeSMTP,),{'instances':[]})
        assert deliver_due(store,settings,lambda:clock[0],factory).status=='sent'
        assert deliver_due(store,settings,lambda:clock[0],factory) is None
        assert len(factory.instances)==1


def test_prepare_persists_safe_jev_failure_diagnostics(tmp_path):
    from btc_jev.pipeline import prepare_daily
    store,settings,market,cross,jev,clock,calls=environment(tmp_path,failed=True)
    job_id=prepare_daily(store,settings,market,cross,jev,lambda:clock[0])
    error=store.diagnostics(job_id)['analysis_error']
    assert error=={'stage':'forecast-v2:trend','code':'jev_attempts_exhausted',
                   'http_status':529,'attempts':3}


def test_crashed_preparation_fallback_and_missed_window(tmp_path):
    from btc_jev.pipeline import deliver_due
    store,settings,market,cross,jev,clock,calls=environment(tmp_path)
    day=snapshot().as_of.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Taipei')).date()
    store.create_job(day,clock[0]); clock[0]=snapshot().as_of+timedelta(minutes=30)
    assert deliver_due(store,settings,lambda:clock[0],FakeSMTP).status=='sent'
    assert store.get_job(day).weekly_snapshot_id and not calls
    clock[0]+=timedelta(days=1,minutes=31)
    assert deliver_due(store,settings,lambda:clock[0],FakeSMTP) is None


def test_collection_calendar_refresh_and_zero_model_calls(tmp_path):
    from btc_jev.pipeline import collect_context
    from btc_jev.store import Store
    store=Store(tmp_path/'db'); s=snapshot(); clock=[s.as_of-timedelta(minutes=5)]
    counts={'news':0,'calendar':0}
    def collector(kind):
        def run(at):
            counts[kind]+=1
            health=s.source_health if kind=='news' else s.calendar_health
            return [],{k:v.model_copy(update={'last_success_at':at,'last_attempt_at':at}) for k,v in health.items()}
        return SimpleNamespace(collect=run)
    collect_context(store,collector('news'),collector('calendar'),lambda:clock[0])
    clock[0]+=timedelta(hours=1)
    collect_context(store,collector('news'),collector('calendar'),lambda:clock[0])
    assert counts=={'news':2,'calendar':1}


def test_restart_recovers_snapshot_saved_before_job_link(tmp_path):
    from btc_jev.pipeline import prepare_daily
    store,settings,market,cross,jev,clock,calls=environment(tmp_path)
    s=snapshot(); store.save_snapshot(s)
    def no_recollect(*args,**kwargs): raise AssertionError('frozen snapshot must be recovered')
    market.collect=no_recollect
    jobid=prepare_daily(store,settings,market,cross,jev,lambda:clock[0])
    assert len(store.predictions())==3 and store.job_snapshot(jobid)


def test_model_excluded_news_counts_are_persisted(tmp_path):
    from btc_jev.pipeline import prepare_daily
    from tests.factories import article
    from tests.test_jev import answer_questions
    import json
    store,settings,market,cross,jev,clock,calls=environment(tmp_path)
    s=snapshot(); store.save_articles([article()],s.source_health,s.as_of-timedelta(minutes=1))
    def handler(req):
        body=json.loads(req.content); response=answer_questions(body)
        for answer in response['answers'].values():
            if answer['type']=='noul': answer['noul']=.1
        return httpx.Response(200,json=response)
    jev.http=httpx.Client(transport=httpx.MockTransport(handler))
    job=prepare_daily(store,settings,market,cross,jev,lambda:clock[0])
    assert store.diagnostics(job)['news_counts']['relevance_excluded']==1


def test_restart_after_predictions_preserves_news_exclusion_counts(tmp_path,monkeypatch):
    from btc_jev import pipeline
    store,settings,market,cross,jev,clock,calls=environment(tmp_path)
    s=snapshot(); sid=store.save_snapshot(s)
    from tests.factories import prediction
    store.save_predictions([prediction(snapshot_id=sid,horizon_days=h) for h in (3,7)])
    day=s.as_of.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Taipei')).date()
    job=store.create_job(day,clock[0]); store.freeze_job_snapshot(job.id,sid)
    store.save_diagnostics(job.id,dict(status='complete',reasons=[],news_counts={'excluded':3,'relevance_excluded':3,'size_excluded':0}))
    pipeline.prepare_daily(store,settings,market,cross,jev,lambda:clock[0])
    assert store.diagnostics(job.id)['news_counts']['relevance_excluded']==3
    assert not calls


def test_malformed_maturity_response_keeps_pending_and_monday_report(tmp_path):
    from btc_jev.pipeline import prepare_daily
    from btc_jev.sources.market import MarketClient
    from tests.factories import prediction
    from tests.test_sources import raw_bar,ms
    store,settings,market,cross,jev,clock,calls=environment(tmp_path)
    s=snapshot(); sid=store.save_snapshot(s)
    bad=prediction(id='bad',as_of=s.as_of-timedelta(days=7),snapshot_id=sid)
    good=prediction(id='good',as_of=s.as_of-timedelta(days=6),snapshot_id=sid)
    store.save_predictions([bad,good])
    def handler(req):
        start=int(req.url.params['startTime'])
        return httpx.Response(200,json=[[]] if start==ms(bad.due_at)-60000 else [raw_bar(start)])
    client=MarketClient(httpx.Client(transport=httpx.MockTransport(handler)),now=lambda:clock[0])
    market.price_at=client.price_at
    jobid=prepare_daily(store,settings,market,cross,jev,lambda:clock[0])
    assert store.get_job(jobid).state=='ready'
    assert store.get_job(jobid).weekly_snapshot_id
    assert [p.id for p in store.pending_settlements(clock[0])]==['bad']
    assert [p.id for p,result in store.evaluated_records(clock[0])]==['good']
