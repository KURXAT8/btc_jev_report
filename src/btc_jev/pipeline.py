"""Daily workflow. Injectable clocks/transports keep acceptance entirely offline."""
from contextlib import contextmanager
from datetime import datetime,timedelta
from decimal import Decimal
import fcntl
import logging
import os
import smtplib
import uuid
from btc_jev.baselines import build_baselines
from btc_jev.context import build_context
from btc_jev.evaluation import settle
from btc_jev.forecast import forecast,RULES_VERSION
from btc_jev.domain import HORIZONS
from btc_jev.mail import build_mime,send_mail
from btc_jev.quality import assess_quality
from btc_jev.render import render_report
from btc_jev.sources.market import build_snapshot
from btc_jev.util import TAIPEI,utcnow,time_limit
from btc_jev.weekly import build_weekly_report,previous_week_window
from btc_jev.jev import JevRequestError

logger=logging.getLogger(__name__)


@contextmanager
def day_lock(store,day):
    path=store.path.parent/f'.prepare-{day}.lock'
    with path.open('a') as handle:
        path.chmod(0o600)
        try: fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try: yield True
        finally: fcntl.flock(handle,fcntl.LOCK_UN)


def collect_context(store,news,calendars,now=utcnow):
    run_at=now()
    with time_limit(180):
        articles,health=news.collect(run_at)
        store.save_articles(articles,health,now())
        _,old=store.cached_calendar(now())
        force=run_at.astimezone(TAIPEI).hour==7 and run_at.astimezone(TAIPEI).minute==25
        if force or any(source not in old or old[source].last_success_at is None or
            run_at-old[source].last_success_at>=timedelta(hours=6) for source in ('fomc','cpi')):
            events,health=calendars.collect(run_at)
            store.save_calendar(events,health,now())


def evaluate(store,market,now=utcnow,deadline=None):
    count=0
    for p in store.pending_settlements(now()):
        if deadline and now()>=deadline: break
        try:
            price=market.price_at(p.due_at,deadline=deadline)
            if price is not None:
                observed_at=now()
                store.save_settlement(settle(p,price,observed_at),observed_at=observed_at); count+=1
                store.save_anchor(p.due_at,price,now())
        except (ValueError,OSError,TimeoutError):
            continue
    return count


def weekly_for(store,at):
    if at.astimezone(TAIPEI).weekday()!=0: return None
    return build_weekly_report(store,at,at)


def news_answers(store,day,snapshot):
    # The IDs are bound through deterministic batch order and question prefixes.
    answers={}
    for batch in range(2):
        row=store.connection.execute('SELECT payload FROM model_stages WHERE day=? AND stage=?',
            (str(day),f'{RULES_VERSION}:news-{batch+1}')).fetchone()
        if not row:
            row=store.connection.execute('SELECT payload FROM model_stages WHERE day=? AND stage=?',
                (str(day),f'news-{batch+1}')).fetchone()
        if not row: continue
        from btc_jev.domain import StageResult
        result=StageResult.model_validate_json(row[0])
        for i,a in enumerate(snapshot.articles[batch*10:(batch+1)*10]):
            classified={k[len(f'n{i}_'):]:v for k,v in result.answers.items() if k.startswith(f'n{i}_')}
            if classified: answers[a.id]=classified
    return answers


def freeze_mail(store,settings,job,predictions,snapshot,weekly,failure,owner=None):
    from btc_jev.learning import LearningStore
    details={'snapshot':snapshot,'model':settings.jev_model,'news_answers':news_answers(store,job.day,snapshot)} if snapshot else {'model':settings.jev_model}
    details['calibrations']={p.id:LearningStore(store).application(p.id) for p in predictions}
    rendered=render_report(job.day,predictions,details,weekly,failure)
    payload=build_mime(rendered,settings,job.message_id,job.created_at)
    store.ready_job(job.id,payload,weekly.snapshot_id if weekly else None,failure,owner=owner)


