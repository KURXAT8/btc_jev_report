from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path

import httpx
import pytest

UTC = timezone.utc
ASOF = datetime(2026, 9, 20, 23, 30, tzinfo=UTC)


def ms(dt):
    return int(dt.timestamp() * 1000)


def raw_bar(start, width=60_000, price="100"):
    return [start, price, price, price, price, "2", start + width - 1, "200", 1, "1", "100", "0"]


def market_transport(request):
    p = request.url.params
    assert p["symbol"] == "BTCUSDT"
    if request.url.path == "/api/v3/klines":
        assert request.url.host == "api.binance.com"
        width = {"1d": 86400000, "4h": 14400000, "1h": 3600000, "1m": 60000}[p["interval"]]
        end = (int(p["endTime"]) + 1) // width * width
        n = min(int(p["limit"]), 100)  # Force pagination of all feature histories.
        return httpx.Response(200, json=[raw_bar(end - width * i, width) for i in range(n, 0, -1)])
    if request.url.path == "/fapi/v1/fundingRate":
        return httpx.Response(200, json=[{"fundingTime": ms(ASOF - timedelta(hours=i)),
            "fundingRate": "0.0001", "symbol": "BTCUSDT"} for i in range(168, 0, -8)])
    assert request.url.path == "/futures/data/openInterestHist"
    assert p["period"] == "1h"
    end = ASOF.replace(minute=0)
    return httpx.Response(200, json=[{"timestamp": ms(end-timedelta(hours=i)),
        "sumOpenInterest": "100", "sumOpenInterestValue": "10000", "symbol": "BTCUSDT"}
        for i in range(192, -1, -1)])


def test_collect_complete_market_paginated_and_uses_spot_reference():
    from btc_jev.sources.market import MarketClient
    market = MarketClient(httpx.Client(transport=httpx.MockTransport(market_transport)), now=lambda: ASOF).collect(ASOF)
    assert [len(market[k]) for k in ("daily", "four_hour", "hourly")] == [120, 180, 200]
    assert market["reference_price"] == Decimal("100")
    assert market["daily"][-1].close_at == datetime(2026, 9, 19, 23, 59, 59, 999000, tzinfo=UTC)
    assert market["hourly"][-1].close_at == ASOF.replace(minute=0)-timedelta(milliseconds=1)
    assert market["open_interest"][-1]["quantity"] == Decimal("100")
    assert market["open_interest"][-1]["value"] == Decimal("10000")


def test_exact_minute_missing_does_not_use_neighbor():
    from btc_jev.sources.market import MarketClient
    def handler(req):
        return httpx.Response(200, json=[raw_bar(ms(ASOF)-120000)])
    assert MarketClient(httpx.Client(transport=httpx.MockTransport(handler))).price_at(ASOF) is None


@pytest.mark.parametrize('rows',[[[]],[None],[{}],[['invalid']],{'error':'bad'},[True]])
def test_malformed_settlement_candles_raise_controlled_source_error(rows):
    from btc_jev.sources.market import MarketClient
    from btc_jev.sources.common import SourceError
    http=httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json=rows)))
    with pytest.raises(SourceError):
        MarketClient(http).price_at(ASOF)


@pytest.mark.parametrize("bad", ["gap", "nan", "bad_close"])
def test_market_rejects_gaps_nonfinite_and_inconsistent_candle_timestamps(bad):
    from btc_jev.sources.market import MarketClient
    from btc_jev.sources.common import SourceError
    def handler(req):
        response = market_transport(req)
        if req.url.path == "/api/v3/klines" and req.url.params["interval"] == "1h":
            rows = response.json()
            if bad == "gap":
                rows.pop(50)
            elif bad == "nan":
                rows[0][4] = "NaN"
            else:
                rows[0][6] += 1
            return httpx.Response(200, json=rows)
        return response
    with pytest.raises(SourceError):
        MarketClient(httpx.Client(transport=httpx.MockTransport(handler)), now=lambda: ASOF).collect(ASOF)


def test_closed_candles_excludes_open_bar():
    from btc_jev.sources.market import closed_candles
    from btc_jev.domain import Candle
    c = Candle(open_at=ASOF, close_at=ASOF+timedelta(hours=1), open=100, high=102, low=99, close=101, volume=2)
    assert closed_candles([c], ASOF) == []


def test_request_retry_after_and_host_guard():
    from btc_jev.sources.common import PublicHTTP, SourceError
    calls, delays = [], []
    def handler(req):
        calls.append(req.url)
        return httpx.Response(429, headers={"Retry-After": "2"}) if len(calls) < 3 else httpx.Response(200, json=[])
    client = PublicHTTP(httpx.Client(transport=httpx.MockTransport(handler)), now=lambda: ASOF, sleep=delays.append)
    assert client.get("https://api.binance.com/api/v3/klines", deadline=ASOF+timedelta(seconds=10)).json() == []
    assert delays == [2, 2] and len(calls) == 3
    with pytest.raises(SourceError):
        client.get("https://evil.example/api")
    with pytest.raises(SourceError):
        client.get("http://api.binance.com/api")


def test_request_budget_and_content_type():
    from btc_jev.sources.common import PublicHTTP, SourceError
    calls = []
    def handler(req):
        calls.append(1)
        return httpx.Response(503)
    c = PublicHTTP(httpx.Client(transport=httpx.MockTransport(handler)), now=lambda: ASOF, sleep=lambda _: None)
    with pytest.raises(SourceError):
        c.get("https://api.binance.com/api/v3/klines")
    assert len(calls) == 3
    c = PublicHTTP(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="not-json"))))
    with pytest.raises(SourceError):
        c.get("https://api.binance.com/api/v3/klines", kind="json")


