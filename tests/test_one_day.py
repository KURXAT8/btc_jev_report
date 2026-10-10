from datetime import timedelta
from decimal import Decimal
from email import policy
from email.parser import BytesParser

import pytest

from tests.factories import snapshot, prediction, article
from tests.test_pipeline import environment


@pytest.mark.parametrize('price,label', [('101','sideways'),('99','sideways'),
    ('101.0001','bullish'),('98.9999','bearish')])
def test_one_day_exact_maturity_and_closed_threshold(price,label):
    from btc_jev.evaluation import settle
    from btc_jev.domain import Prediction
    p=prediction(horizon_days=1)
    assert p.due_at-p.as_of==timedelta(hours=24)
    assert Prediction.model_validate_json(p.model_dump_json())==p
    with pytest.raises(ValueError):
        settle(p,Decimal(price),p.due_at-timedelta(seconds=1))
    assert settle(p,Decimal(price),p.due_at).actual==label


def test_one_day_news_and_trend_contract():
    from btc_jev.forecast import news_questions,trend_questions
    news=news_questions([article()],snapshot().as_of)
    assert '24 hours' in news['n0_impact_24']['instructions']
    trend=trend_questions()
    assert set(trend)=={'trend_1d','trend_3d','trend_7d'}
    assert '24 hours' in trend['trend_1d']['instructions']
    assert 'impact_24' in trend['trend_1d']['instructions']
    assert '-1 and +1 percent inclusive' in trend['trend_1d']['criteria']['sideways']


def test_three_horizons_prepare_restart_settle_and_weekly(tmp_path):
    from btc_jev.pipeline import prepare_daily,evaluate
    from btc_jev.weekly import build_weekly_report
    store,settings,market,cross,jev,clock,calls=environment(tmp_path)
    job_id=prepare_daily(store,settings,market,cross,jev,lambda:clock[0])
    assert [p.horizon_days for p in store.predictions()]==[1,3,7]
    assert all(p.rules_version=='forecast-v2' for p in store.predictions())
    assert len(store.baselines_for(snapshot().as_of,1))==4
    msg=BytesParser(policy=policy.default).parsebytes(store.get_job(job_id).mime_bytes)
    assert '1 天趨勢' in msg.get_body(preferencelist=('plain',)).get_content()
    prepare_daily(store,settings,market,cross,jev,lambda:clock[0])
    assert len(calls)==3
    clock[0]=snapshot().as_of+timedelta(days=1)
    market.price_at=lambda *a,**kw:Decimal('101.5')
    assert evaluate(store,market,lambda:clock[0])==1
    p,s=store.evaluated_records(clock[0])[0]
    assert p.horizon_days==1 and s.actual=='bullish'
    next_week=snapshot().as_of+timedelta(days=7)
    weekly=build_weekly_report(store,next_week,next_week)
    one=next(g for g in weekly.groups if g['horizon']==1 and g['window']=='week')
    assert one['settled_count']==one['correct_count']==1
    assert one['baselines']['uniform']['common_count']==1


def test_resume_new_predictions_before_mail_freeze_makes_no_model_calls(tmp_path):
    from btc_jev import pipeline
    store,settings,market,cross,jev,clock,calls=environment(tmp_path)
    s=snapshot(); sid=store.save_snapshot(s)
    store.save_predictions([prediction(snapshot_id=sid,horizon_days=h,rules_version='forecast-v2') for h in (1,3,7)])
    pipeline.prepare_daily(store,settings,market,cross,jev,lambda:clock[0])
    assert not calls
    assert len(store.predictions())==3


