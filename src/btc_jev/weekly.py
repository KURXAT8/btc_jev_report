"""Descriptive cohorts use maturity dates, never delivery dates."""
from datetime import date,datetime,time,timedelta
import math
from btc_jev.domain import WeeklyReport,HORIZONS
from btc_jev.evaluation import brier_score
from btc_jev.calibration import paired_metrics
from btc_jev.learning import LearningStore
from btc_jev.forecast import direction_clear,RULES_VERSION
from btc_jev.util import TAIPEI,digest

KEYS=('bullish','sideways','bearish')


def previous_week_window(now):
    local=now.astimezone(TAIPEI)
    end=local.replace(hour=0,minute=0,second=0,microsecond=0)-timedelta(days=local.weekday())
    return end-timedelta(days=7),end


def reliability_bin(probability):
    if not math.isfinite(probability) or not 0<=probability<=1: raise ValueError('invalid_probability')
    return sum(probability>=edge for edge in (.2,.4,.6,.8))


def reliability_table(records):
    result={}
    for key in KEYS:
        rows=[]
        for index in range(5):
            sample=[(p,s) for p,s in records if reliability_bin(p.distribution.probabilities[key])==index]
            n=len(sample)
            rows.append(dict(bin=index,n=n,mean_probability=sum(p.distribution.probabilities[key] for p,s in sample)/n if n else None,
                event_rate=sum(s.actual==key for p,s in sample)/n if n else None,small_sample=n<30))
        result[key]=rows
    return result


def metrics(records):
    n=len(records); correct=sum(p.distribution.choice==s.actual for p,s in records)
    return dict(n=n,correct=correct,accuracy=correct/n if n else None,
        brier=sum(brier_score(p.distribution.probabilities,s.actual) for p,s in records)/n if n else None,small_sample=n<30)


def top_class_calibration_error(records):
    """Five-bin top-choice ECE, paired on the same settled forecasts."""
    bins=[[] for _ in range(5)]
    for probabilities,choice,actual in records:
        confidence=max(probabilities.values())
        index=min(int(confidence*5),4)
        bins[index].append((confidence,choice==actual))
    count=sum(map(len,bins))
    return sum(len(rows)/count*abs(sum(p for p,_ in rows)/len(rows)-
        sum(correct for _,correct in rows)/len(rows)) for rows in bins if rows) if count else None


def subgroup(records,fn,labels):
    return {label:metrics([(p,s) for p,s in records if fn(p)==label]) for label in labels}


def calibration_metrics(records,applications,cutoff):
    from btc_jev.util import dt
    cutoff=dt(cutoff)
    policies={}
    for p,s in records:
        app=applications.get(p.id)
        if (not app or app['status']!='active' or dt(app['created_at'])>=cutoff
                or p.created_at>=cutoff or p.due_at>=cutoff or s.settled_at>=cutoff): continue
        policies.setdefault(app['policy_version'],[]).append((p,s,app))
    result={}
    for version,rows in policies.items():
        values=paired_metrics([(p.distribution.probabilities,app['probabilities'],s.actual) for p,s,app in rows])
        values['samples']=[dict(prediction_id=p.id,result_version=s.result_version,run_id=app['run_id'],
            raw_probabilities=dict(p.distribution.probabilities),probabilities=app['probabilities']) for p,s,app in rows]
        result[version]=values
    return result


