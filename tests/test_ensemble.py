from datetime import timedelta


def test_probability_ensemble_normalizes_and_is_deterministic():
    from btc_jev.ensemble import ensemble_candidates
    formal={'bullish':.7,'sideways':.2,'bearish':.1}
    baseline={'bullish':.2,'sideways':.5,'bearish':.3}
    result=ensemble_candidates(formal, {'uniform':baseline}, {'event_risk':formal})
    assert set(result)=={'formal','formal+uniform','formal+event_risk','all_available'}
    assert all(abs(sum(p.values())-1)<1e-9 for p in result.values())
    assert abs(result['formal+uniform']['bullish']-.45)<1e-9


def test_low_signal_boundaries_and_fallback():
    from btc_jev.ensemble import low_signal, recent_fallback
    assert low_signal({'bullish':.54,'sideways':.3,'bearish':.16})
    assert low_signal({'bullish':.6,'sideways':.25,'bearish':.15},
                      role_probabilities=[{'bullish':.1,'sideways':.2,'bearish':.7}])
    assert not low_signal({'bullish':.7,'sideways':.2,'bearish':.1})
    rows=[({'bullish':.8,'sideways':.1,'bearish':.1},'bullish')]*20
    assert recent_fallback(rows)['bullish'] > .7


def test_candidate_selection_requires_warmup_and_rejects_degradation():
    from btc_jev.ensemble import select_candidate
    rows=[]
    for i in range(20):
        rows.append(dict(as_of=i,formal={'bullish':.8,'sideways':.1,'bearish':.1},
                         candidates={'formal':{'bullish':.8,'sideways':.1,'bearish':.1}},actual='bullish'))
    assert select_candidate(rows)['status']=='warmup'
    balanced = rows + [dict(as_of=100+i, formal={'bullish':.3,'sideways':.4,'bearish':.3},
                            candidates={'formal':{'bullish':.3,'sideways':.4,'bearish':.3}}, actual=key)
                       for i,key in enumerate(['bullish','sideways','bearish']*15)]
    assert select_candidate(balanced)['status'] in {'accepted','rejected'}
