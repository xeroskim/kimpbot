from binance.client import Client
import requests
from binance.enums import *
import time
import math

#from upbit import Client

u = Client("3LHfYMvhWTeHtS6RIxbslS7NbB7MjHxvXQd284zx", "tphvhKdys3PIqiNdUkbMJ7EIMxVIyLOxgc2vL1Jz")
b = Client("MVvSNcIuXnaiS5jtZPEEDZDuN7carNL8HKCXSXeXFukEJfjVX2p0jPaAMyGvRx64", "BREixpAfIsuxtiHq3kW2gg4vXx79tIJJUV1ehuoEcAGRZ6O73rgDdH7UKthALCgJ")
symbol_info = b.get_symbol_info(symbol="ADAUSDT")
step_size = float(list(filter(lambda f: f['filterType'] == 'LOT_SIZE', symbol_info['filters']))[0]['stepSize'])
res = b.futures_account_balance()[1]
print(res)
