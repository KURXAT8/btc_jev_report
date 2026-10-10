from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import httpx
import pytest

NOW = datetime(2026, 9, 20, 23, 25, tzinfo=timezone.utc)


def test_cpi_exact_time_dst_and_missing_time():
    from btc_jev.sources.calendars import parse_calendar
    events = parse_calendar(Path("tests/fixtures/cpi.html").read_text(), "cpi", NOW)
    assert len(events) == 3
    assert events[0].scheduled_start.hour == 13
    assert events[1].scheduled_start.hour == 12
    assert events[2].time_precision == "date"
    assert events[2].scheduled_start.astimezone(ZoneInfo("America/New_York")).hour == 0
    assert events[2].scheduled_end-events[2].scheduled_start == timedelta(days=1)
    assert all(e.known_at == NOW for e in events)


def test_fomc_date_range_preserves_full_local_days():
    from btc_jev.sources.calendars import parse_calendar
    events = parse_calendar(Path("tests/fixtures/fomc.html").read_text(), "fomc", NOW)
    assert len(events) == 2
    assert events[0].time_precision == "date_range"
    assert events[0].scheduled_start == datetime(2026,9,15,4,tzinfo=timezone.utc)
    assert events[0].scheduled_end == datetime(2026,9,17,4,tzinfo=timezone.utc)


@pytest.mark.parametrize('month,days,start,end,title', [
    ('Apr/May', '30-1', (2026,4,30), (2026,5,2), 'FOMC meeting'),
    ('Jan/Feb', '31-1', (2026,1,31), (2026,2,2), 'FOMC meeting'),
    ('August', '22 (notation vote)', (2026,8,22), (2026,8,23), 'FOMC notation vote'),
])
def test_fomc_live_page_month_abbreviations_and_notation_votes(month,days,start,end,title):
    from btc_jev.sources.calendars import parse_calendar
    text = ('<h4>2026 FOMC Meetings</h4>'
            f'<div class="fomc-meeting__month"><strong>{month}</strong></div>'
            f'<div class="fomc-meeting__date">{days}</div>')
    event, = parse_calendar(text, 'fomc', NOW)
    zone = ZoneInfo('America/New_York')
    assert event.scheduled_start == datetime(*start,tzinfo=zone)
    assert event.scheduled_end == datetime(*end,tzinfo=zone)
    assert event.title == title


def test_fomc_unknown_date_annotation_is_still_rejected():
    from btc_jev.sources.calendars import parse_calendar
    from btc_jev.sources.common import SourceError
    text = ('<h4>2026 FOMC Meetings</h4><div class="fomc-meeting__month">August</div>'
            '<div class="fomc-meeting__date">22 (unrecognized)</div>')
    with pytest.raises(SourceError):
        parse_calendar(text, 'fomc', NOW)


def test_future_event_requires_prior_knowledge_and_overlaps_date_range():
    from btc_jev.sources.calendars import event_in_window
    from btc_jev.domain import CalendarEvent
    e = CalendarEvent(id="cpi", source="cpi", url="https://www.bls.gov/",title="Synthetic CPI",
        content_hash="hash", scheduled_start=NOW+timedelta(days=3),scheduled_end=NOW+timedelta(days=3),
        known_at=NOW,time_precision="exact",source_timezone="America/New_York")
    assert event_in_window(e, NOW, 3)
    assert not event_in_window(e.model_copy(update={"known_at":NOW+timedelta(seconds=1)}), NOW,3)
    assert not event_in_window(e.model_copy(update={"scheduled_start":NOW,"scheduled_end":NOW}), NOW,3)


def test_changed_schedule_has_new_hash_and_parsing_failure_is_error():
    from btc_jev.sources.calendars import parse_calendar, CalendarClient
    from btc_jev.sources.common import SourceError
    text = Path("tests/fixtures/cpi.html").read_text()
    assert parse_calendar(text,"cpi",NOW)[0].content_hash != parse_calendar(text.replace("Jan. 13", "Jan. 14"),"cpi",NOW)[0].content_hash
    with pytest.raises(SourceError):
        parse_calendar("<html>Access denied</html>","cpi",NOW)
    def handler(r):
        return httpx.Response(200, text=text, headers={"content-type":"text/html"}) if r.url.host == "www.bls.gov" else httpx.Response(403)
    events,health = CalendarClient(httpx.Client(transport=httpx.MockTransport(handler)),now=lambda:NOW).collect(NOW)
    assert health["cpi"].status == "ok" and health["fomc"].status == "error"
    assert len(events)==3
