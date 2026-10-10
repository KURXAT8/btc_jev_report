from datetime import timezone
from email.utils import parsedate_to_datetime
import time
from urllib.parse import urlsplit
import httpx

from btc_jev.util import utcnow, time_limit

HOSTS = frozenset(("api.binance.com", "fapi.binance.com", "www.okx.com", "www.coindesk.com",
                  "cointelegraph.com", "www.federalreserve.gov", "www.bls.gov"))


class SourceError(ValueError):
    """A stable reason code; never a remote body or credentials."""


def retry_delay(value, attempt, now):
    if value:
        try:
            number = float(value)
            if 0 <= number <= 86400:
                return number
        except ValueError:
            try:
                return max(0, (parsedate_to_datetime(value).astimezone(timezone.utc)-now).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
    return float(2 ** attempt)


class PublicHTTP:
    def __init__(self, http, *, now=utcnow, sleep=time.sleep):
        self.http, self.now, self.sleep = http, now, sleep

    def get(self, url, params=None, *, deadline=None, kind="json"):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in HOSTS or parsed.username or parsed.port not in (None, 443):
            raise SourceError("unapproved_source")
        for attempt in range(3):
            remaining = (deadline-self.now()).total_seconds() if deadline else 20
            if remaining <= 0:
                raise SourceError("collection_deadline")
            response = None
            try:
                with time_limit(min(20, remaining)):
                    response = self.http.get(url, params=params, timeout=min(20, remaining),
                        follow_redirects=False, headers={"User-Agent": "btc-jev/0.1 (+public-data-report)"})
                if deadline and self.now() >= deadline:
                    raise SourceError("collection_deadline")
                if response.status_code == 200:
                    content_type = response.headers.get("content-type", "").lower()
                    expected = {"json": ("json",), "xml": ("xml", "rss", "atom"), "html": ("html",)}[kind]
                    if not any(token in content_type for token in expected):
                        raise SourceError("invalid_content_type")
                    if len(response.content) > 4 * 1024 * 1024:
                        raise SourceError("response_too_large")
                    return response
                if response.status_code != 429 and response.status_code < 500:
                    raise SourceError(f"source_http_{response.status_code}")
            except (httpx.HTTPError, TimeoutError):
                pass
            if attempt == 2:
                raise SourceError("source_attempts_exhausted")
            delay = retry_delay(response.headers.get("Retry-After") if response else None, attempt, self.now())
            if delay > 60 or (deadline and self.now().timestamp()+delay >= deadline.timestamp()):
                raise SourceError("retry_after_deadline")
            self.sleep(delay)
        raise SourceError("source_attempts_exhausted")
