from datetime import timedelta
from btc_jev.domain import BaselineForecast
from btc_jev.evaluation import classify_return
from btc_jev.util import digest,dt

KEYS=('bullish','sideways','bearish')


def historical_probabilities(labels):
    if any(label not in KEYS for label in labels): raise ValueError('invalid_label')
    if len(labels)<30: return dict.fromkeys(KEYS,1/3),'uniform_fallback'
    return {k:(labels.count(k)+1)/(len(labels)+3) for k in KEYS},'ready'


def build_baselines(snapshot,horizon_days,history):
    history=sorted([r for r in history if dt(r['known_at'])<snapshot.as_of and
        snapshot.as_of-timedelta(days=365)<=dt(r['due_at'])<snapshot.as_of],key=lambda r:dt(r['due_at']))
    probabilities,status=historical_probabilities([r['actual'] for r in history])
    historical_choice=None if status=='uniform_fallback' else max(('sideways','bullish','bearish'),key=lambda k:probabilities[k])
    bars=snapshot.hourly
    target=bars[-1].open_at-timedelta(days=horizon_days) if bars else None
    previous=next((b for b in bars if b.open_at==target),None)
    momentum=classify_return(100*(bars[-1].close/previous.close-1),horizon_days) if previous else None
    onehot=lambda choice:{k:float(k==choice) for k in KEYS}
    specs=[('always_sideways',onehot('sideways'),'sideways','ready',[]),
        ('uniform',dict.fromkeys(KEYS,1/3),None,'ready',[]),
        ('historical',probabilities,historical_choice,status,[r['id'] for r in history]),
        ('momentum',onehot(momentum) if momentum else None,momentum,'ready' if momentum else 'unavailable',
         [digest(previous),digest(bars[-1])] if momentum else [])]
    return [BaselineForecast(id=digest([snapshot.as_of,horizon_days,name,'baseline-v1']),name=name,
        version='baseline-v1',label_version='labels-v1',as_of=snapshot.as_of,
        due_at=snapshot.as_of+timedelta(days=horizon_days),horizon_days=horizon_days,
        reference_price=snapshot.reference_price,probabilities=p,choice=choice,history_count=len(history) if name=='historical' else 0,
        input_ids=ids,status=state) for name,p,choice,state,ids in specs]
