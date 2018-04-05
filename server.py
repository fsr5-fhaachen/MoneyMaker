from flask import Flask, render_template, render_template_string, g, session, Response, redirect, request, url_for, flash, escape
from functools import wraps
import sqlite3
import hashlib
import locale
import random
import string
import os
import datetime
from PIL import Image
import io
import traceback
import json
import queue

locale.setlocale(locale.LC_ALL, 'de_DE.utf8')

app = Flask(__name__)

app.jinja_env.trim_blocks = True
app.jinja_env.lstrip_blocks = True

config = app.config
config['SECRET_KEY'] = os.urandom(32)


def load_config_file():
	config.from_pyfile('config.py.example', silent=True)
	config.from_pyfile('config.py', silent=True)
	if config['DEBUG']:
		app.jinja_env.auto_reload = True

def init_db():
	db = sqlite3.connect(config['SQLITE_DB'])
	cur = db.cursor()
	with app.open_resource('schema.sql', mode='r') as schema_file:
		cur.executescript(schema_file.read())
	db.commit()
	db.close()

load_config_file()
init_db()

def date_json_handler(obj):
	return obj.isoformat() if hasattr(obj, 'isoformat') else obj

@app.template_global()
def logentrytotext(inputentry, user, html=True):
	entry = {}
	for i in inputentry:
		if inputentry[i] is not None:
			entry[i] = str(escape(inputentry[i]))
			if i in ['oldbalance', 'newbalance', 'user_id', 'parameter']:
				entry[i] = int(entry[i])
		else:
			entry[i] = None
	if entry['method'] in ['transferTo', 'transferFrom']:
		undolink = url_for('api_user_transfer', sender=user['name'], recipient=(useridtoobj(entry['parameter'])['name']), amount=(entry['newbalance'] - entry['oldbalance'])/100, ref=request.url)
	elif entry['method'] in ['buy', 'recharge']:
		newbalance = entry['oldbalance'] - entry['newbalance'] + user['balance']
		undolink = url_for('api_user_balance', name=user['name'], newbalance=newbalance, ref=request.url)
	else:
		undolink = url_for('api_user_balance', name=user['name'], newbalance=entry['oldbalance'], ref=request.url)

	desc = 'something is broken: '+json.dumps(entry, default=date_json_handler)
	if entry['method'] == "buy":
		desc = 'bought {} for {}'.format(itemidtoobj(entry['parameter'])['name'], euro(itemprice(itemidtoobj(entry['parameter']))))
	elif entry['method'] == "recharge":
		desc = 'recharged balance with {}'.format(euro(abs(itemprice(itemidtoobj(entry['parameter'])))))
	elif entry['method'] == "set_balance":
		desc = 'set balance from {} to {}'.format(euro(entry['oldbalance']), euro(entry['newbalance']))
	elif entry['method'] == "transferTo":
		if html:
			desc = 'transfered {} to <a href="{}">{}</a>'.format(euro(entry['oldbalance']-entry['newbalance']), url_for('userpage', id=entry['parameter']), useridtoobj(entry['parameter'])['name'] )
		else:
			desc = 'transfered {} to {}'.format(euro(entry['oldbalance']-entry['newbalance']), useridtoobj(entry['parameter'])['name'] )
		if entry['reason']:
			desc += ' reason: "{}"'.format(entry['reason'])
	elif entry['method'] == "transferFrom":
		if html:
			desc = '<a href="{}">{}</a> transfered {}'.format(url_for('userpage', id=entry['parameter']), useridtoobj(entry['parameter'])['name'], euro(entry['oldbalance']-entry['newbalance']))
		else:
			desc = '{} transfered {}'.format(useridtoobj(entry['parameter'])['name'], euro(entry['oldbalance']-entry['newbalance']))
		if entry['reason']:
			desc += ' reason: "{}"'.format(entry['reason'])

	if html:
		return '<a class="btn btn-default" href="{}"><span class="fa fa-undo"></span></a>{} {}'.format(undolink, entry['time'], desc)
	else:
		return '{} {}'.format(entry['time'], desc)


def get_dbcursor():
	if 'db' not in g:
		g.db = sqlite3.connect(config['SQLITE_DB'], detect_types=sqlite3.PARSE_DECLTYPES)
		g.db.isolation_level = None
	if not hasattr(request, 'db'):
		request.db = g.db.cursor()
	return request.db

@app.teardown_request
def commit_db(*args):
	if hasattr(request, 'db'):
		request.db.close()
		g.db.commit()

@app.teardown_appcontext
def close_db(*args):
	if 'db' in g:
		g.db.close()
		del g.db

def query(operation, *params, delim="sep"):
	cur = get_dbcursor()
	cur.execute(operation, params)
	rows = []
	rows = cur.fetchall()
	res = []
	for row in rows:
		res.append({})
		ptr = res[-1]
		for col, desc in zip(row, cur.description):
			name = desc[0].split('.')[-1].split(':')[0]
			if name == delim:
				ptr = res[-1][col] = {}
				continue
			if type(col) == str:
				col = col.replace('\\n', '\n').replace('\\r', '\r')
			ptr[name] = col
	return res

