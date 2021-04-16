import threading
import time
import json
import logging
import requests

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.DEBUG
    # filename='debug.log'
)

from binance.exceptions import BinanceAPIException, BinanceWithdrawException
from ws import BinanceWS, UpbitWS, HuobiWS
from trader import Trader

log = logging.getLogger(__name__)


def get_settings():
    # IOTA removed due to wallet condition
    setting_file = "./settings.json"

    try:
        with open(setting_file, "r") as f:
            setting_data = json.load(f)
    except FileNotFoundError:
        print("settings.json not found")
        exit()

    return setting_data


def get_usd_krw():
    url = 'https://krapi-aws.huobi.pro/market/trade?symbol=usdtkrw'
    data = requests.get(url).json()

    if data["status"] != "ok":
        log.error("Failed to get usdt price from Huobi")

    return data["tick"]["data"][0]["price"]


def init_prices(cur_prices, settings):
    market_list = settings["market_list"]
    for i in range(len(market_list)):
        cur_prices["Upbit"]["KRW-"+market_list[i]] = 0
        cur_prices["Binance"][market_list[i]+"USDT"] = 0
    cur_prices["Huobi"]["usdt"] = get_usd_krw()


if __name__ == '__main__':
    settings = get_settings()

    cur_prices = {
        "Binance": {},
        "Upbit": {},
        "Huobi": {}
    }

    init_prices(cur_prices, settings)

    try:
        binance_ws = BinanceWS(
            exchange="Binance",
            cur_prices=cur_prices,
            settings=settings
        )

        upbit_ws = UpbitWS(
            exchange="Upbit",
            cur_prices=cur_prices,
            settings=settings
        )

        huobi_ws = HuobiWS(
            cur_prices=cur_prices,
        )

        trader = Trader(
            cur_prices=cur_prices,
            settings=settings
        )

        huobi_ws.start()
        binance_ws.start()
        upbit_ws.start()

        while True:
            time.sleep(1)
            trader.monitor()

    except BinanceAPIException as e:
        print(e)
    except BinanceWithdrawException as e:
        print(e)
    except Exception as e:
        print(e)


