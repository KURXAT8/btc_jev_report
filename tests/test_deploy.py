from pathlib import Path
import pytest


def test_rendered_units_pass_systemd_verification(tmp_path):
    import shutil
    import subprocess
    from deploy.install_units import render
    if not shutil.which('systemd-analyze'):
        pytest.skip('systemd-analyze unavailable')
    project=tmp_path/'project with space'
    executable=project/'.venv/bin/btc-jev'
    executable.parent.mkdir(parents=True)
    executable.symlink_to('/bin/true')
    paths=render(project,tmp_path/'units')
    result=subprocess.run(['systemd-analyze','--user','verify',*map(str,paths)],capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert not result.stderr,result.stderr


def test_render_absolute_space_paths_and_schedules(tmp_path):
    from deploy.install_units import render,install
    project=tmp_path/'project with space'; output=tmp_path/'units'
    files=render(project,output)
    assert len(files)==7
    prepare=(output/'btc-jev-prepare.service').read_text()
    assert f'"{project}/.venv/bin/btc-jev" prepare' in prepare
    assert 'TimeoutStartSec=26min' in prepare and 'UMask=0077' in prepare
    assert (output/'btc-jev-prepare.service').stat().st_mode & 0o777 == 0o600
    assert 'foggy' not in prepare
    timer=(output/'btc-jev-deliver.timer').read_text()
    assert all(f'08:{minute}:00 Asia/Taipei' in timer for minute in ('00','05','15','30'))
    assert 'Persistent=false' in (output/'btc-jev-collect-context.timer').read_text()
    capture=(output/'btc-jev-binance-capture.service').read_text()
    assert f'"{project}/.venv/bin/btc-jev" capture-announcements' in capture
    assert 'Restart=always' in capture and 'EnvironmentFile=' in capture
    destination=tmp_path/'user-config/systemd/user'; install(output,destination)
    link=destination/'btc-jev-prepare.service'
    assert link.is_symlink()
    assert link.resolve()==(output/'btc-jev-prepare.service').resolve()
    install(output,destination)
    assert link.resolve()==(output/'btc-jev-prepare.service').resolve()


def test_install_refuses_to_replace_a_different_existing_unit(tmp_path):
    from deploy.install_units import render,install
    output=tmp_path/'units'; render(tmp_path/'project',output)
    destination=tmp_path/'user-config/systemd/user'; destination.mkdir(parents=True)
    conflict=destination/'btc-jev-prepare.service'
    conflict.write_text('unrelated existing unit')
    with pytest.raises(ValueError,match='existing_unit_differs'):
        install(output,destination)
    assert conflict.read_text()=='unrelated existing unit'
