"""Render and install are separate. Activation is an explicit operator action."""
import argparse
import os
from pathlib import Path
import subprocess

TIMER_NAMES=('prepare','deliver','collect-context')
SERVICE_NAMES=(*TIMER_NAMES,'binance-capture')
UNIT_FILES=tuple(f'btc-jev-{name}.{suffix}' for name in SERVICE_NAMES
                 for suffix in (('service','timer') if name in TIMER_NAMES else ('service',)))


def quote(value):
    value=str(value)
    if '\n' in value or '\r' in value or '\x00' in value: raise ValueError('invalid_unit_path')
    return '"'+value.replace('\\','\\\\').replace('"','\\"').replace('%','%%').replace('$','$$')+'"'


def render(project,destination):
    project=Path(project).resolve(); destination=Path(destination)
    destination.mkdir(parents=True,exist_ok=True)
    paths=[]
    for name in SERVICE_NAMES:
        for suffix in (('service','timer') if name in TIMER_NAMES else ('service',)):
            template=Path(__file__).with_name(f'btc-jev-{name}.{suffix}').read_text()
            # WorkingDirectory consumes one path, not a shell-style argument list.
            quote(project)  # Validate control characters before embedding it.
            values={'@PROJECT@':str(project).replace('%','%%'),'@ENV@':str(project/'.env').replace('%','%%'),
                '@EXEC@':quote(project/'.venv/bin/btc-jev')}
            for key,value in values.items(): template=template.replace(key,value)
            path=destination/f'btc-jev-{name}.{suffix}'
            path.write_text(template); paths.append(path)
            path.chmod(0o600)
    return paths


def install(source,destination):
    source,destination=Path(source),Path(destination)
    files=[source/name for name in UNIT_FILES]
    # Validate every collision before creating any link.
    for path in files:
        target=destination/path.name
        if not path.is_file(): raise ValueError('missing_rendered_unit')
        if (target.exists() or target.is_symlink()) and target.read_bytes()!=path.read_bytes():
            raise ValueError('existing_unit_differs')
    destination.mkdir(parents=True,exist_ok=True)
    for path in files:
        target=destination/path.name
        if target.is_symlink() and target.resolve()==path.resolve(): continue
        if target.exists() or target.is_symlink(): target.unlink()
        target.symlink_to(path.resolve())


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    r=sub.add_parser('render'); r.add_argument('--project',type=Path,required=True); r.add_argument('--destination',type=Path,required=True)
    default_user_config=Path(os.environ.get('XDG_CONFIG_HOME',Path.home()/'.config'))
    i=sub.add_parser('install'); i.add_argument('--source',type=Path,required=True); i.add_argument('--destination',type=Path,default=default_user_config/'systemd/user')
    sub.add_parser('enable')
    args=parser.parse_args(argv)
    if args.command=='render':
        for path in render(args.project,args.destination): print(path)
    elif args.command=='install': install(args.source,args.destination)
    else:
        subprocess.run(['systemctl','--user','daemon-reload'],check=True)
        subprocess.run(['systemctl','--user','enable','--now',
            *[f'btc-jev-{name}.timer' for name in TIMER_NAMES],
            'btc-jev-binance-capture.service'],check=True)
    return 0


if __name__=='__main__': raise SystemExit(main())
