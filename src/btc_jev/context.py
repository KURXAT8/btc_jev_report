from datetime import timedelta
from decimal import Decimal as D
from btc_jev.domain import ContextSummary
from btc_jev.indicators import compute_features,volatility
from btc_jev.util import dt


def historical_percentile(value, earlier_values):
    if value is None or len(earlier_values)<30:
        return None
    return 100*(D(sum(x<value for x in earlier_values))+D(sum(x==value for x in earlier_values))/2)/len(earlier_values)


def volatility_regime(value):
    return 'unknown' if value is None else 'low' if value<25 else 'high' if value>75 else 'medium'


def market_regime(trend, volatility_percentile):
    if volatility_percentile is None or trend == 'unknown':
        return 'unknown'
    if volatility_percentile > 75:
        return 'high_volatility'
    if volatility_percentile < 25:
        return 'low_volatility'
    if trend == 'up':
        return 'trend_up'
    if trend == 'down':
        return 'trend_down'
    return 'mixed'


def build_context(market,history,as_of):
    as_of=dt(as_of)
    market=dict(market,as_of=as_of)
    features=compute_features(market)
    daily=market['daily']
    def attr(b,k): return b[k] if isinstance(b,dict) else getattr(b,k)
    prices=[D(attr(b,'close')) for b in daily]
    series={
        'volume':[(dt(attr(b,'close_at')),D(attr(b,'volume'))) for b in daily[:-1]][-90:],
        'volatility':[(dt(attr(daily[i],'close_at')),volatility(prices[:i+1])) for i in range(20,len(daily)-1)][-90:],
        'oi_24h':[(dt(r['at']),D(r['value'])) for r in history.get('oi_24h',[]) if as_of-timedelta(days=90)<=dt(r['at'])<as_of and dt(r['at']).hour==as_of.hour],
    }
    funding=sorted({dt(r['at']):D(r['rate']) for r in market['funding'] if dt(r['at'])<=as_of}.items())
    interval=funding[-1][0]-funding[-2][0] if len(funding)>1 else None
    all_funding=sorted(dict([(dt(r['at']),D(r['value'])) for r in history.get('funding',[])]+funding).items())
    series['funding']=[(t,v) for (prev,_),(t,v) in zip(all_funding,all_funding[1:]) if funding and as_of-timedelta(days=90)<=t<funding[-1][0] and t-prev==interval]
    current={'volume':D(attr(daily[-1],'volume')),'volatility':features['volatility20'],
             'oi_24h':features['oi_24h_change_pct'],'funding':features['funding_rate']}
    percentiles={}
    for name,rows in series.items():
        rows=sorted({t:v for t,v in rows if v is not None and t<as_of}.items())
        p=historical_percentile(current[name],[v for t,v in rows])
        percentiles[name]=dict(value=current[name],percentile=p,start=rows[0][0] if rows else None,
            end=rows[-1][0] if rows else None,sample_count=len(rows),warmup=p is None)
    p0,s20,s60=D(market['reference_price']),features['sma20'],features['sma60']
    trend='unknown' if s20 is None or s60 is None else 'up' if p0>s20>s60 else 'down' if p0<s20<s60 else 'mixed'
    volatility_percentile=percentiles['volatility']['percentile']
    return ContextSummary(percentiles=percentiles,trend=trend,
        volatility=volatility_regime(volatility_percentile),
        regime=market_regime(trend,volatility_percentile),rules_version='context-v1')