def modify(operation, *params):
	cur = get_dbcursor()
	cur.execute(operation, params)
	return cur.lastrowid

@app.template_global()
def isadmin(*args):
        return session.get('loggedin', False)

admin_endpoints = []
def admin_required(func):
	admin_endpoints.append(func.__name__)
	@wraps(func)
	def decorator(*args, **kwargs):
		if not isadmin():
			flash('You need to be logged in to do that!')
			return redirect(url_for('login', ref=request.url))

		else:
			return func(*args, **kwargs)
	return decorator

@app.template_filter()
def md5(val):
	return hashlib.md5(val.encode('ascii', 'ignore')).hexdigest()

@app.template_filter()
def itemidtoobj(val):
	return query("SELECT * FROM item where id = ?", val)[0]

@app.template_filter()
def useridtoobj(val):
	return query("SELECT * FROM user where id = ?", val)[0]

@app.template_filter()
def euro(val, symbol=True):
	if symbol:
		return '{:.2f}€'.format(val/100)
	else:
		return '{:.2f}'.format(val/100)

@app.template_filter()
def itemprice(item):
	if not item:
		return -1
	if item['price']:
		return item['price']
	else:
		step = 20
		margin = 0.2
		base = item['purchasingprice']*(1+margin)
		if base % step != 0:
			if base > 0:
				return int(base/step)*step + step
			else:
				return int(base/step)*step - step
		else:
			return base

@app.template_filter()
def itemstock(item):
	return 0

csrf_endpoints = []

def csrf_protect(func):
	csrf_endpoints.append(func.__name__)
	@wraps(func)
	def decorator(*args, **kwargs):
		if '_csrf_token' in request.values:
			token = request.values['_csrf_token']
		elif request.get_json() and ('_csrf_token' in request.get_json()):
			token = request.get_json()['_csrf_token']
		else:
			token = None
		if app.testing:
			return func(*args, **kwargs)
		if not ('_csrf_token' in session) or (session['_csrf_token'] != token ) or not token:
			return 'csrf test failed', 403
		else:
			return func(*args, **kwargs)
	return decorator

@app.url_defaults
def csrf_inject(endpoint, values):
	if not '_csrf_token' in session:
		session['_csrf_token'] = ''.join(random.SystemRandom().choice(string.ascii_letters + string.digits) for _ in range(64))
	if endpoint not in csrf_endpoints or not session.get('_csrf_token'):
		return
	values['_csrf_token'] = session['_csrf_token']

app.jinja_env.globals['navbar'] = []
# iconlib can be 'bootstrap'
# ( see: http://getbootstrap.com/components/#glyphicons )
# or 'fa'
# ( see: http://fontawesome.io/icons/ )
def register_navbar(name, iconlib='bootstrap', icon=None, visible=False):
	def wrapper(func):
		endpoint = func.__name__
		app.jinja_env.globals['navbar'].append((endpoint, name, iconlib, icon, visible))
		return func
	return wrapper

def log_action(userid, old, new, method, parameter, reason=None):
	from email.message import EmailMessage
	import smtplib

	user = useridtoobj(userid)
	if user['allow_logging']:
		query('INSERT INTO "log" (user_id, method, oldbalance, newbalance, parameter, reason) values (?, ?, ?, ?, ?, ?)', userid, method, old, new, parameter, reason)
	if user['transaction_mail']:
		entry = { "user_id": userid, "method": method, "oldbalance": old, "newbalance": new, "parameter": parameter, "reason": reason, "time": datetime.datetime.now() }
		content = '{}\nIf you notice any errors, please contact the admins <admins@aachen.ccc.de>.'.format(logentrytotext(entry, user, html=False))
		msg = EmailMessage()
		msg.set_content(content)
		msg['Subject'] = 'M.U.K.A.S Transaction Notification'
		msg['From'] = 'M.U.K.A.S <noreply@aachen.ccc.de>'
		msg['To'] = "{} <{}>".format(user['name'],user['mail'])
		s = smtplib.SMTP(config['SMTPSERVER'])
		s.send_message(msg)
		s.quit()

@register_navbar('User', icon='user', iconlib='fa', visible=True)
@app.route("/")
def index():
	return render_template('index.html', allusers=query('SELECT * FROM user WHERE deleted=0'))

@register_navbar('Items', icon='list', iconlib='fa', visible=True)
@app.route("/items")
def itemlist():
	return render_template('itemlist.html',
			groups=query('SELECT * FROM "group" ORDER BY sortorder '),
			items=query('SELECT * FROM "item" WHERE deleted=0 or deleted=? ORDER BY name',
				request.values.get('showdeleted', 0) and isadmin()))

