#!/usr/bin/python3

import kraken

import datetime
import calendar
import time
import json



req_data = {
    'type': 'all',
    'trades': 'true',
    'start': datetime.datetime(2017, 8, 1).timestamp(),
    'end': datetime.datetime(2017, 8, 14).timestamp(),
    'ofs': 1
}


api = kraken.API()
api.loadkeys("keys.json")

#res = api.query_private('TradesHistory', req_data)

res = api.query_public('OHLC', { 'pair': 'ETHEUR', 'since': '1500197742', 'interval': '15' })
print(res)


