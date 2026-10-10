from datetime import date
from tests.factories import prediction,snapshot,article


def test_failure_and_escaping_and_uncertainty():
    from btc_jev.render import render_report
    from btc_jev.domain import Distribution
    day=date(2026,9,21)
    failed=render_report(day,[],{},None,'新聞來源無法取得')
    assert '分析未完成' in failed.subject and '新聞來源無法取得' in failed.text
    assert '命中率：0%' not in failed.text
    p=prediction(distribution=Distribution(probabilities={'bullish':.4,'sideways':.4,'bearish':.2},choice='sideways',confidence=.2))
    report=render_report(day,[p],{'snapshot':snapshot(articles=[article(title='<script>x</script>',url='javascript:evil')]),'gmail_app_password':'NEVER_RENDER'},None,None)
    assert '方向不明確' in report.text
    assert '<script>' not in report.html and 'javascript:' not in report.html
    assert 'NEVER_RENDER' not in report.text+report.html
    assert '40.0%' in report.text and '模型確定程度' in report.text


def test_weekly_empty_and_missing_deviation(tmp_path):
    from btc_jev.render import render_report
    from btc_jev.weekly import build_weekly_report
    from btc_jev.store import Store
    s=snapshot(); q=s.quality.model_copy(update={'status':'degraded','crosscheck_deviation_pct':None})
    report=render_report(s.as_of.date(),[],{'snapshot':s.model_copy(update={'quality':q})},build_weekly_report(Store(tmp_path/'db'),s.as_of,s.as_of),'Jev unavailable')
    assert 'Jev Weekly Report' in report.subject
    assert '尚無可評估資料' in report.text and '小樣本' in report.text
    assert '跨所價差' not in report.text and '部分缺漏' in report.text
