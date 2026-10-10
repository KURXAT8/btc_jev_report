from decimal import Decimal as D
from datetime import timedelta
import pytest
from tests.factories import prediction


def test_boundaries_and_brier():
    from btc_jev.evaluation import classify_return,brier_score,settle
    assert [classify_return(D(x),h) for x,h in [('2',3),('-4',7),('4.0001',7)]]==['sideways','sideways','bullish']
    assert brier_score({'bullish':1,'sideways':0,'bearish':0},'bearish')==2
    p=prediction()
    with pytest.raises(ValueError): settle(p,D(103),p.due_at-timedelta(seconds=1))
    assert settle(p,D(102),p.due_at).actual=='sideways'
