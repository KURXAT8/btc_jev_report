from datetime import timedelta
from decimal import Decimal
import httpx
import pytest
from tests.test_sources import ASOF, market_transport, ms
from btc_jev.domain import SourceHealth


@pytest.mark.parametrize("args,want", [
    ((True,2,True,".3",True),"complete"),((True,2,True,"1",True),"degraded"),
    ((True,2,True,"1.0001",True),"unavailable"),((True,1,True,".1",True),"degraded"),
    ((True,0,True,None,True),"unavailable"),((False,2,True,".1",True),"unavailable"),
    ((True,2,True,None,True),"degraded"),((True,2,True,".1",False),"degraded"),
])
def test_quality_boundaries(args,want):
    from btc_jev.quality import classify_quality
    main,n,fed,deviation,cal=args
    assert classify_quality(main,n,fed,Decimal(deviation) if deviation else None,cal)==want


def health(age=0,status="ok"):
    return SourceHealth(status=status,last_success_at=ASOF-timedelta(seconds=age),
        last_attempt_at=ASOF,reason=None,item_count=0)


def test_fresh_failed_attempt_cache_and_calendar_boundaries():
    from btc_jev.quality import assess_quality
    from btc_jev.sources.market import MarketClient
    market=MarketClient(httpx.Client(transport=httpx.MockTransport(market_transport)),now=lambda:ASOF).collect(ASOF)
    news={s:health(5400,"error") for s in ("coindesk","cointelegraph","fed")}
    calendars={s:health(86400) for s in ("cpi","fomc")}
    assert assess_quality(market,news,calendars,Decimal("100.3"),ASOF).status=="complete"
    calendars["cpi"]=health(86401)
    assert assess_quality(market,news,calendars,Decimal("100"),ASOF).status=="degraded"
    news["coindesk"]=news["cointelegraph"]=health(5401)
    result=assess_quality(market,news,calendars,None,ASOF)
    assert result.status=="unavailable" and result.crosscheck_deviation_pct is None


def test_stale_funding_and_missing_oi_are_unavailable():
    from btc_jev.quality import assess_quality
    from btc_jev.sources.market import MarketClient
    market=MarketClient(httpx.Client(transport=httpx.MockTransport(market_transport)),now=lambda:ASOF).collect(ASOF)
    news={s:health() for s in ("coindesk","cointelegraph","fed")}
    calendars={s:health() for s in ("cpi","fomc")}
    for override in ({"funding":[{"at":ASOF-timedelta(hours=13),"rate":Decimal(".01")}]},
                     {"open_interest":[]}, {"hourly":market["hourly"][:-1]}):
        assert assess_quality(market|override,news,calendars,Decimal(100),ASOF).status=="unavailable"


@pytest.mark.parametrize("offset,confirm,price,want",[(0,"1","100","100"),(60000,"1","100",None),
    (0,"0","100",None),(0,"1","NaN",None)])
def test_okx_only_accepts_exact_confirmed_minute(offset,confirm,price,want):
    from btc_jev.sources.crosscheck import CrosscheckClient
    def handler(r):
        assert r.url.params["instId"]=="BTC-USDT" and r.url.params["bar"]=="1m"
        return httpx.Response(200,json={"code":"0","data":[[str(ms(ASOF)-60000+offset),"100","100","100",price,"2","2","200",confirm]]})
    result=CrosscheckClient(httpx.Client(transport=httpx.MockTransport(handler))).price_at(ASOF)
    assert result==(Decimal(want) if want else None)


def test_oi_matching_uses_only_past_with_one_hour_tolerance():
    from btc_jev.quality import oi_at
    assert oi_at([{"at":ASOF+timedelta(seconds=1),"quantity":100}],ASOF) is None
    assert oi_at([{"at":ASOF-timedelta(hours=1),"quantity":100}],ASOF)["quantity"]==100
    assert oi_at([{"at":ASOF-timedelta(hours=1,seconds=1),"quantity":100}],ASOF) is None
