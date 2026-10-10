"""Offline checks of planning documents and their existing example formulas.

Run from any directory with Python 3.11+. This does not test the future app.
Only explicitly selected local example functions are executed, with fake data.
"""

import ast
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import re
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[3]
PLAN = ROOT / 'docs/superpowers/plans/2026-09-21-btc-jev-daily-weekly-email.md'
DOCUMENTS = [ROOT / 'README.md', *sorted((ROOT / 'docs').rglob('*.md'))]
FUNCTION_NAMES = {
    'select_mail_settings', 'closed_candles', 'event_in_window',
    'classify_quality', 'historical_percentile', 'classify_return',
    'brier_score', 'historical_probabilities', 'previous_week_window',
}


def implementation_progress(plan):
    """Count documented checkboxes, not verified application completion."""
    tasks = re.findall(r'^## Task (\d+):[^\n]*\n(.*?)(?=^## |\Z)', plan, re.M | re.S)
    assert [number for number, _ in tasks] == [str(i) for i in range(1, 10)]
    complete = 0
    for _, body in tasks:
        checks = re.findall(r'^- \[([ xX])\]', body, re.M)
        assert checks, 'Every task needs a checklist'
        complete += all(check.lower() == 'x' for check in checks)
    return complete, len(tasks)


def main():
    namespace = {
        'Decimal': Decimal, 'timedelta': timedelta, 'ZoneInfo': ZoneInfo,
        'MAIL_KEYS': ('GMAIL_ADDRESS', 'GMAIL_APP_PASSWORD', 'RECIPIENT_EMAIL'),
    }
    found = set()
    links = blocks = cases = 0
    for path in DOCUMENTS:
        text = path.read_text()
        if re.search(r'\b(?:TODO|TBD|FIXME)\b', text):
            raise AssertionError(f'Unresolved placeholder: {path}')
        assert sum(line.startswith('```') for line in text.splitlines()) % 2 == 0, path
        for target in re.findall(r'\[[^\]\n]+\]\(([^)\s]+)\)', text):
            parsed = urlsplit(target)
            if parsed.scheme or not parsed.path:
                continue
            assert (path.parent / unquote(parsed.path)).exists(), (path, target)
            links += 1
        for number, code in enumerate(re.findall(r'^```python\n(.*?)^```', text, re.M | re.S)):
            tree = ast.parse(code, filename=f'{path}:{number + 1}')
            compile(tree, str(path), 'exec')
            blocks += 1
            if path != PLAN:
                continue
            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and node.name in FUNCTION_NAMES:
                    assert node.name not in found, node.name
                    found.add(node.name)
                    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    assert found == FUNCTION_NAMES, FUNCTION_NAMES - found
    plan = PLAN.read_text()
    complete, total = implementation_progress(plan)

    def check(value, expected):
        nonlocal cases
        assert value == expected, (value, expected)
        cases += 1

    classify = namespace['classify_return']
    for horizon, limit in [(3, Decimal(2)), (7, Decimal(4))]:
        for value, result in [(limit, 'sideways'), (-limit, 'sideways'),
                              (limit + Decimal('.0001'), 'bullish'),
                              (-limit - Decimal('.0001'), 'bearish')]:
            check(classify(value, horizon), result)
    score = namespace['brier_score']
    check(score({'bullish': 1, 'sideways': 0, 'bearish': 0}, 'bullish'), 0)
    check(score({'bullish': 1, 'sideways': 0, 'bearish': 0}, 'bearish'), 2)
    percentile = namespace['historical_percentile']
    check(percentile(Decimal(5), [Decimal(5)] * 29), None)
    check(percentile(Decimal(5), [Decimal(5)] * 30), Decimal(50))
    check(percentile(Decimal(6), [Decimal(5)] * 30), Decimal(100))
    check(percentile(Decimal(4), [Decimal(5)] * 30), Decimal(0))
    quality = namespace['classify_quality']
    for args, result in [((True, 2, True, Decimal('.3'), True), 'complete'),
                         ((True, 2, True, Decimal('1'), True), 'degraded'),
                         ((True, 2, True, Decimal('1.0001'), True), 'unavailable'),
                         ((True, 1, True, Decimal('.1'), True), 'degraded'),
                         ((True, 0, True, None, True), 'unavailable'),
                         ((True, 2, True, None, True), 'degraded'),
                         ((False, 2, True, Decimal('.1'), True), 'unavailable'),
                         ((True, 2, True, Decimal('.1'), False), 'degraded')]:
        check(quality(*args), result)
    prior = namespace['historical_probabilities']
    p, status = prior(['bullish'] * 29)
    check(status, 'uniform_fallback')
    check(p['bullish'], 1 / 3)
    p, status = prior(['bullish'] * 30)
    check(status, 'ready')
    check(p['bullish'], 31 / 33)
    check(p['bearish'], 1 / 33)
    cutoff = datetime(2026, 9, 21, tzinfo=timezone.utc)
    event = SimpleNamespace(known_at=cutoff - timedelta(seconds=1),
                            time_precision='exact', scheduled_start=cutoff + timedelta(days=3),
                            scheduled_end=cutoff + timedelta(days=3))
    in_window = namespace['event_in_window']
    check(in_window(event, cutoff, 3), True)
    event.known_at = cutoff + timedelta(seconds=1)
    check(in_window(event, cutoff, 3), False)
    event.known_at = cutoff - timedelta(seconds=1)
    event.scheduled_start = cutoff
    check(in_window(event, cutoff, 3), False)
    event.time_precision = 'date_range'
    event.scheduled_start = cutoff - timedelta(days=1)
    event.scheduled_end = cutoff
    check(in_window(event, cutoff, 3), False)
    event.scheduled_end = cutoff + timedelta(days=1)
    check(in_window(event, cutoff, 3), True)
    tz = ZoneInfo('Asia/Taipei')
    check(namespace['previous_week_window'](datetime(2027, 1, 4, 7, 30, tzinfo=tz)),
          (datetime(2026, 12, 28, tzinfo=tz), datetime(2027, 1, 4, tzinfo=tz)))
    old = SimpleNamespace(open_at=cutoff - timedelta(minutes=1),
                          close_at=cutoff - timedelta(milliseconds=1))
    future = SimpleNamespace(open_at=cutoff, close_at=cutoff + timedelta(minutes=1))
    check(namespace['closed_candles']([future, old], cutoff), [old])
    check(namespace['select_mail_settings']({'GMAIL_ADDRESS': 'fake@example.org',
          'GEMINI_API_KEY': 'fake-unused', 'DATA_DIR': '/fake'}),
          {'GMAIL_ADDRESS': 'fake@example.org'})
    for month, expected_hour in [(1, 13), (7, 12)]:
        check(datetime(2026, month, 15, 8, 30, tzinfo=ZoneInfo('America/New_York'))
              .astimezone(timezone.utc).hour, expected_hour)
    print(f'PASS: {len(DOCUMENTS)} documents; {links} local links; {blocks} Python blocks compile; '
          f'{len(found)} example functions; {cases} example/boundary checks; '
          f'documented progress {complete}/{total} tasks (not verified by this script).')
    print('Scope: document structure and selected example formulas only; no application, API, SMTP or timer validation.')


if __name__ == '__main__':
    main()
