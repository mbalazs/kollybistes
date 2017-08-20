#!/usr/bin/python3

import json
import time

import hashlib
import hmac
import base64

import http.client
import urllib.request
import urllib.parse
import urllib.error

class Connection(object):

  def __init__(self, uri='api.kraken.com', timeout=30):
    self.headers = {}
    self.conn = http.client.HTTPSConnection(uri, timeout=timeout)
    return

  def close(self):
    self.conn.close()
    return

  def _request(self, url, req=None, headers=None):
    if req is None:
      req = {}

    if headers is None:
      headers = {}

    data = urllib.parse.urlencode(req)
    headers.update(self.headers)

    self.conn.request('POST', url, data, headers)
    response = self.conn.getresponse()

    if response.status not in (200,201,202):
      raise http.client.HTTPException(response.status)

    return response.read().decode()

class API(object):

  def __init__(self, key='', secret='', conn=None):
    self.key = key
    self.secret = secret
    self.uri = 'https://api.kraken.com'
    self.apiversion = '0'
    self.conn = conn
    return

  def _query(self, urlpath, req, conn=None, headers=None):
    url = self.uri + urlpath

    if conn is None:
      if self.conn is None:
        self.conn = Connection()
      conn = self.conn

    if headers is None:
      headers = {}

    ret = conn._request(url, req, headers)
    return json.loads(ret)

  def loadkeys(self, jsonfile):
    with open(jsonfile) as f:
      data = json.load(f)
    self.key = data['api_key']
    self.secret = data['api_secret']


  def query_public(self, method, req=None, conn=None):
    urlpath = '/' + self.apiversion + '/public/' + method

    if req is None:
      req = {}

    return self._query(urlpath, req, conn)

  def query_private(self, method, req=None, conn=None):
    if req is None:
      req = {}

    urlpath = '/' + self.apiversion + '/private/' + method
    
    req['nonce'] = int(1000*time.time())
    postdata = urllib.parse.urlencode(req)

    encoded = (str(req['nonce']) + postdata).encode()
    message = urlpath.encode() + hashlib.sha256(encoded).digest()

    signature = hmac.new(base64.b64decode(self.secret), message, hashlib.sha512)
    sigdigest = base64.b64encode(signature.digest())

    headers = {
      'API-Key': self.key,
      'API-Sign': sigdigest.decode()
    }

    return self._query(urlpath, req, conn, headers)




