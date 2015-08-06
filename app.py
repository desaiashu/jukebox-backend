import os
import time
from functools import wraps
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

#index, by phone number and code(array)
def authenticate(f):
  @wraps(f)
  def decorated_function(*args, **kwargs):
    if not users.find({'phone_number':request.json['phone_number'], 'code':request.json['code']}).count():
      return abort(401)
    return f(*args, **kwargs)  
  return decorated_function

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
  phone_number = request.json['phone_number']
  #TODO create custom code
  code = "foobar"
  users.update({'phone_number':phone_number}, {'$push':{'code':code}}, upsert=True)
  send_sms(phone_number, "your auth code is " + code)
  return jsonify({'result':True})

#confirm device w/ code
#params - phone number, code
#response - success/failure
@app.route('/confirm', methods=['POST'])
@authenticate
def confirm():
  return jsonify({'result':True})

@app.route('/pushtoken', methods=['POST'])
@authenticate
def pushtoken():
  users.update({'phone_number':request.json['phone_number']}, {'$push':{'push_token':request.json['push_token']}})
  return jsonify({'result':True})

#retrieve list of songs sent or shared by user (can this be done using one or query? should these be kept separate?)
#future, add pagination
#params - phone number, code, last_updated
#response - reverse chronological list of items
@app.route('/inbox', methods=['POST'])
@authenticate
def inbox():
  inbox = []
  for song in songs.find(query_for_inbox(request.json['phone_number'], request.json['last_updated'])).sort('date', -1).limit(-100):
    song['id'] = str(song['_id'])
    del song['_id']
    inbox.append(song)
  return jsonify({'inbox':inbox})

#send song to people
#create a new item per recipient
#params - phone number, code, songID/URL, title, artist, array of recipients, timestamp
#response - success
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

  song_copies = []
  for recipient in recipients:
    song['recipient'] = recipient
    song_copies.append(song)
    song = song.copy()
    if user_exists(recipient):
      send_push(recipient, push_message, song)
    else: #should probably batch these? or queue them? use url shortner?
      send_sms(recipient, sms_message)

  songs.insert(song_copies)
  return jsonify({'result':True})

#add listen:True to item
#params - phone number, code, songID/URL, sender, timestamp
#response - success
@app.route('/listen', methods=['POST'])
@authenticate
def listen():
  songs.update({'_id':ObjectId(request.json['id'])}, {'$set':{'listen':True, 'updated':timestamp()}})
  return jsonify({'result':True})

#add love:True to item
#params - phone number, code, songID/URL, sender, timestamp
#response - success
@app.route('/love', methods=['POST'])
@authenticate
def love():
  songs.update({'_id':ObjectId(request.json['id'])}, {'$set':{'love':True, 'updated':timestamp()}})
  return jsonify({'result':True})

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

def query_for_inbox(phone_number, last_updated):
  return {'$or':[{'sender':phone_number}, {'recipient':phone_number}], 'updated':{'$gt':last_updated}}

def query_for_song(song):
  return {'sender':song['sender'], 'recipient':song['phone_number'], 'yt_id':song['yt_id'], 'date':song['date']}
