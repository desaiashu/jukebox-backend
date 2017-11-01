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
notify_emails = db.notify_emails
notify_numbers = db.notify_numbers
toshbeats_numbers = db.toshbeats_numbers
engagesf_signups = db.engagesf_signups
engagesf_links = db.engagesf_links

TWILIO_SID = os.environ.get('TWILIO_SID')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
twilio = TwilioRestClient(TWILIO_SID, TWILIO_AUTH_TOKEN)

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


@app.route('/', methods=['GET', 'POST'])
def base():
  if 'engagesf' in request.url or 'localhost' in request.url:
    return engageSF()
  if 'toshbeats' in request.url:
    return toshbeats()
  if request.method == 'POST':
    phone = request.form['phone']
    notify_numbers.insert({'phone_number':phone})
    return render_template('splash.html', submitted=True)
  elif request.user_agent.platform in ['iphone', 'ipad']:
    url = 'itms-services://?action=download-manifest&url=' + urllib.quote('https://www.jkbx.es/static/jukebox.plist')
    pic = 'https://s3.amazonaws.com/mgwu-misc/jukebox/jukebox.png'
    return render_template('download.html', title='Jukebox', link=url, picture=pic)
  else:
    return render_template('splash.html', submitted=False)

def toshbeats():
  if request.user_agent.platform in ['iphone', 'ipad', 'android']:
    template = "toshbeatsmobile.html"
  else:
    template = "toshbeats.html"
  if request.method == 'POST':
    phone = request.form['phone']
    toshbeats_numbers.insert({'phone_number':phone})
    return render_template(template, submitted=True)
  else:
    return render_template(template, submitted=False)

@app.route('/<any(ashu, drew, makeschool, hotelcalifornia, holberton, missionu, minerva, jeremy, clean):code>', methods=['GET', 'POST'])
def engageSF_code(code): #should refactor this to read from a DB
  # if code == 'clean':
  #   i = 1
  #   for s in engagesf_signups.find():
  #     s['num_id'] = i
  #     engagesf_signups.save(s)
  #     i+=1
  #   return 'Cleaned'
  return engageSF(code)

@app.route('/text/<text_id>')
def send_text(text_id):
  if text_id == 'test':
    for s in engagesf_signups.find():
    #for s in [{'phone_number':'6504305130', 'num_id':1000}]:
      if s['phone_number'] == '':
        continue
      #grab or create the links object for this user
      l = engagesf_links.find_one({'num_id': s['num_id']})
      if not l:
        l = {'phone_number':s['phone_number'], 'num_id':s['num_id']}
        engagesf_links.insert(l)

      #if text number a hasn't been sent yet, send
      if not 'a' in l:
        v_link = 'engagesf.org/v/a'+str(s['num_id'])
        d_link = 'engagesf.org/d/a'+str(s['num_id'])
        l['a'] = {'v': 0, 'd': 0, 'v_link':'https://www.facebook.com/events/170346770214838/', 'd_link':'https://rcu-community-fund.squarespace.com/donate/'}
        engagesf_links.save(l)
        #send text to numbers
        message = "Engage SF’s first initiative is to support victims of the North Bay fire. The fire was one of the most devastating events in California history, with 9,000 buildings burned down, and 100,000 evacuated citizens. Your support will make a difference!\r\rVolunteer\rJoin us at our first volunteer day from 11a-4p on Saturday to help a donations warehouse with intake and sorting, followed by optional dinner and drinks to support a local business:\r"+v_link+"\r\rDonate\rIf you’re unable to make it out this weekend, please consider making a donation to the Redwood Credit Union Community Fund - 100% of your donation will go to disaster relief:\r"+d_link
        #send_sms_engage(s['phone_number'], message)
  return 'Complete'

@app.route('/v/<link>')
def clicked_volunteer(link):
  return track_link('v', link)

@app.route('/d/<link>')
def clicked_donation(link):
  return track_link('d', link)

def track_link(link_type, link):
  #track link, temp redirect
  num_id = ''.join(i for i in link if i.isdigit())
  link_id = link.replace(num_id, '')
  l = engagesf_links.find_one({'num_id':int(num_id)})
  if not l:
    return "Link not found :/"
  if not link_id in l:
    return "Link not found"
  link_dict = l[link_id]
  link_dict[link_type] += 1
  engagesf_links.save(l)
  return redirect(link_dict[link_type+'_link'])

# see this doc for URL routing needed for link tracking - http://werkzeug.pocoo.org/docs/0.12/routing/

