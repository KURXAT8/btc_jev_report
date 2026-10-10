from datetime import timedelta
import json

import pytest

from tests.factories import prediction, settlement, snapshot
from tests.test_calibration import history, cutoff


def populated_store(tmp_path):
    from btc_jev.store import Store
    store = Store(tmp_path / 'db')
    sid = store.save_snapshot(snapshot())
    rows = [(p.model_copy(update={'snapshot_id': sid}), s) for p, s in history()]
    store.save_predictions([p for p, _ in rows])
    for _, s in rows:
        store.save_settlement(s, observed_at=s.settled_at)
    return store, rows


def new_prediction(store, rows, at):
    p = prediction(as_of=at, snapshot_id=rows[0][0].snapshot_id,
                   distribution=dict(probabilities=dict(bullish=.98, sideways=.01, bearish=.01),
                                     choice='bullish', confidence=.9))
    store.save_predictions([p])
    return p


def test_weekly_learning_is_immutable_across_restart_and_late_results(tmp_path):
    from btc_jev.learning import learn, LearningStore
    from btc_jev.store import Store
    store, rows = populated_store(tmp_path)
    at = cutoff(rows) + timedelta(days=7)
    first = learn(store, at)
    assert len(first) == 1 and first[0]['status'] == 'accepted'
    assert first[0]['created_at'] == at.isoformat()
    assert learn(store, at + timedelta(hours=1)) == first
    latest = rows[-1]
    corrected = settlement(latest[0], '90', at + timedelta(hours=1)).model_copy(update={'result_version': 2})
    store.save_settlement(corrected, observed_at=at + timedelta(hours=1))
    store.close()
    store = Store(tmp_path / 'db')
    assert learn(store, at + timedelta(hours=2)) == first
    second = learn(store, at + timedelta(days=7))
    assert second[0]['id'] != first[0]['id']
    assert len(LearningStore(store).runs()) == 2
    with pytest.raises(ValueError, match='calibration_run_frozen'):
        LearningStore(store).save_run(first[0] | {'temperature': .5})


def test_a_backdated_late_correction_is_not_known_at_an_earlier_learning_cutoff(tmp_path):
    from btc_jev.learning import learn
    store, rows = populated_store(tmp_path)
    at = cutoff(rows) + timedelta(days=7)
    # This origin stays inside the later model's validation window so its
    # corrected target can be scored there without entering training.
    p, _ = rows[-50]
    correction = settlement(p, '90', at - timedelta(seconds=1)).model_copy(update={'result_version': 2})
    store.save_settlement(correction, observed_at=at + timedelta(seconds=1))
    # The first weekly run happens after a delayed revision. The revision's
    # claimed settlement time predates the fixed cutoff, but it was not yet known.
    original = learn(store, at + timedelta(hours=1))[0]
    original_versions = {s['prediction_id']: s['result_version']
                         for s in original['train_samples'] + original['validation_samples']}
    assert original_versions[p.id] == 1
    # Reruns in the same fixed weekly window remain frozen.
    assert learn(store, at + timedelta(hours=2))[0] == original
    # The later weekly cutoff may use the new version.
    later = learn(store, at + timedelta(days=7))[0]
    assert later['id'] != original['id']
    assert later['cutoff'] != original['cutoff']
    later_version = next(version for q, version in store.evaluated_records(later['cutoff'],strict_observed=True)
                         if q.id == p.id)
    assert later_version.result_version == 2
    later_versions = {s['prediction_id']: s['result_version'] for s in later['train_samples'] + later['validation_samples']}
    assert later_versions[p.id] == 2


def test_mode_off_does_not_train_and_shadow_does_not_apply(tmp_path):
    from btc_jev.learning import learn, apply_predictions, LearningStore
    store, rows = populated_store(tmp_path)
    at = cutoff(rows) + timedelta(days=7)
    assert learn(store, at, mode='off') == []
    assert LearningStore(store).runs() == []
    p = new_prediction(store, rows, at)
    raw = p.model_dump_json()
    result = apply_predictions(store, [p], p.created_at, mode='shadow')[p.id]
    assert result['status'] == 'shadow'
    assert result['probabilities'] == dict(p.distribution.probabilities)
    assert result['candidate_probabilities']['bullish'] < .98
    assert result['run_id']
    assert next(q for q in store.predictions() if q.id == p.id).model_dump_json() == raw
    # A restart/mode change cannot change an already frozen report decision.
    assert apply_predictions(store, [p], p.created_at + timedelta(seconds=1), mode='auto')[p.id] == result


