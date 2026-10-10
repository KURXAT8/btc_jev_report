from datetime import timedelta
from email import policy
from email.parser import BytesParser
import json

import pytest

from tests.factories import settlement
from tests.test_calibration import cutoff
from tests.test_learning import populated_store, new_prediction
from tests.test_pipeline import environment


def test_calibration_config_modes_and_invalid_value(tmp_path):
    from btc_jev.config import load_settings, ConfigurationError
    assert load_settings({}, tmp_path / '.env').calibration_mode == 'auto'
    for mode in ('auto', 'shadow', 'off'):
        assert load_settings({'CALIBRATION_MODE': mode}, tmp_path / '.env').calibration_mode == mode
    with pytest.raises(ConfigurationError, match='CALIBRATION_MODE'):
        load_settings({'CALIBRATION_MODE': 'secret-invalid'}, tmp_path / '.env')


@pytest.mark.parametrize('mode,expected', [('auto', 'warmup'), ('shadow', 'warmup'), ('off', 'off')])
def test_daily_prepare_freezes_learning_without_extra_jev_calls(tmp_path, mode, expected):
    from btc_jev.pipeline import prepare_daily
    from btc_jev.learning import LearningStore
    store, settings, market, cross, jev, clock, calls = environment(tmp_path)
    settings = settings.model_copy(update={'calibration_mode': mode})
    job_id = prepare_daily(store, settings, market, cross, jev, lambda: clock[0])
    apps = LearningStore(store).applications()
    assert len(apps) == 3 and {app['status'] for app in apps} == {expected}
    raw = [p.model_dump_json() for p in store.predictions()]
    mime = store.get_job(job_id).mime_bytes
    prepare_daily(store, settings, market, cross, jev, lambda: clock[0])
    assert len(calls) == 3
    assert [p.model_dump_json() for p in store.predictions()] == raw
    assert store.get_job(job_id).mime_bytes == mime
    assert LearningStore(store).applications() == apps


def test_learning_timeout_preserves_successful_daily_forecast(tmp_path, monkeypatch):
    from btc_jev.pipeline import prepare_daily
    from btc_jev import learning
    store, settings, market, cross, jev, clock, calls = environment(tmp_path)
    def fail(*args, **kwargs):
        raise RuntimeError('synthetic local learner failure')
    monkeypatch.setattr(learning, 'apply_predictions', fail)
    job_id = prepare_daily(store, settings, market, cross, jev, lambda: clock[0])
    job = store.get_job(job_id)
    assert job.state == 'ready' and len(store.predictions()) == 3
    assert {a['status'] for a in learning.LearningStore(store).applications()} == {'error'}
    msg = BytesParser(policy=policy.default).parsebytes(job.mime_bytes)
    assert '分析未完成' not in msg['Subject']
    assert store.diagnostics(job_id)['calibration_error'] == 'learning_unavailable'


def test_calibrated_render_is_labeled_and_direction_uses_applied_probability(tmp_path):
    from btc_jev.learning import apply_predictions
    from btc_jev.render import render_report
    store, rows = populated_store(tmp_path)
    p = new_prediction(store, rows, cutoff(rows) + timedelta(days=7))
    apps = apply_predictions(store, [p], p.created_at)
    report = render_report(p.as_of.date(), [p], {'calibrations': apps}, None, None)
    assert 'Jev 原始機率' in report.text
    assert '本機校準機率' in report.text and '本機校準機率' in report.html
    assert 'Jev 原始確定程度：90.0%' in report.text
    assert '98.0%' in report.text
    # A genuine diffuse applied distribution changes the display policy only.
    apps[p.id]['probabilities'] = dict(bullish=.5, sideways=.25, bearish=.25)
    report = render_report(p.as_of.date(), [p], {'calibrations': apps}, None, None)
    assert '方向不明確' in report.subject
    assert p.distribution.probabilities['bullish'] == .98


def test_weekly_compares_only_prospectively_applied_pairs(tmp_path):
    from btc_jev.learning import apply_predictions
    from btc_jev.weekly import build_weekly_report
    from btc_jev.evaluation import brier_score
    store, rows = populated_store(tmp_path)
    at = cutoff(rows) + timedelta(days=7)
    p = new_prediction(store, rows, at)
    applied = apply_predictions(store, [p], p.created_at)[p.id]
    store.save_settlement(settlement(p, '100'),observed_at=p.due_at)
    shadow = new_prediction(store, rows, at + timedelta(days=1))
    apply_predictions(store, [shadow], shadow.created_at, mode='shadow')
    store.save_settlement(settlement(shadow, '100'),observed_at=shadow.due_at)
    report = build_weekly_report(store, at + timedelta(days=14), at + timedelta(days=14))
    group = next(g for g in report.groups if g['window'] == 'cumulative' and g['horizon'] == 3)
    metrics = group['calibration']['temperature-v1']
    assert metrics['n'] == 1
    assert metrics['raw_brier'] == pytest.approx(brier_score(p.distribution.probabilities, 'sideways'))
    assert metrics['calibrated_brier'] == pytest.approx(brier_score(applied['probabilities'], 'sideways'))
    assert metrics['samples'][0]['prediction_id'] == p.id
    assert metrics['samples'][0]['run_id'] == applied['run_id']
    assert group['settled_count'] == len(rows) + 2


def test_weekly_excludes_application_not_yet_known_at_report_cutoff(tmp_path):
    from btc_jev.learning import apply_predictions
    from btc_jev.weekly import calibration_metrics
    store, rows = populated_store(tmp_path)
    p = new_prediction(store, rows, cutoff(rows) + timedelta(days=7))
    app = apply_predictions(store, [p], p.created_at)[p.id]
    settled = settlement(p, '100')
    assert calibration_metrics([(p, settled)], {p.id: app}, app['created_at']) == {}


def test_learning_cli_is_offline_and_status_does_not_create_database(tmp_path, monkeypatch, capsys):
    from btc_jev.cli import main
    import httpx
    monkeypatch.chdir(tmp_path)
    def no_network(*args, **kwargs):
        raise AssertionError('offline learning must not create HTTP client')
    monkeypatch.setattr(httpx, 'Client', no_network)
    assert main(['learning-status']) == 0
    status = json.loads(capsys.readouterr().out)
    assert status['mode'] == 'auto' and not (tmp_path / 'data').exists()
    assert main(['learn']) == 0
    assert json.loads(capsys.readouterr().out)['runs'] == []
    assert (tmp_path / 'data' / 'btc-jev.sqlite3').exists()
