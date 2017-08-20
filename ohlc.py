import datastore

from sqlalchemy import *
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

class OHLC(datastore.Base):
  __tablename__ = 'ohlc'
  id = Column(Integer, primary_key=True)
  pair = Column(String(250), nullable=False)
  timestamp = Column(DateTime)
  open = Column(Numeric(precision=18, scale=6))
  high = Column(Numeric(precision=18, scale=6))
  low = Column(Numeric(precision=18, scale=6))
  close = Column(Numeric(precision=18, scale=6))
  vwap = Column(Numeric(precision=18, scale=6))
  volume = Column(Numeric(precision=25, scale=12))
  count = Column(Integer)

  def __eq__(self, other):
    if self.timestamp == other.timestamp:
      #print("self: %f %f %f %f %f %f %d" % (float(self.open), float(self.high), float(self.low), float(self.close), float(self.vwap), float(self.volume), int(self.count)))
      #print("othe: %f %f %f %f %f %f %d" % (float(other.open), float(other.high), float(other.low), float(other.close), float(other.vwap), float(other.volume), int(other.count)))
      data_equal = self.pair == other.pair \
               and self.timestamp == other.timestamp \
               and float(self.open) == float(other.open) \
               and float(self.high) == float(other.high) \
               and float(self.low) == float(other.low) \
               and float(self.close) == float(other.close) \
               and float(self.vwap) == float(other.vwap) \
               and float(self.volume) == float(other.volume) \
               and int(self.count) == int(other.count)

      if self.id == None or other.id == None:
        #print("__eq__: %r" % data_equal)
        return data_equal
      else:
        #print("__eq__: %r ----- %r (%d %d)" % (self.id == other.id and data_equal, data_equal, self.id, other.id))
        return self.id == other.id and data_equal
    else:
      #print("__eq__: timestamps dont match")
      return False

