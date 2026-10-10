"""Operations are explicit: preview/status never make network requests."""
import argparse
from contextlib import closing
from email import policy
from email.parser import BytesParser
import json
import logging
import os
from pathlib import Path
import sqlite3
import httpx
from btc_jev.config import load_settings,SETTING_KEYS,require_secrets
from btc_jev.domain import Snapshot,Prediction
from btc_jev.util import utcnow,TAIPEI


def main(argv=None):
    parser=argparse.ArgumentParser(prog='btc-jev',description='BTC 1／3／7 天日報與週報')
    parser.add_argument('--env-file',type=Path,default=Path('.env'))
    parser.add_argument('--debug',action='store_true',help='log safe analysis-stage diagnostics')
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('collect-context','collect','prepare','evaluate','status','check-config','cleanup','learn','learning-status','capture-announcements'): sub.add_parser(name)
    preview=sub.add_parser('preview'); preview.add_argument('--fixture',type=Path); preview.add_argument('--day'); preview.add_argument('--output-dir',type=Path,default=Path('data/preview'))
    send=sub.add_parser('send'); send.add_argument('--due',action='store_true',required=True)
    rec=sub.add_parser('reconcile'); rec.add_argument('job_id'); rec.add_argument('--result',choices=['sent','not-sent'],required=True)
    args=parser.parse_args(argv)
    if args.debug:
        logging.basicConfig(level=logging.WARNING,format='%(asctime)s %(levelname)s %(name)s %(message)s')
        logging.getLogger('btc_jev').setLevel(logging.DEBUG)
    try:
        settings=load_settings(os.environ,args.env_file)
        database=settings.data_dir/'btc-jev.sqlite3'
        if args.command=='learning-status':
            from btc_jev.learning import learning_status
            print(json.dumps(learning_status(database,settings.calibration_mode),ensure_ascii=False,indent=2))
            return 0
        if args.command=='check-config':
            missing=False
            for name in ('TYPESAFE_API_KEY','GMAIL_ADDRESS','GMAIL_APP_PASSWORD','RECIPIENT_EMAIL'):
                present=bool(getattr(settings,SETTING_KEYS[name]).get_secret_value().strip())
                print(f'{name}: '+('SET' if present else 'MISSING')); missing|=not present
            for name in ('BINANCE_API_KEY','BINANCE_API_SECRET'):
                present=bool(getattr(settings,SETTING_KEYS[name]).get_secret_value().strip())
                print(f'{name}: '+('SET' if present else 'MISSING (optional)'))
            return int(missing)
        if args.command=='status' and not database.exists():
            print('no jobs'); return 0
        if args.command=='preview' and args.fixture:
            from btc_jev.render import render_report
            data=json.loads(args.fixture.read_text())
            snap=Snapshot(**data['snapshot'])
            predictions=[Prediction(**p) for p in data.get('predictions',[])]
            rendered=render_report(snap.as_of.astimezone(TAIPEI).date(),predictions,{'snapshot':snap,'synthetic':True},None,None)
            return write_preview(args.output_dir,rendered.text,rendered.html)
        if args.command=='preview' and not database.exists():
            print('No saved report; use preview --fixture PATH.'); return 1
        from btc_jev.store import Store
        from btc_jev import pipeline
        with closing(Store(database)) as store:
            if args.command=='learn':
                from btc_jev.learning import learn,learning_status
                learn(store,utcnow(),settings.calibration_mode)
                print(json.dumps(learning_status(database,settings.calibration_mode),ensure_ascii=False,indent=2))
                return 0
            if args.command=='status':
                for job in store.jobs():
                    print(f'{job.day} {job.state} attempts={store.attempt_count(job.day)} slots={job.used_slots}')
                    error=store.diagnostics(job.id).get('analysis_error')
                    if error:
                        print('  analysis_error '+json.dumps(error,ensure_ascii=False,sort_keys=True))
                return 0
            if args.command=='preview':
                jobs=store.jobs(); job=store.get_job(args.day) if args.day else jobs[-1] if jobs else None
                if not job or not job.mime_bytes: print('No retained rendered report.'); return 1
                msg=BytesParser(policy=policy.default).parsebytes(job.mime_bytes)
                return write_preview(args.output_dir,msg.get_body(preferencelist=('plain',)).get_content(),msg.get_body(preferencelist=('html',)).get_content())
            if args.command=='cleanup': store.cleanup(utcnow()); print('cleanup complete'); return 0
            if args.command=='reconcile': pipeline.reconcile(store,args.job_id,args.result); print('reconciled'); return 0
            if args.command=='send':
                require_secrets(settings,'mail')
                result=pipeline.deliver_due(store,settings)
                print(result.status if result else 'no eligible delivery'); return int(result is not None and result.status!='sent')
            if args.command=='capture-announcements':
                import asyncio
                require_secrets(settings,'binance')
                from btc_jev.sources.binance import capture_forever
                asyncio.run(capture_forever(store,settings))
                return 0
            with httpx.Client() as http:
                from btc_jev.sources.market import MarketClient
                market=MarketClient(http)
                if args.command=='collect-context':
                    from btc_jev.sources.news import NewsClient
                    from btc_jev.sources.calendars import CalendarClient
                    pipeline.collect_context(store,NewsClient(http),CalendarClient(http)); print('context archived'); return 0
                if args.command=='evaluate': print(f'settled={pipeline.evaluate(store,market)}'); return 0
                if args.command=='collect':
                    now=utcnow(); cutoff=now.astimezone(TAIPEI).replace(hour=7,minute=30,second=0,microsecond=0)
                    if not cutoff<=now<cutoff+__import__('datetime').timedelta(minutes=10): raise ValueError('outside_collection_window')
                    values=market.collect(cutoff); store.save_anchor(cutoff,values['reference_price'],utcnow())
                    print('market collected; reference anchor archived'); return 0
                from btc_jev.jev import JevClient
                from btc_jev.sources.crosscheck import CrosscheckClient
                day=utcnow().astimezone(TAIPEI).date()
                client=JevClient(http,settings,reserve_attempt=lambda stage,key,tokens:store.reserve_attempt(day,stage,key,tokens))
                # Rendering needs valid addresses; the SMTP password is required only at send.
                from btc_jev.mail import addresses
                addresses(settings)
                job_id=pipeline.prepare_daily(store,settings,market,CrosscheckClient(http),client)
                print(f'{job_id} {store.get_job(job_id).state}'); return 0
    except (ValueError,OSError,sqlite3.Error,TimeoutError):
        print('Operation failed; check configuration, source availability and the permitted time window. No secrets logged.')
        return 1


def write_preview(destination,text,html):
    destination.mkdir(parents=True,exist_ok=True,mode=0o700)
    for name,content in [('report.txt',text),('report.html',html)]:
        path=destination/name; path.write_text(content); path.chmod(0o600)
    print(f'Preview: {destination.resolve()/"report.html"}')
    return 0
