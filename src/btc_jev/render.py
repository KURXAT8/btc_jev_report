"""One content tree drives both MIME alternatives. External text is escaped."""
from pathlib import Path
from datetime import timedelta
from urllib.parse import urlsplit
from jinja2 import Environment,FileSystemLoader,StrictUndefined,select_autoescape
from btc_jev.domain import RenderedMail,Snapshot
from btc_jev.forecast import direction_clear
from btc_jev.indicators import compute_features
from btc_jev.util import TAIPEI,dt

LABELS={'bullish':'偏多','sideways':'盤整','bearish':'偏空','complete':'完整','degraded':'部分缺漏',
    'unavailable':'不足／衝突','up':'上行排列','down':'下行排列','mixed':'混合／無明確排列',
    'unknown':'未知','low':'低','medium':'中','high':'高','positive':'利多','negative':'利空','neutral':'中性',
    'low_volatility':'低波動','trend_up':'上行趨勢','trend_down':'下行趨勢','high_volatility':'高波動',
    'unclear':'方向不明確','clear':'方向明確','reported':'已發生事件的報導','opinion':'預測或評論',
    'unverified':'未經證實的說法','insufficient':'資訊不足','macro':'總經／政策','regulation':'監管',
    'market':'市場','other':'其他','always_sideways':'永遠盤整','uniform':'均勻機率',
    'historical':'歷史類別比例','momentum':'簡單動能','week':'前一完整週','four_weeks':'最近四個完整週',
    'cumulative':'保留期間累積','ready':'可用','uniform_fallback':'暖機：均勻機率替代',
    'jev_separate_horizons':'Jev 分開期限影子',
    'coindesk':'CoinDesk','cointelegraph':'Cointelegraph','fed':'Federal Reserve','sec':'SEC',
    'cftc':'CFTC','cftc-enforcement':'CFTC enforcement','binance':'Binance','cpi':'BLS CPI','fomc':'FOMC'}


def label(value): return LABELS.get(value,value)

def number(value): return '缺失' if value is None else f'{float(value):,.4f}'

def percent(value): return '尚無可評估資料' if value is None else f'{value*100:.1f}%'

def local(value): return dt(value).astimezone(TAIPEI).isoformat() if value else '缺失'

def safe_url(value):
    try:
        p=urlsplit(value)
        return value if p.scheme in ('http','https') and p.netloc and not p.username else None
    except ValueError: return None