def prepare_daily(store,settings,market,crosscheck,jev,now=utcnow):
    at=now(); local=at.astimezone(TAIPEI); day=local.date()
    as_of=local.replace(hour=7,minute=30,second=0,microsecond=0)
    if at<as_of: raise ValueError('preparation_not_due')
    job=store.create_job(day,at)
    if job.state!='preparing': return job.id
    with day_lock(store,day) as acquired:
        if not acquired: return job.id
        job=store.get_job(day)
        if job.state!='preparing': return job.id
        owner=uuid.uuid4().hex
        # The OS lock has exclusive ownership and is released even on process death.
        with store.transaction():
            store._write_job(job.model_copy(update={'lease_owner':owner,'lease_until':as_of+timedelta(minutes=25)}))
        failure=None; predictions=[]; snapshot=None; market_started=False
        deadline=as_of+timedelta(minutes=25)
        if now()<deadline:
            store.set_metadata('horizon_1_activation_day',str(day))
            evaluate(store,market,now,as_of+timedelta(minutes=10))
        weekly=weekly_for(store,now())
        try:
            if now()>=deadline: raise ValueError('preparation_deadline')
            with time_limit((deadline-now()).total_seconds()):
                sid=store.job_snapshot(job.id) or store.snapshot_at(as_of)
                if sid:
                    store.freeze_job_snapshot(job.id,sid)
                    snapshot=store.get_snapshot(sid)
                else:
                    # Preserve a valid P0 even if subsequent required market fields fail.
                    market_started=True
                    price=market.price_at(as_of,deadline=as_of+timedelta(minutes=10))
                    if price is not None: store.save_anchor(as_of,price,now())
                    values=market.collect(as_of)
                    articles,health=store.cached_news(as_of)
                    events,calendar_health=store.cached_calendar(as_of)
                    try:
                        with time_limit((as_of+timedelta(minutes=10)-now()).total_seconds()):
                            cross_price=crosscheck.price_at(as_of)
                    except (ValueError,OSError,TimeoutError): cross_price=None
                    quality=assess_quality(values,health,calendar_health,cross_price,as_of)
                    counts=store.news_counts(as_of)
                    quality=quality.model_copy(update={'news_counts':dict(quality.news_counts,**counts)})
                    context=build_context(values,store.historical_features(as_of),as_of)
                    snapshot=build_snapshot(values,articles,health,as_of,now(),quality,events,calendar_health,context)
                    sid=store.save_snapshot(snapshot,job_id=job.id)
                if not store.diagnostics(job.id):
                    store.save_diagnostics(job.id,dict(status=snapshot.quality.status,reasons=snapshot.quality.reasons,news_counts=snapshot.quality.news_counts))
                for horizon in HORIZONS:
                    if not store.baselines_for(as_of,horizon):
                        store.save_baselines(build_baselines(snapshot,horizon,store.historical_outcomes(horizon,as_of,'labels-v1')))
                existing=[p for p in store.predictions() if p.snapshot_id==sid]
                def record_diagnostics(model_counts):
                    counts=dict(snapshot.quality.news_counts,**model_counts)
                    counts['excluded']=counts.get('excluded',0)+sum(model_counts.values())
                    store.save_diagnostics(job.id,dict(status=snapshot.quality.status,reasons=snapshot.quality.reasons,news_counts=counts))
                horizons={p.horizon_days for p in existing}
                complete=len(existing)==len(HORIZONS) and horizons==set(HORIZONS)
                legacy=len(existing)==2 and horizons=={3,7} and all(p.rules_version!=RULES_VERSION for p in existing)
                predictions=existing if complete or legacy else forecast(snapshot,jev,now,store.get_stage,store.save_stage,
                    record_diagnostics,save_shadow=store.save_baselines,save_roles=store.save_stage)
                store.save_predictions(predictions)
        except (ValueError,OSError,TimeoutError) as error:
            # Only controlled codes reach reports; transport errors may contain secrets.
            codes={'quality_unavailable':'資料不足／衝突，未進行趨勢推論。',
                'preparation_deadline':'已超過 07:55 準備截止時間。','missing_typesafe_api_key':'缺少 TYPESAFE_API_KEY。'}
            if snapshot is None and market_started:
                store.save_diagnostics(job.id,dict(status='unavailable',reasons=['required_market_incomplete'],news_counts=store.news_counts(as_of)))
            diagnostics={k:v for k,v in store.diagnostics(job.id).items() if k!='schema_version'}
            if isinstance(error,JevRequestError):
                diagnostic=dict(stage=error.stage,code=error.code,http_status=error.http_status,attempts=error.attempts)
                diagnostics['analysis_error']=diagnostic
                logger.warning('Jev analysis failed stage=%s code=%s http_status=%s attempts=%d',
                    error.stage,error.code,error.http_status,error.attempts)
            else:
                diagnostics['analysis_error']=dict(stage=None,code='analysis_unavailable',
                    exception_class=type(error).__name__)
                logger.warning('Analysis failed exception_class=%s',type(error).__name__)
            store.save_diagnostics(job.id,diagnostics)
            failure=codes.get(str(error),'分析未完成：來源或 Jev 請求未能在限制內取得有效結果。')
            predictions=[]
        # Calibration has its own bounded failure boundary: never discard valid
        # raw forecasts because local learning failed or exceeded the deadline.
        if predictions and all(p.rules_version==RULES_VERSION for p in predictions):
            from btc_jev.learning import apply_predictions,freeze_fallback
            try:
                with time_limit(min(5,(deadline-now()).total_seconds())):
                    apply_predictions(store,predictions,now(),settings.calibration_mode)
            except Exception:
                freeze_fallback(store,predictions,now())
                diagnostics={k:v for k,v in store.diagnostics(job.id).items() if k!='schema_version'}
                store.save_diagnostics(job.id,dict(diagnostics,calibration_error='learning_unavailable'))
        freeze_mail(store,settings,job,predictions,snapshot,weekly,failure,owner=owner)
        return job.id


def deliver_due(store,settings,now=utcnow,smtp_factory=smtplib.SMTP_SSL):
    at=now(); local=at.astimezone(TAIPEI); day=local.date(); slot=local.hour*60+local.minute
    store.recover(at)
    if slot not in (480,485,495,510): return None
    job=store.get_job(day) or store.create_job(day,at)
    if job.state=='preparing':
        with day_lock(store,day) as acquired:
            if not acquired: return None
            job=store.get_job(day)
            if job.state=='preparing':
                weekly=weekly_for(store,at)
                sid=store.job_snapshot(job.id)
                snapshot=store.get_snapshot(sid) if sid else None
                freeze_mail(store,settings,job,[],snapshot,weekly,'準備工作未完成，今日僅提供狀態與已保存的週報。')
    owner=uuid.uuid4().hex
    job=store.claim_delivery(day,slot,owner,now())
    if not job: return None
    try:
        result=send_mail(job.mime_bytes,settings,smtp_factory)
    except ValueError:
        from btc_jev.domain import DeliveryResult
        result=DeliveryResult(status='failed',reason='mail_configuration')
    store.finish_delivery(job.id,owner,result)
    return result


def reconcile(store,job_id,result,now=utcnow):
    store.reconcile(job_id,result,now())
