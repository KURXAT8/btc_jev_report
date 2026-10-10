"""Weekly local learning and immutable, prospective calibration decisions."""
from collections import defaultdict
from contextlib import closing
from datetime import timedelta
import json
from pathlib import Path
import sqlite3

from btc_jev.calibration import (POLICY_VERSION, identity, fit_calibration,
                                 temperature_scale, paired_metrics)
from btc_jev.util import TAIPEI, digest, dt, dumps, utcnow


def week_cutoff(at):
    local = dt(at).astimezone(TAIPEI)
    return dt(local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=local.weekday()))


def run_id(group, cutoff):
    return digest([group, dt(cutoff), POLICY_VERSION])


class LearningStore:
    def __init__(self, store):
        self.store = store
        self.connection = store.connection

    def runs(self):
        return [json.loads(r[0]) for r in self.connection.execute(
            'SELECT payload FROM calibration_runs ORDER BY cutoff,id')]

    def get_run(self, key):
        row = self.connection.execute('SELECT payload FROM calibration_runs WHERE id=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_run(self, run):
        payload = dumps(run)
        with self.store.transaction():
            old = self.get_run(run['id'])
            if old is not None and dumps(old) != payload:
                raise ValueError('calibration_run_frozen')
            if run['id'] != run_id(run['identity'], run['cutoff']):
                raise ValueError('invalid_calibration_run_id')
            self.connection.execute('INSERT OR IGNORE INTO calibration_runs VALUES (?,?,?,?,?)',
                                    (run['id'], digest(run['identity']), dt(run['cutoff']).isoformat(),
                                     run['policy_version'], payload))

    def application(self, prediction_id):
        row = self.connection.execute('SELECT payload FROM calibrated_forecasts WHERE prediction_id=?',
                                      (prediction_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def applications(self):
        return [json.loads(r[0]) for r in self.connection.execute(
            'SELECT payload FROM calibrated_forecasts ORDER BY created_at,prediction_id')]

    def save_applications(self, applications):
        with self.store.transaction():
            for app in applications:
                old = self.application(app['prediction_id'])
                if old is not None and dumps(old) != dumps(app):
                    raise ValueError('calibration_application_frozen')
                self.connection.execute('INSERT OR IGNORE INTO calibrated_forecasts VALUES (?,?,?,?)',
                                        (app['prediction_id'], app['run_id'], dt(app['created_at']).isoformat(), dumps(app)))


def learn(store, now, mode='auto', groups=None):
    if mode not in ('auto', 'shadow', 'off'):
        raise ValueError('invalid_calibration_mode')
    if mode == 'off':
        return []
    now = dt(now)
    cutoff = week_cutoff(now)
    repository = LearningStore(store)
    if groups is None:
        groups = [identity(p) for p in store.predictions()
                  if now - timedelta(days=365) <= p.as_of <= now and p.created_at <= now]
    groups = {digest(group): group for group in groups}
    by_group = None
    result = []
    for key, group in sorted(groups.items()):
        saved = repository.get_run(run_id(group, cutoff))
        if saved:
            result.append(saved)
            continue
        if by_group is None:
            by_group = defaultdict(list)
            for p, s in store.evaluated_records(cutoff,strict_observed=True):
                by_group[digest(identity(p))].append((p, s))
        fitted = fit_calibration(by_group[key], cutoff)
        run = dict(fitted, id=run_id(group, cutoff), identity=group, created_at=now,
                   expires_at=cutoff + timedelta(days=14))
        # Return exactly the persisted representation on the first call and restarts.
        run = json.loads(dumps(run))
        repository.save_run(run)
        result.append(repository.get_run(run['id']))
    return result


def prospective_metrics(store, group, cutoff):
    """Only previously applied forecasts, with results known before this cutoff."""
    cutoff = dt(cutoff)
    apps = {app['prediction_id']: app for app in LearningStore(store).applications()
            if app['status'] == 'active' and app['policy_version'] == POLICY_VERSION
            and dt(app['created_at']) < cutoff}
    pairs = []
    for p, s in store.evaluated_records(cutoff,strict_observed=True):
        app = apps.get(p.id)
        if (app and identity(p) == group and cutoff - timedelta(days=90) <= p.as_of < cutoff
                and p.due_at < cutoff and s.settled_at < cutoff):
            pairs.append((p.distribution.probabilities, app['probabilities'], s.actual))
    return paired_metrics(pairs)


def regressed(metrics):
    return metrics['n'] >= 30 and (
        metrics['calibrated_brier'] > metrics['raw_brier'] + .01 or
        metrics['calibrated_log_loss'] > metrics['raw_log_loss'] + .02)


def raw_application(p, now, status, reason):
    return dict(prediction_id=p.id, policy_version=POLICY_VERSION, identity=identity(p), created_at=dt(now),
                run_id=None, status=status, reason=reason, temperature=1.,
                raw_probabilities=dict(p.distribution.probabilities), probabilities=dict(p.distribution.probabilities),
                candidate_probabilities=None, train_count=0, validation_count=0, monitoring=None)


def freeze_fallback(store, predictions, now):
    """A bounded learning failure must not discard an otherwise valid forecast."""
    repo = LearningStore(store)
    apps = [repo.application(p.id) or raw_application(p, now, 'error', 'learning_unavailable') for p in predictions]
    repo.save_applications(apps)
    return {app['prediction_id']: repo.application(app['prediction_id']) for app in apps}


def apply_predictions(store, predictions, now, mode='auto'):
    if mode not in ('auto', 'shadow', 'off'):
        raise ValueError('invalid_calibration_mode')
    now = dt(now)
    repo = LearningStore(store)
    saved = {p.id: repo.application(p.id) for p in predictions}
    pending = [p for p in predictions if saved[p.id] is None]
    for p in pending:
        if not p.created_at <= now < p.as_of + timedelta(minutes=25):
            raise ValueError('calibration_not_live')
    runs = {digest(run['identity']): run for run in learn(store, now, mode, [identity(p) for p in pending])}
    applications = []
    for p in pending:
        app = raw_application(p, now, 'off', 'disabled')
        run = runs.get(digest(identity(p)))
        if run:
            app.update(run_id=run['id'], status=run['status'], reason=run['reason'],
                       train_count=run['train_count'], validation_count=run['validation_count'])
            if not dt(run['cutoff']) <= p.as_of < dt(run['expires_at']) or dt(run['created_at']) > now:
                app.update(status='stale', reason='calibration_outside_validity')
            elif run['status'] == 'accepted':
                candidate = temperature_scale(p.distribution.probabilities, run['temperature'])
                monitoring = prospective_metrics(store, identity(p), p.as_of)
                app.update(temperature=run['temperature'], candidate_probabilities=candidate, monitoring=monitoring)
                if regressed(monitoring):
                    app.update(status='regression', reason='prospective_degradation')
                elif mode == 'shadow':
                    app.update(status='shadow', reason='shadow_only')
                else:
                    app.update(status='active', probabilities=candidate)
        applications.append(app)
    repo.save_applications(applications)
    return {p.id: repo.application(p.id) for p in predictions}


def learning_status(path, mode='auto', now=None):
    """Read-only inspection; does not create directories or upgrade old stores."""
    now = dt(now or utcnow())
    result = dict(mode=mode, policy_version=POLICY_VERSION, runs=[], applications=0,
                  note='尚無學習紀錄；樣本不足時保留 Jev 原始機率。')
    path = Path(path)
    if not path.exists():
        return result
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'calibration_runs', 'calibrated_forecasts'} <= tables:
            return result
        latest = {}
        for (payload,) in connection.execute('SELECT payload FROM calibration_runs ORDER BY cutoff,id'):
            run = json.loads(payload)
            latest[digest(run['identity'])] = {key: run[key] for key in
                ('identity', 'status', 'reason', 'temperature', 'train_count', 'validation_count',
                 'eligible_count', 'cutoff', 'created_at', 'expires_at', 'validation')}
        for (payload,) in connection.execute('SELECT payload FROM calibrated_forecasts ORDER BY created_at,prediction_id'):
            app = json.loads(payload)
            result['applications'] += 1
            group = latest.get(digest(app['identity']))
            if group is not None:
                group['last_application'] = dict(status=app['status'], reason=app['reason'], created_at=app['created_at'])
        for group in latest.values():
            group['expired'] = dt(group['expires_at']) <= now
        result['runs'] = list(latest.values())
        if latest:
            result['note'] = '驗證分數來自較晚保留資料；不代表已提高真實命中率。'
    return result
