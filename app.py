#!/usr/bin/python
# -*- coding: utf-8 -*-

import os
import time
import string
import random
import urllib
import multiprocessing
from flask import Flask
from flask import abort
from flask import request
from flask import jsonify
from flask import Response
from flask import redirect
from flask import render_template
from numbers import Number
from functools import wraps
from urlparse import urlparse
from pymongo import MongoClient
from pymongo import ReturnDocument
from bson.objectid import ObjectId
from apns import APNs, Frame, Payload
from twilio.rest import TwilioRestClient
from bugsnag.flask import handle_exceptions

if os.environ.get('DEBUG') == 'True':
  DEBUG = True
else:
  DEBUG = False

MONGO_URL = os.environ.get('MONGOHQ_URL')
if MONGO_URL:
  mongo_client = MongoClient(MONGO_URL)
  db = mongo_client[urlparse(MONGO_URL).path[1:]]
else:
  mongo_client = MongoClient('localhost', 27017)
  db = mongo_client['jukebox']

users = db.users
songs = db.songs

TWILIO_SID = os.environ.get('TWILIO_SID')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
twilio = TwilioRestClient(TWILIO_SID, TWILIO_AUTH_TOKEN) 

apns = APNs(use_sandbox=False, cert_file='static/JukeboxBetaPush.pem', key_file='static/JukeboxBetaPush.pem')

app = Flask(__name__)

if DEBUG:
  app.debug = True
else:
  handle_exceptions(app)


#could pass through user object since most functions use it
def authenticate(f):
  @wraps(f)
  def decorated_function(*args, **kwargs):
    if not users.find({'phone_number':request.json['phone_number'], 'code':request.json['code']}).count():
      return abort(401)
    return f(*args, **kwargs)  
  return decorated_function


@app.route('/')
def base():
  url = 'itms-services://?action=download-manifest&url=' + urllib.quote('https://www.jkbx.es/static/jukebox.plist')
  pic = 'https://s3.amazonaws.com/mgwu-misc/jukebox/jukebox.png'
  return render_template('download.html', title='Jukebox', link=url, picture=pic)

@app.route('/testpush')
def testpush():
  send_push_background(['6d64369b6e4c2cf4ad045fe164fb780824bab9a5581dac2422a8c6c56079e01b'], 'yay', 1, None)
  return 'yay'


@app.route('/version')
def version():
  return jsonify({'version':'0.463', 'forced':True, 'url':'http://www.jkbx.es'})


@app.route('/join', methods=['POST'])
def join():
  phone_number = request.json['phone_number']
  code = ''.join(random.choice(string.digits) for _ in range(6))
  user = users.find_one_and_update({'phone_number':phone_number}, {'$push':{'code':code}}, upsert=True, return_document=ReturnDocument.AFTER)
  if not 'badge' in user:
    create_ashus_songs(phone_number)
    user['badge'] = 5
    users.save(user)
  send_sms(phone_number, 'Auth code is ' + code)
  return jsonify({'success':True})


@app.route('/confirm', methods=['POST'])
@authenticate
def confirm():
  return jsonify({'success':True})


@app.route('/pushtoken', methods=['POST'])
@authenticate
def pushtoken():
  user = users.find_one_and_update({'phone_number':request.json['phone_number']}, {'$addToSet':{'push_token':request.json['push_token']}})
  send_push_background(user['push_token'], None, user['badge'], None)
  return jsonify({'success':True})


@app.route('/inbox', methods=['POST'])
@authenticate
def inbox():
  inbox = []
  updated = timestamp()
  for song in songs.find(query_for_inbox(request.json['phone_number'], request.json['last_updated'])).sort('date', -1).limit(-100):
    song['id'] = str(song['_id'])
    del song['_id']
    inbox.append(song)
  return jsonify({'inbox':inbox, 'updated':updated})


