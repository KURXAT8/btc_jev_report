from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import feedparser

from btc_jev.domain import Article, SourceHealth
from btc_jev.sources.common import PublicHTTP, SourceError
from btc_jev.util import digest, utcnow

FEEDS = {"coindesk":"https://www.coindesk.com/arc/outboundfeeds/rss",
         "cointelegraph":"https://cointelegraph.com/rss",
         "fed":"https://www.federalreserve.gov/feeds/press_monetary.xml",
         "sec":"https://www.sec.gov/news/pressreleases.rss",
         "cftc":"https://www.cftc.gov/RSS/RSSGP/rssgp.xml",
         "cftc-enforcement":"https://www.cftc.gov/RSS/RSSENF/rssenf.xml"}
OFFICIAL_SOURCES=frozenset(("fed","sec","cftc","cftc-enforcement"))
CRYPTO_TERMS=re.compile(r"\b(bitcoin|btc|crypto\w*|cryptocurrenc\w*|digital assets?|blockchain|"
    r"tokeni[sz]\w*|stablecoins?|spot bitcoin|bitcoin etf|etf)\b",re.I)
MEDIA_TERMS=re.compile(r"\b(bitcoin|btc|crypto\w*|cryptocurrenc\w*|digital assets?|blockchain|"
    r"tokeni[sz]\w*|stablecoins?|spot bitcoin|bitcoin etf|etf|inflation|fed|federal reserve|"
    r"regulat\w*|interest rates?|monetary policy)\b",re.I)


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0
    def handle_starttag(self,tag,attrs):
        if tag in ("script","style"):
            self.hidden += 1
    def handle_endtag(self,tag):
        if tag in ("script","style"):
            self.hidden = max(0,self.hidden-1)
    def handle_data(self,data):
        if not self.hidden:
            self.parts.append(data)


def plain(value):
    parser=PlainText(); parser.feed(value)
    return re.sub(r"\s+"," "," ".join(parser.parts)).strip()


def canonical_url(value):
    p=urlsplit(value)
    if p.scheme not in ("https","http") or not p.hostname or p.username:
        raise ValueError("unsafe_article_url")
    query=[(k,v) for k,v in parse_qsl(p.query) if not k.lower().startswith("utm_")]
    return urlunsplit((p.scheme,p.netloc.lower(),p.path.rstrip("/"),urlencode(sorted(query)),""))


def published_time(value):
    try:
        result=parsedate_to_datetime(value)
    except (TypeError,ValueError):
        result=datetime.fromisoformat(value.replace("Z","+00:00"))
    if result.tzinfo is None:
        raise ValueError("undated_article")
    return result.astimezone(timezone.utc)


def parse_feed(content, source, completed_at):
    parsed=feedparser.parse(content, sanitize_html=False)
    if parsed.get("bozo") or not parsed.get("version") or not parsed.get("feed"):
        raise SourceError("invalid_feed")
    result=[]
    for entry in parsed.entries:
        entry = dict(entry)  # Avoid feedparser's deprecated updated→published alias.
        try:
            at=published_time(entry.get("published",entry.get("updated","")))
            if not completed_at-timedelta(days=7 if source in OFFICIAL_SOURCES or source=="binance" else 3) <= at <= completed_at:
                continue
            title=plain(entry.get("title","")); summary=plain(entry.get("summary",""))
            keep_fed=source=="fed"
            keep_media=source in ("coindesk","cointelegraph") and MEDIA_TERMS.search(title+" "+summary)
            keep_official=source in ("sec","cftc","cftc-enforcement") and CRYPTO_TERMS.search(title+" "+summary)
            if not title or not (keep_fed or keep_media or keep_official):
                continue
            url=canonical_url(entry["link"])
            content_hash=digest([title,summary,at])
            result.append(Article(id=digest([source,url,content_hash]),source=source,url=url,title=title,
                summary=summary[:600],content_hash=content_hash,published_at=at,first_seen_at=completed_at,
                fetched_at=completed_at,truncated=len(summary)>600))
        except (ValueError,KeyError,TypeError):
            continue
    return result


def select_articles(articles, as_of):
    candidates=[a for a in articles if a.first_seen_at <= as_of and a.fetched_at <= as_of and
        as_of-timedelta(days=7 if a.source=="fed" else 3) <= a.published_at <= as_of]
    # Latest visible revision per URL; later revisions may not replace earlier text.
    latest={}
    for a in sorted(candidates,key=lambda a:(a.fetched_at,a.id)):
        identity=(a.source,a.id) if a.source=="binance" else a.url
        latest[identity]=a
    seen, unique=set(),[]
    for a in sorted(latest.values(),key=lambda a:(a.published_at,a.id),reverse=True):
        keys=(a.content_hash, a.title.casefold())
        if any(k in seen for k in keys):
            continue
        seen.update(keys); unique.append(a)
    groups={s:[a for a in unique if a.source==s] for s in dict.fromkeys(a.source for a in unique)}
    chosen=[]
    quotas={"fed":5,"sec":3,"cftc":3,"cftc-enforcement":2,"binance":4}
    for source,limit in quotas.items():
        chosen.extend(groups.get(source,[])[:limit])
    chosen_ids={a.id for a in chosen}
    media=("coindesk","cointelegraph")
    while len(chosen)<20 and any(groups.get(s) for s in media):
        for source in media:
            while groups.get(source) and groups[source][0].id in chosen_ids: groups[source].pop(0)
            if groups.get(source) and len(chosen)<20:
                article=groups[source].pop(0); chosen.append(article); chosen_ids.add(article.id)
    # Fill unused capacity from any source while preserving recency within a feed.
    for source,items in groups.items():
        while items and len(chosen)<20:
            article=items.pop(0)
            if article.id not in chosen_ids:
                chosen.append(article); chosen_ids.add(article.id)
    return chosen,{"candidates":len(candidates),"selected":len(chosen),"excluded":len(candidates)-len(chosen)}


class NewsClient:
    def __init__(self,http,*,now=utcnow,sleep=None):
        self.now=now; self.io=PublicHTTP(http,now=now,**({"sleep":sleep} if sleep else {}))
    def collect(self,run_at):
        articles,health=[],{}
        for source,url in FEEDS.items():
            try:
                r=self.io.get(url,kind="xml",deadline=run_at+timedelta(minutes=3))
                completed=self.now(); items=parse_feed(r.content,source,completed)
                articles.extend(items)
                health[source]=SourceHealth(status="ok",last_success_at=completed,last_attempt_at=completed,
                                            reason=None,item_count=len(items))
            except (SourceError,ValueError):
                health[source]=SourceHealth(status="error",last_success_at=None,last_attempt_at=self.now(),
                                            reason="news_fetch_failed",item_count=0)
        return articles,health
