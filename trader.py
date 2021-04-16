import logging
import requests
import time
import binance.client
import upbit
import math

from binance.enums import *
from binance.exceptions import BinanceAPIException, BinanceWithdrawException

from huobi.client.account import AccountClient
from huobi.client.wallet import WalletClient
from huobi.client.trade import TradeClient
from huobi.client.market import MarketClient
from huobi.constant import *
from huobi.utils import *

log = logging.getLogger(__name__)


class Trader:
    def __init__(self, cur_prices, settings):
        self.PREMIUM_RATIO = 1.5
        self.BINANCE_MIN_BALANCE = 400

        self.cur_prices = cur_prices
        self.settings = settings

        self.u_prices = cur_prices["Upbit"]
        self.b_prices = cur_prices["Binance"]
        self.market_list = settings["market_list"]

        self.binance_client = binance.client.Client(settings["binance_access_key"], settings["binance_secret_key"])
        self.upbit_client = upbit.Client(settings["upbit_access_key"], settings["upbit_secret_key"])

        self.huobi_wallet_client = WalletClient(
            api_key=self.settings["huobi_korea_access_key"],
            secret_key=self.settings["huobi_korea_secret_key"],
            url="https://krapi-aws.huobi.pro"
        )
        self.huobi_account_client = AccountClient(
            api_key=self.settings["huobi_korea_access_key"],
            secret_key=self.settings["huobi_korea_secret_key"],
            url="https://krapi-aws.huobi.pro"
        )
        self.huobi_trade_client = TradeClient(
            api_key=self.settings["huobi_korea_access_key"],
            secret_key=self.settings["huobi_korea_secret_key"],
            url="https://krapi-aws.huobi.pro"
        )
        self.huobi_market_client = MarketClient(url="https://krapi-aws.huobi.pro")
        self.huobi_account_id = 20694732

        self.trade_info = {
            "symbol": "",
            "price": 0,
            "Upbit": {"deposit_addr": "", "secondary_addr": None},
            "Binance": {"spot_balance": 0, "futures_balance": 0}
        }

    def monitor(self):
        """
        Check whether their is premium
        """
        if len(self.u_prices) != len(self.b_prices):
            log.error("Length of market list data, upbit data and binance data should be same")
            exit()

        premium_data = {} # upbit-binance
        usdt_price = self.cur_prices["Huobi"]["usdt"]
        for i in range(len(self.market_list)):
            u_price = self.u_prices["KRW-"+self.market_list[i]]
            b_price = self.b_prices[self.market_list[i]+"USDT"]

            if u_price == 0 or b_price == 0:
                return

            if u_price >= b_price * usdt_price:
                premium_data[self.market_list[i]] = round(u_price/(b_price*usdt_price)*100-100, 3)
            else:
                premium_data[self.market_list[i]] = round((b_price*usdt_price)/u_price*100-100, 3)

        # premium_data looks like this {'ADA': 4.2, 'ATOM': 4.033, 'BAT': 3.933}
        log.info(premium_data)
        if max(premium_data.values()) > self.PREMIUM_RATIO:
            #print("hi")
            self.trade_binance_to_upbit(premium_data)
            self.send_btc_upbit_to_huobi()
            self.trade_huobi_to_binance()
            #exit()
        elif min(premium_data.values()) < -self.PREMIUM_RATIO:
            log.info(premium_data)
            self.trade_upbit_to_binance(premium_data)
        else:
            return

        return

    def trade_binance_to_upbit(self, premium_data):
        """
        How to trade
        1. Setup trading pre condition.
        2. Place market order and future short with 9:1 ratio.
        3. Only allow one trade at a time.
        """

        # Get symbol of currency with maximum premium
        self.trade_info["symbol"] = list(premium_data.keys())[list(premium_data.values()).index(max(premium_data.values()))]
        self.trade_info["price"] = self.b_prices[self.trade_info["symbol"]+"USDT"]

        log.info("Trading "+self.trade_info["symbol"])

        self.prepare_binance_to_upbit()

        # Get symbol info and setup filters for new order
        symbol_info = self.binance_client.get_symbol_info(symbol=self.trade_info["symbol"]+"USDT")

        """
        What it does.
        1. Trade 85% of balance and leave 15% for hedging with future short.
            It's 85% because after you sold the coin in Upbit, balance has increased and 10x leverage is no longer 
            possible with the original balance in futures account if the percentage is set to 10%. 
        2. Filter quantity LOT_SIZE according to binance manuel.
            quantity >= minQty
            quantity <= maxQty
            (quantity-minQty) % stepSize == 0
        3. Format float precision according to given baseAssetPrecision. Not sure if this is necessary.
        """

        # Multiply 0.99 because you buy in market price and their is taker fee. Real price and saved price can be different
        step_size = float(list(filter(lambda f: f['filterType'] == 'LOT_SIZE', symbol_info['filters']))[0]['stepSize'])
        quantity = self.trade_info["Binance"]["spot_balance"] * 0.99 / self.trade_info["price"]
        spot_quantity_str = self.float_precision(quantity,  step_size)

        # Real order
        order = self.binance_client.order_market_buy(symbol=self.trade_info["symbol"]+"USDT", quantity=spot_quantity_str)
        log.info("Binance buy executed.")

        # Futures hedge short
        futures_quantity = self.binance_hedge_short(self.trade_info["symbol"], quantity)

        # To be precise get the balance again and don't use spot_quantity_str.
        res = self.binance_client.get_asset_balance(asset=self.trade_info["symbol"])
        trans_quantity_str = self.float_precision(res["free"], step_size)
        log.debug(trans_quantity_str)

        # Balance doesn't get update immediately and prints insufficient balance error. Need to try multiple times.
        while True:
            try:
                if self.trade_info["Upbit"]["secondary_addr"]:
                    res = self.binance_client.withdraw(
                        asset=self.trade_info["symbol"],
                        address=self.trade_info["Upbit"]["deposit_addr"],
                        addressTag=self.trade_info["Upbit"]["secondary_addr"],
                        amount=trans_quantity_str
                    )
                else:
                    print("Trying withdraw")
                    res = self.binance_client.withdraw(
                        asset=self.trade_info["symbol"],
                        address=self.trade_info["Upbit"]["deposit_addr"],
                        amount=trans_quantity_str
                    )

                if "id" in res:
                    break

            except BinanceWithdrawException as e:
                print(e)
                time.sleep(1)
                pass

        log.info("Binance withdrawing to Upbit.")
        txid = self.monitor_trans_binance_to_upbit(res["id"])
        log.info("Binance withdraw complete. Txid : "+txid)

        # Check again in upbit side.
        while True:
            data = self.upbit_client.deposits(self.trade_info["symbol"], txid)
            if data:
                res = data[0]
                if "error" in res:
                    log.error(res["error"]["message"])
                    exit()
                if res["state"] == "ACCEPTED":
                    break
                elif res["state"] == "REJECTED":
                    log.error("Upbit deposit rejected.")
                    exit()
                else:
                    time.sleep(3)
                    continue
            else:
                continue

        upbit_quantity = ""
        res = self.upbit_client.accounts()
        for currency in res:
            if currency["currency"] == self.trade_info["symbol"]:
                upbit_quantity = currency["balance"]

        res = self.upbit_client.order(
            market="KRW-"+self.trade_info["symbol"],
            side="ask",
            volume=upbit_quantity,
            ord_type="market"
        )
        log.info("Upbit sell complete.")
        log.info(premium_data)

        # After upbit side trade was made, cover short hedge
        order = self.binance_client.futures_create_order(
            symbol=self.trade_info["symbol"] + "USDT",
            side=SIDE_BUY,
            type=ORDER_TYPE_MARKET,
            quantity=futures_quantity
        )

        log.info("Binance futures cover complete.")

        return

    def monitor_trans_binance_to_upbit(self, id):
        """
        status : 0(0:Email Sent,1:Cancelled 2:Awaiting Approval 3:Rejected 4:Processing 5:Failure 6:Completed)
        """
        while True:
            res = self.binance_client.get_withdraw_history(asset=self.trade_info["symbol"])
            for t in res["withdrawList"]:
                if t["id"] == id:
                    if t["status"] == 6:
                        log.info("transaction success.")
                        return t["txId"]
                    elif t["status"] == 4 or t["status"] == 2 or t["status"] == 0:
                        time.sleep(3)
                        break
                    else:
                        log.error("transaction failed")
                        exit()

    def trade_upbit_to_binance(self, premium_data):
        print("Trade reverse premium. Currently not supported")

    def prepare_binance_to_upbit(self):
        """
        Things to check before trading starts
        1. Balance. (Over $200)
        2. Check all wallet address that is needed for the trading.
        3. Transfer 15% of spot balance to futures balance.
        """

        # Check binance account status.
        info = self.binance_client.get_account()
        if info["canTrade"] is False or info["canWithdraw"] is False or info["canDeposit"] is False:
            log.error("Can't proceed trading. Check binance account status.")
            exit()

        # Check whether futures account is empty.
        if float(self.binance_client.futures_account_balance()[0]["balance"]) != 0:
            log.error("Can't proceed trading. Futures account needs to be empty.")
            exit()

        # Get binance balance
        self.trade_info["Binance"]["spot_balance"] = float(self.binance_client.get_asset_balance(asset='USDT')["free"])
        log.info("Binance balance : " + str(self.trade_info["Binance"]["spot_balance"]))
        if self.trade_info["Binance"]["spot_balance"] < self.BINANCE_MIN_BALANCE:
            log.error("Check binance account minimum balance.")
            exit()

        # Get Upbit wallet address.
        while True:
            res = self.upbit_client.generate_coin_addr(self.trade_info["symbol"])
            if "success" in res:
                if res["success"]:
                    time.sleep(0.5)
                    continue
                else:
                    log.error("Upbit wallet generation failed for "+self.trade_info["symbol"])
                    exit()
            elif "error" in res:
                log.error(res)
            else:
                if self.trade_info["symbol"] == "BCH":
                    # BCH has prefix bitcoincash:
                    self.trade_info["Upbit"]["deposit_addr"] = res["deposit_address"].split(":")[1]
                else:
                    self.trade_info["Upbit"]["deposit_addr"] = res["deposit_address"]
                self.trade_info["Upbit"]["secondary_addr"] = res["secondary_address"]
                break

        # Transfer balance to futures account.
        self.binance_client.futures_account_transfer(asset="USDT", amount=self.trade_info["Binance"]["spot_balance"] * 0.15, type=1)
        self.trade_info["Binance"]["spot_balance"] *= 0.85

        return

    def send_btc_upbit_to_huobi(self):
        """
        1. Buy BTC and hedge with binance short
        2. Send it to Huobi.
        3. Sell with limit order so you can avoid slippage. Need precise calculation.
        """
        # Get Huobi BTC wallet address.
        list_obj = self.huobi_wallet_client.get_account_deposit_address(currency="btc")

        huobi_btc_addr = ""
        for obj in list_obj:
            if obj.chain == "btc":  # eos1
                huobi_btc_addr = obj.address

        # Buy BTC in Upbit
        upbit_krw_balance = 0
        res = self.upbit_client.accounts()

        if "error" in res:
            log.error(res["error"]["message"])
            exit()

        for currency in res:
            if currency["currency"] == "KRW":
                upbit_krw_balance = currency["balance"]

        log.info("Upbit buying.")
        log.debug("KRW Balance : "+upbit_krw_balance)
        res = self.upbit_client.order(
            market="KRW-BTC",
            side="bid",
            price=math.floor(float(upbit_krw_balance)*0.9995),
            ord_type="price"
        )

        if "error" in res:
            log.error(res["error"]["message"])
            exit()

        trade_uuid = res["uuid"]

        while True:
            res = self.upbit_client.check_order(trade_uuid)
            if res["trades_count"] != 0:
                upbit_avg_price = res["trades"][0]["price"]
                break
            time.sleep(0.1)
        log.info("Upbit bought.")

        # Get Upbit BTC balance.
        btc_balance = 0
        res = self.upbit_client.accounts()
        for currency in res:
            if currency["currency"] == "BTC":
                btc_balance = float(currency["balance"]) - 0.0009 # withdraw fee is 0.0009

        # Futures hedge short
        futures_quantity = self.binance_hedge_short("BTC", btc_balance)

        # Withdraw decimal point differs for each currency.
        log.info("Upbit to Huobi withdraw started.")
        res = self.upbit_client.withdraw(
            currency="BTC",
            amount=self.float_precision(btc_balance, 0.0001),  # eos -> 0.0001
            address=huobi_btc_addr  # eos secondary_address=6837108
        )
        if "error" in res:
            log.error(res["error"]["message"])
            exit()

        withdraw_uuid = res["uuid"]

        while True:
            try:
                res = self.upbit_client.check_withdraw(withdraw_uuid)
                time.sleep(10)
                if res["done_at"]:
                    break
            except Exception as e:
                print(e)
                time.sleep(10)
                pass

        log.info("Upbit to Huobi withdraw done.")

        # Sell BTC at Huobi.
        # Balance is not updated immediately so loop the order function.
        huobi_quantity = float(self.float_precision(btc_balance, 0.0001))  # eos -> 0.01 # btc -> 0.0001
        while True:
            try:
                order_id = self.huobi_trade_client.create_order(
                    symbol="btckrw",
                    account_id=self.huobi_account_id,
                    order_type=OrderType.SELL_MARKET,
                    amount=huobi_quantity,
                    source=OrderSource.API,
                    price=None
                )
                if order_id:
                    break
            except Exception as e:
                time.sleep(4)
                print(e)
                pass
        log.info("Huobi sell order created.")

        # Monitor until order is filled.
        while True:
            order = self.huobi_trade_client.get_order(order_id=order_id)
            if float(order.filled_amount) == huobi_quantity:
                break
            time.sleep(5)
        log.info("Huobi sell order filled.")

        # Cover binance short hedge.
        order = self.binance_client.futures_create_order(
            symbol="BTCUSDT",
            side=SIDE_BUY,
            type=ORDER_TYPE_MARKET,
            quantity=futures_quantity
        )

        log.info("Upbit to Huobi complete.")
        return

    def trade_huobi_to_binance(self):
        """
        1. Get krw balance.
        2. Buy usdt with krw.
        3. Buy eos with usdt.( hedge needed )
        4. Send eos to binance.
        5. Sell eos at binance.

        Here you buy with eos because eosusdt market has enough liquidity.
        """
        # Get krw balance.
        krw_balance = 0
        account_balance_list = self.huobi_account_client.get_account_balance()
        for balance_obj in account_balance_list[0].list:
            if balance_obj.currency == "krw":
                krw_balance = balance_obj.balance
                break

        # Get usdt first ask price and place a limit order their.
        depth = self.huobi_market_client.get_pricedepth("usdtkrw", DepthStep.STEP0, 1)
        usdt_ask_price = depth.asks[0].price

        log.info("Huobi buying usdt.")
        usdt_quantity = float(self.float_precision(float(krw_balance) / usdt_ask_price, 0.01))
        order_id = self.huobi_trade_client.create_order(
            symbol="usdtkrw",
            account_id=self.huobi_account_id,
            order_type=OrderType.BUY_LIMIT,
            amount=usdt_quantity,
            source=OrderSource.API,
            price=int(usdt_ask_price)
        )

        # Monitor until order is filled.
        while True:
            order = self.huobi_trade_client.get_order(order_id=order_id)
            if float(order.filled_amount) == usdt_quantity:
                break
            time.sleep(5)

        log.info("Huobi bought usdt.")
        usdt_quantity *= 0.999  # adjust market fee 0.1%

        # Order with binance price.
        log.info("Huobi buying EOS.")
        eos_quantity = float(self.float_precision(usdt_quantity / self.b_prices["EOSUSDT"], 0.0001))
        order_id = self.huobi_trade_client.create_order(
            symbol="eosusdt",
            account_id=self.huobi_account_id,
            order_type=OrderType.BUY_LIMIT,
            amount=eos_quantity,
            source=OrderSource.API,
            price=self.b_prices["EOSUSDT"]
        )

        # Monitor until order is filled.
        while True:
            order = self.huobi_trade_client.get_order(order_id=order_id)
            if float(order.filled_amount) == eos_quantity:
                break
            time.sleep(5)

        log.info("Huobi EOS bought.")
        eos_quantity *= 0.999 # Adjust market fee.

        # Futures hedge short.
        futures_quantity = self.binance_hedge_short("EOS", eos_quantity)

        # Get binance deposit wallet address.
        deposit_addr_data = self.binance_client.get_deposit_address(asset="EOS")

        eos_quantity = float(self.float_precision(eos_quantity - 0.1, 0.0001))  # Adjust withdraw fee(0.1) and precision

        log.info("Huobi withdrawing EOS to Binance.")
        log.debug("EOS quantity : "+str(eos_quantity))
        # Balance doesn't get updated immediately.
        while True:
            try:
                if deposit_addr_data["addressTag"]:
                    withdraw_id = self.huobi_wallet_client.post_create_withdraw(
                        address=deposit_addr_data["address"],
                        address_tag=deposit_addr_data["addressTag"],
                        amount=eos_quantity,
                        currency="eos",
                        fee=0.1,
                    )
                else:  # Actually you don't need this if else condition because you specify the currency.
                    withdraw_id = self.huobi_wallet_client.post_create_withdraw(
                        address=deposit_addr_data["address"],
                        amount=eos_quantity,
                        currency="eos",
                        fee=0.1,
                    )
                if withdraw_id:
                    break
            except Exception as e:
                log.error(e)
                time.sleep(1)
                pass

        while True:
            time.sleep(5)
            list_obj = self.huobi_wallet_client.get_deposit_withdraw(
                op_type=DepositWithdraw.WITHDRAW,
                currency="eos",
                size=1,
                direct=QueryDirection.NEXT
            )
            if list_obj[0].state == "confirmed":
                break
        log.info("Huobi withdraw complete.")
        log.info(eos_quantity)

        symbol_info = self.binance_client.get_symbol_info(symbol="EOSUSDT")
        step_size = float(list(filter(lambda f: f['filterType'] == 'LOT_SIZE', symbol_info['filters']))[0]['stepSize'])

        eos_quantity = float(self.binance_client.get_asset_balance(asset="EOS")["free"])
        eos_quantity = self.float_precision(eos_quantity,  step_size)

        log.info(eos_quantity)

        order = self.binance_client.order_market_sell(symbol="EOSUSDT", quantity=eos_quantity)

        log.info("Binance sell executed")

        order = self.binance_client.futures_create_order(
            symbol="EOSUSDT",
            side=SIDE_BUY,
            type=ORDER_TYPE_MARKET,
            quantity=futures_quantity
        )

        futures_balance = self.binance_client.futures_account_balance()[1]
        if futures_balance["balance"] != futures_balance["withdrawAvailable"]:
            log.error("Maybe their is futures position left in binance.")
            exit()

        self.binance_client.futures_account_transfer(
            asset="USDT",
            amount=futures_balance["balance"],
            type=2
        )

        log.info("Huobi to Binance done")
        return

    def binance_hedge_short(self, symbol, quantity):
        # Binance BTC futures short
        self.binance_client.futures_change_leverage(symbol=symbol+"USDT", leverage=10)

        # Adjust precision. It's different from spot market.
        res = self.binance_client.futures_recent_trades(symbol=symbol+"USDT", limit=1)
        if "." in res[0]["qty"]:
            precision = len(res[0]["qty"].split(".")[1])
        else:
            precision = 0
        futures_quantity = round(quantity, precision)

        order = self.binance_client.futures_create_order(
            symbol=symbol+"USDT",
            side=SIDE_SELL,
            type=ORDER_TYPE_MARKET,
            quantity=futures_quantity
        )

        log.info("Futures short executed")

        return futures_quantity

    def float_precision(self, f, n):
        n = int(math.log10(1 / float(n)))
        f = math.floor(float(f) * 10 ** n) / 10 ** n
        f = "{:0.0{}f}".format(float(f), n)
        return str(int(f)) if int(n) == 0 else f