#notes: 
# batch insert could improve performance
# queueing notifications could improve performance
@app.route('/share', methods=['POST'])
@authenticate
def share():
  song = request.json

  recipients = song['recipients'].split(',')
  del song['recipients']
  song['sender'] = song['phone_number']
  del song['phone_number']
  del song['code']

  push_message = song['sender_name'] + ' thought you would like ' + song['title'] + ' by ' + song['artist'] + ' :)'
  sms_message = push_message + '\nListen now: http://youtu.be/' + song['yt_id'] + ' \n\nDownload Jukebox to send songs to friends - jkbx.es'
  del song['sender_name']

  songs_to_return = []
  for recipient in recipients:
    song = song.copy()
    song['recipient'] = recipient
    song['updated'] = timestamp()
    songs.insert(song)
    song['id'] = str(song['_id'])
    del song['_id']
    songs_to_return.append(song)

    recipient_user = users.find_one_and_update({'phone_number':recipient}, {'$inc':{'badge':1}}, return_document=ReturnDocument.AFTER)
    if recipient_user and 'tokens' in recipient_user:
      send_push(recipient_user['tokens'], push_message, recipient_user['badge'], {'share':song})
    else:
      send_sms(recipient, sms_message)

  return jsonify({'songs':songs_to_return})


@app.route('/listen', methods=['POST'])
@authenticate
def listen():
  song = request.json
  songs.update({'_id':ObjectId(song['id'])}, {'$set':{'listen':True, 'updated':timestamp()}})

  push_message = song['listener_name'] + ' listened to ' + song['title'] + ' by ' + song['artist'] + ' :)'
  sender = users.find_one({'phone_number':song['sender']})
  if 'tokens' in sender:
    send_push(sender['tokens'], push_message, None, {'listen':song['id']})

  listener = users.find_one_and_update({'phone_number':song['phone_number']}, {'$inc':{'badge':-1}}, return_document=ReturnDocument.AFTER)
  if 'tokens' in listener:
    send_push(listener['tokens'], None, listener['badge'], None)

  return jsonify({'success':True})


@app.route('/love', methods=['POST'])
@authenticate
def love():
  song = request.json
  songs.update({'_id':ObjectId(request.json['id'])}, {'$set':{'love':True, 'updated':timestamp()}})

  push_message = song['lover_name'] + ' listened to ' + song['title'] + ' by ' + song['artist'] + ' :)'
  sender = users.find_one({'phone_number':song['sender']})
  if 'tokens' in sender:
    send_push(sender['tokens'], push_message, None, {'listen':song['id']})

  return jsonify({'success':True})


def send_sms(phone_number, message):
  p = multiprocessing.Process(target=send_sms_background, args=(phone_number, message))
  p.start()

def send_sms_background(phone_number, message):
  twilio.messages.create(to=phone_number, from_='+16502521370', body=message)

#TODO add better error handling if user provided a bad number
# def send_sms_safe(phone_number, message):
#   try:
#     twilio.messages.create(to=phone_number, from_='+16502521370', body=message)
#   except twilio.TwilioRestException as e:
#     return e


def send_push(recipient, text, badge, data):
  p = multiprocessing.Process(target=send_push_background, args=(tokens, text, badge, data))
  p.start()

def send_push_background(tokens, text, badge, data):
  frame = Frame()
  identifier = 1
  expiry = time.time()+3600
  priority = 10
  for deviceToken in tokens:
    sound = None
    if text:
      sound = "default"
    if not data:
      data = {}
    payload = Payload(alert=text, sound=sound, badge=badge, custom=data)
    frame.add_item(deviceToken, payload, identifier, expiry, priority)
  apns.gateway_server.send_notification_multiple(frame)


def timestamp():
  return int(time.time())


def query_for_inbox(phone_number, last_updated):
  return {'$or':[{'sender':phone_number}, {'recipient':phone_number}], 'updated':{'$gt':last_updated}}


def create_ashus_songs(recipient):
  ashus_songs = [{'title':'Taro', 'artist':'Alt-J (∆)', 'yt_id':'S3fTw_D3l10'},
           {'title':'From Eden', 'artist':'Hozier', 'yt_id':'JmWbBUxSNUU'},
           {'title':'Uncantena', 'artist':'Sylvan Esso', 'yt_id':'BHBgdiSsTY8'},
           {'title':'1998', 'artist':'Chet Faker', 'yt_id':'EIQQnoeepgU'},
           {'title':'Toes', 'artist':'Glass Animals', 'yt_id':'z4ifSSg1HAo'}]
  i = 0
  now = timestamp()
  for song in ashus_songs:
    song['sender'] = 'Ashu'
    song['recipient'] = recipient
    song['date'] = now+i
    song['updated'] = song['date']
    i+=1
  songs.insert(ashus_songs)