def engageSF(partner="None"):
  if request.user_agent.platform in ['iphone', 'ipad', 'android']:
    mobile = True
  else:
    mobile = False
  if request.method == 'POST':
    phone = request.form['phone'].replace('+', '').replace('(', '').replace(')', '').replace(' ', '').replace('-', '')
    if phone == '':
      return render_template("engagesf.html", submitted=False, mobile=mobile, partner=partner)
    if engagesf_signups.find({'phone_number', phone}).count() > 0:
      return render_template("engagesf.html", submitted=True, mobile=mobile, partner=partner)

    #need to grab HTTP headers / referrer
    user = dict(request.headers)
    user['phone_number'] = phone
    user['time'] = int(time.time())
    user['partner'] = partner
    user['num_id'] = engagesf_signups.count()+1
    engagesf_signups.insert(user)
    send_sms_engage(phone, "Thanks for registering for Engage SF, we'll be in touch soon with volunteer opportunities!")
    return render_template("engagesf.html", submitted=True, mobile=mobile, partner=partner)
  else:
    return render_template("engagesf.html", submitted=False, mobile=mobile, partner=partner)

@app.route('/testpush')
def testpush():
  listener = users.find_one({'phone_number':'+16504305130'})
  if 'push_token' in listener:
    send_push(listener['push_token'], 'Testing', listener['push_badge'], None, content_available=True)
  return 'yay'


@app.route('/version')
def version():
  return jsonify({'version':'0.465', 'forced':False, 'url':'https://www.jkbx.es'})


@app.route('/join', methods=['POST'])
def join():
  phone_number = request.json['phone_number']
  code = ''.join(random.choice(string.digits) for _ in range(6))
  user = users.find_one_and_update({'phone_number':phone_number}, {'$push':{'code':code}}, upsert=True, return_document=ReturnDocument.AFTER)
  if not 'push_badge' in user:
    create_ashus_songs(phone_number)
    user['push_badge'] = 5
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
  user = users.find_one_and_update({'phone_number':request.json['phone_number']}, {'$addToSet':{'push_token':request.json['push_token']}}, return_document=ReturnDocument.AFTER)
  send_push(user['push_token'], None, user['push_badge'], None)
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

    recipient_user = users.find_one_and_update({'phone_number':recipient}, {'$inc':{'push_badge':1}}, return_document=ReturnDocument.AFTER)
    if recipient_user and 'push_token' in recipient_user:
      send_push(recipient_user['push_token'], push_message, recipient_user['push_badge'], {'share':song}, content_available=True)
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
  if 'push_token' in sender:
    send_push(sender['push_token'], push_message, None, {'listen':song['id']})

  listener = users.find_one_and_update({'phone_number':song['phone_number']}, {'$inc':{'push_badge':-1}}, return_document=ReturnDocument.AFTER)
  if 'push_token' in listener:
    send_push(listener['push_token'], None, listener['push_badge'], None)

  return jsonify({'success':True})


@app.route('/love', methods=['POST'])
@authenticate
def love():
  song = request.json
  songs.update({'_id':ObjectId(request.json['id'])}, {'$set':{'love':True, 'updated':timestamp()}})

  push_message = song['lover_name'] + ' loved ' + song['title'] + ' by ' + song['artist'] + ' :)'
  sender = users.find_one({'phone_number':song['sender']})
  if 'push_token' in sender:
    send_push(sender['push_token'], push_message, None, {'listen':song['id']})

  return jsonify({'success':True})


def send_sms(phone_number, message):
  p = multiprocessing.Process(target=send_sms_background, args=(phone_number, message))
  p.start()

def send_sms_background(phone_number, message):
  twilio.messages.create(to=phone_number, from_='+16502521370', body=message)

def send_sms_engage(phone_number, message):
  p = multiprocessing.Process(target=send_sms_engage_background, args=(phone_number, message))
  p.start()

def send_sms_engage_background(phone_number, message):
  twilio.messages.create(to=phone_number, from_='+14153013881', body=message)

#TODO lookup numbers on twilio
# https://www.twilio.com/lookup

#TODO add better error handling if user provided a bad number
# def send_sms_safe(phone_number, message):
#   try:
#     twilio.messages.create(to=phone_number, from_='+16502521370', body=message)
#   except twilio.TwilioRestException as e:
#     return e


def send_push(tokens, text, badge, data, content_available=False):
  p = multiprocessing.Process(target=send_push_background, args=(tokens, text, badge, data, content_available))
  p.start()

def send_push_background(tokens, text, badge, data, content_available):
  apns = APNs(use_sandbox=False, cert_file='static/JukeboxBetaPush.pem', key_file='static/JukeboxBetaPush.pem')
  frame = Frame()
  identifier = 1
  expiry = time.time()+3600
  priority = 10
  for device_token in tokens:
    sound = None
    if text:
      sound = "default"
    if not data:
      data = {}
    payload = Payload(alert=text, sound=sound, badge=badge, custom=data, content_available=content_available)
    frame.add_item(device_token, payload, identifier, expiry, priority)
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
  date = timestamp()
  song = songs.find_one({'recipient':recipient}, sort=[('date', 1)])
  if song: #if user has already been sent a song, make created songs older
      date = song['date'] - 100
  for song in ashus_songs:
    song['sender'] = 'Ashu'
    song['recipient'] = recipient
    song['date'] = date+i
    song['updated'] = song['date']
    i+=1
  songs.insert(ashus_songs)
