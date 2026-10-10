"""Hand-controlled synthetic records; no production evaluation helpers."""

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo


def prediction(**overrides):
    from btc_jev.domain import Prediction

    as_of = overrides.get("as_of", datetime(2026, 9, 21, 7, 30, tzinfo=ZoneInfo("Asia/Taipei")))
    horizon = overrides.get("horizon_days", 3)
    values = dict(
        id=f"synthetic-{as_of.isoformat()}-{horizon}", as_of=as_of,
        created_at=as_of + timedelta(minutes=10),
        due_at=overrides["due_at"] if "due_at" in overrides else as_of + timedelta(days=horizon),
        horizon_days=horizon, reference_price="100",
        distribution={"probabilities": {"bullish": .7, "sideways": .2, "bearish": .1},
                      "choice": "bullish", "confidence": .7},
        model="jev-1.13.0", rules_version="v1", label_version="labels-v1",
        config_hash="synthetic-config", snapshot_id="synthetic-snapshot",
    )
    values.update(overrides)
    return Prediction(**values)


def settlement(prediction, target_price="103", settled_at=None):
    from btc_jev.domain import Settlement

    price = Decimal(target_price)
    change = (price / prediction.reference_price - 1) * 100
    limit = {1: Decimal("1"), 3: Decimal("2"), 7: Decimal("4")}[prediction.horizon_days]
    actual = "sideways"
    if change > limit:
        actual = "bullish"
    elif change < -limit:
        actual = "bearish"
    return Settlement(prediction_id=prediction.id, target_price=price, return_pct=change,
                      actual=actual, settled_at=settled_at or prediction.due_at,
                      result_version=1)


def snapshot(**overrides):
    from datetime import timezone
    from btc_jev.domain import Snapshot, Candle, SourceHealth
    as_of=overrides.get("as_of",datetime(2026,9,20,23,30,tzinfo=timezone.utc))
    def bars(width,n):
        end=int(as_of.timestamp())//width*width
        return [Candle(open_at=datetime.fromtimestamp(end-i*width,timezone.utc),
            close_at=datetime.fromtimestamp(end-(i-1)*width,timezone.utc)-timedelta(milliseconds=1),
            open=100,high=100,low=100,close=100,volume=2) for i in range(n,0,-1)]
    h=SourceHealth(status="ok",last_success_at=as_of,last_attempt_at=as_of,reason=None,item_count=0)
    values=dict(as_of=as_of,fetched_at=as_of,reference_price=100,daily=bars(86400,120),
        four_hour=bars(14400,180),hourly=bars(3600,200),
        funding=[{"at":as_of-timedelta(hours=i),"rate":Decimal(".0001")} for i in range(168,0,-8)],
        open_interest=[{"at":as_of.replace(minute=0)-timedelta(hours=i),"quantity":Decimal(100),
                       "value":Decimal(10000)} for i in range(192,-1,-1)],
        articles=[],events=[],calendar_health={s:h for s in ("cpi","fomc")},
        source_health={s:h for s in ("coindesk","cointelegraph","fed")},
        context=dict(percentiles={},trend="unknown",volatility="unknown",rules_version="context-v1"),
        quality=dict(status="complete",reasons=[],rules_version="quality-v1",crosscheck_deviation_pct=0,
                     source_ages_seconds={},news_counts={}))
    return Snapshot(**(values|overrides))


def article(number=0,**overrides):
    from btc_jev.domain import Article
    as_of=prediction().as_of
    values=dict(id=f"article-{number}",source="coindesk",url=f"https://example.org/{number}",
        title=f"Bitcoin synthetic {number}",summary="Synthetic description",content_hash=f"hash-{number}",
        published_at=as_of-timedelta(hours=1),first_seen_at=as_of-timedelta(minutes=5),
        fetched_at=as_of-timedelta(minutes=5),truncated=False)
    return Article(**(values|overrides))
