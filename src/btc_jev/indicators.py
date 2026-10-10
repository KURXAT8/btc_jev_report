"""Deterministic indicators; all return percentages are unannualized."""
from datetime import timedelta
from decimal import Decimal as D
from btc_jev.quality import oi_at
from btc_jev.util import dt


def ratio(a, b):
    return a / b if b > 0 else None


def returns(prices, periods):
    if len(prices) <= periods or prices[-1-periods] <= 0:
        return None
    return 100 * (prices[-1] / prices[-1-periods] - 1)


def volatility(prices):
    if len(prices) < 21 or any(p <= 0 for p in prices[-21:]):
        return None
    values = [(b/a).ln() for a,b in zip(prices[-21:-1],prices[-20:])]
    mean = sum(values)/20
    return (sum((x-mean)**2 for x in values)/19).sqrt()


def _window_volatility(prices, size):
    return volatility(prices[-size:]) if len(prices) >= size else None


def rsi(prices):
    if len(prices) < 15:
        return None
    changes = [b-a for a,b in zip(prices,prices[1:])]
    gain = sum(max(x,D(0)) for x in changes[:14])/14
    loss = sum(max(-x,D(0)) for x in changes[:14])/14
    for change in changes[14:]:
        gain = (13*gain+max(change,D(0)))/14
        loss = (13*loss+max(-change,D(0)))/14
    return D(50) if gain==loss==0 else D(100) if loss==0 else 100-100/(1+gain/loss)


def compare(a,b):
    return 'missing' if a is None or b is None else 'above' if a>b else 'below' if a<b else 'equal'


def compute_features(snapshot):
    s = snapshot if isinstance(snapshot,dict) else snapshot.model_dump()
    def field(bar,key):
        return bar[key] if isinstance(bar,dict) else getattr(bar,key)
    daily,hourly,four = [s[k] for k in ('daily','hourly','four_hour')]
    d,h,f = [[D(field(b,'close')) for b in bars] for bars in (daily,hourly,four)]
    value = {f'daily_return_{n}d_pct':returns(d,n) for n in (1,3,7,30)}
    value.update({f'hourly_return_{n}h_pct':returns(h,n) for n in (1,4,24)})
    value.update(sma20=sum(d[-20:])/20 if len(d)>=20 else None,
        sma60=sum(d[-60:])/60 if len(d)>=60 else None,rsi14=rsi(d),volatility20=volatility(d),
        four_hour_return_6_pct=returns(f,6), daily_cutoff=field(daily[-1],'close_at') if daily else None,
        hourly_cutoff=field(hourly[-1],'close_at') if hourly else None)
    value['daily_volume_ratio'] = ratio(D(field(daily[-1],'volume')),sum(D(field(b,'volume')) for b in daily[-21:-1])/20) if len(daily)>=21 else None
    volume = ratio(sum(D(field(b,'volume')) for b in hourly[-24:]),sum(D(field(b,'volume')) for b in hourly[-48:-24])) if len(hourly)>=48 else None
    value['hourly_volume_change_pct'] = 100*(volume-1) if volume is not None else None
    value['price_vs_sma20_pct'] = (100*(D(s['reference_price'])/value['sma20']-1)
                                   if value['sma20'] else None)
    value['price_vs_sma60_pct'] = (100*(D(s['reference_price'])/value['sma60']-1)
                                   if value['sma60'] else None)
    recent_vol = _window_volatility(d, 21)
    prior_vol = volatility(d[-42:-21]) if len(d) >= 42 else None
    value['volatility_persistence'] = (recent_vol/prior_vol if recent_vol is not None and prior_vol and prior_vol > 0 else None)
    value['volatility_jump'] = (recent_vol-prior_vol if recent_vol is not None and prior_vol is not None else None)
    current = oi_at(s['open_interest'],s['as_of'])
    for hours,name in ((24,'24h'),(168,'7d')):
        previous=oi_at(s['open_interest'],s['as_of']-timedelta(hours=hours))
        change=ratio(D(current['quantity']),D(previous['quantity'])) if current and previous else None
        value_change=ratio(D(current['value']),D(previous['value'])) if current and previous else None
        value[f'oi_{name}_change_pct']=100*(change-1) if change is not None else None
        value[f'oi_{name}_value_change_pct']=100*(value_change-1) if value_change is not None else None
        value[f'oi_{name}_comparison_at']=dt(previous['at']) if previous else None
    value['oi_at']=dt(current['at']) if current else None
    value['oi_quantity_btc']=D(current['quantity']) if current else None
    value['oi_value_usdt']=D(current['value']) if current else None
    funding=[r for r in s['funding'] if dt(r['at'])<=s['as_of']]
    value['funding_rate']=D(funding[-1]['rate']) if funding else None
    value['funding_at']=dt(funding[-1]['at']) if funding else None
    previous_funding = funding[-2] if len(funding) >= 2 else None
    value['funding_change_pct'] = (100*(value['funding_rate']/D(previous_funding['rate'])-1)
                                   if previous_funding and D(previous_funding['rate']) != 0 else None)
    value['funding_change_abs'] = (value['funding_rate']-D(previous_funding['rate'])
                                   if previous_funding else None)
    exchanges = s.get('exchange_prices') or {}
    exchange_rows = {}
    for name, row in exchanges.items():
        try:
            at = dt(row['at']); price = D(row['price'])
            if at <= s['as_of'] and price > 0:
                exchange_rows[name] = (price, at)
        except (KeyError, TypeError, ValueError, ArithmeticError):
            continue
    if len(exchange_rows) >= 2:
        prices = [pair[0] for pair in exchange_rows.values()]
        value['exchange_deviation_pct'] = 100*(max(prices)/min(prices)-1)
        value['exchange_freshness_seconds'] = max((s['as_of']-pair[1]).total_seconds() for pair in exchange_rows.values())
    else:
        value['exchange_deviation_pct'] = None
        value['exchange_freshness_seconds'] = None
    def sign(v):
        return 'missing' if v is None else 'positive' if v>0 else 'negative' if v<0 else 'zero'
    value['semantic']={'reference_vs_sma20':compare(D(s['reference_price']),value['sma20']),
        'sma20_vs_sma60':compare(value['sma20'],value['sma60']),
        'oi_24h_direction':sign(value['oi_24h_change_pct']),'funding_sign':sign(value['funding_rate'])}
    return value
