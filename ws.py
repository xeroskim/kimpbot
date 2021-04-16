import websocket
import threading
import requests
import logging
import gzip

from huobi.client.market import MarketClient
from json import loads, dumps
from datetime import datetime

log = logging.getLogger(__name__)


class Client(threading.Thread):
    def __init__(self, url, exchange):
        super().__init__()

        self.exchange = exchange

        self.ws = websocket.WebSocketApp(
            url=url,
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )

    def run(self):
        self.ws.run_forever()

    def on_open(self, ws):
        print(f'Connected to {self.exchange}\n')

    def on_message(self, ws, message):
        pass

    def on_error(self, ws, error):
        log.error(error)

    def on_close(self, ws):
        print("### closed ###")


class UpbitWS(Client):
    def __init__(self, exchange, cur_prices, settings):
        url = "wss://api.upbit.com/websocket/v1"
        super().__init__(url, exchange)

        self.settings = settings
        self.cur_price = cur_prices[exchange]

    def on_open(self, ws):
        super().on_open(ws)

        codes = ["KRW-" + m for m in self.settings["market_list"]]

        params = dumps([{"ticket": "test"}, {"type": "ticker", "codes": codes}])
        self.ws.send(params)

    def on_message(self, ws, message):
        data = loads(message)

        # ex) code = KRW-ADA
        self.cur_price[data["code"]] = data["trade_price"]


class BinanceWS(Client):
    def __init__(self, exchange, cur_prices, settings):
        url = "wss://stream.binance.com:9443/ws/"

        streams = [market.lower()+"usdt@aggTrade" for market in settings["market_list"]]
        url = url + '/'.join(streams)

        super().__init__(url, exchange)

        self.cur_price = cur_prices[exchange]

    def on_open(self, ws):
        super().on_open(ws)

    def on_message(self, ws, message):
        data = loads(message)

        # s is symbol ex) BTCUSDT, p is current price
        self.cur_price[data["s"]] = float(data["p"])

"""
class HuobiWS(threading.Thread):
    def __init__(self, cur_prices):
        super().__init__()

    def on_message(self, trade_event: 'TradeDetailEvent'):
        print("---- trade_event:  ----")
        trade_event.print_object()

    def on_error(self):
        print("error")

    market_client = MarketClient()
    market_client.sub_trade_detail("usdtkrw", on_message, on_error)
"""


class HuobiWS(Client):
    def __init__(self, cur_prices):
        self.host = "krapi-aws.huobi.pro"
        url = "wss://"+self.host+"/ws" # if the host changes pre_sign host should be changed too
        super().__init__(url, "Huobi")

        self.cur_price = cur_prices["Huobi"]

    def on_open(self, ws):
        super().on_open(ws)

        data = {"sub": "market.usdtkrw.trade.detail"}
        self.ws.send(dumps(data))

    def on_message(self, ws, message):
        data = loads(gzip.decompress(message))

        if "ping" in data:
            params = {"pong": data['ping']}
            self.ws.send(dumps(params))
        elif "tick" in data:
            self.cur_price["usdt"] = data["tick"]["data"][0]["price"]
        else:
            pass
