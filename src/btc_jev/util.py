"""Small deterministic primitives and Linux whole-operation deadlines."""
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import signal
import threading
import time
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")


def utcnow():
    return datetime.now(timezone.utc)


def json_default(value):
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("nonfinite JSON value")
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive JSON timestamp")
        return value.astimezone(timezone.utc).isoformat()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError("unsupported JSON value")


def dumps(value):
    return json.dumps(value, default=json_default, allow_nan=False, sort_keys=True,
                      ensure_ascii=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def dt(value):
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        raise ValueError("aware datetime required")
    return parsed.astimezone(timezone.utc)


@contextmanager
def time_limit(seconds):
    """Abort blocking network work, including trickle responses, in the CLI thread.

    Refuse unsupported execution contexts instead of promising a false timeout.
    Nested deadlines retain the smaller enclosing timer and restore its remainder.
    """
    if seconds <= 0:
        raise TimeoutError("deadline")
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "setitimer"):
        raise RuntimeError("bounded I/O requires a POSIX main thread")
    previous = signal.getsignal(signal.SIGALRM)
    old_delay, old_interval = signal.getitimer(signal.ITIMER_REAL)
    started = time.monotonic()
    def expired(signum, frame):
        raise TimeoutError("deadline")
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, min(seconds, old_delay) if old_delay else seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if old_delay:
            signal.setitimer(signal.ITIMER_REAL, max(.000001, old_delay-(time.monotonic()-started)), old_interval)
