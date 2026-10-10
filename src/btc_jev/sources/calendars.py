from datetime import datetime,timedelta
import calendar
import re
from zoneinfo import ZoneInfo

from btc_jev.domain import CalendarEvent,SourceHealth
from btc_jev.sources.common import PublicHTTP,SourceError
from btc_jev.sources.news import plain
from btc_jev.util import digest,utcnow

URLS={"fomc":"https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
      "cpi":"https://www.bls.gov/schedule/news_release/cpi.htm"}
NEW_YORK=ZoneInfo("America/New_York")


def event_in_window(event,as_of,horizon_days):
    if event.known_at > as_of:
        return False
    end=as_of+timedelta(days=horizon_days)
    if event.time_precision=="exact":
        return as_of < event.scheduled_start <= end
    return event.scheduled_end > as_of and event.scheduled_start <= end


def parse_calendar(text,source,known_at):
    definitions=[]
    try:
        if source=="cpi":
            if "Consumer Price Index" not in text or "Release Date" not in plain(text):
                raise ValueError
            for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>",text,re.S|re.I):
                cells=[plain(x) for x in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>",row,re.S|re.I)]
                if len(cells)<3 or "Release Date" in cells:
                    continue
                date_text=cells[1].replace(".","")
                parsed=None
                for fmt in ("%b %d, %Y","%B %d, %Y"):
                    try:
                        parsed=datetime.strptime(date_text,fmt).replace(tzinfo=NEW_YORK); break
                    except ValueError:
                        pass
                if parsed is None:
                    raise ValueError
                if cells[2]:
                    t=datetime.strptime(cells[2],"%I:%M %p")
                    parsed=parsed.replace(hour=t.hour,minute=t.minute)
                    end=parsed; precision="exact"
                else:
                    end=parsed+timedelta(days=1); precision="date"
                definitions.append(("CPI "+cells[0],parsed,end,precision))
        elif source=="fomc":
            sections=re.split(r"(\d{4})\s+FOMC\s+Meetings",text)
            if len(sections)<3:
                raise ValueError
            months={name.lower():i for i,name in enumerate(calendar.month_name) if name}
            months.update({name.lower():i for i,name in enumerate(calendar.month_abbr) if name})
            for n in range(1,len(sections),2):
                year=int(sections[n]); section=sections[n+1]
                pairs=re.findall(r'class="[^"]*fomc-meeting__month[^\"]*"[^>]*>(.*?)</div>.*?class="[^"]*fomc-meeting__date[^\"]*"[^>]*>(.*?)</div>',section,re.S)
                for month_text,day_text in pairs:
                    month_names=plain(month_text).lower().split("/")
                    days=re.fullmatch(r"(\d{1,2})(?:\s*[-–]\s*(\d{1,2}))?\*?(?:\s+(\(notation vote\)))?",plain(day_text))
                    if not days or any(m.strip() not in months for m in month_names):
                        raise ValueError
                    first,last=int(days[1]),int(days[2] or days[1])
                    start=datetime(year,months[month_names[0].strip()],first,tzinfo=NEW_YORK)
                    end=datetime(year,months[month_names[-1].strip()],last,tzinfo=NEW_YORK)+timedelta(days=1)
                    title="FOMC notation vote" if days[3] else "FOMC meeting"
                    definitions.append((title,start,end,"date_range" if start+timedelta(days=1)!=end else "date"))
        else:
            raise ValueError
        if not definitions:
            raise ValueError  # No recognizable scheduled rows is a parser failure, not healthy empty.
        result=[]
        for title,start,end,precision in definitions:
            fingerprint=digest([source,title,start,end,precision])
            result.append(CalendarEvent(id=digest([source,title,start]),source=source,url=URLS[source],
                title=title,content_hash=fingerprint,scheduled_start=start,scheduled_end=end,
                known_at=known_at,time_precision=precision,source_timezone="America/New_York"))
        return result
    except (ValueError,KeyError,IndexError):
        raise SourceError("calendar_parse_failed") from None


class CalendarClient:
    def __init__(self,http,*,now=utcnow,sleep=None):
        self.now=now; self.io=PublicHTTP(http,now=now,**({"sleep":sleep} if sleep else {}))
    def collect(self,run_at):
        events,health=[],{}
        for source,url in URLS.items():
            try:
                r=self.io.get(url,kind="html",deadline=run_at+timedelta(minutes=3))
                completed=self.now(); items=parse_calendar(r.text,source,completed)
                events.extend(items)
                health[source]=SourceHealth(status="ok",last_success_at=completed,last_attempt_at=completed,
                                            reason=None,item_count=len(items))
            except (SourceError,ValueError):
                health[source]=SourceHealth(status="error",last_success_at=None,last_attempt_at=self.now(),
                                            reason="calendar_fetch_failed",item_count=0)
        return events,health
