from server import *
import json

@app.route("/api/user/add", methods=['POST'])
@csrf_protect
def api_user_add():
    ref = request.values.get('ref', None)
    name = request.values.get('name', '')
    if name == '':
        if ref:
            return redirect(ref)
        else:
            return 'name is empty', 422
    mail = request.values.get('mail', '')
    args = []
    args.append(request.values.get('name', ''))
    args.append(mail)
    if request.values.get('transaction_mail', False):
        args.append(True)
    else:
        args.append(False)
    if request.values.get('allow_logging', False):
        args.append(True)
    else:
        args.append(False)
    if request.values.get('sort_by_buycount', False):
        args.append(True)
    else:
        args.append(False)
    try:
        query("INSERT INTO user (name, mail, transaction_mail, allow_logging, sort_by_buycount) VALUES (?, ?, ?, ?, ?)", *args)
    except sqlite3.IntegrityError:
        flash('Username already taken.')
        return redirect(ref)

    if mail != '':
        img_data = load_gravatar(mail)
        if img_data:
            fp = io.BytesIO()
            fp.write(img_data)
            picture_id = import_image(fp)
            if picture_id:
                query('UPDATE user SET picture_id = ? WHERE name = ?', picture_id, name)

    if ref:
        return redirect(ref)
    else:
        return 'OK'

def load_gravatar(mail):
    import hashlib
    import requests

    if not mail or mail == '':
        return None

    mailhash = hashlib.md5(mail.encode()).hexdigest()
    url = 'https://www.gravatar.com/avatar/{}?s=200&d=404'.format(mailhash)
    res = requests.get(url)
    if res.status_code == 404:
        return None
    bytes_ = next(res.iter_content(chunk_size=config['MAX_CONTENT_LENGTH']))
    return bytes_

@app.route("/api/user/<name>/edit", methods=['POST'])
@csrf_protect
def api_user_edit(name):
    ref = request.values.get('ref', None)
    user = query('SELECT * FROM user WHERE name = ?', name)
    if len(user) != 1:
        return "User not found", 404

    args = []
    args.append(request.values.get('name', ''))
    args.append(request.values.get('mail', ''))
    if request.values.get('transaction_mail', False):
        args.append(True)
    else:
        args.append(False)
    if request.values.get('allow_logging', False):
        args.append(True)
    else:
        args.append(False)
    args.append(request.values.get('picture_id', -1, type=int))
    if request.values.get('sort_by_buycount', False):
        args.append(True)
    else:
        args.append(False)
    args.append(name)

    query("UPDATE user SET name = ?, mail = ?, transaction_mail = ?, allow_logging = ?, picture_id = ?, sort_by_buycount = ? WHERE name = ?", *args)

    if ref:
        return redirect(ref)
    else:
        return 'OK'

@app.route("/api/user/<sender>/transfer", methods=['GET', 'POST'])
@csrf_protect
def api_user_transfer(sender):
    ref = request.values.get('ref', None)
    sender = query('SELECT * FROM user WHERE name = ?', sender)
    if not len(sender) == 1:
        if ref:
            flash('Sender not found')
            return redirect(ref)
        else:
            return 'Sender not found', 422
    sender = sender[0]

    recipient = request.values.get('recipient', None)
    recipient = query('SELECT * FROM user WHERE name = ?', recipient)
    if not len(recipient) == 1:
        if ref:
            flash('Recipient not found')
            return redirect(ref)
        else:
            return 'Recipient not found', 422
    recipient = recipient[0]


    amount = int(float(request.values.get('amount', 0))*100)
    reason = request.values.get('reason')

    query('UPDATE user SET balance = balance - ? WHERE id = ?', amount, sender['id'])
    log_action(sender['id'], sender['balance'], sender['balance'] - amount, 'transferTo', recipient['id'], reason)

    query('UPDATE user SET balance = balance + ? WHERE id = ?', amount, recipient['id'])
    log_action(recipient['id'], recipient['balance'], recipient['balance'] + amount, 'transferFrom', sender['id'], reason)

    if ref:
        return redirect(ref)
    else:
        return 'OK'


