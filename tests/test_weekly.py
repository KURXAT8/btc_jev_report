from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import pytest
from tests.factories import snapshot,prediction,settlement


def test_previous_week_crosses_year_and_bins():
    from btc_jev.weekly import previous_week_window,reliability_bin
    tz=ZoneInfo('Asia/Taipei'); now=datetime(2027,1,4,7,30,tzinfo=tz)
    assert previous_week_window(now)==(datetime(2026,12,28,tzinfo=tz),datetime(2027,1,4,tzinfo=tz))
    assert [reliability_bin(p) for p in (0,.2,.4,.6,.8,1)]==[0,1,2,3,4,4]
    with pytest.raises(ValueError): reliability_bin(float('nan'))


def test_empty_startup_has_no_fake_zero_or_missing_seven_day(tmp_path):
    from btc_jev.weekly import build_weekly_report
    from btc_jev.store import Store
    s=Store(tmp_path/'db'); now=snapshot().as_of
    s.create_job(now.astimezone(ZoneInfo('Asia/Taipei')).date(),now)
    report=build_weekly_report(s,now,now)
    assert report.groups and all(g['accuracy'] is None and g['missing_count']==0 for g in report.groups)
    assert build_weekly_report(s,now,now+timedelta(minutes=1))==report


def test_due_cohort_versions_corrections_and_common_baselines(tmp_path):
    from btc_jev.weekly import build_weekly_report
    from btc_jev.store import Store
    from btc_jev.baselines import build_baselines
    s=Store(tmp_path/'db'); snap=snapshot(); sid=s.save_snapshot(snap)
    p=prediction(snapshot_id=sid); q=prediction(id='q',snapshot_id=sid,model='jev-1.14.0')
    seven=prediction(snapshot_id=sid,horizon_days=7)
    s.save_predictions([p,q,seven]); s.save_baselines(build_baselines(snap,3,[]))
    s.save_settlement(settlement(p),observed_at=p.due_at)
    monday=snap.as_of+timedelta(days=7)
    report=build_weekly_report(s,monday,monday)
    weekly=[g for g in report.groups if g['window']=='week']
    g=next(g for g in weekly if g['model']==p.model and g['horizon']==3)
    assert g['settled_count']==1 and g['correct_count']==1 and g['accuracy']==1
    assert g['baselines']['uniform']['accuracy'] is None
    assert g['baselines']['uniform']['common_count']==1
    assert sum(row['n'] for row in g['quality_groups'].values())==1
    assert all(sum(row['n'] for row in rows)==1 for rows in g['reliability'].values())
    assert next(g for g in weekly if g['model']==q.model)['pending_count']==1
    # Seven-day forecast matures Monday, never part of the previous week's cohort.
    assert all(seven.id not in g['sample_ids'] for g in weekly)
    s.save_settlement(settlement(q,settled_at=monday+timedelta(days=1)),observed_at=monday+timedelta(days=1))
    later=build_weekly_report(s,monday+timedelta(days=7),monday+timedelta(days=7))
    assert any(c['prediction_id']==q.id for c in later.corrections)
    assert s.get_weekly_report(report.week_start)==report


def test_weekly_compares_shadow_forecast_with_same_settlements(tmp_path):
    from btc_jev.weekly import build_weekly_report
    from btc_jev.store import Store
    from btc_jev.domain import BaselineForecast
    store=Store(tmp_path/'db'); snap=snapshot(); sid=store.save_snapshot(snap)
    p=prediction(snapshot_id=sid); store.save_predictions([p])
    shadow=BaselineForecast(id='shadow',name='jev_separate_horizons',version='shadow-v1',
        label_version=p.label_version,as_of=p.as_of,due_at=p.due_at,horizon_days=p.horizon_days,
        reference_price=p.reference_price,probabilities={'bullish':.8,'sideways':.1,'bearish':.1},
        choice='bullish',history_count=0,input_ids=[p.id],status='ready')
    store.save_baselines([shadow]); store.save_settlement(settlement(p),observed_at=p.due_at+timedelta(hours=1))
    report=build_weekly_report(store,p.due_at+timedelta(days=1),p.due_at+timedelta(days=1))
    group=next(g for g in report.groups if g['window']=='cumulative' and g['horizon']==3)
    measured=group['baselines']['jev_separate_horizons']
    assert measured['common_count']==measured['directional_count']==1
    assert measured['calibration_error']==pytest.approx(0.2)
    assert measured['jev_calibration_error'] is not None
    assert measured['jev_brier'] is not None and measured['brier'] is not None


@pytest.mark.parametrize('delivery_state',['sent','missed','failed','unknown'])
def test_late_correction_is_announced_once_only_after_confirmed_delivery(tmp_path,delivery_state):
    from btc_jev.weekly import build_weekly_report
    from btc_jev.store import Store
    store=Store(tmp_path/'db'); s=snapshot(); sid=store.save_snapshot(s)
    p=prediction(snapshot_id=sid); store.save_predictions([p])
    monday=s.as_of+timedelta(days=7)
    build_weekly_report(store,monday,monday)
    store.save_settlement(settlement(p,settled_at=monday+timedelta(days=1)),observed_at=monday+timedelta(days=1))
    first=build_weekly_report(store,monday+timedelta(days=7),monday+timedelta(days=7))
    at=monday+timedelta(days=7,minutes=30)
    job=store.create_job(at.astimezone(ZoneInfo('Asia/Taipei')).date(),at)
    store.ready_job(job.id,b'payload',first.snapshot_id)
    if delivery_state=='missed':
        store.recover(at+timedelta(days=1))
    else:
        from btc_jev.domain import DeliveryResult
        store.claim_delivery(job.day,480,'test',at)
        store.finish_delivery(job.id,'test',DeliveryResult(status=delivery_state,reason='synthetic'))
    second=build_weekly_report(store,monday+timedelta(days=14),monday+timedelta(days=14))
    assert len(first.corrections)==1
    assert len(second.corrections)==(0 if delivery_state=='sent' else 1)


def test_failed_market_day_is_counted_as_unavailable_with_reason(tmp_path):
    from btc_jev.pipeline import prepare_daily
    from btc_jev.weekly import build_weekly_report
    from tests.test_pipeline import environment
    store,settings,market,cross,jev,clock,calls=environment(tmp_path)
    def fail(*args,**kwargs): raise ValueError('missing_reference_minute')
    market.collect=fail
    prepare_daily(store,settings,market,cross,jev,lambda:clock[0])
    next_week=snapshot().as_of+timedelta(days=7)
    report=build_weekly_report(store,next_week,next_week)
    group=next(g for g in report.groups if g['window']=='week' and g['horizon']==3)
    assert group['unavailable_count']==1
    assert group['source_failure_counts']['required_market_incomplete']==1