def test_partial_new_prediction_set_resumes_all_three_from_saved_stage(tmp_path,monkeypatch):
    from btc_jev.pipeline import prepare_daily
    store,settings,market,cross,jev,clock,calls=environment(tmp_path)
    save=store.save_predictions
    class SimulatedCrash(BaseException): pass
    def partial(predictions):
        save([p for p in predictions if p.horizon_days!=1])
        raise SimulatedCrash()
    monkeypatch.setattr(store,'save_predictions',partial)
    with pytest.raises(SimulatedCrash):
        prepare_daily(store,settings,market,cross,jev,lambda:clock[0])
    assert [p.horizon_days for p in store.predictions()]==[3,7]
    monkeypatch.setattr(store,'save_predictions',save)
    prepare_daily(store,settings,market,cross,jev,lambda:clock[0])
    assert [p.horizon_days for p in store.predictions()]==[1,3,7]
    assert len(calls)==3


def test_weekly_one_day_does_not_count_pre_upgrade_days_as_missing(tmp_path):
    from btc_jev.pipeline import prepare_daily
    from btc_jev.weekly import build_weekly_report
    store,settings,market,cross,jev,clock,calls=environment(tmp_path)
    start=snapshot().as_of
    store.create_job((start-timedelta(days=30)).date(),start-timedelta(days=30))
    prepare_daily(store,settings,market,cross,jev,lambda:clock[0])
    next_week=start+timedelta(days=7)
    report=build_weekly_report(store,next_week,next_week)
    one=next(g for g in report.groups if g['horizon']==1 and g['window']=='four_weeks')
    assert one['missing_count']==5


def test_concise_report_has_three_horizons_and_no_diagnostics(tmp_path):
    from btc_jev.render import render_report
    from btc_jev.weekly import build_weekly_report
    from btc_jev.store import Store
    s=snapshot(articles=[article(i) for i in range(5)])
    ps=[prediction(horizon_days=h) for h in (1,3,7)]
    store=Store(tmp_path/'db')
    report=render_report(s.as_of.date(),ps,{'snapshot':s},build_weekly_report(store,s.as_of,s.as_of),None)
    for body in (report.text,report.html):
        assert all(f'{h} 天趨勢' in body for h in (1,3,7))
        assert '資料與品質' in body and '模型：jev-1.13.0' in body
        assert '市場摘要' in body and 'RSI' in body
        assert all(word not in body for word in ('設定指紋','品質規則','缺漏原因','截止：','取得：',
            '預測完成','到期：','來源品質明細','混淆矩陣','可靠度','synthetic-config'))
        assert 'Bitcoin synthetic 2' in body and 'Bitcoin synthetic 3' not in body
        assert '尚無可評估資料' in body and '小樣本' in body
    assert report.text.count('jev-1.13.0')==1


def test_empty_weekly_includes_all_horizons(tmp_path):
    from btc_jev.weekly import build_weekly_report
    from btc_jev.store import Store
    s=snapshot(); report=build_weekly_report(Store(tmp_path/'db'),s.as_of,s.as_of)
    assert {g['horizon'] for g in report.groups}=={1,3,7}


def test_concise_weekly_keeps_mixed_versions_separate_without_internal_codes(tmp_path):
    from btc_jev.render import render_report
    from btc_jev.weekly import build_weekly_report
    from btc_jev.store import Store
    from tests.factories import settlement
    s=snapshot(); store=Store(tmp_path/'db'); sid=store.save_snapshot(s)
    old=prediction(id='old',snapshot_id=sid,rules_version='forecast-v1')
    new=prediction(id='new',snapshot_id=sid,rules_version='forecast-v2')
    store.save_predictions([old,new])
    store.save_settlement(settlement(old,'103'),observed_at=old.due_at)
    store.save_settlement(settlement(new,'97'),observed_at=new.due_at)
    at=s.as_of+timedelta(days=7)
    weekly=build_weekly_report(store,at,at)
    report=render_report(at.date(),[new],{'snapshot':s},weekly,None)
    for body in (report.text,report.html):
        assert '3 天（版本 1）：命中率 100.0%；樣本 1' in body
        assert '3 天（版本 2）：命中率 0.0%；樣本 1' in body
        assert not any(code in body for code in ('forecast-v1','forecast-v2','labels-v1','quality-v1'))
