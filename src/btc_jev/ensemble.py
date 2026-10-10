"""Small, auditable ensemble policy used as a shadow/adoption layer.

The formal Jev distribution is never modified by this module.  Candidates are
normalized averages and can only be adopted after a prospective validation
gate.
"""
import math

KEYS = ('bullish', 'sideways', 'bearish')


def normalize(values):
    values = {key: max(0.0, float(values.get(key, 0.0))) for key in KEYS}
    total = math.fsum(values.values())
    return ({key: 1 / 3 for key in KEYS} if total <= 0 else
            {key: values[key] / total for key in KEYS})


def _average(rows):
    rows = [normalize(row) for row in rows if row is not None]
    if not rows:
        return None
    return normalize({key: math.fsum(row[key] for row in rows) / len(rows) for key in KEYS})


def ensemble_candidates(formal, baselines=None, roles=None):
    """Return formal, one-source, and all-available normalized candidates."""
    sources = {}
    for name, probabilities in sorted((baselines or {}).items()):
        if probabilities is not None:
            sources[name] = normalize(probabilities)
    for name, probabilities in sorted((roles or {}).items()):
        if probabilities is not None:
            sources[name] = normalize(probabilities)
    result = {'formal': normalize(formal)}
    for name, probabilities in sources.items():
        result[f'formal+{name}'] = _average([formal, probabilities])
    result['all_available'] = _average([formal, *sources.values()])
    return result


def low_signal(probabilities, role_probabilities=None, disagreement=0.30):
    probabilities = normalize(probabilities)
    ordered = sorted(probabilities.values(), reverse=True)
    if ordered[0] < .55 or ordered[0] - ordered[1] < .15:
        return True
    if role_probabilities:
        winner = max(probabilities, key=probabilities.get)
        if any(max(normalize(row), key=normalize(row).get) != winner for row in role_probabilities):
            return True
        if any(max(abs(normalize(row)[key] - probabilities[key]) for key in KEYS) > disagreement
               for row in role_probabilities):
            return True
    return False


def recent_fallback(rows, limit=30):
    """Regression-safe fallback from the latest adopted probability rows."""
    selected = list(rows)[-limit:]
    return _average([probabilities for probabilities, _ in selected]) or {key: 1 / 3 for key in KEYS}


def _brier(probabilities, actual):
    return sum((probabilities[key] - (key == actual)) ** 2 for key in KEYS)


def _metrics(rows, candidate):
    if not rows:
        return {'n': 0, 'accuracy': None, 'brier': None}
    accuracy = sum(max(candidate(row), key=candidate(row).get) == row['actual'] for row in rows) / len(rows)
    brier = sum(_brier(candidate(row), row['actual']) for row in rows) / len(rows)
    return {'n': len(rows), 'accuracy': accuracy, 'brier': brier}


def select_candidate(rows):
    """Prospectively gate a candidate; return warmup/rejected/accepted metadata."""
    rows = sorted(rows, key=lambda row: row['as_of'])
    if len(rows) < 45:
        return {'status': 'warmup', 'reason': 'minimum_samples', 'train_count': max(0, len(rows)-15), 'validation_count': min(15, len(rows))}
    train, validation = rows[:-15], rows[-15:]
    if len(train) < 30 or any(sum(row['actual'] == key for row in train) < 5 for key in KEYS):
        return {'status': 'warmup', 'reason': 'class_or_train_warmup', 'train_count': len(train), 'validation_count': len(validation)}
    if any(sum(row['actual'] == key for row in validation) < 5 for key in KEYS):
        return {'status': 'warmup', 'reason': 'validation_class_warmup', 'train_count': len(train), 'validation_count': len(validation)}
    names = sorted({name for row in rows for name in row.get('candidates', {})})
    formal = lambda row: normalize(row['formal'])
    base = _metrics(validation, formal)
    scored = []
    for name in names:
        candidate = lambda row, n=name: normalize(row.get('candidates', {}).get(n, row['formal']))
        metrics = _metrics(validation, candidate)
        if metrics['accuracy'] >= base['accuracy'] + .02 and metrics['brier'] <= base['brier'] - .005:
            scored.append((metrics['brier'], name, metrics))
    if not scored:
        return {'status': 'rejected', 'reason': 'acceptance_gate', 'train_count': len(train), 'validation_count': len(validation), 'formal': base}
    _, name, metrics = min(scored)
    return {'status': 'accepted', 'candidate': name, 'train_count': len(train), 'validation_count': len(validation), 'formal': base, 'metrics': metrics}
