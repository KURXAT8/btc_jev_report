"""Local probability calibration. No network, model calls or mutable forecasts.

Policy constants are versioned together. Validation is later than fitting and
never refitted into the deployed temperature. Temperature preserves class rank.
"""
from collections import Counter, defaultdict
from datetime import timedelta
import math
import random

from btc_jev.evaluation import brier_score
from btc_jev.util import dt

KEYS = ('bullish', 'sideways', 'bearish')
POLICY_VERSION = 'temperature-v1'
TEMPERATURES = (.5, .67, .8, 1., 1.25, 1.5, 2., 3., 4.)
EPSILON = 1e-6
TRAIN_MIN = 120
VALIDATION_SIZE = 60
LOOKBACK_DAYS = 365
GAP_DAYS = 7
BLOCK_DAYS = 7
MIN_BLOCKS = 8


def identity(prediction):
    return {key: getattr(prediction, key) for key in
            ('horizon_days', 'model', 'rules_version', 'label_version', 'config_hash')}


def temperature_scale(probabilities, temperature):
    if not math.isfinite(temperature) or not .05 <= temperature <= 20:
        raise ValueError('invalid_temperature')
    if (set(probabilities) != set(KEYS) or
            any(not math.isfinite(v) or not 0 <= v <= 1 for v in probabilities.values()) or
            abs(math.fsum(probabilities.values()) - 1) > 1e-4):
        raise ValueError('invalid_calibration_probabilities')
    if temperature == 1:
        return dict(probabilities)
    logits = {key: math.log(max(probabilities[key], EPSILON)) / temperature for key in KEYS}
    peak = max(logits.values())
    weights = {key: math.exp(value - peak) for key, value in logits.items()}
    total = math.fsum(weights.values())
    return {key: value / total for key, value in weights.items()}


def log_loss(probabilities, actual):
    return -math.log(max(probabilities[actual], EPSILON))


def paired_metrics(pairs):
    """Each row is (raw probabilities, calibrated probabilities, actual label)."""
    n = len(pairs)
    result = {'n': n}
    for name, index in (('raw', 0), ('calibrated', 1)):
        result[name + '_brier'] = sum(brier_score(row[index], row[2]) for row in pairs) / n if n else None
        result[name + '_log_loss'] = sum(log_loss(row[index], row[2]) for row in pairs) / n if n else None
    return result


def block_upper_bound(dated_deltas):
    """Deterministic paired calendar-block bootstrap, not an IID error bar."""
    if not dated_deltas:
        return None, 0
    start = min(at for at, _ in dated_deltas)
    grouped = defaultdict(list)
    for at, delta in dated_deltas:
        grouped[(at - start).days // BLOCK_DAYS].append(delta)
    blocks = [(sum(values), len(values)) for _, values in sorted(grouped.items())]
    if len(blocks) < MIN_BLOCKS:
        return None, len(blocks)
    rng = random.Random(1729)
    estimates = []
    for _ in range(500):
        draw = rng.choices(blocks, k=len(blocks))
        estimates.append(sum(value for value, n in draw) / sum(n for value, n in draw))
    estimates.sort()
    return estimates[math.ceil(.95 * len(estimates)) - 1], len(blocks)


def fit_calibration(records, cutoff):
    cutoff = dt(cutoff)
    groups = {tuple(identity(p).items()) for p, _ in records}
    if len(groups) > 1:
        raise ValueError('mixed_calibration_identity')
    # Duplicate origins do not create evidence. Latest known settlement wins.
    eligible = {}
    for p, s in sorted(records, key=lambda row: (row[0].as_of, row[1].result_version, row[0].id)):
        if s.prediction_id != p.id:
            raise ValueError('mismatched_calibration_settlement')
        if (cutoff - timedelta(days=LOOKBACK_DAYS) <= p.as_of < cutoff and
                p.created_at < cutoff and p.due_at < cutoff and s.settled_at < cutoff):
            eligible[p.as_of] = (p, s)
    rows = [eligible[key] for key in sorted(eligible)]
    validation = rows[-VALIDATION_SIZE:]
    first_validation = validation[0][0].as_of if validation else cutoff
    train = [(p, s) for p, s in rows[:-VALIDATION_SIZE]
             if p.as_of <= first_validation - timedelta(days=GAP_DAYS)
             and p.due_at < first_validation and s.settled_at < first_validation]

    def manifest(sample):
        return [dict(prediction_id=p.id, result_version=s.result_version) for p, s in sample]

    result = dict(policy_version=POLICY_VERSION, cutoff=cutoff, status='warmup', reason='sample_count',
                  temperature=1., eligible_count=len(rows), train_count=len(train), validation_count=len(validation),
                  train_samples=manifest(train), validation_samples=manifest(validation), validation=None)
    if len(train) < TRAIN_MIN or len(validation) < VALIDATION_SIZE:
        return result
    for sample, minimum in ((train, 10), (validation, 5)):
        counts = Counter(s.actual for _, s in sample)
        if any(counts[key] < minimum for key in KEYS):
            result['reason'] = 'class_coverage'
            return result

    # The held-out labels never choose the temperature, even on rejection.
    def training_loss(temperature):
        return sum(log_loss(temperature_scale(p.distribution.probabilities, temperature), s.actual)
                   for p, s in train) / len(train)

    temperature = min(TEMPERATURES, key=lambda t: (training_loss(t), abs(t - 1)))
    pairs = [(p.distribution.probabilities, temperature_scale(p.distribution.probabilities, temperature), s.actual)
             for p, s in validation]
    metrics = paired_metrics(pairs)
    deltas = [(p.as_of, brier_score(pair[1], s.actual) - brier_score(pair[0], s.actual))
              for (p, s), pair in zip(validation, pairs)]
    upper, blocks = block_upper_bound(deltas)
    metrics.update(delta_upper_95=upper, block_count=blocks)
    accepted = (temperature != 1 and metrics['raw_brier'] - metrics['calibrated_brier'] >= .005
                and metrics['calibrated_log_loss'] <= metrics['raw_log_loss']
                and upper is not None and upper < 0)
    result.update(temperature=temperature, validation=metrics, status='accepted' if accepted else 'rejected',
                  reason='validated_improvement' if accepted else 'no_validated_improvement')
    return result
