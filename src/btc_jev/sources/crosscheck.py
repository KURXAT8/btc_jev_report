from btc_jev.sources.common import PublicHTTP,SourceError
from btc_jev.sources.market import millis,finite_decimal
from btc_jev.util import utcnow


class CrosscheckClient:
    def __init__(self,http,*,now=utcnow,sleep=None):
        self.io=PublicHTTP(http,now=now,**({"sleep":sleep} if sleep else {}))

    def price_at(self,boundary,*,deadline=None):
        try:
            payload=self.io.get("https://www.okx.com/api/v5/market/history-candles",
                {"instId":"BTC-USDT","bar":"1m","after":millis(boundary),"before":millis(boundary)-60001,"limit":2},
                deadline=deadline).json()
            if payload.get("code")!="0":
                return None
            rows=[r for r in payload["data"] if int(r[0])==millis(boundary)-60000 and r[8]=="1"]
            return finite_decimal(rows[0][4],positive=True) if len(rows)==1 else None
        except (ValueError,KeyError,TypeError,IndexError,ArithmeticError,SourceError):
            return None
