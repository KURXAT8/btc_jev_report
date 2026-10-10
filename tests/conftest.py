from pathlib import Path
import socket

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Task 1 never needs a live API, SMTP server or real credentials."""
    def blocked(*args, **kwargs):
        raise AssertionError("Network access is forbidden in offline tests")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture
def project_env(tmp_path: Path) -> Path:
    return tmp_path / ".env"


@pytest.fixture(autouse=True)
def close_test_stores(monkeypatch):
    from btc_jev.store import Store
    original=Store.__init__
    stores=[]
    def tracked(self,*args,**kwargs):
        original(self,*args,**kwargs)
        stores.append(self)
    monkeypatch.setattr(Store,'__init__',tracked)
    yield
    for store in stores:
        store.close()
