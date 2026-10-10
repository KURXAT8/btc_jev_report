from datetime import timedelta
from decimal import Decimal as D
import pytest
from tests.factories import snapshot


def test_history_and_frozen_four_baselines():
    from btc_jev.baselines import historical_probabilities,build_baselines
    p,status=historical_probabilities(['bullish']*29)
    assert status=='uniform_fallback' and p['bullish']==pytest.approx(1/3)
    p,status=historical_probabilities(['bullish']*30)
    assert status=='ready' and p['bullish']==pytest.approx(31/33)
    s=snapshot()
    rows=build_baselines(s,3,[])
    assert len(rows)==4
    assert [r.choice for r in rows]==['sideways',None,None,'sideways']
    assert build_baselines(s,7,[])[-1].choice=='sideways'
    assert build_baselines(s.model_copy(update={'hourly':s.hourly[-5:]}),7,[])[-1].status=='unavailable'


def test_nonuniform_fallback_rejected():
    from btc_jev.baselines import build_baselines
    from btc_jev.domain import BaselineForecast
    b=build_baselines(snapshot(),3,[])[2].model_dump()
    b['probabilities']={'bullish':.7,'sideways':.2,'bearish':.1}
    with pytest.raises(ValueError): BaselineForecast(**b)
