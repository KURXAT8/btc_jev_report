from datetime import datetime, timedelta, timezone
from decimal import Decimal

from btc_jev.domain import Candle, Snapshot
from btc_jev.sources.common import PublicHTTP, SourceError
from btc_jev.util import utcnow

SPOT = "https://api.binance.com/api/v3/klines"
FUTURES = "https://fapi.binance.com"
WIDTHS = {"1m": 60000, "1h": 3600000, "4h": 14400000, "1d": 86400000}


def millis(value):
    return int(value.timestamp() * 1000)


def timestamp(value):
    return datetime.fromtimestamp(int(value)/1000, timezone.utc)


def finite_decimal(value, *, positive=False):
    parsed = Decimal(str(value))
    if not parsed.is_finite() or (positive and parsed <= 0):
        raise SourceError("invalid_market_number")
    return parsed


def closed_candles(candles, as_of):
    return sorted((c for c in candles if c.close_at < as_of), key=lambda c: c.open_at)


def parse_bar(row, width):
    try:
        if not isinstance(row,list):
            raise ValueError
        if int(row[0]) % width or int(row[6]) != int(row[0])+width-1:
            raise ValueError
        return Candle(open_at=timestamp(row[0]), close_at=timestamp(row[6]),
                      open=row[1], high=row[2], low=row[3], close=row[4], volume=row[5])
    except (ValueError, TypeError, IndexError, OverflowError):
        raise SourceError("invalid_candle") from None


class MarketClient:
    def __init__(self, http, *, now=utcnow, sleep=None):
        self.io = PublicHTTP(http, now=now, **({"sleep": sleep} if sleep else {}))
        self.now = now

    def candles(self, as_of, interval, count, deadline=None):
        width, end, found = WIDTHS[interval], millis(as_of)-1, {}
        for _ in range(20):
            rows = self.io.get(SPOT, {"symbol":"BTCUSDT", "interval":interval,
                "endTime":end,"limit":min(1000, count+1)}, deadline=deadline).json()
            if not isinstance(rows, list) or not rows:
                break
            bars = [parse_bar(row, width) for row in rows]
            for c in closed_candles(bars, as_of):
                if c.open_at in found and found[c.open_at] != c:
                    raise SourceError("conflicting_candle")
                found[c.open_at] = c
            if len(found) >= count:
                break
            next_end = min(millis(c.open_at) for c in bars)-1
            if next_end >= end:
                raise SourceError("pagination_stalled")
            end = next_end
        selected = sorted(found.values(), key=lambda c:c.open_at)[-count:]
        expected_end = millis(as_of)//width*width-1
        if len(selected) != count or millis(selected[-1].close_at) != expected_end:
            raise SourceError("missing_required_candles")
        if any(millis(b.open_at)-millis(a.open_at) != width for a,b in zip(selected,selected[1:])):
            raise SourceError("candle_gap")
        return selected

    def price_at(self, boundary, *, deadline=None):
        if boundary.second or boundary.microsecond:
            raise SourceError("reference_not_minute_boundary")
        try:
            rows = self.io.get(SPOT, {"symbol":"BTCUSDT", "interval":"1m", "limit":1,
                "startTime":millis(boundary)-60000,"endTime":millis(boundary)-1}, deadline=deadline).json()
            if not isinstance(rows,list):
                raise ValueError
            bars = [parse_bar(row,60000) for row in rows]
        except (ValueError,TypeError,KeyError,IndexError,ArithmeticError):
            raise SourceError("invalid_candle_response") from None
        exact = [bar for bar in bars if millis(bar.open_at)==millis(boundary)-60000]
        return exact[0].close if len(exact)==1 else None

    def collect(self, as_of):
        deadline = as_of+timedelta(minutes=10)
        try:
            reference = self.price_at(as_of, deadline=deadline)
            if reference is None:
                raise SourceError("missing_reference_minute")
            result = {"reference_price":reference}
            for key, interval, count in (("daily","1d",120),("four_hour","4h",180),("hourly","1h",200)):
                result[key] = self.candles(as_of,interval,count,deadline)
            start, funding = millis(as_of-timedelta(days=7)), {}
            for _ in range(20):
                rows = self.io.get(FUTURES+"/fapi/v1/fundingRate", {"symbol":"BTCUSDT", "startTime":start,
                    "endTime":millis(as_of),"limit":1000},deadline=deadline).json()
                if not isinstance(rows,list):
                    raise SourceError("invalid_funding")
                for r in rows:
                    t = int(r["fundingTime"])
                    if start <= t <= millis(as_of):
                        funding[t] = {"at":timestamp(t), "rate":finite_decimal(r["fundingRate"])}
                if len(rows) < 1000:
                    break
                next_start=max(int(r["fundingTime"]) for r in rows)+1
                if next_start <= start:
                    raise SourceError("pagination_stalled")
                start=next_start
            result["funding"] = [funding[k] for k in sorted(funding)]
            rows = self.io.get(FUTURES+"/futures/data/openInterestHist", {"symbol":"BTCUSDT", "period":"1h",
                "startTime":millis(as_of-timedelta(days=8)), "endTime":millis(as_of), "limit":500},deadline=deadline).json()
            oi = {}
            for r in rows:
                at=timestamp(r["timestamp"])
                if at <= as_of:
                    oi[at] = {"at":at,"quantity":finite_decimal(r["sumOpenInterest"],positive=True),
                              "value":finite_decimal(r["sumOpenInterestValue"],positive=True)}
            result["open_interest"] = [oi[k] for k in sorted(oi)]
            result["fetched_at"] = self.now()
            return result
        except (ValueError, TypeError, KeyError, IndexError, ArithmeticError) as error:
            if isinstance(error,SourceError):
                raise
            raise SourceError("invalid_market_response") from None


def build_snapshot(market, articles, health, as_of, fetched_at, quality, events, calendar_health, context):
    return Snapshot(as_of=as_of,fetched_at=fetched_at,articles=articles,source_health=health,
        quality=quality,events=events,calendar_health=calendar_health,context=context,
        **{k:market[k] for k in ("reference_price","daily","four_hour","hourly","funding","open_interest")})