def build_weekly_report(store,now,cutoff):
    start,end=previous_week_window(now)
    saved=store.get_weekly_report(start)
    if saved: return saved
    predictions=[p for p in store.predictions() if p.created_at<=cutoff]
    records=store.evaluated_records(cutoff,strict_observed=True)
    applications={app['prediction_id']:app for app in LearningStore(store).applications()}
    results={p.id:s for p,s in records}
    snapshots={p.id:store.snapshot_numeric(p.snapshot_id) for p in predictions}
    def policy(p): return snapshots[p.id].get('quality',{}).get('rules_version','unknown')
    def identity(p): return p.horizon_days,p.model,p.rules_version,p.label_version,policy(p)
    identities=set(identity(p) for p in predictions)
    for horizon in HORIZONS:
        if not any(i[0]==horizon for i in identities):
            identities.add((horizon,'jev-1.13.0',RULES_VERSION,'labels-v1','quality-v1'))
    activation=store.metadata('activation_day')
    earliest=min((p.due_at for p in predictions),default=start)
    groups=[]
    for horizon,model,rules,labels,quality_version in sorted(identities):
        ident=(horizon,model,rules,labels,quality_version)
        own=[p for p in predictions if identity(p)==ident]
        for window,low,high in (('week',start,end),('four_weeks',end-timedelta(days=28),end),('cumulative',min(earliest,start),cutoff+timedelta(microseconds=1))):
            cohort=[p for p in own if low<=p.due_at<high and p.due_at<=cutoff]
            pairs=[(p,results[p.id]) for p in cohort if p.id in results]
            m=metrics(pairs)
            scheduled=[]
            horizon_activation=activation
            if horizon==1:
                first=min((p.as_of.astimezone(TAIPEI).date() for p in predictions if p.horizon_days==1),default=None)
                horizon_activation=store.metadata('horizon_1_activation_day',str(first) if first else None)
            if horizon_activation:
                day=date.fromisoformat(horizon_activation)
                while datetime.combine(day,time(7,30),TAIPEI)+timedelta(days=horizon)<high:
                    origin=datetime.combine(day,time(7,30),TAIPEI)
                    due=origin+timedelta(days=horizon)
                    if low<=due<=cutoff: scheduled.append(origin)
                    day+=timedelta(days=1)
            existing={p.as_of for p in predictions if p.horizon_days==horizon}
            missing=[origin for origin in scheduled if origin not in existing]
            baselines={}
            for name in ('always_sideways','uniform','historical','momentum','jev_separate_horizons'):
                common=[]; unavailable=0; warmup=0
                for p,s in pairs:
                    b=next((b for b in store.baselines_for(p.as_of,horizon) if b.name==name and b.label_version==p.label_version),None)
                    if not b or b.probabilities is None: unavailable+=1; continue
                    common.append((p,s,b)); warmup+=b.status=='uniform_fallback'
                n=len(common); directional=[(s,b) for p,s,b in common if b.choice is not None]
                baselines[name]=dict(common_count=n,unavailable_count=unavailable,warmup_count=warmup,
                    versions=sorted({b.version for p,s,b in common}),directional_count=len(directional),
                    accuracy=sum(s.actual==b.choice for s,b in directional)/len(directional) if directional else None,
                    brier=sum(brier_score(b.probabilities,s.actual) for p,s,b in common)/n if n else None,
                    calibration_error=top_class_calibration_error([(b.probabilities,b.choice,s.actual) for p,s,b in common if b.choice is not None]) if directional else None,
                    jev_accuracy=sum(p.distribution.choice==s.actual for p,s,b in common)/n if n else None,
                    jev_calibration_error=top_class_calibration_error([(p.distribution.probabilities,p.distribution.choice,s.actual) for p,s,b in common]) if n else None,
                    jev_brier=sum(brier_score(p.distribution.probabilities,s.actual) for p,s,b in common)/n if n else None)
            group=dict(window=window,window_start=low,window_end=high,horizon=horizon,model=model,
                rules_version=rules,label_version=labels,quality_version=quality_version,
                settled_count=m['n'],correct_count=m['correct'],accuracy=m['accuracy'],brier=m['brier'],small_sample=m['small_sample'],
                pending_count=sum(p.id not in results for p in cohort),missing_count=len(missing),
                missing_scope='all versions for this horizon',future_count=sum(p.due_at>cutoff for p in own),
                actual_start=min((p.due_at for p,s in pairs),default=None),actual_end=max((p.due_at for p,s in pairs),default=None),
                cumulative_label='保留期間累積',baselines=baselines,
                confusion={choice:{actual:sum(p.distribution.choice==choice and s.actual==actual for p,s in pairs) for actual in KEYS} for choice in KEYS},
                sample_ids=[p.id for p,s in pairs],
                samples=[dict(prediction_id=p.id,result_version=s.result_version,as_of=p.as_of,due_at=p.due_at,
                    probabilities=dict(p.distribution.probabilities),choice=p.distribution.choice,return_pct=s.return_pct,
                    actual=s.actual,correct=p.distribution.choice==s.actual) for p,s in pairs],
                clear_groups=subgroup(pairs,lambda p:'clear' if direction_clear(p.distribution) else 'unclear',('clear','unclear')),
                confidence_groups=subgroup(pairs,lambda p:'high' if p.distribution.confidence>=.55 else 'low',('high','low')),
                quality_groups=subgroup(pairs,lambda p:snapshots[p.id].get('quality',{}).get('status','unknown'),('complete','degraded','unknown')),
                trend_groups=subgroup(pairs,lambda p:snapshots[p.id].get('context',{}).get('trend','unknown'),('up','down','mixed','unknown')),
                volatility_groups=subgroup(pairs,lambda p:snapshots[p.id].get('context',{}).get('volatility','unknown'),('low','medium','high','unknown')),
                regime_groups=subgroup(pairs,lambda p:snapshots[p.id].get('context',{}).get('regime','unknown'),
                    ('low_volatility','trend_up','trend_down','high_volatility','mixed','unknown')),
                reliability=reliability_table(pairs),calibration=calibration_metrics(pairs,applications,cutoff))
            unavailable_days=0; failure_counts={}; news_excluded=0
            origin_days={p.as_of.astimezone(TAIPEI).date() for p in cohort}|{origin.astimezone(TAIPEI).date() for origin in missing}
            for origin_day in origin_days:
                job=store.get_job(origin_day)
                if not job: continue
                diagnostics=store.diagnostics(job.id)
                sid=store.job_snapshot(job.id)
                quality=store.snapshot_numeric(sid).get('quality',{}) if sid else {}
                diagnostics=diagnostics or quality
                if diagnostics.get('status')=='unavailable': unavailable_days+=1
                for reason in diagnostics.get('reasons',[]):
                    failure_counts[reason]=failure_counts.get(reason,0)+1
                news_excluded+=diagnostics.get('news_counts',{}).get('excluded',0)
            group['unavailable_count']=unavailable_days
            group['source_failure_counts']=failure_counts
            group['news_excluded_count']=news_excluded
            groups.append(group)
    corrections=[]
    old_reports=store.weekly_reports()
    delivered={job.weekly_snapshot_id for job in store.jobs() if job.state=='sent'}
    announced={(c["prediction_id"],c["result_version"]) for report in old_reports
        if report.snapshot_id in delivered for c in report.corrections}
    for old in old_reports:
        seen={sample['prediction_id']:sample['result_version'] for g in old.groups if g['window']=='week' for sample in g['samples']}
        for p,s in records:
            if old.week_start<=p.due_at<old.week_end and seen.get(p.id)!=s.result_version and (p.id,s.result_version) not in announced:
                corrections.append(dict(week_start=old.week_start,week_end=old.week_end,prediction_id=p.id,
                    previous_version=seen.get(p.id),result_version=s.result_version,actual=s.actual,
                    return_pct=s.return_pct,settled_at=s.settled_at))
    values=dict(week_start=start,week_end=end,cutoff=cutoff,groups=groups,corrections=corrections)
    # Round trip freezes generic dict timestamps into their storage representation.
    report=WeeklyReport.model_validate_json(WeeklyReport(**values,snapshot_id=digest(values)).model_dump_json())
    store.save_weekly_report(report)
    return report