def test_collect_deadline_stops_before_any_request():
    from btc_jev.sources.market import MarketClient
    from btc_jev.sources.common import SourceError
    def handler(req):
        pytest.fail("request after cutoff")
    with pytest.raises(SourceError):
        MarketClient(httpx.Client(transport=httpx.MockTransport(handler)), now=lambda: ASOF+timedelta(minutes=11)).collect(ASOF)


def test_news_parse_dates_revisions_empty_failure_and_clean_html():
    from btc_jev.sources.news import NewsClient
    xml = Path("tests/fixtures/news.xml").read_bytes()
    def handler(req):
        if req.url.host == "www.coindesk.com":
            return httpx.Response(200, content=xml, headers={"content-type": "application/rss+xml"})
        if req.url.host == "cointelegraph.com":
            return httpx.Response(200, text='<rss version="2.0"><channel><title>Empty</title></channel></rss>', headers={"content-type": "application/xml"})
        return httpx.Response(403)
    articles, health = NewsClient(httpx.Client(transport=httpx.MockTransport(handler)), now=lambda: ASOF).collect(ASOF)
    assert len(articles) == 1
    assert articles[0].title == "Bitcoin synthetic news"
    assert "<" not in articles[0].summary and "evil()" not in articles[0].summary
    assert articles[0].first_seen_at == ASOF
    assert health["cointelegraph"].status == "ok" and health["cointelegraph"].item_count == 0
    assert health["fed"].status == "error"


def test_coindesk_uses_canonical_feed_without_redirect():
    from btc_jev.sources.news import NewsClient
    xml = Path('tests/fixtures/news.xml').read_bytes()
    def handler(req):
        if req.url.host != 'www.coindesk.com':
            return httpx.Response(403)
        if req.url.path.endswith('/'):
            return httpx.Response(308, headers={'location':'/arc/outboundfeeds/rss'})
        return httpx.Response(200, content=xml, headers={'content-type':'application/xml'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        articles,health = NewsClient(http,now=lambda:ASOF).collect(ASOF)
    assert health['coindesk'].status == 'ok'
    assert len(articles) == 1


def test_news_selection_diversity_revision_and_cutoff():
    from btc_jev.sources.news import parse_feed, select_articles
    def rss(title, url, published="Sun, 20 Sep 2026 22:00:00 GMT"):
        return f'<rss version="2.0"><channel><title>Test</title><item><title>{title}</title><link>{url}</link><pubDate>{published}</pubDate></item></channel></rss>'.encode()
    all_articles = []
    for source in ("coindesk", "cointelegraph", "fed"):
        for i in range(25):
            all_articles += parse_feed(rss(f"Bitcoin {source} {i}", f"https://example.org/{source}/{i}"), source, ASOF)
    selected, counts = select_articles(all_articles, ASOF)
    assert len(selected) == 20
    assert sum(a.source == "fed" for a in selected) == 5
    assert abs(sum(a.source == "coindesk" for a in selected)-sum(a.source == "cointelegraph" for a in selected)) <= 1
    old = parse_feed(rss("Bitcoin old", "https://example.org/a"), "coindesk", ASOF)[0]
    revised = parse_feed(rss("Bitcoin new", "https://example.org/a"), "coindesk", ASOF+timedelta(seconds=1))[0]
    selected, _ = select_articles([old, revised], ASOF)
    assert selected == [old] and old.content_hash != revised.content_hash
    assert parse_feed(rss("Bitcoin future", "https://example.org", "Mon, 21 Sep 2026 22:00:00 GMT"), "coindesk", ASOF) == []


def test_malformed_rss_is_not_healthy_empty_and_summaries_are_bounded():
    from btc_jev.sources.news import parse_feed
    from btc_jev.sources.common import SourceError
    with pytest.raises(SourceError):
        parse_feed(b"<html>blocked</html>", "coindesk", ASOF)
    xml = Path("tests/fixtures/news.xml").read_text().replace("&lt;b&gt;summary&lt;/b&gt;", "x" * 700)
    a = parse_feed(xml.encode(), "coindesk", ASOF)[0]
    assert len(a.summary) == 600 and a.truncated


def test_sec_and_cftc_official_feeds_keep_crypto_relevant_items_only():
    from btc_jev.sources.news import parse_feed
    def rss(title,summary=''):
        return (f'<rss version="2.0"><channel><title>Official</title><item><title>{title}</title>'
            f'<link>https://example.org/{title.replace(" ","-")}</link>'
            f'<pubDate>Sun, 20 Sep 2026 22:00:00 GMT</pubDate><description>{summary}</description>'
            '</item></channel></rss>').encode()
    sec=parse_feed(rss('SEC discusses Bitcoin ETF market structure'),'sec',ASOF)
    cftc=parse_feed(rss('Digital asset derivatives enforcement action'),'cftc-enforcement',ASOF)
    noise=parse_feed(rss('SEC announces office closure'),'sec',ASOF)
    assert [a.source for a in sec]==['sec']
    assert [a.source for a in cftc]==['cftc-enforcement']
    assert noise==[]


def test_official_regulatory_feeds_accept_seven_day_items():
    from btc_jev.sources.news import parse_feed
    xml=b'<rss version="2.0"><channel><title>Test</title><item><title>Bitcoin ETF review</title>' \
        b'<link>https://example.org/sec</link><pubDate>Mon, 14 Sep 2026 22:00:00 GMT</pubDate></item></channel></rss>'
    assert parse_feed(xml,'sec',ASOF)
    assert parse_feed(xml,'coindesk',ASOF)==[]
