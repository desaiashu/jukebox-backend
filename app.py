import os
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
import multiprocessing
from APNSWrapper import APNSNotification
from APNSWrapper import APNSNotificationWrapper
from APNSWrapper import APNSAlert
from APNSWrapper import APNSProperty


if os.environ.get('DEBUG') == 'True':
  DEBUG = True
else:
  DEBUG = False

bugsnag.configure(
  api_key = "1d3ac292042ce89cc96685e16164e61e"
)

MONGO_URL = os.environ.get('MONGOHQ_URL')
if MONGO_URL:
  client = MongoClient(MONGO_URL)
  db = client[urlparse(MONGO_URL).path[1:]]
else:
  client = MongoClient('localhost', 27017)
  db = client['jukebox']

app = Flask(__name__)

if DEBUG:
  app.debug = True
else:
	handle_exceptions(app)

@app.route('/')
def base():
    return 'Welcome to Jambox :)'

@app.route('/bugsnag')
def bugsnag():
	bugsnag.notify(Exception("Test Error"))
	return 'yay'

@app.route('/except')
def exceptit():
	return 's'+5
