from datetime import timedelta
from decimal import Decimal

from btc_jev.domain import QualityReport
from btc_jev.util import dt
from btc_jev.sources.market import millis,WIDTHS

QUALITY_VERSION="quality-v1"


def classify_quality(required_ok,crypto_fresh_count,fed_fresh,deviation_pct,calendars_fresh):
    if not required_ok or crypto_fresh_count==0 or (deviation_pct is not None and deviation_pct>Decimal("1")):
        return "unavailable"
    if crypto_fresh_count==2 and fed_fresh and calendars_fresh and deviation_pct is not None and deviation_pct<=Decimal(".3"):
        return "complete"
    return "degraded"


def oi_at(rows,target):
    earlier=[row for row in rows if dt(row["at"])<=target]
    row=max(earlier,key=lambda r:dt(r["at"])) if earlier else None
    return row if row and target-dt(row["at"])<=timedelta(hours=1) else None


def source_age(health,as_of):
    if health is None or health.last_success_at is None or health.last_success_at>as_of:
        return None
    return int((as_of-health.last_success_at).total_seconds())


def required_market_ok(market,as_of):
    try:
        if not Decimal(str(market["reference_price"])).is_finite() or market["reference_price"]<=0:
            return False
        for key,interval,count in (("daily","1d",120),("four_hour","4h",180),("hourly","1h",200)):
            bars=market[key]; width=WIDTHS[interval]
            if len(bars)<count or millis(bars[-1].close_at)!=millis(as_of)//width*width-1:
                return False
            if any(millis(b.open_at)-millis(a.open_at)!=width for a,b in zip(bars,bars[1:])):
                return False
        funding=market["funding"]
        if not funding or not timedelta(0)<=as_of-dt(funding[-1]["at"])<=timedelta(hours=12):
            return False
        for h in (0,24,168):
            row=oi_at(market["open_interest"],as_of-timedelta(hours=h))
            if not row or Decimal(str(row["quantity"]))<=0:
                return False
        return True
    except (KeyError,ValueError,TypeError,IndexError,ArithmeticError):
        return False


def assess_quality(market,news_health,calendar_health,crosscheck_price,as_of):
    ages={s:source_age(news_health.get(s),as_of) for s in ("coindesk","cointelegraph","fed")}
    ages.update({s:source_age(calendar_health.get(s),as_of) for s in ("cpi","fomc")})
    fresh={s:age is not None and age<=(86400 if s in ("cpi","fomc") else 5400) for s,age in ages.items()}
    required=required_market_ok(market,as_of)
    deviation=None
    if crosscheck_price is not None and market.get("reference_price"):
        deviation=abs(Decimal(str(crosscheck_price))/Decimal(str(market["reference_price"]))-1)*100
    reasons=["stale_or_missing_"+s for s in ages if not fresh[s]]
    if not required:
        reasons.append("required_market_incomplete")
    if deviation is None:
        reasons.append("missing_okx")
    elif deviation>Decimal(".3"):
        reasons.append("crosscheck_conflict" if deviation>1 else "crosscheck_deviation")
    status=classify_quality(required,int(fresh["coindesk"])+int(fresh["cointelegraph"]),fresh["fed"],
                            deviation,fresh["cpi"] and fresh["fomc"])
    return QualityReport(status=status,reasons=reasons,rules_version=QUALITY_VERSION,
        crosscheck_deviation_pct=deviation,source_ages_seconds=ages,
        news_counts={s:h.item_count for s,h in news_health.items()})
