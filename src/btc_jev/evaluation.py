from decimal import Decimal
from btc_jev.domain import Settlement,HORIZON_LIMITS


def classify_return(return_pct,horizon_days):
    limit=HORIZON_LIMITS[horizon_days]
    if not return_pct.is_finite(): raise ValueError('nonfinite_return')
    return 'bullish' if return_pct>limit else 'bearish' if return_pct<-limit else 'sideways'


def brier_score(probabilities,actual):
    return sum((probabilities[k]-float(k==actual))**2 for k in ('bullish','sideways','bearish'))


def settle(prediction,target_price,settled_at):
    if settled_at<prediction.due_at: raise ValueError('not_mature')
    change=100*(target_price/prediction.reference_price-1)
    return Settlement(prediction_id=prediction.id,target_price=target_price,return_pct=change,
        actual=classify_return(change,prediction.horizon_days),settled_at=settled_at,result_version=1)
