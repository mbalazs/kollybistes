#!/usr/bin/python3

import kraken
import datastore

from ohlc import OHLC

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql.expression import *
from contextlib import contextmanager

import datetime
import calendar
import time
import json
import math
import array



class TradeHistorySynchronizer(object):

  def __init__(self, tickers, interval=5, api_key=None, api_secret=None):
    self.tickers = tickers
    self.interval = interval
    self.api = kraken.API()
    self.api.loadkeys("keys.json")

  def sync(self, session):
    for pair in self.tickers:
      self.sync_pair(pair, session)

  def sync_pair(self, pair, session):
    result = self._get_ohlc(pair)
    if 'error' in result and result['error'] != []:
      raise Exception(result['error'])
    last = result['result']['last']
    for k,v in result['result'].items():
      if k == 'last' or k == 'errors':
        continue

      new_ohlcs = self._get_models(k, v, last, session)

      last_dt = datetime.datetime.fromtimestamp(last)
      first_dt = last_dt
      for ohlc in new_ohlcs:
        if ohlc.timestamp < first_dt:
          first_dt = ohlc.timestamp
        
      # retrieve all OHLCs from the database where timestamp > first and < last
      db_ohlcs = session.query(OHLC).\
                 filter( and_( OHLC.pair == pair, 
                               OHLC.timestamp >= first_dt,
                               OHLC.timestamp <= last_dt ))

      new_records = 0
      existing_records = 0
      updated_records = 0
      
      for new_ohlc in new_ohlcs:
        existing_record = False
        for db_ohlc in db_ohlcs:
          if db_ohlc.timestamp == new_ohlc.timestamp:
            existing_records += 1
            existing_record = True
            if db_ohlc != new_ohlc:
              updated_records += 1
              db_ohlc.open = new_ohlc.open
              db_ohlc.high = new_ohlc.high
              db_ohlc.low = new_ohlc.low
              db_ohlc.close = new_ohlc.close
              db_ohlc.vwap = new_ohlc.vwap
              db_ohlc.volume = new_ohlc.volume
              db_ohlc.count = new_ohlc.count

        if not existing_record:
          new_records += 1
          session.add(new_ohlc)

      session.commit()
      print("%s New records: %d, existing records: %d, updated records: %d" % (pair, new_records, existing_records, updated_records))

  def _get_models(self, ticker, data, last, session):
    new_ohlcs = []

    for d in data:
      ohlc = OHLC(timestamp = datetime.datetime.fromtimestamp(d[0]),
                  open = d[1],
                  high = d[2],
                  low = d[3],
                  close = d[4],
                  vwap = d[5],
                  volume = d[6],
                  count = d[7],
                  pair = ticker)
      new_ohlcs.append(ohlc)

    return new_ohlcs

  def _get_ohlc(self, pair):
    request = {
      'pair': pair,
      'interval': self.interval
    }
    result = self.api.query_public('OHLC', request)
    return result

class TradeHistory(object):
  """ Generates historic OHLC data from database, averaged to the given interval in minutes

  E.g., to generate daily OHLC intervals, set interval to 1440 minutes.
  The timestamp of each OHLC entry will be set to the beginning of the interval

  """

  def __init__(self, session, pair, interval=5, since = None):
    self.pair = pair
    self.interval = interval
    self.session = session
    if since is None:
      since = datetime.datetime.now() - datetime.timedelta(days = 30)
    self.since = since


  def ohlc(self):
    ohlcs = self._load_ohlcs()
    groups = self._group_db_ohlcs(ohlcs)
    return groups[::-1]

  def _load_ohlcs(self):
    db_ohlcs = session.query(OHLC).\
               filter( and_( OHLC.pair == self.pair, 
                             OHLC.timestamp >= self.since ))
    return db_ohlcs

  def _group_db_ohlcs(self, ohlcs):
    groups = []
    for ohlc in ohlcs:
      group_timestamp = datetime.datetime.fromtimestamp(int(int(ohlc.timestamp.timestamp()) / int(self.interval * 60)) * (int(self.interval * 60)))
      group_ohlc = OHLC(timestamp = group_timestamp,
                  open = ohlc.open,
                  high = ohlc.high,
                  low = ohlc.low,
                  close = ohlc.close,
                  vwap = ohlc.vwap,
                  volume = ohlc.volume,
                  count = ohlc.count,
                  pair = ohlc.pair)
      groups.append(group_ohlc)

    if len(groups) == 0:
      return []

    res = []
    current_timestamp = groups[0].timestamp
    curr = groups[0]
    counter = 1
    for g in groups:
      if g.timestamp == current_timestamp:
        curr.close = g.close
        if(g.high > curr.high):
          curr.high = g.high
        if(g.low < curr.low):
          curr.low = g.low
        curr.vwap = ((g.vwap * g.volume) + (curr.vwap * curr.volume)) / (g.volume + curr.volume)
        curr.volume += g.volume
        curr.count += g.count
      else:
        # push the current element and start a new group entry
        res.append(curr)
        current_timestamp = g.timestamp
        curr = OHLC(timestamp = g.timestamp,
            open = g.open,
            high = g.high,
            low = g.low,
            close = g.close,
            vwap = g.vwap,
            volume = g.volume,
            count = g.count,
            pair = g.pair)
        counter += 1
    return res


