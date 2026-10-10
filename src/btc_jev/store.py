"""SQLite transactions protect frozen inputs, budgets and delivery claims.

No transaction is held during a network operation. Text lives in separately
expiring payloads; immutable identities and numeric evaluation remain available.
"""
from contextlib import contextmanager
from datetime import date,datetime,timedelta
from decimal import Decimal
import hashlib
import json
import sqlite3
from pathlib import Path
from btc_jev.domain import (Snapshot,Prediction,Settlement,Article,CalendarEvent,SourceHealth,
    BaselineForecast,StageResult,Job,WeeklyReport)
from btc_jev.util import dumps,digest,dt,TAIPEI,utcnow

MAX_DAILY_ESTIMATED_INPUT_TOKENS=3_571_428  # $0.15 at the published $42/M input-token rate.
MAX_DAILY_ATTEMPTS=15  # Five Jev stages, at most three attempts each.


def stamp(value): return dt(value).isoformat()


class Store:
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.connection=sqlite3.connect(self.path,timeout=10,isolation_level=None)
        self.connection.row_factory=sqlite3.Row
        self.connection.execute('PRAGMA foreign_keys=ON')
        self.connection.execute('PRAGMA journal_mode=WAL')
        self.connection.execute('PRAGMA secure_delete=ON')
        version=self.connection.execute('PRAGMA user_version').fetchone()[0]
        if version>7: raise ValueError('unsupported_database_version')
        self.connection.executescript('''
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY,as_of TEXT UNIQUE NOT NULL,numeric TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS text_blobs(id TEXT PRIMARY KEY,payload TEXT,created_at TEXT NOT NULL,text_expired_at TEXT);
        CREATE TABLE IF NOT EXISTS predictions(id TEXT PRIMARY KEY,snapshot_id TEXT NOT NULL REFERENCES snapshots(id),as_of TEXT NOT NULL,horizon INTEGER NOT NULL,model TEXT NOT NULL,rules TEXT NOT NULL,payload TEXT NOT NULL,UNIQUE(as_of,horizon,model,rules));
        CREATE TABLE IF NOT EXISTS settlements(prediction_id TEXT REFERENCES predictions(id),version INTEGER,settled_at TEXT,payload TEXT,PRIMARY KEY(prediction_id,version));
        CREATE TABLE IF NOT EXISTS source_runs(kind TEXT,completed_at TEXT,health TEXT,PRIMARY KEY(kind,completed_at));
        CREATE TABLE IF NOT EXISTS article_versions(id TEXT PRIMARY KEY,source_id TEXT,content_hash TEXT,known_at TEXT,numeric TEXT);
        CREATE TABLE IF NOT EXISTS article_observations(version_id TEXT REFERENCES article_versions(id),observed_at TEXT,PRIMARY KEY(version_id,observed_at));
        INSERT OR IGNORE INTO article_observations SELECT id,known_at FROM article_versions;
        CREATE TABLE IF NOT EXISTS calendar_versions(id TEXT PRIMARY KEY,source_id TEXT,known_at TEXT,payload TEXT);
        CREATE TABLE IF NOT EXISTS calendar_fetches(source TEXT,completed_at TEXT,event_ids TEXT,PRIMARY KEY(source,completed_at));
        CREATE TABLE IF NOT EXISTS price_anchors(at TEXT PRIMARY KEY,price TEXT,known_at TEXT,id TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS historical_features(at TEXT PRIMARY KEY,payload TEXT);
        CREATE TABLE IF NOT EXISTS baseline_forecasts(id TEXT PRIMARY KEY,as_of TEXT,horizon INTEGER,payload TEXT);
        CREATE TABLE IF NOT EXISTS daily_attempts(day TEXT PRIMARY KEY,count INTEGER NOT NULL,reserved_input_tokens INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS stage_attempts(day TEXT,stage TEXT,request_hash TEXT,count INTEGER NOT NULL,PRIMARY KEY(day,stage));
        CREATE TABLE IF NOT EXISTS model_stages(day TEXT,stage TEXT,request_hash TEXT,payload TEXT,PRIMARY KEY(day,stage));
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,day TEXT UNIQUE,payload TEXT NOT NULL,snapshot_id TEXT REFERENCES snapshots(id),failure TEXT);
        CREATE TABLE IF NOT EXISTS job_diagnostics(job_id TEXT PRIMARY KEY REFERENCES jobs(id),payload TEXT);
        CREATE TABLE IF NOT EXISTS weekly_reports(week_start TEXT PRIMARY KEY,id TEXT UNIQUE,payload TEXT);
        CREATE TABLE IF NOT EXISTS weekly_report_samples(report_id TEXT REFERENCES weekly_reports(id),prediction_id TEXT REFERENCES predictions(id),result_version INTEGER,PRIMARY KEY(report_id,prediction_id));
        CREATE TABLE IF NOT EXISTS calibration_runs(id TEXT PRIMARY KEY,group_key TEXT NOT NULL,cutoff TEXT NOT NULL,policy TEXT NOT NULL,payload TEXT NOT NULL,UNIQUE(group_key,cutoff,policy));
        CREATE TABLE IF NOT EXISTS calibrated_forecasts(prediction_id TEXT PRIMARY KEY REFERENCES predictions(id),run_id TEXT REFERENCES calibration_runs(id),created_at TEXT NOT NULL,payload TEXT NOT NULL);
        ''')
        if version < 6:
            with self.transaction():
                columns={row['name'] for row in self.connection.execute('PRAGMA table_info(settlements)')}
                if 'observed_at' not in columns:
                    self.connection.execute('ALTER TABLE settlements ADD COLUMN observed_at TEXT')
                # Old settlement rows have no trustworthy record of when they
                # arrived; conservatively make them known at this migration.
                self.connection.execute('UPDATE settlements SET observed_at=? WHERE observed_at IS NULL',(stamp(utcnow()),))
                self.connection.execute('PRAGMA user_version=6')
        if version < 7:
            with self.transaction():
                columns={row['name'] for row in self.connection.execute('PRAGMA table_info(daily_attempts)')}
                if 'reserved_input_tokens' not in columns:
                    self.connection.execute('ALTER TABLE daily_attempts ADD COLUMN reserved_input_tokens INTEGER NOT NULL DEFAULT 0')
                self.connection.execute('PRAGMA user_version=7')
        self.path.chmod(0o600)

    @contextmanager
    def transaction(self):
        self.connection.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.connection.execute('COMMIT')
        except BaseException:
            self.connection.execute('ROLLBACK')
            raise

    def close(self): self.connection.close()

    def metadata(self,key,default=None):
        row=self.connection.execute('SELECT value FROM metadata WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_metadata(self,key,value):
        with self.transaction():
            self.connection.execute('INSERT OR IGNORE INTO metadata VALUES (?,?)',(key,dumps(value)))

    def _blob(self,key,payload,created):
        self.connection.execute('INSERT OR IGNORE INTO text_blobs VALUES (?,?,?,NULL)',(key,dumps(payload),stamp(created)))

    def save_snapshot(self,snapshot,job_id=None):
        key=digest(snapshot)
        numeric=snapshot.model_dump(mode='json'); numeric['articles']=[]
        with self.transaction():
            row=self.connection.execute('SELECT id FROM snapshots WHERE as_of=?',(stamp(snapshot.as_of),)).fetchone()
            if row and row[0]!=key: raise ValueError('snapshot_frozen')
            self.connection.execute('INSERT OR IGNORE INTO snapshots VALUES (?,?,?)',(key,stamp(snapshot.as_of),dumps(numeric)))
            self._blob(key,snapshot,snapshot.as_of)
            if job_id is not None:
                self.connection.execute("UPDATE jobs SET snapshot_id=? WHERE id=? AND (snapshot_id IS NULL OR snapshot_id=?)",(key,job_id,key))
            from btc_jev.indicators import compute_features
            f=compute_features(snapshot)
            payload=dict(oi_24h=f['oi_24h_change_pct'],funding=snapshot.funding)
            self.connection.execute('INSERT OR IGNORE INTO historical_features VALUES (?,?)',(stamp(snapshot.as_of),dumps(payload)))
        return key

    def snapshot_at(self,as_of):
        row=self.connection.execute("SELECT id FROM snapshots WHERE as_of=?",(stamp(as_of),)).fetchone()
        return row[0] if row else None

    def get_snapshot(self,key):
        row=self.connection.execute('SELECT payload FROM text_blobs WHERE id=?',(key,)).fetchone()
        if not row or row[0] is None: raise ValueError('snapshot_text_expired_or_missing')
        return Snapshot.model_validate_json(row[0])

    def snapshot_numeric(self,key):
        row=self.connection.execute('SELECT numeric FROM snapshots WHERE id=?',(key,)).fetchone()
        return json.loads(row[0]) if row else {}

    def save_predictions(self,predictions):
        with self.transaction():
            for p in predictions:
                payload=p.model_dump_json()
                row=self.connection.execute('SELECT payload FROM predictions WHERE id=? OR (as_of=? AND horizon=? AND model=? AND rules=?)',
                    (p.id,stamp(p.as_of),p.horizon_days,p.model,p.rules_version)).fetchone()
                if row and row[0]!=payload: raise ValueError('prediction_frozen')
                self.connection.execute('INSERT OR IGNORE INTO predictions VALUES (?,?,?,?,?,?,?)',
                    (p.id,p.snapshot_id,stamp(p.as_of),p.horizon_days,p.model,p.rules_version,payload))

    def predictions(self):
        return [Prediction.model_validate_json(r[0]) for r in self.connection.execute('SELECT payload FROM predictions ORDER BY as_of,horizon')]

    def evaluated_records(self,cutoff,*,strict_observed=False):
        cutoff=dt(cutoff)
        result=[]
        known_operator='<' if strict_observed else '<='
        for p in self.predictions():
            row=self.connection.execute(f'SELECT payload FROM settlements WHERE prediction_id=? AND settled_at<=? AND observed_at{known_operator}? ORDER BY version DESC LIMIT 1',(p.id,stamp(cutoff),stamp(cutoff))).fetchone()
            matured=(p.due_at<cutoff if strict_observed else p.due_at<=cutoff)
            issued=(p.created_at<cutoff if strict_observed else p.created_at<=cutoff)
            if row and issued and matured: result.append((p,Settlement.model_validate_json(row[0])))
        return result

    def pending_settlements(self,as_of):
        settled={p.id for p,s in self.evaluated_records(as_of)}
        return [p for p in self.predictions() if p.due_at<=as_of and p.id not in settled]

    def save_settlement(self,result,observed_at=None):
        known_at=max(dt(observed_at) if observed_at is not None else utcnow(),dt(result.settled_at))
        with self.transaction():
            row=self.connection.execute('SELECT payload FROM predictions WHERE id=?',(result.prediction_id,)).fetchone()
            if not row: raise ValueError('unknown_prediction')
            from btc_jev.evaluation import settle
            expected=settle(Prediction.model_validate_json(row[0]),result.target_price,result.settled_at)
            if result.actual!=expected.actual or result.return_pct!=expected.return_pct: raise ValueError('incorrect_settlement')
            existing=self.connection.execute('SELECT payload FROM settlements WHERE prediction_id=? AND version=?',(result.prediction_id,result.result_version)).fetchone()
            if existing and existing[0]!=result.model_dump_json(): raise ValueError('settlement_version_frozen')
            latest=self.connection.execute('SELECT MAX(version) FROM settlements WHERE prediction_id=?',(result.prediction_id,)).fetchone()[0] or 0
            if not existing and result.result_version!=latest+1: raise ValueError('settlement_version_gap')
            self.connection.execute('INSERT OR IGNORE INTO settlements VALUES (?,?,?,?,?)',(result.prediction_id,result.result_version,stamp(result.settled_at),result.model_dump_json(),stamp(known_at)))

    def save_articles(self,articles,health,completed_at):
        with self.transaction():
            for a in articles:
                known=max(completed_at,a.first_seen_at,a.fetched_at)
                key=digest([a.id,a.content_hash])
                numeric=a.model_dump(mode='json'); numeric.pop('title'); numeric.pop('summary')
                self.connection.execute('INSERT OR IGNORE INTO article_versions VALUES (?,?,?,?,?)',
                    (key,a.id,a.content_hash,stamp(known),dumps(numeric)))
                self._blob('article:'+key,a,known)
                self.connection.execute('INSERT OR IGNORE INTO article_observations VALUES (?,?)',(key,stamp(known)))
            self.connection.execute('INSERT OR IGNORE INTO source_runs VALUES (?,?,?)',('news',stamp(completed_at),dumps(health)))

    def _health(self,kind,as_of):
        rows=self.connection.execute('SELECT health FROM source_runs WHERE kind=? AND completed_at<=? ORDER BY completed_at',(kind,stamp(as_of)))
        health={}
        for row in rows:
            for source,value in json.loads(row[0]).items():
                h=SourceHealth(**value); old=health.get(source)
                if h.last_success_at is None and old:
                    h=h.model_copy(update={'last_success_at':old.last_success_at})
                health[source]=h
        return health

    def _news_candidates(self,as_of):
        rows=self.connection.execute('SELECT v.source_id,b.payload,v.known_at,MAX(o.observed_at) AS observed_at FROM article_versions v JOIN text_blobs b ON b.id="article:"||v.id JOIN article_observations o ON o.version_id=v.id WHERE o.observed_at<=? GROUP BY v.id ORDER BY observed_at',(stamp(as_of),))
        latest={}
        for row in rows:
            if row[1]:
                article=Article.model_validate_json(row[1])
                latest[row[0]]=article.model_copy(update={'first_seen_at':dt(row[2]),'fetched_at':dt(row[3])})
        return list(latest.values())

    def cached_news(self,as_of):
        from btc_jev.sources.news import select_articles
        return select_articles(self._news_candidates(as_of),as_of)[0],self._health('news',as_of)

    def news_counts(self,as_of):
        from btc_jev.sources.news import select_articles
        return select_articles(self._news_candidates(as_of),as_of)[1]

    def save_calendar(self,events,health,completed_at):
        with self.transaction():
            for event in events:
                key=digest([event.id,event.content_hash])
                self.connection.execute('INSERT OR IGNORE INTO calendar_versions VALUES (?,?,?,?)',
                    (key,event.id,stamp(max(completed_at,event.known_at)),event.model_dump_json()))
            for source,h in health.items():
                if h.status=='ok':
                    keys=[digest([e.id,e.content_hash]) for e in events if e.source==source]
                    self.connection.execute('INSERT OR IGNORE INTO calendar_fetches VALUES (?,?,?)',(source,stamp(completed_at),dumps(keys)))
            self.connection.execute('INSERT OR IGNORE INTO source_runs VALUES (?,?,?)',('calendar',stamp(completed_at),dumps(health)))

    def cached_calendar(self,as_of):
        latest={}
        for source,completed,ids in self.connection.execute('SELECT source,completed_at,event_ids FROM calendar_fetches WHERE completed_at<=? ORDER BY completed_at',(stamp(as_of),)):
            latest[source]=json.loads(ids)
        events=[]
        for ids in latest.values():
            for key in ids:
                row=self.connection.execute('SELECT payload FROM calendar_versions WHERE id=? AND known_at<=?',(key,stamp(as_of))).fetchone()
                if row: events.append(CalendarEvent.model_validate_json(row[0]))
        from btc_jev.sources.calendars import event_in_window
        return [e for e in events if event_in_window(e,as_of,7)],self._health('calendar',as_of)

    def save_anchor(self,as_of,price,fetched_at):
        if price<=0 or not price.is_finite() or fetched_at<as_of: raise ValueError('invalid_anchor')
        with self.transaction():
            row=self.connection.execute('SELECT price FROM price_anchors WHERE at=?',(stamp(as_of),)).fetchone()
            if row and Decimal(row[0])!=price: raise ValueError('anchor_frozen')
            self.connection.execute('INSERT OR IGNORE INTO price_anchors VALUES (?,?,?,?)',
                (stamp(as_of),str(price),stamp(fetched_at),digest([as_of,price])))

    def historical_outcomes(self,horizon_days,as_of,label_version):
        if label_version!='labels-v1': return []
        from btc_jev.evaluation import classify_return
        rows={dt(r['at']):r for r in self.connection.execute('SELECT * FROM price_anchors WHERE known_at<?',(stamp(as_of),))}
        results=[]
        for at,row in sorted(rows.items()):
            target=rows.get(at+timedelta(days=horizon_days))
            if target and dt(target['at'])>=as_of-timedelta(days=365):
                results.append(dict(id=digest([row['id'],target['id']]),input_ids=[row['id'],target['id']],
                    due_at=target['at'],known_at=max(row['known_at'],target['known_at']),
                    actual=classify_return(100*(Decimal(target['price'])/Decimal(row['price'])-1),horizon_days)))
        return results

    def historical_features(self,as_of):
        result={'oi_24h':[],'funding':[]}
        for at,payload in self.connection.execute('SELECT at,payload FROM historical_features WHERE at<?',(stamp(as_of),)):
            values=json.loads(payload)
            if values['oi_24h'] is not None: result['oi_24h'].append(dict(at=at,value=values['oi_24h']))
            result['funding'] += [dict(at=r['at'],value=r['rate']) for r in values['funding']]
        return result

    def save_baselines(self,forecasts):
        with self.transaction():
            for b in forecasts:
                row=self.connection.execute('SELECT payload FROM baseline_forecasts WHERE id=?',(b.id,)).fetchone()
                if row and row[0]!=b.model_dump_json(): raise ValueError('baseline_frozen')
                self.connection.execute('INSERT OR IGNORE INTO baseline_forecasts VALUES (?,?,?,?)',(b.id,stamp(b.as_of),b.horizon_days,b.model_dump_json()))

    def baselines_for(self,as_of,horizon_days):
        return [BaselineForecast.model_validate_json(r[0]) for r in self.connection.execute('SELECT payload FROM baseline_forecasts WHERE as_of=? AND horizon=?',(stamp(as_of),horizon_days))]

    def reserve_attempt(self,day,stage,request_hash,estimated_input_tokens=0):
        if stage not in ('news-1','news-2','trend','forecast-v2:news-1','forecast-v2:news-2',
            'forecast-v2:trend','trend-shadow-3d','trend-shadow-7d'): raise ValueError('invalid_stage')
        if type(estimated_input_tokens) is not int or estimated_input_tokens<0: raise ValueError('invalid_usage_estimate')
        day=str(day)
        with self.transaction():
            row=self.connection.execute('SELECT request_hash,count FROM stage_attempts WHERE day=? AND stage=?',(day,stage)).fetchone()
            count=self.attempt_count(day)
            if count>=MAX_DAILY_ATTEMPTS or row and (row[0]!=request_hash or row[1]>=3): return False
            if self.daily_input_reservation(day)+estimated_input_tokens>MAX_DAILY_ESTIMATED_INPUT_TOKENS: return False
            self.connection.execute('INSERT INTO daily_attempts(day,count,reserved_input_tokens) VALUES (?,1,?) ON CONFLICT(day) DO UPDATE SET count=count+1,reserved_input_tokens=reserved_input_tokens+excluded.reserved_input_tokens',(day,estimated_input_tokens))
            self.connection.execute('INSERT INTO stage_attempts VALUES (?,?,?,1) ON CONFLICT(day,stage) DO UPDATE SET count=count+1',(day,stage,request_hash))
            return True

    def daily_input_reservation(self,day):
        row=self.connection.execute('SELECT reserved_input_tokens FROM daily_attempts WHERE day=?',(str(day),)).fetchone()
        return row[0] if row else 0

    def attempt_count(self,day):
        row=self.connection.execute('SELECT count FROM daily_attempts WHERE day=?',(str(day),)).fetchone()
        return row[0] if row else 0

    def get_stage(self,day,stage,request_hash):
        row=self.connection.execute('SELECT request_hash,payload FROM model_stages WHERE day=? AND stage=?',(str(day),stage)).fetchone()
        if row and row[0]!=request_hash: raise ValueError('stage_identity_frozen')
        return StageResult.model_validate_json(row[1]) if row else None

    def save_stage(self,result):
        with self.transaction():
            existing=self.get_stage(result.day,result.stage,result.request_hash)
            if existing and existing!=result: raise ValueError('stage_frozen')
            self.connection.execute('INSERT OR IGNORE INTO model_stages VALUES (?,?,?,?)',
                (str(result.day),result.stage,result.request_hash,result.model_dump_json()))

    def save_diagnostics(self,job_id,values):
        with self.transaction():
            job=self.get_job(job_id)
            if not job or job.state!='preparing': raise ValueError('diagnostics_frozen')
            self.connection.execute('INSERT INTO job_diagnostics VALUES (?,?) ON CONFLICT(job_id) DO UPDATE SET payload=excluded.payload',
                (job_id,dumps(dict(schema_version=1,**values))))

    def diagnostics(self,job_id):
        row=self.connection.execute('SELECT payload FROM job_diagnostics WHERE job_id=?',(job_id,)).fetchone()
        return json.loads(row[0]) if row else {}

    def _write_job(self,job):
        self.connection.execute('UPDATE jobs SET payload=? WHERE id=?',(job.model_dump_json(),job.id))

    def create_job(self,day,now):
        with self.transaction():
            existing=self.get_job(day)
            if existing: return existing
            job=Job(id='btc-'+str(day),day=day,state='preparing',lease_owner=None,lease_until=None,
                used_slots=[],mime_bytes=None,message_id=f'<btc-jev.{day}@btc-jev.local>',created_at=now,
                payload_hash=None,weekly_snapshot_id=None)
            self.connection.execute('INSERT INTO jobs(id,day,payload) VALUES (?,?,?)',(job.id,str(day),job.model_dump_json()))
            self.connection.execute('INSERT OR IGNORE INTO metadata VALUES (?,?)',('activation_day',dumps(str(day))))
            return job

    def get_job(self,day_or_id):
        row=self.connection.execute('SELECT payload FROM jobs WHERE day=? OR id=?',(str(day_or_id),str(day_or_id))).fetchone()
        return Job.model_validate_json(row[0]) if row else None

    def jobs(self):
        return [Job.model_validate_json(r[0]) for r in self.connection.execute('SELECT payload FROM jobs ORDER BY day')]

    def claim_prepare(self,day,owner,now):
        with self.transaction():
            job=self.get_job(day)
            if not job or job.state!='preparing' or job.lease_until and job.lease_until>now: return False
            until=now.astimezone(TAIPEI).replace(hour=7,minute=55,second=0,microsecond=0)
            self._write_job(job.model_copy(update={'lease_owner':owner,'lease_until':max(until,now+timedelta(seconds=30))}))
            return True

    def freeze_job_snapshot(self,job_id,snapshot_id):
        with self.transaction():
            row=self.connection.execute('SELECT snapshot_id FROM jobs WHERE id=?',(job_id,)).fetchone()
            if row and row[0] and row[0]!=snapshot_id: raise ValueError('job_snapshot_frozen')
            self.connection.execute('UPDATE jobs SET snapshot_id=? WHERE id=?',(snapshot_id,job_id))

    def job_snapshot(self,job_id):
        row=self.connection.execute('SELECT snapshot_id FROM jobs WHERE id=?',(job_id,)).fetchone()
        return row[0] if row else None

    def ready_job(self,job_id,mime_bytes,weekly_id,failure=None,owner=None):
        with self.transaction():
            job=self.get_job(job_id)
            if job.state!='preparing': return False
            if owner is not None and job.lease_owner!=owner: return False
            job=job.model_copy(update=dict(state='ready',mime_bytes=mime_bytes,
                payload_hash=hashlib.sha256(mime_bytes).hexdigest(),weekly_snapshot_id=weekly_id,lease_owner=None,lease_until=None))
            self._write_job(job)
            self.connection.execute('UPDATE jobs SET failure=? WHERE id=?',(failure,job_id))
            return True

    def recover(self,now):
        with self.transaction():
            for job in self.jobs():
                if job.state=='sending' and job.lease_until<=now:
                    self._write_job(job.model_copy(update=dict(state='unknown',lease_owner=None,lease_until=None)))
                elif job.state in ('preparing','ready','retryable') and (job.day<now.astimezone(TAIPEI).date() or
                    job.day==now.astimezone(TAIPEI).date() and now.astimezone(TAIPEI).hour*60+now.astimezone(TAIPEI).minute>510):
                    self._write_job(job.model_copy(update=dict(state='missed',lease_owner=None,lease_until=None)))

    def claim_delivery(self,day,slot,owner,now):
        local=now.astimezone(TAIPEI)
        if local.date()!=day or local.hour*60+local.minute!=slot or slot not in (480,485,495,510): return None
        with self.transaction():
            job=self.get_job(day)
            if not job or job.state not in ('ready','retryable') or slot in job.used_slots or not job.mime_bytes: return None
            job=job.model_copy(update=dict(state='sending',used_slots=job.used_slots+[slot],lease_owner=owner,lease_until=now+timedelta(minutes=3)))
            self._write_job(job)
            return job

    def finish_delivery(self,job_id,owner,result):
        with self.transaction():
            job=self.get_job(job_id)
            if job.state!='sending' or job.lease_owner!=owner: raise ValueError('delivery_owner_mismatch')
            self._write_job(job.model_copy(update=dict(state=result.status,lease_owner=None,lease_until=None)))

    def reconcile(self,job_id,result,now):
        if result not in ('sent','not-sent'): raise ValueError('invalid_reconciliation')
        with self.transaction():
            job=self.get_job(job_id)
            if not job or job.state!='unknown': raise ValueError('job_not_unknown')
            local=now.astimezone(TAIPEI)
            state='sent' if result=='sent' else 'retryable' if job.day==local.date() and local.hour*60+local.minute<=510 else 'missed'
            self._write_job(job.model_copy(update={'state':state}))

    def save_weekly_report(self,report):
        with self.transaction():
            old=self.get_weekly_report(report.week_start)
            if old and old!=report: raise ValueError('weekly_report_frozen')
            self.connection.execute('INSERT OR IGNORE INTO weekly_reports VALUES (?,?,?)',(stamp(report.week_start),report.snapshot_id,report.model_dump_json()))
            for group in report.groups:
                for sample in group.get('samples',[]):
                    self.connection.execute('INSERT OR IGNORE INTO weekly_report_samples VALUES (?,?,?)',(report.snapshot_id,sample['prediction_id'],sample['result_version']))

    def get_weekly_report(self,week_start):
        row=self.connection.execute('SELECT payload FROM weekly_reports WHERE week_start=?',(stamp(week_start),)).fetchone()
        return WeeklyReport.model_validate_json(row[0]) if row else None

    def weekly_reports(self):
        return [WeeklyReport.model_validate_json(r[0]) for r in self.connection.execute('SELECT payload FROM weekly_reports ORDER BY week_start')]

    def cleanup(self,now):
        cutoff=stamp(now-timedelta(days=90))
        with self.transaction():
            protected=set()
            for job in self.jobs():
                if job.state in ('unknown','sending','preparing','ready','retryable'):
                    sid=self.job_snapshot(job.id)
                    if sid: protected.add(sid)
                elif job.created_at<now-timedelta(days=90) and job.mime_bytes:
                    self._write_job(job.model_copy(update={'mime_bytes':None}))
            protected.update(p.snapshot_id for p in self.pending_settlements(now))
            for key,created,payload in self.connection.execute('SELECT id,created_at,payload FROM text_blobs WHERE payload IS NOT NULL AND created_at<?',(cutoff,)).fetchall():
                if key not in protected:
                    self.connection.execute('UPDATE text_blobs SET payload=NULL,text_expired_at=? WHERE id=?',(stamp(now),key))
        self.connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
