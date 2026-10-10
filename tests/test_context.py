from decimal import Decimal as D
from datetime import timedelta
import pytest
from tests.factories import snapshot


def test_percentile_ties_warmup_and_current_exclusion():
    from btc_jev.context import historical_percentile, build_context
    assert historical_percentile(D(5),[D(5)]*30)==50
    assert historical_percentile(D(5),[D(5)]*29) is None
    assert historical_percentile(D(6),[D(5)]*30)==100
    s=snapshot(); market=s.model_dump()
    market.update(daily=s.daily,four_hour=s.four_hour,hourly=s.hourly)
    context=build_context(market,{},s.as_of)
    assert context.percentiles["volume"]["sample_count"]==90
    assert context.percentiles["volume"]["percentile"]==50
    assert context.percentiles["funding"]["warmup"]
    assert context.trend=="mixed" and context.volatility=="medium"


def test_historical_oi_uses_past_same_hour_only_and_actual_span():
    from btc_jev.context import build_context
    s=snapshot(); market=s.model_dump(); market.update(daily=s.daily,four_hour=s.four_hour,hourly=s.hourly)
    rows=[{"at":s.as_of-timedelta(days=i),"value":"0"} for i in range(1,31)]
    rows += [{"at":s.as_of,"value":"999"},{"at":s.as_of+timedelta(days=1),"value":"999"},
             {"at":s.as_of-timedelta(hours=1),"value":"999"}]
    result=build_context(market,{"oi_24h":rows},s.as_of)
    assert result.percentiles["oi_24h"]["sample_count"]==30
    assert result.percentiles["oi_24h"]["percentile"]==50


@pytest.mark.parametrize("value,want",[(None,"unknown"),(D("24.99"),"low"),(D(25),"medium"),(D(75),"medium"),(D("75.01"),"high")])
def test_volatility_regime_boundaries(value,want):
    from btc_jev.context import volatility_regime
    assert volatility_regime(value)==want


def test_context_hour_sampling_is_timezone_invariant():
    from btc_jev.context import build_context
    from zoneinfo import ZoneInfo
    s=snapshot(); market=s.model_dump(); market.update(daily=s.daily,four_hour=s.four_hour,hourly=s.hourly)
    history={'oi_24h':[{'at':s.as_of-timedelta(days=i),'value':'0'} for i in range(1,31)]}
    utc=build_context(market,history,s.as_of)
    local=build_context(market,history,s.as_of.astimezone(ZoneInfo('Asia/Taipei')))
    assert local==utc


def test_regime_is_finite_and_cutoff_safe():
    from btc_jev.context import build_context
    s=snapshot(); market=s.model_dump()
    market['daily'] = [*market['daily']]
    market['daily'][-1] = dict(market['daily'][-1],close='110')
    context=build_context(market,{},s.as_of)
    assert context.regime in {'low_volatility','trend_up','trend_down','high_volatility','mixed','unknown'}
