import pytest


def test_help_status_and_config_are_offline(tmp_path,capsys,monkeypatch):
    from btc_jev.cli import main
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as e: main(['--help'])
    assert e.value.code==0 and 'preview' in capsys.readouterr().out
    assert main(['status'])==0
    assert 'no jobs' in capsys.readouterr().out
    assert not (tmp_path/'data').exists()
    assert main(['check-config'])==1
    assert 'MISSING' in capsys.readouterr().out


def test_status_displays_safe_saved_analysis_error(tmp_path,capsys,monkeypatch):
    from datetime import datetime,timezone
    from btc_jev.cli import main
    from btc_jev.store import Store
    monkeypatch.chdir(tmp_path); monkeypatch.setenv('DATA_DIR','test-data')
    store=Store(tmp_path/'test-data'/'btc-jev.sqlite3')
    job=store.create_job(datetime(2026,9,27,0,tzinfo=timezone.utc).date(),datetime(2026,9,27,0,tzinfo=timezone.utc))
    store.save_diagnostics(job.id,dict(analysis_error={'stage':'forecast-v2:news-2','code':'invalid_jev_response',
        'http_status':200,'attempts':2}))
    store.close()
    assert main(['status'])==0
    output=capsys.readouterr().out
    assert 'forecast-v2:news-2' in output and 'invalid_jev_response' in output


def test_debug_flag_enables_project_debug_logging_only(tmp_path,capsys,monkeypatch):
    import logging
    from btc_jev.cli import main
    app_logger=logging.getLogger('btc_jev'); previous=app_logger.level
    monkeypatch.chdir(tmp_path)
    try:
        assert main(['--debug','status'])==0
        assert app_logger.level==logging.DEBUG
        assert logging.getLogger().level!=logging.DEBUG
    finally:
        app_logger.setLevel(previous)
