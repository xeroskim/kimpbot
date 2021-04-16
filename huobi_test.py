import time
import math

from huobi.client.wallet import WalletClient

from huobi.client.account import AccountClient
from huobi.client.trade import TradeClient
from huobi.client.market import MarketClient
from huobi.constant import *
from huobi.utils import *

url = "https://krapi-aws.huobi.pro"
g_api_key = "15ac2130-bg5t6ygr6y-7e02a886-3c197"
g_secret_key = "ccac859a-49b82348-d40fa073-29288"

wallet_client = WalletClient(api_key=g_api_key, secret_key=g_secret_key, url=url)
list_obj = wallet_client.get_deposit_withdraw(op_type=DepositWithdraw.DEPOSIT, currency=None, from_id=1, size=10, direct=QueryDirection.PREV)


def float_precision(f, n):
    n = int(math.log10(1 / float(n)))
    f = math.floor(float(f) * 10 ** n) / 10 ** n
    f = "{:0.0{}f}".format(float(f), n)
    return str(int(f)) if int(n) == 0 else f


while True:
    time.sleep(2)
    list_obj = wallet_client.get_deposit_withdraw(
        op_type=DepositWithdraw.WITHDRAW,
        currency="eos",
        size=1,
        direct=QueryDirection.NEXT
    )
    LogInfo.output_list(list_obj)
    #print(list_obj[-1].state)
    if list_obj[0].state == "confirmed":
        break
"""
trade_client = TradeClient(
    api_key=g_api_key,
    secret_key=g_secret_key,
    url="https://krapi-aws.huobi.pro"
)

account_id = 20694732  # You can get this with AccountClient
order_id = trade_client.create_order(
    symbol="eoskrw",
    account_id=account_id,
    order_type=OrderType.SELL_LIMIT,
    amount=12.54,
    source=OrderSource.API,
    price=4480
)
print(order_id)

# Monitor until order is filled.
while True:
    order = trade_client.get_order(order_id=order_id)
    print(order.filled_amount)
    print(type(order.filled_amount))
    order.print_object()
    if float(order.filled_amount) == 12.55:
        break
    time.sleep(5)
"""
print("done")