@register_navbar('Groups', icon='object-group', iconlib='fa')
@app.route("/groups")
@admin_required
def grouplist():
	return render_template('grouplist.html', groups=query('SELECT * FROM "group" ORDER BY sortorder '))

@app.route("/items/<itemid>", methods=['GET', 'POST'])
@csrf_protect
@admin_required
def edititem(itemid):
	itemid = int(itemid)
	newid = int(itemid)

	if ('name' in request.values):
		args = []
		args.append(request.values.get('name', 'FIXME'))
		args.append(request.values.get('group_id', -1))
		args.append(int(float(request.values.get('purchasingprice', 0))*100))
		if not request.values.get('usecalculated', False):
			args.append(int(float(request.values.get('price', 0))*100))
		else:
			args.append(None)
		args.append(request.values.get('info_public', ''))
		args.append(request.values.get('picture_id', -1))

		if len(query("SELECT * from item WHERE id = ?", itemid)) > 0:
			query("UPDATE item SET name = ?, group_id = ?, purchasingprice = ?, price = ?, info_public = ?, picture_id = ? WHERE id = ?", *args, itemid)
		else:
			newid = modify("INSERT INTO item (name, group_id, purchasingprice, price, info_public, picture_id) VALUES (?, ?, ?, ?, ?, ?)", *args)
	
	if 'action' in request.values:
		if (request.values.get('action', 'save') == 'save'):
			if itemid != newid :
				return redirect(url_for("edititem", itemid=newid))
		else:
			return redirect(url_for("itemlist"))

	if itemid != -1:
		item = query("SELECT * from item WHERE id = ?", itemid)[0]
	else:
		item = None
	pictures = query("SELECT id from pictures")
	return render_template('item.html', item=item, pictures=pictures, groups=query('SELECT * FROM "group" ORDER BY sortorder'))

@app.route("/groups/<groupid>", methods=['GET', 'POST'])
@csrf_protect
@admin_required
def editgroup(groupid):
	groupid = int(groupid)
	newid = int(groupid)

	if ('name' in request.values):
		args = []
		args.append(request.values.get('name', 'FIXME'))
		args.append(request.values.get('sortorder', 0))

		if len(query('SELECT * from "group" WHERE id = ?', groupid)) > 0:
			query('UPDATE "group" SET name = ?, sortorder = ? WHERE id = ?', *args, groupid)
		else:
			newid = modify('INSERT INTO "group" (name, sortorder) VALUES (?, ?)', *args)
	
	if 'action' in request.values:
		if (request.values.get('action', 'save') == 'save'):
			if groupid != newid :
				return redirect(url_for("editgroup", groupid=groupid))
		else:
			return redirect(url_for("grouplist"))
	if groupid != -1:
		group = query('SELECT * from "group" WHERE id = ?', groupid)[0]
	else:
		group = None
	return render_template('group.html', group=group)

@app.route("/u/<name>")
@app.route("/u/<int:id>")
def userpage(name=None, id=None):
	user=query('SELECT * FROM user WHERE (name = ?) or (id = ?)', name, id)
	if len(user) != 1:
		flash('User %s does not exist'%name)
		return redirect(url_for('index'))
	else:
		user = user[0]
	users=query('SELECT * FROM user')
	log=query('SELECT log.* FROM log JOIN user ON log.user_id=user.id WHERE (user.name = ?)  ORDER BY log.time DESC LIMIT 50', user['name'])
	groups=query('SELECT * FROM "group" ORDER BY sortorder ')
	items=query('SELECT * FROM "item" WHERE deleted=0 ')
	return render_template('user.html', user=user, log=log, groups=groups, items=items, users=users )

@app.route("/login", methods=['GET', 'POST'])
def login():
	if request.method == 'GET':
		return render_template('login.html')
	user, pw = request.form.get('user'), request.form.get('password')
	if not valid_credentials(user, pw):
		flash('Login failed!')
		return render_template('login.html')
	session['user'] = user
	session['loggedin'] = True
	session['logindate'] = datetime.datetime.now()
	return redirect(request.values.get('ref', url_for('index')))



def valid_credentials(user, pw):
	from ldap3 import Server, Connection

	if not user or not pw:
		return False

	bindstring = config['LDAP_BINDSTRING_USER'].format(user)
	conn = Connection(Server(config['LDAP_SERVER'], use_ssl=True), bindstring, pw)
	if not conn.bind():
		return False
	conn.search(config['LDAP_GROUPS'], config['LDAP_GROUP_FILTER'].format('admin'), attributes=[config['LDAP_GROUP_MEMBERS_ATTRIBUTE']])
	members = conn.response[0]['attributes'][config['LDAP_GROUP_MEMBERS_ATTRIBUTE']]
	conn.unbind()
	return user in members

@app.route("/logout")
def logout():
	session.pop('user', None)
	session.pop('logindate', None)
	session.pop('loggedin', None)
	return redirect(request.values.get('ref', url_for('index')))

import api