def test_auto_freezes_only_forward_probabilities_and_keeps_raw(tmp_path):
    from btc_jev.learning import apply_predictions, LearningStore
    store, rows = populated_store(tmp_path)
    at = cutoff(rows) + timedelta(days=7)
    p = new_prediction(store, rows, at)
    result = apply_predictions(store, [p], p.created_at)[p.id]
    assert result['status'] == 'active'
    assert result['probabilities']['bullish'] < .98
    assert result['raw_probabilities'] == dict(p.distribution.probabilities)
    assert len(LearningStore(store).applications()) == 1
    with pytest.raises(ValueError, match='calibration_application_frozen'):
        LearningStore(store).save_applications([result | {'status': 'off'}])
    # Historical replays are not prospective applications.
    with pytest.raises(ValueError, match='calibration_not_live'):
        apply_predictions(store, [rows[0][0]], p.created_at)


def test_warmup_and_new_identity_never_reuse_another_groups_temperature(tmp_path):
    from btc_jev.learning import apply_predictions
    store, rows = populated_store(tmp_path)
    at = cutoff(rows) + timedelta(days=7)
    p = prediction(as_of=at, snapshot_id=rows[0][0].snapshot_id, config_hash='new-prompt')
    store.save_predictions([p])
    result = apply_predictions(store, [p], p.created_at)[p.id]
    assert result['status'] == 'warmup'
    assert result['probabilities'] == dict(p.distribution.probabilities)
    assert result['train_count'] == 0


def test_online_regression_uses_only_known_forward_results(tmp_path):
    from btc_jev.learning import apply_predictions, prospective_metrics, identity
    store, rows = populated_store(tmp_path)
    at = cutoff(rows) + timedelta(days=7)
    live = []
    # All predictions are actually issued before any of their results arrive.
    for i in range(30):
        p = new_prediction(store, rows, at + timedelta(days=i))
        result = apply_predictions(store, [p], p.created_at)[p.id]
        assert result['status'] == 'active'
        live.append(p)
    decision_at = at + timedelta(days=34)
    for p in live:
        store.save_settlement(settlement(p, '110', decision_at - timedelta(seconds=1)),
                              observed_at=decision_at - timedelta(seconds=1))
    assert prospective_metrics(store, identity(live[0]), decision_at + timedelta(microseconds=1))['n'] == 30
    next_p = new_prediction(store, rows, decision_at)
    result = apply_predictions(store, [next_p], next_p.created_at)[next_p.id]
    assert result['status'] == 'regression'
    assert result['probabilities'] == dict(next_p.distribution.probabilities)
    assert prospective_metrics(store, identity(live[0]), decision_at - timedelta(seconds=1))['n'] == 0


def test_status_reads_without_creating_or_migrating_database(tmp_path):
    from btc_jev.learning import learning_status
    path = tmp_path / 'missing' / 'db'
    status = learning_status(path, 'auto')
    assert status['runs'] == [] and status['applications'] == 0
    assert not path.parent.exists()


def test_storage_upgrade_preserves_frozen_predictions(tmp_path):
    from datetime import datetime, timezone
    from btc_jev.store import Store
    from btc_jev.learning import LearningStore
    store, _ = populated_store(tmp_path)
    raw = [p.model_dump_json() for p in store.predictions()]
    latest_known = datetime.now(timezone.utc) + timedelta(seconds=1)
    job = store.create_job(store.predictions()[0].as_of.date(), store.predictions()[0].created_at)
    store.ready_job(job.id, b'preserved-frozen-message', None)
    saved_settlements = store.connection.execute('SELECT COUNT(*) FROM settlements').fetchone()[0]
    store.connection.execute('DROP TABLE calibrated_forecasts')
    store.connection.execute('DROP TABLE calibration_runs')
    store.connection.execute('ALTER TABLE settlements DROP COLUMN observed_at')
    store.connection.execute('PRAGMA user_version=4')
    store.close()
    store = Store(tmp_path / 'db')
    assert [p.model_dump_json() for p in store.predictions()] == raw
    assert store.get_job(job.id).mime_bytes == b'preserved-frozen-message'
    assert store.connection.execute('SELECT COUNT(*) FROM settlements').fetchone()[0] == saved_settlements
    # Legacy settlements have no verifiable ingestion time; migration records
    # them as known only at migration, never at a guessed historical date.
    assert store.evaluated_records(latest_known, strict_observed=True)
    assert not store.evaluated_records(latest_known - timedelta(days=2), strict_observed=True)
    assert LearningStore(store).runs() == []
    assert store.connection.execute('PRAGMA user_version').fetchone()[0] == 7
    assert 'observed_at' in {row['name'] for row in store.connection.execute('PRAGMA table_info(settlements)')}
    assert 'reserved_input_tokens' in {row['name'] for row in store.connection.execute('PRAGMA table_info(daily_attempts)')}