class ANNStrategy(object):

  def __init__(self, ohlc):
    self.ohlc = ohlc
    self.buying = False
    self.threshold = 0.0014

  def orders(self):
    print(len(self.ohlc))
    for i in range(len(self.ohlc)-2):
      self._tick(i)
      print("%d. %s: %r" % (i, self.ohlc[i].timestamp.strftime("%c"), self.buying))

  def _get_diff(self,i):
    last = self.ohlc[i+1].vwap
    current = self.ohlc[i].vwap
    delta = current - last
    percentage = delta / last
    return percentage

  def _act_linear(self, v):
    return float(v)

  def _act_tanh(self, v):
    v = float(v)
    return (math.exp(v) - math.exp(-1*v)) / (math.exp(v) + math.exp(-1*v))

  def _tick(self, i):
    l0 = [0] * 15
    l1 = [0] * 30 
    l2 = [0] * 9

    l0[0] = self._act_linear(self._get_diff(i))
    l0[1] = self._act_linear(self._get_diff(i))
    l0[4] = self._act_linear(self._get_diff(i))
    l0[5] = self._act_linear(self._get_diff(i))
    l0[6] = self._act_linear(self._get_diff(i))
    l0[7] = self._act_linear(self._get_diff(i))
    l0[8] = self._act_linear(self._get_diff(i))
    l0[9] = self._act_linear(self._get_diff(i))
    l0[10] = self._act_linear(self._get_diff(i))
    l0[11] = self._act_linear(self._get_diff(i))
    l0[12] = self._act_linear(self._get_diff(i))
    l0[13] = self._act_linear(self._get_diff(i))
    l0[14] = self._act_linear(self._get_diff(i))

    l1[0] = self._act_tanh(l0[0]*5.040340774 + l0[1]*-1.3025994088 + l0[2]*19.4225543981 + l0[3]*1.1796960423 + l0[4]*2.4299395823 + l0[5]*3.159003445 + l0[6]*4.6844527551 + l0[7]*-6.1079267196 + l0[8]*-2.4952869198 + l0[9]*-4.0966081154 + l0[10]*-2.2432843111 + l0[11]*-0.6105764807 + l0[12]*-0.0775684605 + l0[13]*-0.7984753138 + l0[14]*3.4495907342)
    l1[1] = self._act_tanh(l0[0]*5.9559031982 + l0[1]*-3.1781960056 + l0[2]*-1.6337491061 + l0[3]*-4.3623166512 + l0[4]*0.9061990402 + l0[5]*-0.731285093 + l0[6]*-6.2500232251 + l0[7]*0.1356087758 + l0[8]*-0.8570572885 + l0[9]*-4.0161353298 + l0[10]*1.5095552083 + l0[11]*1.324789197 + l0[12]*-0.1011973878 + l0[13]*-2.3642090162 + l0[14]*-0.7160862442)
    l1[2] = self._act_tanh(l0[0]*4.4350881378 + l0[1]*-2.8956461034 + l0[2]*1.4199762607 + l0[3]*-0.6436844261 + l0[4]*1.1124274281 + l0[5]*-4.0976954985 + l0[6]*2.9317456342 + l0[7]*0.0798318393 + l0[8]*-5.5718144311 + l0[9]*-0.6623352208 +l0[10]*3.2405203222 + l0[11]*-10.6253384513 + l0[12]*4.7132919253 + l0[13]*-5.7378151597 + l0[14]*0.3164836695)
    l1[3] = self._act_tanh(l0[0]*-6.1194605467 + l0[1]*7.7935605604 + l0[2]*-0.7587522153 + l0[3]*9.8382495905 + l0[4]*0.3274314734 + l0[5]*1.8424796541 + l0[6]*-1.2256355427 + l0[7]*-1.5968600758 + l0[8]*1.9937700922 + l0[9]*5.0417809111 + l0[10]*-1.9369944654 + l0[11]*6.1013201778 + l0[12]*1.5832910747 + l0[13]*-2.148403244 + l0[14]*1.5449437366)
    l1[4] = self._act_tanh(l0[0]*3.5700040028 + l0[1]*-4.4755892733 + l0[2]*0.1526702072 + l0[3]*-0.3553664401 + l0[4]*-2.3777962662 + l0[5]*-1.8098849587 + l0[6]*-3.5198449134 + l0[7]*-0.4369370497 + l0[8]*2.3350169623 + l0[9]*1.9328960346 + l0[10]*1.1824141812 + l0[11]*3.0565148049 + l0[12]*-9.3253401534 + l0[13]*1.6778555498 + l0[14]*-3.045794332)
    l1[5] = self._act_tanh(l0[0]*3.6784907623 + l0[1]*1.1623683715 + l0[2]*7.1366362145 + l0[3]*-5.6756546585 + l0[4]*12.7019884334 + l0[5]*-1.2347823331 + l0[6]*2.3656619827 + l0[7]*-8.7191778213 + l0[8]*-13.8089238753 + l0[9]*5.4335943836 + l0[10]*-8.1441181338 + l0[11]*-10.5688113287 + l0[12]*6.3964140758 + l0[13]*-8.9714236223 + l0[14]*-34.0255456929)
    l1[6] = self._act_tanh(l0[0]*-0.4344517548 + l0[1]*-3.8262167437 + l0[2]*-0.2051098003 + l0[3]*0.6844201221 + l0[4]*1.1615893422 + l0[5]*-0.404465314 + l0[6]*-0.1465747632 + l0[7]*-0.006282458 + l0[8]*0.1585655487 + l0[9]*1.1994484991 + l0[10]*-0.9879081404 + l0[11]*-0.3564970612 + l0[12]*1.5814717823 + l0[13]*-0.9614804676 + l0[14]*0.9204822346)
    l1[7] = self._act_tanh(l0[0]*-4.2700957175 + l0[1]*9.4328591157 + l0[2]*-4.3045548 + l0[3]*5.0616868842 + l0[4]*3.3388781058 + l0[5]*-2.1885073225 + l0[6]*-6.506301518 + l0[7]*3.8429000108 + l0[8]*-1.6872237349 + l0[9]*2.4107095799 + l0[10]*-3.0873985314 + l0[11]*-2.8358325447 + l0[12]*2.4044366491 + l0[13]*0.636779082 + l0[14]*-13.2173215035)
    l1[8] = self._act_tanh(l0[0]*-8.3224697492 + l0[1]*-9.4825530183 + l0[2]*3.5294389835 + l0[3]*0.1538618049 + l0[4]*-13.5388631898 + l0[5]*-0.1187936017 + l0[6]*-8.4582741139 + l0[7]*5.1566299292 + l0[8]*10.345519938 + l0[9]*2.9211759333 + l0[10]*-5.0471804233 + l0[11]*4.9255989983 + l0[12]*-9.9626142544 + l0[13]*23.0043143258 + l0[14]*20.9391809343)
    l1[9] = self._act_tanh(l0[0]*-0.9120518654 + l0[1]*0.4991807488 + l0[2]*-1.877244586 + l0[3]*3.1416466525 + l0[4]*1.063709676 + l0[5]*0.5210126835 + l0[6]*-4.9755780108 + l0[7]*2.0336532347 + l0[8]*-1.1793121093 + l0[9]*-0.730664855 + l0[10]*-2.3515987428 + l0[11]*-0.1916546514 + l0[12]*-2.2530340504 + l0[13]*-0.2331829119 + l0[14]*0.7216218149)
    l1[10] = self._act_tanh(l0[0]*-5.2139618683 + l0[1]*1.0663790028 + l0[2]*1.8340834959 + l0[3]*1.6248173447 + l0[4]*-0.7663740145 + l0[5]*0.1062788171 + l0[6]*2.5288021501 + l0[7]*-3.4066549066 + l0[8]*-4.9497988755 + l0[9]*-2.3060668143 + l0[10]*-1.3962486274 + l0[11]*0.6185583427 + l0[12]*0.2625299576 + l0[13]*2.0270246444 + l0[14]*0.6372015811)
    l1[11] = self._act_tanh(l0[0]*0.2020072665 + l0[1]*0.3885852709 + l0[2]*-0.1830248843 + l0[3]*-1.2408598444 + l0[4]*-0.6365798088 + l0[5]*1.8736534268 + l0[6]*0.656206442 + l0[7]*-0.2987482678 + l0[8]*-0.2017485963 + l0[9]*-1.0604095303 + l0[10]*0.239793356 + l0[11]*-0.3614172938 + l0[12]*0.2614678044 + l0[13]*1.0083551762 + l0[14]*-0.5473833797)
    l1[12] = self._act_tanh(l0[0]*-0.4367517149 + l0[1]*-10.0601304934 + l0[2]*1.9240604838 + l0[3]*-1.3192184047 + l0[4]*-0.4564760159 + l0[5]*-0.2965270368 + l0[6]*-1.1407423613 + l0[7]*2.0949647291 + l0[8]*-5.8212599297 + l0[9]*-1.3393321939 + l0[10]*7.6624548265 + l0[11]*1.1309391851 + l0[12]*-0.141798054 + l0[13]*5.1416736187 + l0[14]*-1.8142503125)
    l1[13] = self._act_tanh(l0[0]*1.103948336 + l0[1]*-1.4592033032 + l0[2]*0.6146278432 + l0[3]*0.5040966421 + l0[4]*-2.4276090772 + l0[5]*-0.0432902426 + l0[6]*-0.0044259999 + l0[7]*-0.5961347308 + l0[8]*0.3821026107 + l0[9]*0.6169102373 +l0[10]*-0.1469847611 + l0[11]*-0.0717167683 + l0[12]*-0.0352403695 + l0[13]*1.2481310788 + l0[14]*0.1339628411)
    l1[14] = self._act_tanh(l0[0]*-9.8049980534 + l0[1]*13.5481068519 + l0[2]*-17.1362809025 + l0[3]*0.7142100864 + l0[4]*4.4759163422 + l0[5]*4.5716161777 + l0[6]*1.4290884628 + l0[7]*8.3952862712 + l0[8]*-7.1613700432 + l0[9]*-3.3249489518+ l0[10]*-0.7789587912 + l0[11]*-1.7987628873 + l0[12]*13.364752545 + l0[13]*5.3947219678 + l0[14]*12.5267547127)
    l1[15] = self._act_tanh(l0[0]*0.9869461803 + l0[1]*1.9473351905 + l0[2]*2.032925759 + l0[3]*7.4092080633 + l0[4]*-1.9257741399 + l0[5]*1.8153585328 + l0[6]*1.1427866392 + l0[7]*-0.3723167449 + l0[8]*5.0009927384 + l0[9]*-0.2275103411 + l0[10]*2.8823012914 + l0[11]*-3.0633141934 + l0[12]*-2.785334815 + l0[13]*2.727981E-4 + l0[14]*-0.1253009512)
    l1[16] = self._act_tanh(l0[0]*4.9418118585 + l0[1]*-2.7538199876 + l0[2]*-16.9887588104 + l0[3]*8.8734475297 + l0[4]*-16.3022734814 + l0[5]*-4.562496601 + l0[6]*-1.2944373699 + l0[7]*-9.6022946986 + l0[8]*-1.018393866 + l0[9]*-11.4094515429 + l0[10]*24.8483091382 + l0[11]*-3.0031522277 + l0[12]*0.1513114555 + l0[13]*-6.7170487021 + l0[14]*-14.7759227576)
    l1[17] = self._act_tanh(l0[0]*5.5931454656 + l0[1]*2.22272078 + l0[2]*2.603416897 + l0[3]*1.2661196599 + l0[4]*-2.842826446 + l0[5]*-7.9386099121 + l0[6]*2.8278849111 + l0[7]*-1.2289445238 + l0[8]*4.571484248 + l0[9]*0.9447425595 + l0[10]*4.2890688351 + l0[11]*-3.3228258483 + l0[12]*4.8866215526 + l0[13]*1.0693412194 + l0[14]*-1.963203112)
    l1[18] = self._act_tanh(l0[0]*0.2705520264 + l0[1]*0.4002328199 + l0[2]*0.1592515845 + l0[3]*0.371893552 + l0[4]*-1.6639467871 + l0[5]*2.2887318884 + l0[6]*-0.148633664 + l0[7]*-0.6517792263 + l0[8]*-0.0993032992 + l0[9]*-0.964940376 + l0[10]*0.1286342935 + l0[11]*0.4869943595 + l0[12]*1.4498648166 + l0[13]*-0.3257333384 + l0[14]*-1.3496419812)
    l1[19] = self._act_tanh(l0[0]*-1.3223200798 + l0[1]*-2.2505204324 + l0[2]*0.8142804525 + l0[3]*-0.848348177 + l0[4]*0.7208860589 + l0[5]*1.2033423756 + l0[6]*-0.1403005786 + l0[7]*0.2995941644 + l0[8]*-1.1440473062 + l0[9]*1.067752916 + l0[10]*-1.2990534679 + l0[11]*1.2588583869 + l0[12]*0.7670409455 + l0[13]*2.7895972983 + l0[14]*-0.5376152512)
    l1[20] = self._act_tanh(l0[0]*0.7382351572 + l0[1]*-0.8778865631 + l0[2]*1.0950766363 + l0[3]*0.7312146997 + l0[4]*2.844781386 + l0[5]*2.4526730903 + l0[6]*-1.9175165077 + l0[7]*-0.7443755288 + l0[8]*-3.1591419438 + l0[9]*0.8441602697 + l0[10]*1.1979484448 + l0[11]*2.138098544 + l0[12]*0.9274159536 + l0[13]*-2.1573448803 + l0[14]*-3.7698356464)
    l1[21] = self._act_tanh(l0[0]*5.187120117 + l0[1]*-7.7525670576 + l0[2]*1.9008346975 + l0[3]*-1.2031603996 + l0[4]*5.917669142 + l0[5]*-3.1878682719 + l0[6]*1.0311747828 + l0[7]*-2.7529484612 + l0[8]*-1.1165884578 + l0[9]*2.5524942323 + l0[10]*-0.38623241 + l0[11]*3.7961317445 + l0[12]*-6.128820883 + l0[13]*-2.1470707709 + l0[14]*2.0173792965)
    l1[22] = self._act_tanh(l0[0]*-6.0241676562 + l0[1]*0.7474455584 + l0[2]*1.7435724844 + l0[3]*0.8619835076 + l0[4]*-0.1138406797 + l0[5]*6.5979359352 + l0[6]*1.6554154348 + l0[7]*-3.7969458806 + l0[8]*1.1139097376 + l0[9]*-1.9588417 + l0[10]*3.5123392221 + l0[11]*9.4443103128 + l0[12]*-7.4779291395 + l0[13]*3.6975940671 + l0[14]*8.5134262747)
    l1[23] = self._act_tanh(l0[0]*-7.5486576471 + l0[1]*-0.0281420865 + l0[2]*-3.8586839454 + l0[3]*-0.5648792233 + l0[4]*-7.3927282026 + l0[5]*-0.3857538046 + l0[6]*-2.9779885698 + l0[7]*4.0482279965 + l0[8]*-1.1522499578 + l0[9]*-4.1562500212 + l0[10]*0.7813134307 + l0[11]*-1.7582667612 + l0[12]*1.7071109988 + l0[13]*6.9270873208 + l0[14]*-4.5871357362)
    l1[24] = self._act_tanh(l0[0]*-5.3603442228 + l0[1]*-9.5350611629 + l0[2]*1.6749984422 + l0[3]*-0.6511065892 + l0[4]*-0.8424823239 + l0[5]*1.9946675213 + l0[6]*-1.1264361638 + l0[7]*0.3228676616 + l0[8]*5.3562230396 + l0[9]*-1.6678168952+ l0[10]*1.2612580068 + l0[11]*-3.5362671399 + l0[12]*-9.3895191366 + l0[13]*2.0169228673 + l0[14]*-3.3813191557)
    l1[25] = self._act_tanh(l0[0]*1.1362866429 + l0[1]*-1.8960071702 + l0[2]*5.7047307243 + l0[3]*-1.6049785053 + l0[4]*-4.8353898931 + l0[5]*-1.4865381145 + l0[6]*-0.2846893475 + l0[7]*2.2322095997 + l0[8]*2.0930488668 + l0[9]*1.7141411002 + l0[10]*-3.4106032176 + l0[11]*3.0593289612 + l0[12]*-5.0894813904 + l0[13]*-0.5316299133 + l0[14]*0.4705265416)
    l1[26] = self._act_tanh(l0[0]*-0.9401400975 + l0[1]*-0.9136086957 + l0[2]*-3.3808688582 + l0[3]*4.7200776773 + l0[4]*3.686296919 + l0[5]*14.2133723935 + l0[6]*1.5652940954 + l0[7]*-0.2921139433 + l0[8]*1.0244504511 + l0[9]*-7.6918299134 + l0[10]*-0.594936135 + l0[11]*-1.4559914156 + l0[12]*2.8056435224 + l0[13]*2.6103905733 + l0[14]*2.3412348872)
    l1[27] = self._act_tanh(l0[0]*1.1573980186 + l0[1]*2.9593661909 + l0[2]*0.4512594325 + l0[3]*-0.9357210858 + l0[4]*-1.2445804495 + l0[5]*4.2716471631 + l0[6]*1.5167912375 + l0[7]*1.5026853293 + l0[8]*1.3574772038 + l0[9]*-1.9754386842 + l0[10]*6.727671436 + l0[11]*8.0145772889 + l0[12]*7.3108970663 + l0[13]*-2.5005627841 + l0[14]*8.9604502277)
    l1[28] = self._act_tanh(l0[0]*6.3576350212 + l0[1]*-2.9731672725 + l0[2]*-2.7763558082 + l0[3]*-3.7902984555 + l0[4]*-1.0065574585 + l0[5]*-0.7011836061 + l0[6]*-1.0298068578 + l0[7]*1.201007784 + l0[8]*-0.7835862254 + l0[9]*-3.9863597435 + l0[10]*6.7851825502 + l0[11]*1.1120256721 + l0[12]*-2.263287351 + l0[13]*1.8314374104 + l0[14]*-2.279102097)
    l1[29] = self._act_tanh(l0[0]*-7.8741911036 + l0[1]*-5.3370618518 + l0[2]*11.9153868964 + l0[3]*-4.1237170553 + l0[4]*2.9491152758 + l0[5]*1.0317132502 + l0[6]*2.2992199883 + l0[7]*-2.0250502364 + l0[8]*-11.0785995839 + l0[9]*-6.3615588554 + l0[10]*-1.1687644976 + l0[11]*6.3323478015 + l0[12]*6.0195076962 + l0[13]*-2.8972208702 + l0[14]*3.6107747183)
    
    l2[0] = self._act_tanh(l1[0]*-0.590546797 + l1[1]*0.6608304658 + l1[2]*-0.3358268839 + l1[3]*-0.748530283 + l1[4]*-0.333460383 + l1[5]*-0.3409307681 + l1[6]*0.1916558198 + l1[7]*-0.1200399453 + l1[8]*-0.5166151854 + l1[9]*-0.8537164676 +l1[10]*-0.0214448647 + l1[11]*-0.553290271 + l1[12]*-1.2333302892 + l1[13]*-0.8321813811 + l1[14]*-0.4527761741 + l1[15]*0.9012545631 + l1[16]*0.415853215 + l1[17]*0.1270548319 + l1[18]*0.2000460279 + l1[19]*-0.1741942671 + l1[20]*0.419830522 + l1[21]*-0.059839291 + l1[22]*-0.3383001769 + l1[23]*0.1617814073 + l1[24]*0.3071848006 + l1[25]*-0.3191182045 + l1[26]*-0.4981831822 + l1[27]*-1.467478375 + l1[28]*-0.1676432563 + l1[29]*1.2574849126)
    l2[1] = self._act_tanh(l1[0]*-0.5514235841 + l1[1]*0.4759190049 + l1[2]*0.2103576983 + l1[3]*-0.4754377924 + l1[4]*-0.2362941295 + l1[5]*0.1155082119 + l1[6]*0.7424215794 + l1[7]*-0.3674198672 + l1[8]*0.8401574461 + l1[9]*0.6096563193 + l1[10]*0.7437935674 + l1[11]*-0.4898638101 + l1[12]*-0.4168668092 + l1[13]*-0.0365111095 + l1[14]*-0.342675224 + l1[15]*0.1870268765 + l1[16]*-0.5843050987 + l1[17]*-0.4596547471 + l1[18]*0.452188522 + l1[19]*-0.6737126684 + l1[20]*0.6876072741 + l1[21]*-0.8067776704 + l1[22]*0.7592979467 + l1[23]*-0.0768239468 + l1[24]*0.370536097 + l1[25]*-0.4363884671 + l1[26]*-0.419285676 + l1[27]*0.4380251141 + l1[28]*0.0822528948 + l1[29]*-0.2333910809)
    l2[2] = self._act_tanh(l1[0]*-0.3306539521 + l1[1]*-0.9382247194 + l1[2]*0.0746711276 + l1[3]*-0.3383838985 + l1[4]*-0.0683232217 + l1[5]*-0.2112358049 + l1[6]*-0.9079234054 + l1[7]*0.4898595603 + l1[8]*-0.2039825863 + l1[9]*1.0870698641+ l1[10]*-1.1752901237 + l1[11]*1.1406403923 + l1[12]*-0.6779626786 + l1[13]*0.4281048906 + l1[14]*-0.6327670055 + l1[15]*-0.1477678844 + l1[16]*0.2693637584 + l1[17]*0.7250738509 + l1[18]*0.7905904504 + l1[19]*-1.6417250883 + l1[20]*-0.2108095534 +l1[21]*-0.2698557472 + l1[22]*-0.2433656685 + l1[23]*-0.6289943273 + l1[24]*0.436428207 + l1[25]*-0.8243825184 + l1[26]*-0.8583496686 + l1[27]*0.0983131026 + l1[28]*-0.4107462518 + l1[29]*0.5641683087)
    l2[3] = self._act_tanh(l1[0]*1.7036869992 + l1[1]*-0.6683507666 + l1[2]*0.2589197112 + l1[3]*0.032841148 + l1[4]*-0.4454796342 + l1[5]*-0.6196149423 + l1[6]*-0.1073622976 + l1[7]*-0.1926393101 + l1[8]*1.5280232458 + l1[9]*-0.6136527036 +l1[10]*-1.2722934357 + l1[11]*0.2888655811 + l1[12]*-1.4338638512 + l1[13]*-1.1903556863 + l1[14]*-1.7659663905 + l1[15]*0.3703086867 + l1[16]*1.0409140889 + l1[17]*0.0167382209 + l1[18]*0.6045646461 + l1[19]*4.2388788116 + l1[20]*1.4399738234 + l1[21]*0.3308571935 + l1[22]*1.4501137667 + l1[23]*0.0426123724 + l1[24]*-0.708479795 + l1[25]*-1.2100800732 + l1[26]*-0.5536278651 + l1[27]*1.3547250573 + l1[28]*1.2906250286 + l1[29]*0.0596007114)
    l2[4] = self._act_tanh(l1[0]*-0.462165126 + l1[1]*-1.0996742176 + l1[2]*1.0928262999 + l1[3]*1.806407067 + l1[4]*0.9289147669 + l1[5]*0.8069022793 + l1[6]*0.2374237802 + l1[7]*-2.7143979019 + l1[8]*-2.7779203877 + l1[9]*0.214383903 + l1[10]*-1.3111536623 + l1[11]*-2.3148813568 + l1[12]*-2.4755355804 + l1[13]*-0.6819733236 + l1[14]*0.4425615226 + l1[15]*-0.1298218043 + l1[16]*-1.1744832824 + l1[17]*-0.395194848 + l1[18]*-0.2803397703 + l1[19]*-0.4505071197 + l1[20]*-0.8934956598 + l1[21]*3.3232916348 + l1[22]*-1.7359534851 + l1[23]*3.8540421743 + l1[24]*1.4424032523 + l1[25]*0.2639823693 + l1[26]*0.3597053634 + l1[27]*-1.0470693728 + l1[28]*1.4133480357 + l1[29]*0.6248098695)
    l2[5] = self._act_tanh(l1[0]*0.2215807411 + l1[1]*-0.5628295071 + l1[2]*-0.8795982905 + l1[3]*0.9101585104 + l1[4]*-1.0176831976 + l1[5]*-0.0728884401 + l1[6]*0.6676331658 + l1[7]*-0.7342174108 + l1[8]*9.4428E-4 + l1[9]*0.6439774272 + l1[10]*-0.0345236026 + l1[11]*0.5830977027 + l1[12]*-0.4058921837 + l1[13]*-0.3991888077 + l1[14]*-1.0090426973 + l1[15]*-0.9324780698 + l1[16]*-0.0888749165 + l1[17]*0.2466351736 + l1[18]*0.4993304601 + l1[19]*-1.115408696 + l1[20]*0.9914246705 + l1[21]*0.9687743445 + l1[22]*0.1117130875 + l1[23]*0.7825109733 + l1[24]*0.2217023612 + l1[25]*0.3081256411 + l1[26]*-0.1778007966 + l1[27]*-0.3333287743 + l1[28]*1.0156352461 + l1[29]*-0.1456257813)
    l2[6] = self._act_tanh(l1[0]*-0.5461783383 + l1[1]*0.3246015999 + l1[2]*0.1450605434 + l1[3]*-1.3179944349 + l1[4]*-1.5481775261 + l1[5]*-0.679685633 + l1[6]*-0.9462335139 + l1[7]*-0.6462399371 + l1[8]*0.0991658683 + l1[9]*0.1612892194 +l1[10]*-1.037660602 + l1[11]*-0.1044778824 + l1[12]*0.8309203243 + l1[13]*0.7714766458 + l1[14]*0.2566767663 + l1[15]*0.8649416329 + l1[16]*-0.5847461285 + l1[17]*-0.6393969272 + l1[18]*0.8014049359 + l1[19]*0.2279568228 + l1[20]*1.0565217821 + l1[21]*0.134738029 + l1[22]*0.3420395576 + l1[23]*-0.2417397219 + l1[24]*0.3083072038 + l1[25]*0.6761739059 + l1[26]*-0.4653817053 + l1[27]*-1.0634057566 + l1[28]*-0.5658892281 + l1[29]*-0.6947283681)
    l2[7] = self._act_tanh(l1[0]*-0.5450410944 + l1[1]*0.3912849372 + l1[2]*-0.4118641117 + l1[3]*0.7124695074 + l1[4]*-0.7510266122 + l1[5]*1.4065673913 + l1[6]*0.9870731545 + l1[7]*-0.2609363107 + l1[8]*-0.3583639958 + l1[9]*0.5436375706 +l1[10]*0.4572450099 + l1[11]*-0.4651538878 + l1[12]*-0.2180218212 + l1[13]*0.5241262959 + l1[14]*-0.8529323253 + l1[15]*-0.4200378937 + l1[16]*0.4997885721 + l1[17]*-1.1121528189 + l1[18]*0.5992411048 + l1[19]*-1.0263270781 + l1[20]*-1.725160642 + l1[21]*-0.2653995722 + l1[22]*0.6996703032 + l1[23]*0.348549086 + l1[24]*0.6522482482 + l1[25]*-0.7931928436 + l1[26]*-0.5107994359 + l1[27]*0.0509642698 + l1[28]*0.8711187423 + l1[29]*0.8999449627)
    l2[8] = self._act_tanh(l1[0]*-0.7111081522 + l1[1]*0.4296245062 + l1[2]*-2.0720732038 + l1[3]*-0.4071818684 + l1[4]*1.0632721681 + l1[5]*0.8463224325 + l1[6]*-0.6083948423 + l1[7]*1.1827669608 + l1[8]*-0.9572307844 + l1[9]*-0.9080517673 + l1[10]*-0.0479029057 + l1[11]*-1.1452853213 + l1[12]*0.2884352688 + l1[13]*0.1767851586 + l1[14]*-1.089314461 + l1[15]*1.2991763966 + l1[16]*1.6236630806 + l1[17]*-0.7720263697 + l1[18]*-0.5011541755 + l1[19]*-2.3919413568 + l1[20]*0.0084018338 + l1[21]*0.9975216139 + l1[22]*0.4193541029 + l1[23]*1.4623834571 + l1[24]*-0.6253069691 + l1[25]*0.6119677341 + l1[26]*0.5423948388 + l1[27]*1.0022450377 + l1[28]*-1.2392984069 + l1[29]*1.5021529822)

    l3 = self._act_tanh(l2[0]*0.3385061186 + l2[1]*0.6218531956 + l2[2]*-0.7790340983 + l2[3]*0.1413078332 + l2[4]*0.1857010624 + l2[5]*-0.1769456351 + l2[6]*-0.3242337911 + l2[7]*-0.503944883 + l2[8]*0.1540568869)

    if l3 > self.threshold:
      self.buying = True
    elif l3 < -1 * self.threshold:
      self.buying = False

@contextmanager
def session_scope(engine):
  session = Session()
  try:
    yield session
    session.commit()
  except:
    session.rollback()
    raise
  finally:
    session.close()

engine = create_engine('mysql://kollybistes:titkos@localhost/kollybistes')
Session = sessionmaker(bind=engine)
datastore.Base.metadata.create_all(engine)


syncer = TradeHistorySynchronizer(['XETHZEUR'])

with session_scope(engine) as session:
  hist = TradeHistory(session, 'XETHZEUR', 240)
  for ohlc in hist.ohlc():
    print("%s %f %f %f %f %f" % (ohlc.timestamp.strftime("%c"), ohlc.open, ohlc.high, ohlc.low, ohlc.close, ohlc.vwap))
  ann = ANNStrategy(hist.ohlc())
  ann.orders()
  syncer.sync(session)




