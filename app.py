import os
import time
from flask import Flask
import bugsnag
from bugsnag.flask import handle_exceptions
from urlparse import urlparse
from pymongo import MongoClient
from flask import Flask
from flask import abort
from flask import request
from flask import render_template
from flask import Response
from flask import redirect
from flask import jsonify
from twilio.rest import TwilioRestClient
import multiprocessing
from APNSWrapper import APNSNotification
from APNSWrapper import APNSNotificationWrapper
from APNSWrapper import APNSAlert
from APNSWrapper import APNSProperty

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

TWILIO_SID = os.environ.get('TWILIO_SID')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
twilio = TwilioRestClient(TWILIO_SID, TWILIO_AUTH_TOKEN) 

app = Flask(__name__)

if DEBUG:
  app.debug = True
else:
  handle_exceptions(app)

users = db.users
songs = db.songs

@app.route('/')
def base():
  return 'Welcome to Jukebox :)'

#when user gives phone number
#if phone number exists as a user, just send confirmation code
#params - phone number
#response - success
#index - by phone number - unique
@app.route('/join', methods=['POST'])
def join():
  params = request.form
  phone_number = params['phone_number']
  code = "foobar"
  users.update({'phone_number':phone_number}, {'$push':{'code':code}}, upsert=True)
  send_sms(phone_number, "your auth code is " + code)
  return jsonify({'result':True})

#confirm device w/ code
#params - phone number, code
#response - success/failure
@app.route('/confirm', methods=['POST'])
def confirm():
  params = request.form
  return jsonify({'result':authenticate(params['phone_number'], params['code'])})

#retrieve list of songs sent or shared by user (can this be done using one or query? should these be kept separate?)
#future, add pagination
#params - phone number, code, last_updated
#response - reverse chronological list of items
@app.route('/inbox', methods=['POST'])
def inbox():
  params = request.form
  phone_number = params['phone_number']
  if not authenticate(phone_number, params['code']): 
    return jsonify({'result':False})
  inbox = []
  for song in songs.find(query_for_songs(phone_number, params['last_updated'])).sort('date', -1).limit(-100):
    del song['_id']
    inbox.append(song)
  return jsonify({'inbox':inbox})

#send song to people
#create a new item per recipient
#params - phone number, code, songID/URL, title, artist, array of recipients, timestamp
#response - success
@app.route('/share', methods=['POST'])
def share():
  params = request.form
  phone_number = params['phone_number']
  if not authenticate(phone_number, params['code']):
    return jsonify({'result':False})

  song = params.to_dict()
  recipients = song['recipients'].split(',')
  del song['recipients']
  song['sender'] = phone_number
  del song['phone_number']
  del song['code']

  push_message = song['sender_name'] + ' thought you would like ' + song['title'] + ' by ' + song['artist'] + ' :)'
  sms_message = push_message + '\nListen now: http://youtu.be/' + song['yt_id'] + ' \n\nDownload Jukebox to send songs to friends - jkbx.es'
  del song['sender_name']

  song_copies = []
  for r in recipients:
    song['recipient'] = r
    song_copies.append(song)
    song = song.copy()
    if user_exists(r):
      send_push(r, push_message, song)
    else: #should probably batch these? or queue them? use url shortner?
      send_sms(r, sms_message)

  songs.insert(song_copies)
  return jsonify({'result':True})

#add listen:True to item
#params - phone number, code, songID/URL, sender, timestamp
#response - success
@app.route('/listen', methods=['POST'])
def listen():
  params = request.form
  phone_number = params['phone_number']
  if not authenticate(phone_number, params['code']):
    return jsonify({'result':False})
  songs.update({'sender':params['sender'], 'recipient':phone_number, 'yt_id':params['yt_id'], 'date':params['date']}, {'$set':{'listen':True, 'updated':timestamp()}})
  return jsonify({'result':True})

#add love:True to item
#params - phone number, code, songID/URL, sender, timestamp
#response - success
@app.route('/love', methods=['POST'])
def love():
  params = request.form
  phone_number = params['phone_number']
  if not authenticate(phone_number, params['code']):
    return jsonify({'result':False})
  songs.update({'sender':params['sender'], 'recipient':phone_number, 'yt_id':params['yt_id'], 'date':params['date']}, {'$set':{'love':True, 'updated':timestamp()}})
  return jsonify({'result':True})

#test if code matches any of the codes
#index, by phone number and code(array)
def authenticate(phone_number, code):
  if users.find({'phone_number':phone_number, 'code':code}).count():
    return True
  return False

def user_exists(phone_number):
  if users.find({'phone_number':phone_number}).count():
    return True
  return False

def send_sms(phone_number, message):
  p = multiprocessing.Process(target=send_sms_background, args=(phone_number, message))
  p.start()

def send_sms_background(phone_number, message):
  twilio.messages.create(to=phone_number, from_='+16502521370', body=message)

#send push notification
def send_push(recipient, text, data):
  p = multiprocessing.Process(target=send_push_background, args=(recipient, text, data))
  p.start()

def send_push_background(recipient, text, data):
  wrapper = APNSNotificationWrapper(('static/pushcerts/prod_push_cert.p12'), True)
  tokens = set()
  #need to add device tokens to set
  for deviceToken in tokens:
    message = APNSNotification()
    message.tokenHex(deviceToken)
    alert = APNSAlert()
    alert.body(str(text))
    message.alert(alert)
    message.sound()
    message.badge(badge)
    for key in data:
      if isinstance(data[key], Number):
        prop = APNSProperty(str(key), data[key])
        message.appendProperty(prop)
      elif isinstance(data[key], basestring):
        prop = APNSProperty(str(key), str(data[key]))
        message.appendProperty(prop)
    wrapper.append(message)
  wrapper.notify()

def timestamp():
  return str(time.time())

def query_for_songs(phone_number, last_updated):
  return {'$or':[{'sender':phone_number}, {'recipient':phone_number}], 'updated':{'$gt':last_updated}}