def render_report(day,predictions,details,weekly,failure):
    sections=[]
    def section(title,lines): sections.append(dict(title=title,lines=lines))
    predictions=sorted(predictions,key=lambda p:p.horizon_days)
    calibrations=details.get('calibrations',{})
    def applied(p):
        calibration=calibrations.get(p.id)
        if calibration and calibration['status']=='active':
            return p.distribution.model_copy(update={'probabilities':calibration['probabilities']})
        return p.distribution
    summary='｜'.join(f'{p.horizon_days}日：{label(p.distribution.choice) if direction_clear(applied(p)) else "方向不明確"}' for p in predictions)
    subject=f'[BTC 波段日報] {day}｜'+('分析未完成' if failure or not predictions else summary)
    if weekly: subject+='｜Jev Weekly Report'
    snapshot=details.get('snapshot')
    if isinstance(snapshot,dict): snapshot=Snapshot(**snapshot)
    if details.get('synthetic'): section('合成資料預覽',['此為離線測試資料，不是真實行情或有效預測。'])
    if failure: section('分析未完成',[failure])
    models='、'.join(sorted({p.model for p in predictions})) or details.get('model','未產生預測')
    if snapshot:
        section('資料與品質',[
            f'基準價：{snapshot.reference_price:,.2f} USDT；資料品質：{label(snapshot.quality.status)}',
            f'模型：{models}'])
    elif details.get('model') or predictions:
        section('資料與品質',[f'模型：{models}'])
    for p in predictions:
        calibration=calibrations.get(p.id)
        active=calibration and calibration['status']=='active'
        lines=[f'判斷：{label(p.distribution.choice) if direction_clear(applied(p)) else "方向不明確"}',
            ('Jev 原始機率：' if active else '')+'／'.join(f'{label(k)} {percent(v)}' for k,v in p.distribution.probabilities.items())]
        if active:
            lines.append('本機校準機率：'+'／'.join(f'{label(k)} {percent(v)}' for k,v in calibration['probabilities'].items()))
        lines.append(('Jev 原始確定程度：' if active else '模型確定程度：')+percent(p.distribution.confidence))
        section(f'{p.horizon_days} 天趨勢',lines)
    if snapshot:
        f=compute_features(snapshot); context=snapshot.context
        def concise(value): return '缺失' if value is None else f'{float(value):,.2f}'
        section('市場摘要',[
            f'近 24 小時漲跌：{concise(f["hourly_return_24h_pct"])}%；RSI（14 日）：{concise(f["rsi14"])}',
            f'日成交量／20 日均量：{concise(f["daily_volume_ratio"])} 倍',
            f'趨勢：{label(context.trend)}；波動：{label(context.volatility)}',
            f'市場 regime：{label(getattr(context, "regime", "unknown"))}'])
        from zoneinfo import ZoneInfo
        from btc_jev.sources.calendars import event_in_window
        events=sorted((e for e in snapshot.events if event_in_window(e,snapshot.as_of,7)),key=lambda e:e.scheduled_start)
        lines=[]
        for event in events:
            if event.time_precision=='exact':
                when=event.scheduled_start.astimezone(TAIPEI).strftime('%m/%d %H:%M（台北）')
            else:
                zone=ZoneInfo(event.source_timezone)
                start=event.scheduled_start.astimezone(zone).date()
                end=(event.scheduled_end-timedelta(microseconds=1)).astimezone(zone).date()
                when=f'{start:%m/%d}'+(f'～{end:%m/%d}' if start!=end else '')+'（當地日期）'
            lines.append(f'{event.title}：{when}')
        section('近期事件',lines or ['未有已取得的近期事件安排。'])
        classifications=details.get('news_answers',{})
        eligible=[a for a in snapshot.articles if a.id not in classifications or classifications[a.id].get('relevance',{}).get('noul',0)>=.5]
        news=[]
        for a in eligible[:3]:
            url=safe_url(a.url)
            news.append(f'{a.title}｜{label(a.source)}'+(f'；{url}' if url else ''))
        section('重點新聞',news or ['無近期新聞。'])
    if weekly:
        section('Jev Weekly Report',[f'統計週別：{weekly.week_start.astimezone(TAIPEI):%m/%d}～{(weekly.week_end-timedelta(days=1)).astimezone(TAIPEI):%m/%d}'])
        for window in ('week','four_weeks','cumulative'):
            groups=[g for g in weekly.groups if g['window']==window]
            lines=[]
            for g in groups:
                # Preserve separate cohorts when historical versions coexist.
                peers=[other for other in groups if other['horizon']==g['horizon']]
                version=f'（版本 {peers.index(g)+1}）' if len(peers)>1 else ''
                lines.append(f'{g["horizon"]} 天{version}：命中率 {percent(g["accuracy"])}；樣本 {g["settled_count"]}'+('；小樣本' if g['small_sample'] else ''))
                shadow=g['baselines'].get('jev_separate_horizons',{})
                n=shadow.get('common_count',0)
                if n:
                    lines.append(f'Jev 分開期限影子：命中率 {percent(shadow["accuracy"])}、Brier {number(shadow["brier"])}、校準誤差 {percent(shadow["calibration_error"])}；'
                        f'同樣本 Jev：命中率 {percent(shadow["jev_accuracy"])}、Brier {number(shadow["jev_brier"])}、校準誤差 {percent(shadow["jev_calibration_error"])}；樣本 {n}'+('；小樣本' if n<30 else ''))
                else:
                    lines.append('Jev 分開期限影子：等待預測到期後評估。')
            section(label(window),lines)
        if weekly.corrections:
            section('歷史補正',[f'新增 {len(weekly.corrections)} 筆已結算或修正結果，已納入本次統計。'])
    section('說明',['盤整區間：1 天 ±1%、3 天 ±2%、7 天 ±4%。模型機率與確定程度不等於實際命中率。'])
    env=Environment(loader=FileSystemLoader(Path(__file__).parent/'templates'),autoescape=select_autoescape(['html','xml','j2']),undefined=StrictUndefined)
    text=env.get_template('report.txt.j2').render(sections=sections,subject=subject)
    html=env.get_template('report.html.j2').render(sections=sections,subject=subject)
    return RenderedMail(subject=subject,text=text,html=html)
