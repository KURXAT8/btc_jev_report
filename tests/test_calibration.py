from datetime import datetime, timedelta, timezone
import math

import pytest

from tests.factories import prediction, settlement

KEYS = ('bullish', 'sideways', 'bearish')


def history(n=240, horizon=3):
    """60% correct, balanced labels, but the forecast claims 98%."""
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(n):
        winner = (i // 10) % 3
        actual = winner if i % 10 < 6 else (winner + 1 + i % 2) % 3
        p = prediction(as_of=start + timedelta(days=i), horizon_days=horizon,
                       distribution=dict(probabilities={k: .98 if j == winner else .01 for j, k in enumerate(KEYS)},
                                         choice=KEYS[winner], confidence=.9))
        s = settlement(p, target_price={'bullish': '110', 'sideways': '100', 'bearish': '90'}[KEYS[actual]])
        rows.append((p, s))
    return rows


def cutoff(rows):
    return rows[-1][1].settled_at + timedelta(days=2)


def test_temperature_is_normalized_monotone_and_does_not_mutate_raw():
    from btc_jev.calibration import temperature_scale
    raw = {'bullish': .7, 'sideways': .2, 'bearish': .1}
    assert temperature_scale(raw, 1) == raw
    softened = temperature_scale(raw, 2)
    sharpened = temperature_scale(raw, .5)
    assert sum(softened.values()) == pytest.approx(1)
    assert softened['bullish'] < raw['bullish'] < sharpened['bullish']
    assert list(sorted(softened, key=softened.get)) == list(sorted(raw, key=raw.get))
    assert raw == {'bullish': .7, 'sideways': .2, 'bearish': .1}
    assert all(math.isfinite(v) for v in temperature_scale(dict(bullish=1., sideways=0., bearish=0.), 4).values())


@pytest.mark.parametrize('temperature', [0, -1, float('nan'), float('inf')])
def test_bad_temperature_is_rejected(temperature):
    from btc_jev.calibration import temperature_scale
    with pytest.raises(ValueError):
        temperature_scale(dict(bullish=.7, sideways=.2, bearish=.1), temperature)


def test_independent_later_validation_accepts_overconfidence_correction():
    from btc_jev.calibration import fit_calibration
    rows = history()
    result = fit_calibration(rows, cutoff(rows))
    assert result['status'] == 'accepted'
    assert result['temperature'] > 1
    assert result['validation']['calibrated_brier'] < result['validation']['raw_brier']
    assert result['validation']['delta_upper_95'] < 0
    assert result['validation']['block_count'] >= 8
    assert result['validation_count'] == 60
    assert result['train_count'] >= 120


def test_warmup_and_class_coverage_do_not_fit():
    from btc_jev.calibration import fit_calibration
    rows = history(40)
    assert fit_calibration(rows, cutoff(rows))['status'] == 'warmup'
    rows = [(p, settlement(p, '110')) for p, _ in history()]
    result = fit_calibration(rows, cutoff(rows))
    assert result['status'] == 'warmup'
    assert result['reason'] == 'class_coverage'


def test_future_late_and_boundary_records_are_excluded():
    from btc_jev.calibration import fit_calibration
    rows = history(horizon=7)
    at = cutoff(rows)
    late_p, late_s = rows[20]
    rows[20] = (late_p, late_s.model_copy(update={'settled_at': at}))
    result = fit_calibration(rows, at)
    used = {r['prediction_id'] for r in result['train_samples'] + result['validation_samples']}
    assert late_p.id not in used
    validation_start = min(p.as_of for p, _ in rows if p.id in {r['prediction_id'] for r in result['validation_samples']})
    for p, s in rows:
        if p.id in {r['prediction_id'] for r in result['train_samples']}:
            assert p.as_of <= validation_start - timedelta(days=7)
            assert p.due_at < validation_start and s.settled_at < validation_start
    future = history(260, horizon=7)[-1]
    assert fit_calibration(rows + [future], at) == result


def test_later_regime_change_rejects_candidate_without_refitting():
    from btc_jev.calibration import fit_calibration
    rows = history()
    for i in range(len(rows)-60, len(rows)):
        p, _ = rows[i]
        rows[i] = (p, settlement(p, {'bullish': '110', 'sideways': '100', 'bearish': '90'}[p.distribution.choice]))
    result = fit_calibration(rows, cutoff(rows))
    assert result['temperature'] > 1  # trained on earlier, overconfident forecasts
    assert result['status'] == 'rejected'
    assert result['validation']['calibrated_brier'] > result['validation']['raw_brier']


def test_mixed_identity_is_rejected_and_order_does_not_matter():
    from btc_jev.calibration import fit_calibration
    rows = history()
    assert fit_calibration(list(reversed(rows)), cutoff(rows)) == fit_calibration(rows, cutoff(rows))
    p, s = rows[0]
    rows[0] = (p.model_copy(update={'config_hash': 'different'}), s)
    with pytest.raises(ValueError, match='mixed_calibration_identity'):
        fit_calibration(rows, cutoff(rows))


def test_duplicates_do_not_manufacture_sample_size():
    from btc_jev.calibration import fit_calibration
    rows = history(40)
    result = fit_calibration(rows * 6, cutoff(rows))
    assert result['eligible_count'] == 40
    assert result['status'] == 'warmup'
