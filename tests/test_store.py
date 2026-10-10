from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path
import tomllib
import pytest
from tests.factories import snapshot,prediction,settlement,article


def setup_store(path):
    from btc_jev.store import Store
    store=Store(path); s=snapshot(); sid=store.save_snapshot(s)
    p=prediction(snapshot_id=sid); store.save_predictions([p])
    return store,s,p


def test_immutable_predictions_atomic_rollback_and_settlements(tmp_path):
    store,s,p=setup_store(tmp_path/'db')
    store.save_predictions([p]); assert len(store.predictions())==1
    q=prediction(snapshot_id=p.snapshot_id,horizon_days=7)
    with pytest.raises(ValueError): store.save_predictions([q,p.model_copy(update={'reference_price':D(110)})])
    assert len(store.predictions())==1
    assert store.pending_settlements(p.due_at)==[p]
    store.save_settlement(settlement(p), observed_at=p.due_at); assert not store.pending_settlements(p.due_at)
    assert store.evaluated_records(p.due_at)[0][1].actual=='bullish'
    assert store.evaluated_records(p.due_at-timedelta(seconds=1))==[]


def test_settlement_knowledge_time_excludes_backdated_late_correction(tmp_path):
    store,s,p=setup_store(tmp_path/'db')
    first=p.due_at+timedelta(days=1)
    original=settlement(p,settled_at=first-timedelta(days=1))
    store.save_settlement(original,observed_at=first-timedelta(days=1))
    changed=settlement(p,'90',settled_at=first-timedelta(seconds=1)).model_copy(update={'result_version':2})
    store.save_settlement(changed,observed_at=first+timedelta(seconds=1))
    assert store.evaluated_records(first+timedelta(seconds=1),strict_observed=True)[0][1] == original
    assert store.evaluated_records(first+timedelta(seconds=2),strict_observed=True)[0][1] == changed


def test_settlement_observed_exactly_at_cutoff_is_not_known_before_cutoff(tmp_path):
    store,s,p=setup_store(tmp_path/'db')
    cutoff=p.due_at+timedelta(days=1)
    result=settlement(p,settled_at=p.due_at+timedelta(hours=1))
    store.save_settlement(result,observed_at=cutoff)
    assert store.evaluated_records(cutoff,strict_observed=True)==[]
    assert store.evaluated_records(cutoff+timedelta(microseconds=1),strict_observed=True)[0][1] == result


def test_budget_survives_restart_and_identity_conflicts(tmp_path):
    from btc_jev.store import Store
    path=tmp_path/'db'; store=Store(path); day=prediction().as_of.date()
    assert all(store.reserve_attempt(day,'news-1','a') for _ in range(3))
    store.close(); store=Store(path)
    assert not store.reserve_attempt(day,'news-1','a')
    assert not store.reserve_attempt(day,'news-1','b')
    assert all(store.reserve_attempt(day,'news-2','b') for _ in range(3))
    assert all(store.reserve_attempt(day,'trend','c') for _ in range(3))
    assert store.attempt_count(day)==9


def test_jev_budget_reserves_conservative_tokens_and_caps_daily_calls(tmp_path):
    from btc_jev.store import Store,MAX_DAILY_ESTIMATED_INPUT_TOKENS
    store=Store(tmp_path/'db'); day=prediction().as_of.date()
    assert store.reserve_attempt(day,'trend','a',MAX_DAILY_ESTIMATED_INPUT_TOKENS)
    assert not store.reserve_attempt(day,'trend','a',1)
    assert store.daily_input_reservation(day)==MAX_DAILY_ESTIMATED_INPUT_TOKENS


def test_shadow_stages_fit_under_daily_budget_and_retry_limit(tmp_path):
    from btc_jev.store import Store,MAX_DAILY_ESTIMATED_INPUT_TOKENS,MAX_DAILY_ATTEMPTS
    store=Store(tmp_path/'db'); day=prediction().as_of.date()
    for stage in ('news-1','news-2','trend','trend-shadow-3d','trend-shadow-7d'):
        for _ in range(3):
            assert store.reserve_attempt(day,stage,stage,4*24*1024)
    assert store.attempt_count(day)==MAX_DAILY_ATTEMPTS==15
    assert store.daily_input_reservation(day)<=MAX_DAILY_ESTIMATED_INPUT_TOKENS
    assert not store.reserve_attempt(day,'trend','trend',1)
    assert MAX_DAILY_ATTEMPTS*4*24*1024*.042/1_000_000 < .15


def test_runtime_dependencies_include_websocket_capture_client():
    project = tomllib.loads((Path(__file__).parents[1] / 'pyproject.toml').read_text())
    assert 'websockets>=15,<16' in project['project']['dependencies']


def test_news_versions_and_empty_success_cache(tmp_path):
    from btc_jev.store import Store
    store=Store(tmp_path/'db'); s=snapshot(); cutoff=s.as_of
    a=article(); store.save_articles([a],s.source_health,cutoff-timedelta(minutes=1))
    revised=article(title='LATER',content_hash='new',first_seen_at=cutoff+timedelta(minutes=1),fetched_at=cutoff+timedelta(minutes=1))
    store.save_articles([revised],s.source_health,cutoff+timedelta(minutes=1))
    items,health=store.cached_news(cutoff)
    assert [i.title for i in items]==[a.title]
    assert len(health)==3


def test_anchor_history_requires_both_prices_known_before_cutoff(tmp_path):
    from btc_jev.store import Store
    store=Store(tmp_path/'db'); a=snapshot().as_of
    store.save_anchor(a-timedelta(days=3),D(100),a-timedelta(days=3))
    store.save_anchor(a,D(103),a+timedelta(minutes=1))
    assert store.historical_outcomes(3,a,'labels-v1')==[]
    assert store.historical_outcomes(3,a+timedelta(days=1),'labels-v1')[0]['actual']=='bullish'


def test_delivery_claims_owner_expiration_and_slots(tmp_path):
    from btc_jev.store import Store
    from btc_jev.domain import DeliveryResult
    store=Store(tmp_path/'db'); other=Store(tmp_path/'db'); now=snapshot().as_of+timedelta(minutes=30)
    day=now.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Taipei')).date()
    job=store.create_job(day,now); store.ready_job(job.id,b'mime',None)
    claimed=store.claim_delivery(day,480,'a',now)
    assert claimed and other.claim_delivery(day,480,'b',now) is None
    with pytest.raises(ValueError): other.finish_delivery(job.id,'b',DeliveryResult(status='sent',reason='ok'))
    store.recover(now+timedelta(minutes=4))
    assert store.get_job(day).state=='unknown'
    assert store.claim_delivery(day,485,'a',now+timedelta(minutes=5)) is None
    store.reconcile(job.id,'not-sent',now+timedelta(minutes=5))
    assert store.claim_delivery(day,485,'b',now+timedelta(minutes=5))


def test_retention_removes_all_text_but_keeps_numeric_and_unknown(tmp_path):
    from btc_jev.store import Store
    store=Store(tmp_path/'db'); a=article(title='UNIQUE_NEWS_TEXT'); s=snapshot(articles=[a])
    sid=store.save_snapshot(s); p=prediction(snapshot_id=sid); store.save_predictions([p])
    store.save_articles([a],s.source_health,s.as_of)
    store.save_settlement(settlement(p))
    now=s.as_of+timedelta(days=91)
    store.cleanup(now)
    with pytest.raises(ValueError,match='expired'): store.get_snapshot(sid)
    assert store.evaluated_records(now)[0][0]==p
    assert 'UNIQUE_NEWS_TEXT' not in '\n'.join(store.connection.iterdump())