@app.route("/api/item/<int:itemid>/del")
@csrf_protect
@admin_required
def delitem(itemid):
    query("UPDATE item SET deleted = ? WHERE id = ?", {0:1,1:0}[itemidtoobj(itemid).get('deleted', 0)], itemid)
    return redirect(request.values.get('ref', url_for('itemlist')))

@app.route("/api/img/<imgid>")
@app.route("/api/img/")
def get_img(imgid=None):
    data = query('SELECT data FROM pictures WHERE id = ?', imgid)
    if len(data) == 1:
        r = Response(data[0]['data'], mimetype='image/png')
        r.headers['Cache-Control'] = 'public, max-age=315360000'
        return r
    else:
        return 'Not found', 404

@app.route("/api/img/add", methods=['GET', 'POST'])
@app.route("/api/img/add/<imgid>", methods=['GET', 'POST'])
def api_img_add(imgid=None):
    if (request.method == 'POST') and ('img' in request.files):
        if request.files['img'].filename == '':
            flash('Please select an image')
            return redirect(url_for("api_img_add"))
        newid = import_image(request.files['img'])
        return redirect(url_for("api_img_add",imgid=newid))
    else:
        # TODO why!?!
        if imgid:
            imgid = int(imgid)
        return render_template('imgupload.html', pictures=query("SELECT id from pictures"), selected=imgid)

def import_image(fp):
    img = Image.open(fp)
    tmp = io.BytesIO()
    with img:
        img.load()
        img.thumbnail((200,200) , Image.ANTIALIAS)
        img.save(tmp,format="PNG")
    newid = modify("INSERT INTO pictures (data) values (?)",sqlite3.Binary(tmp.getvalue()))
    return newid

@app.route("/api/user/<name>/buy/<int:itemid>")
@csrf_protect
def api_user_buy(name, itemid):
    user = query('SELECT * FROM user WHERE name = ?', name)[0]
    price = itemprice(itemidtoobj(itemid))
    if user:
        query('UPDATE user SET balance = balance - ? WHERE name = ?', price, name)
        query('UPDATE bought SET count = count + 1 WHERE item_id = ?', itemid)
        usernew = query('SELECT * FROM user WHERE name = ?', name)[0]
        if price > 0:
            log_action(user['id'], user['balance'], usernew['balance'], 'buy', itemid)
        else:
            log_action(user['id'], user['balance'], usernew['balance'], 'recharge', itemid)
    if request.values.get('noref', False):
        return 'OK'
    else:
        ref = request.values.get('ref', None)
        if ref:
            return redirect(ref)
        else:
            return 'OK'

@app.route("/api/user/<name>/balance", methods=['GET', 'POST'])
@csrf_protect
def api_user_balance(name, newbalance=None):
    user = query('SELECT * FROM user WHERE name = ?', name)[0]
    if not newbalance:
        newbalance = request.values.get('newbalance', None)
    if newbalance:
        newbalance = int(newbalance)
        query('UPDATE user SET balance = ? WHERE name = ?', newbalance, name)
        log_action(user['id'], user['balance'], newbalance, 'set_balance', 0)
    else:
        data = query('SELECT balance FROM user WHERE name = ?', name)[0]['balance']
        if request.values.get('formatted', False):
            return euro(data)
        else:
            return str(data)
    ref = request.values.get('ref', None)
    if ref:
        return redirect(ref)
    else:
        return 'OK'

def date_json_handler(obj):
    return obj.isoformat() if hasattr(obj, 'isoformat') else obj

@app.route("/api/user/<name>/log")
def api_user_log(name):
    resulttype = request.values.get('type', 'html')
    log=query('SELECT item.name as itemname, COUNT(log.parameter) AS cu, log.parameter as itemid FROM log JOIN user ON log.user_id=user.id JOIN item ON itemid = item.id WHERE (user.name = ?) GROUP BY log.parameter', name)
    user=query('SELECT * from user WHERE name = ?', name)[0]
    if resulttype == 'json':
        return json.dumps(log, default=date_json_handler)
    else:
        return render_template_string("{% from 'macros.html' import loglist %}{{ loglist(log, user) }}", user=user, log=log)
