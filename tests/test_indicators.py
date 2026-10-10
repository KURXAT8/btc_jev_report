from decimal import Decimal as D
from datetime import timedelta
import pytest
from tests.factories import snapshot


@pytest.mark.parametrize("prices,want",[([100]*30,D(50)),(list(range(1,31)),D(100)),(list(range(30,0,-1)),D(0))])
def test_wilder_rsi_extremes(prices,want):
    from btc_jev.indicators import rsi
    assert rsi([D(p) for p in prices])==want


def test_features_reference_values_and_semantic_equality():
    from btc_jev.indicators import compute_features
    result=compute_features(snapshot())
    assert result["sma20"]==100 and result["sma60"]==100
    assert result["rsi14"]==50 and result["volatility20"]==0
    assert result["daily_volume_ratio"]==1 and result["hourly_volume_change_pct"]==0
    assert result["oi_24h_change_pct"]==0 and result["oi_7d_change_pct"]==0
    assert result["semantic"]["reference_vs_sma20"]=="equal"
    assert result["semantic"]["funding_sign"]=="positive"
    assert result["hourly_cutoff"]<snapshot().as_of


def test_hourly_rally_is_not_lost_in_flat_daily_prices():
    from btc_jev.indicators import compute_features
    s=snapshot(); hourly=list(s.hourly)
    hourly[-1]=hourly[-1].model_copy(update={"open":D(110),"high":D(110),"low":D(110),"close":D(110),"volume":D(50)})
    result=compute_features(s.model_copy(update={"hourly":hourly}))
    assert result["daily_return_1d_pct"]==0
    assert result["hourly_return_24h_pct"]==10
    assert result["hourly_volume_change_pct"]==100


def test_return_volatility_and_insufficient_denominators():
    from btc_jev.indicators import returns,volatility,rsi,ratio
    assert returns([D(100),D(110)],1)==10
    assert ratio(D(1),D(0)) is None
    assert returns([D(100)],1) is None and rsi([D(100)]) is None
    assert volatility([D(100)]*20) is None
    assert volatility([D(100)]*21)==0


def test_extended_features_are_cutoff_safe_and_keep_missing_values():
    from btc_jev.indicators import compute_features
    s=snapshot(); market=s.model_dump()
    market['funding'] = [*market['funding'], {'at':s.as_of+timedelta(hours=1),'rate':'99'}]
    market['exchange_prices'] = {'binance': {'price':'101','at':s.as_of},
                                 'okx': {'price':'99','at':s.as_of+timedelta(minutes=1)}}
    features=compute_features(market)
    assert features['funding_rate']==D('0.0001')
    assert features['funding_change_pct']==0
    assert features['oi_24h_value_change_pct']==0
    assert features['price_vs_sma20_pct']==0
    assert features['exchange_deviation_pct'] is None
    assert features['exchange_freshness_seconds'] is None
    assert 'volatility_persistence' in features and 'volatility_jump' in features
