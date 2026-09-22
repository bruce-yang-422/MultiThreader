import hashlib
import io
import json
import secrets
import time
import uuid
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

import cloudinary
import cloudinary.uploader
from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.security import check_password_hash, generate_password_hash

from . import User
from .db import get_db, transaction
from .threads import APIError, ThreadsAPI

bp = Blueprint('web', __name__)


def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return fn(*args, **kwargs)
    return wrapped


def cipher():
    return current_app.extensions['cipher']


def allowed_accounts():
    sql = 'SELECT id,external_id,username,label,avatar,status,expires_at FROM accounts'
    if current_user.is_admin:
        return get_db().execute(sql + ' ORDER BY id').fetchall()
    return get_db().execute(sql + ' WHERE id IN (SELECT account_id FROM members WHERE user_id=?) ORDER BY id',
                            (current_user.id,)).fetchall()


def can_use(account_id, user_id=None):
    user_id = user_id or current_user.id
    user = get_db().execute('SELECT is_admin FROM users WHERE id=? AND deleted_at IS NULL', (user_id,)).fetchone()
    return bool(user and (user['is_admin'] or get_db().execute(
        'SELECT 1 FROM members WHERE user_id=? AND account_id=?', (user_id, account_id)).fetchone()))


def own_batch(batch_id):
    row = get_db().execute('SELECT * FROM batches WHERE id=?', (batch_id,)).fetchone()
    if not row or (row['user_id'] != current_user.id and not current_user.is_admin):
        abort(404)
    return row


def json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError('資料格式不正確。')
    return data


@bp.errorhandler(ValueError)
def invalid(error):
    return jsonify(error=str(error)), 400


@bp.app_context_processor
def common():
    def asset_url(filename):
        version = (Path(current_app.static_folder) / filename).stat().st_mtime_ns
        return url_for('static', filename=filename, v=version)
    return {'mode': current_app.config['MODE'], 'asset_url': asset_url}


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('web.compose'))
    if request.method == 'POST':
        name = request.form.get('username', '').strip()[:100]
        now = time.time()
        with transaction() as conn:
            attempt = conn.execute('SELECT * FROM login_attempts WHERE username=?', (name,)).fetchone()
            if attempt and attempt['attempts'] >= 8 and attempt['until_at'] > now:
                flash('嘗試次數過多，請在 15 分鐘後再試。', 'error')
                return render_template('login.html'), 429
            row = conn.execute('SELECT * FROM users WHERE username=? AND deleted_at IS NULL', (name,)).fetchone()
            if row and check_password_hash(row['password_hash'], request.form.get('password', '')):
                conn.execute('DELETE FROM login_attempts WHERE username=?', (name,))
                session.clear()
                login_user(User(row))
                session['auth_version'] = row['auth_version']
                session.permanent = True
                return redirect(url_for('web.compose'))
            count = attempt['attempts'] + 1 if attempt and attempt['until_at'] > now else 1
            conn.execute('INSERT OR REPLACE INTO login_attempts VALUES (?,?,?)', (name, count, now + 900))
        flash('帳號或密碼不正確。', 'error')
    has_users = bool(get_db().execute('SELECT 1 FROM users LIMIT 1').fetchone())
    return render_template('login.html', has_users=has_users)


@bp.post('/logout')
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for('web.login'))


@bp.route('/password', methods=['GET', 'POST'])
@login_required
def password():
    if request.method == 'POST':
        row = get_db().execute('SELECT * FROM users WHERE id=?', (current_user.id,)).fetchone()
        value = request.form.get('new_password', '')
        if not check_password_hash(row['password_hash'], request.form.get('old_password', '')):
            flash('目前密碼不正確。', 'error')
        elif len(value) < 12 or value != request.form.get('confirm_password'):
            flash('新密碼需至少 12 字元，且兩次輸入一致。', 'error')
        else:
            with transaction() as conn:
                conn.execute('UPDATE users SET password_hash=?,auth_version=auth_version+1 WHERE id=?', (generate_password_hash(value), current_user.id))
                session['auth_version'] = conn.execute('SELECT auth_version FROM users WHERE id=?', (current_user.id,)).fetchone()[0]
            flash('密碼已更新。若曾產生初始登入檔，可刪除該檔案。')
            return redirect(url_for('web.compose'))
    return render_template('password.html')


@bp.get('/compose')
@bp.get('/')
@login_required
def compose():
    return render_template('index.html', accounts=[dict(r) for r in allowed_accounts()])


@bp.get('/accounts')
@login_required
def accounts():
    users = get_db().execute('SELECT id,username,is_admin FROM users WHERE deleted_at IS NULL ORDER BY id').fetchall() if current_user.is_admin else []
    members = [(r['user_id'], r['account_id']) for r in get_db().execute('SELECT * FROM members')] if current_user.is_admin else []
    configured = all(current_app.config[k] for k in ('THREADS_APP_ID', 'THREADS_APP_SECRET', 'THREADS_REDIRECT_URI'))
    return render_template('accounts.html', accounts=allowed_accounts(), users=users, members=members, configured=configured)


@bp.post('/accounts/<int:account_id>')
@admin_required
def update_account(account_id):
    row = get_db().execute('SELECT * FROM accounts WHERE id=?', (account_id,)).fetchone()
    if not row:
        abort(404)
    with transaction() as conn:
        if request.form.get('action') == 'disconnect':
            conn.execute("UPDATE accounts SET token='',status='disconnected' WHERE id=?", (account_id,))
            conn.execute("UPDATE jobs SET status='failed',error='帳號已解除連結。',updated_at=? WHERE account_id=? AND status='pending'", (time.time(), account_id))
            flash('已解除連結；已送至平台的操作可能仍會完成。')
        else:
            label = request.form.get('label', '').strip()[:80]
            if not label:
                abort(400)
            conn.execute('UPDATE accounts SET label=? WHERE id=?', (label, account_id))
            conn.execute('DELETE FROM members WHERE account_id=?', (account_id,))
            for uid in set(request.form.getlist('members')):
                if not conn.execute('SELECT 1 FROM users WHERE id=? AND deleted_at IS NULL', (uid,)).fetchone():
                    abort(400)
                conn.execute('INSERT INTO members VALUES (?,?)', (uid, account_id))
            flash('帳號名稱與操作權限已更新。')
    return redirect(url_for('web.accounts'))


@bp.get('/users')
@admin_required
def users():
    rows = get_db().execute('SELECT id,username,is_admin FROM users WHERE deleted_at IS NULL ORDER BY id').fetchall()
    members = [(r['user_id'], r['account_id']) for r in get_db().execute('SELECT * FROM members')]
    return render_template('users.html', users=rows, members=members)


@bp.post('/users')
@admin_required
def add_user():
    name = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    if not name or len(name) > 80 or len(password) < 12:
        flash('請填入帳號及至少 12 字元密碼。', 'error')
    elif get_db().execute('SELECT 1 FROM users WHERE username=?', (name,)).fetchone():
        flash('這個內部登入帳號已存在，請查看下方「同事的後台登入帳號」清單。若要連結 Threads，請前往「Threads 發文帳號」頁面。', 'error')
    else:
        get_db().execute('INSERT INTO users(username,password_hash) VALUES (?,?)', (name, generate_password_hash(password)))
        flash('同事登入帳號已建立並列於下方清單。連結 Threads 後，可在各帳號指派操作權限。')
    return redirect(url_for('web.users'))


@bp.route('/users/<int:user_id>/password', methods=['GET', 'POST'])
@admin_required
def reset_user_password(user_id):
    user = get_db().execute('SELECT id,username,is_admin FROM users WHERE id=? AND deleted_at IS NULL', (user_id,)).fetchone()
    if not user:
        abort(404)
    if user_id == current_user.id:
        return redirect(url_for('web.password'))
    if request.method == 'POST':
        value = request.form.get('new_password', '')
        if len(value) < 12 or value != request.form.get('confirm_password'):
            flash('新密碼需至少 12 字元，且兩次輸入一致。', 'error')
        else:
            with transaction() as conn:
                changed = conn.execute('''UPDATE users SET password_hash=?,auth_version=auth_version+1
                                          WHERE id=? AND deleted_at IS NULL''', (generate_password_hash(value), user_id)).rowcount
                if not changed:
                    abort(404)
                conn.execute('DELETE FROM login_attempts WHERE username=?', (user['username'],))
                conn.execute('DELETE FROM oauth_pending WHERE user_id=?', (user_id,))
            flash(f'已重設「{user["username"]}」的密碼，舊登入已失效。請將新密碼告知本人。')
            return redirect(url_for('web.users'))
    return render_template('user_action.html', user=user, action='password')


@bp.route('/users/<int:user_id>/delete', methods=['GET', 'POST'])
@admin_required
def delete_user(user_id):
    with transaction() as conn:
        user = conn.execute('SELECT id,username,is_admin FROM users WHERE id=? AND deleted_at IS NULL', (user_id,)).fetchone()
        if not user:
            abort(404)
        if user_id == current_user.id:
            flash('無法刪除目前登入的帳號。', 'error')
            return redirect(url_for('web.users'))
        if user['is_admin'] and conn.execute('SELECT COUNT(*) FROM users WHERE is_admin=1 AND deleted_at IS NULL').fetchone()[0] <= 1:
            flash('必須保留至少一位管理者。', 'error')
            return redirect(url_for('web.users'))
        if request.method == 'POST':
            if request.form.get('confirm_username') != user['username']:
                flash('請輸入要刪除的完整登入帳號，以確認操作。', 'error')
            else:
                # Keep the user ID for audit history; free the login name for a new account.
                conn.execute('''UPDATE users SET deleted_at=?,deleted_username=username,username=?,
                                password_hash='',auth_version=auth_version+1 WHERE id=?''',
                             (time.time(), f'__deleted_{user_id}_{uuid.uuid4().hex}', user_id))
                conn.execute('DELETE FROM members WHERE user_id=?', (user_id,))
                conn.execute('DELETE FROM oauth_pending WHERE user_id=?', (user_id,))
                conn.execute('DELETE FROM login_attempts WHERE username=?', (user['username'],))
                conn.execute('''UPDATE jobs SET status='failed',error='提交者的登入帳號已刪除，停止發布。',updated_at=?
                                WHERE status='pending' AND batch_id IN (SELECT id FROM batches WHERE user_id=?)''', (time.time(), user_id))
                flash(f'已刪除「{user["username"]}」的登入與操作權限，歷史發文紀錄保留。')
                return redirect(url_for('web.users'))
    return render_template('user_action.html', user=user, action='delete')


@bp.post('/threads/connect')
@admin_required
def connect():
    if current_app.config['MODE'] == 'demo':
        flash('模擬模式使用 A／B／C 測試帳號，正式連結請切換 live 模式。')
        return redirect(url_for('web.accounts'))
    if not all(current_app.config[k] for k in ('THREADS_APP_ID', 'THREADS_APP_SECRET', 'THREADS_REDIRECT_URI')):
        flash('請先依設定指南填入 Meta App 設定。', 'error')
        return redirect(url_for('web.accounts'))
    if urlparse(current_app.config['THREADS_REDIRECT_URI']).scheme != 'https':
        flash('Threads 回呼網址必須使用 HTTPS。', 'error')
        return redirect(url_for('web.accounts'))
    state = secrets.token_urlsafe(32)
    expected = request.form.get('external_id', '')
    session['oauth'] = {'state': state, 'time': time.time(), 'expected': expected}
    # A form POST followed by an external 302 is blocked by form-action 'self'
    # in Chromium. Complete the POST locally; navigate as a separate page action.
    return render_template('oauth_redirect.html', authorization_url=ThreadsAPI(current_app.config).authorization_url(state, include_reply=request.form.get('enable_replies') == '1'))


@bp.get('/threads/callback')
@admin_required
def callback():
    oauth = session.pop('oauth', {})
    if current_app.config['MODE'] != 'live' or not oauth or time.time() - oauth.get('time', 0) > 600 or not secrets.compare_digest(oauth.get('state', ''), request.args.get('state', '')):
        flash('授權驗證失敗或已到期，請重新連結。', 'error')
        return redirect(url_for('web.accounts'))
    if request.args.get('error') or not request.args.get('code'):
        flash('授權未完成，可稍後重新連結。', 'error')
        return redirect(url_for('web.accounts'))
    try:
        token, profile = ThreadsAPI(current_app.config).exchange(request.args['code'])
        if oauth.get('expected') and oauth['expected'] != str(profile['id']):
            raise APIError('登入的 Threads 帳號與要重新連結的帳號不同，請切換後再試。')
    except APIError as error:
        flash(str(error), 'error')
        return redirect(url_for('web.accounts'))
    pending_id = str(uuid.uuid4())
    payload = json.dumps({'token': token, 'profile': profile, 'issued': time.time()})
    get_db().execute('DELETE FROM oauth_pending WHERE expires_at<? OR user_id=?', (time.time(), current_user.id))
    get_db().execute('INSERT INTO oauth_pending VALUES (?,?,?,?)',
                     (pending_id, current_user.id, cipher().encrypt(payload.encode()).decode(), time.time() + 600))
    return redirect(url_for('web.confirm_account', pending_id=pending_id))


@bp.route('/threads/confirm/<pending_id>', methods=['GET', 'POST'])
@admin_required
def confirm_account(pending_id):
    row = get_db().execute('SELECT * FROM oauth_pending WHERE id=? AND user_id=? AND expires_at>?',
                           (pending_id, current_user.id, time.time())).fetchone()
    if not row:
        abort(404)
    payload = json.loads(cipher().decrypt(row['payload'].encode()))
    profile, token = payload['profile'], payload['token']
    if request.method == 'POST':
        with transaction() as conn:
            if request.form.get('action') == 'confirm':
                now = time.time()
                conn.execute('''INSERT INTO accounts(external_id,username,label,avatar,token,expires_at,refreshed_at)
                    VALUES (?,?,?,?,?,?,?) ON CONFLICT(external_id) DO UPDATE SET
                    username=excluded.username,avatar=excluded.avatar,token=excluded.token,
                    expires_at=excluded.expires_at,refreshed_at=excluded.refreshed_at,status='connected' ''',
                    (str(profile['id']), profile['username'], profile['username'], profile.get('threads_profile_picture_url', ''),
                     cipher().encrypt(token['access_token'].encode()).decode(), payload['issued'] + float(token['expires_in']), now))
                flash('帳號已連結。之後可直接選取此帳號發文。')
            conn.execute('DELETE FROM oauth_pending WHERE id=?', (pending_id,))
        return redirect(url_for('web.accounts'))
    return render_template('confirm_account.html', profile=profile)


@bp.post('/api/assets')
@login_required
def upload():
    from .media import save_media
    file = request.files.get('image')
    if not file:
        raise ValueError('請選擇圖片或影片。')
    asset_id, url, media_type = save_media(file)
    get_db().execute('INSERT INTO assets(id,user_id,url,filename,created_at,media_type) VALUES (?,?,?,?,?,?)',
                     (asset_id, current_user.id, url, file.filename[:200], time.time(), media_type))
    return jsonify(id=asset_id, url=url, media_type=media_type)


@bp.get('/media/<uuid:asset_id>')
def public_media(asset_id):
    row = get_db().execute('SELECT * FROM assets WHERE id=?', (str(asset_id),)).fetchone()
    if not row:
        abort(404)
    if current_app.config['MODE'] == 'demo' and (not current_user.is_authenticated or
            (row['user_id'] != current_user.id and not current_user.is_admin)):
        abort(404)
    video = row['media_type'] == 'VIDEO'
    path = Path(current_app.config['DATA_DIR']) / 'media' / (str(asset_id) + ('.mp4' if video else '.jpg'))
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype='video/mp4' if video else 'image/jpeg', conditional=True)


@bp.get('/assets/<asset_id>')
@login_required
def asset_image(asset_id):
    row = get_db().execute('SELECT * FROM assets WHERE id=?', (asset_id,)).fetchone()
    if not row or (row['user_id'] != current_user.id and not current_user.is_admin) or current_app.config['MODE'] != 'demo':
        abort(404)
    return send_file(Path(current_app.config['DATA_DIR']) / 'demo-images' / f'{row["id"]}.jpg', mimetype='image/jpeg')


def validate_payload(data, draft=False):
    mode, cards = data.get('mode'), data.get('cards')
    if mode not in ('same', 'different') or not isinstance(cards, list) or not 1 <= len(cards) <= 20:
        raise ValueError('請選擇模式，並建立 1–20 張圖文卡片。')
    if mode == 'same' and len(cards) != 1:
        raise ValueError('同篇多帳號模式只使用一張圖文卡片。')
    result = []
    for card in cards:
        if not isinstance(card, dict) or not isinstance(card.get('text', ''), str):
            raise ValueError('圖文資料不正確。')
        text = card.get('text', '').strip()
        if len(text) > (10000 if draft else 500):
            raise ValueError('文字超過長度限制；發布上限為 500 字元，請修改後再試。')
        asset_ids = card.get('asset_ids', [card['asset_id']] if card.get('asset_id') else [])
        if not isinstance(asset_ids, list) or len(asset_ids) > 20 or any(not isinstance(i, str) or not i for i in asset_ids):
            raise ValueError('每篇最多 20 張圖片，請重新選擇素材。')
        if len(set(asset_ids)) != len(asset_ids):
            raise ValueError('同一張素材不可重複選取。')
        media = []
        for aid in asset_ids:
            asset = get_db().execute('SELECT * FROM assets WHERE id=? AND user_id=?', (aid, current_user.id)).fetchone()
            if not asset:
                raise ValueError('素材不存在或不屬於你，請重新上傳。')
            media.append({'id': aid, 'url': asset['url'], 'media_type': asset['media_type']})
        if len(media) > 1 and any(m['media_type'] != 'IMAGE' for m in media):
            raise ValueError('多素材目前只支援圖片；影片請單獨發布。')
        reply = card.get('first_reply', '')
        if not isinstance(reply, str) or len(reply.strip()) > 500:
            raise ValueError('第一則回覆最多 500 字元。')
        reply = reply.strip()
        ids = card.get('accounts', [])
        if not isinstance(ids, list) or len(ids) > 50 or any(type(i) is not int for i in ids):
            raise ValueError('帳號選擇不正確。')
        ids = sorted(set(ids))
        if not draft:
            if not ids or not (text or media):
                raise ValueError('每張卡片都需要內容及至少一個目標帳號。')
            if mode == 'different' and len(ids) != 1:
                raise ValueError('不同篇指定帳號模式，每張卡片請選一個帳號。')
            for account_id in ids:
                account = get_db().execute('SELECT * FROM accounts WHERE id=?', (account_id,)).fetchone()
                if not account or not can_use(account_id):
                    raise ValueError('包含未獲授權操作的帳號。')
                if account['status'] != 'connected' or account['expires_at'] <= time.time():
                    raise ValueError(f'「{account["label"]}」需要重新連結。')
        result.append({'text': text, 'asset_ids': asset_ids, 'media': media,
                       'asset_id': asset_ids[0] if asset_ids else '', 'image_url': media[0]['url'] if media else '',
                       'media_type': 'CAROUSEL' if len(media) > 1 else media[0]['media_type'] if media else 'TEXT',
                       'first_reply': reply, 'accounts': ids})
    return {'mode': mode, 'cards': result}


@bp.route('/api/draft', methods=['GET', 'PUT', 'DELETE'])
@login_required
def draft():
    if request.method == 'PUT':
        payload = validate_payload(json_body(), draft=True)
        get_db().execute('INSERT OR REPLACE INTO drafts VALUES (?,?,?)', (current_user.id, json.dumps(payload), time.time()))
        return jsonify(saved=True)
    if request.method == 'DELETE':
        get_db().execute('DELETE FROM drafts WHERE user_id=?', (current_user.id,))
        return jsonify(deleted=True)
    row = get_db().execute('SELECT payload FROM drafts WHERE user_id=?', (current_user.id,)).fetchone()
    return jsonify(json.loads(row['payload']) if row else None)


@bp.post('/api/batches')
@login_required
def submit_batch():
    data = json_body()
    key = data.get('request_key', '')
    if not isinstance(key, str) or not 16 <= len(key) <= 100:
        raise ValueError('發布識別碼不正確，請重新整理。')
    # Stable hash of submitted user data, not mutable account/asset state.
    digest = hashlib.sha256(json.dumps({'mode': data.get('mode'), 'cards': data.get('cards')}, sort_keys=True).encode()).hexdigest()
    with transaction() as conn:
        prior = conn.execute('SELECT * FROM batches WHERE user_id=? AND request_key=?', (current_user.id, key)).fetchone()
        if prior:
            if prior['payload_hash'] != digest:
                return jsonify(error='此發布識別碼已使用，請重新載入發布頁。'), 409
            return jsonify(id=prior['id'])
        payload = validate_payload(data)
        batch_id = str(uuid.uuid4())
        now = time.time()
        conn.execute('INSERT INTO batches VALUES (?,?,?,?,?,?)', (batch_id, current_user.id, key, digest, payload['mode'], now))
        for index, card in enumerate(payload['cards']):
            for account_id in card['accounts']:
                account = conn.execute('SELECT * FROM accounts WHERE id=?', (account_id,)).fetchone()
                conn.execute('''INSERT INTO jobs(id,batch_id,account_id,card_index,account_label,external_id,text,image_url,updated_at,media_type,media_json,first_reply,reply_status)
                                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                             (str(uuid.uuid4()), batch_id, account_id, index, account['label'], account['external_id'], card['text'], card['image_url'], now, card['media_type'], json.dumps(card['media']), card['first_reply'], 'pending' if card['first_reply'] else 'none'))
    return jsonify(id=batch_id), 201


@bp.get('/history')
@login_required
def history():
    where, params = ('', ()) if current_user.is_admin else (' WHERE b.user_id=?', (current_user.id,))
    rows = get_db().execute('''SELECT b.*,CASE WHEN u.deleted_at IS NULL THEN u.username
        ELSE u.deleted_username || '（已刪除）' END username,COUNT(j.id) total,
        SUM(j.status='success') successful,SUM(j.status='failed') failed,
        SUM(j.reply_status='failed') reply_failed,SUM(j.reply_status='uncertain') reply_uncertain,
        SUM(j.status='uncertain') uncertain, SUM(j.status NOT IN ('success','failed') OR (j.status='success' AND j.reply_status IN ('pending','processing','uncertain'))) protected FROM batches b JOIN users u ON b.user_id=u.id
        JOIN jobs j ON j.batch_id=b.id''' + where + ' GROUP BY b.id ORDER BY b.created_at DESC LIMIT 100', params).fetchall()
    return render_template('history.html', batches=rows)


@bp.route('/history/clear', methods=['GET', 'POST'])
@bp.route('/history/<batch_id>/delete', methods=['GET', 'POST'])
@login_required
def delete_history(batch_id=None):
    if batch_id:
        own_batch(batch_id)
    if request.method == 'POST':
        with transaction() as conn:
            conditions, params = [], []
            if not current_user.is_admin:
                conditions.append('b.user_id=?')
                params.append(current_user.id)
            if batch_id:
                conditions.append('b.id=?')
                params.append(batch_id)
            where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
            rows = conn.execute("""SELECT b.id,
                SUM(j.status NOT IN ('success','failed') OR (j.status='success' AND j.reply_status IN ('pending','processing','uncertain'))) protected
                FROM batches b JOIN jobs j ON j.batch_id=b.id""" + where + ' GROUP BY b.id', params).fetchall()
            eligible = [row['id'] for row in rows if not row['protected']]
            for selected in eligible:
                conn.execute('DELETE FROM jobs WHERE batch_id=?', (selected,))
            # Retain only batch request identifiers to prevent a replay from republishing.
            skipped = len(rows) - len(eligible)
        flash(f'已刪除 {len(eligible)} 筆發文紀錄。Threads 上的貼文保留。' +
              (f'另有 {skipped} 筆待發布、處理中或待確認紀錄已保留。' if skipped else ''))
        return redirect(url_for('web.history'))
    return render_template('delete_history.html', batch_id=batch_id)


@bp.get('/batches/<batch_id>')
@login_required
def batch_page(batch_id):
    own_batch(batch_id)
    return render_template('batch.html', batch_id=batch_id)


@bp.get('/api/batches/<batch_id>')
@login_required
def batch_data(batch_id):
    own_batch(batch_id)
    jobs = [dict(r) for r in get_db().execute('''SELECT id,account_label,card_index,text,image_url,media_type,media_json,first_reply,reply_status,reply_error,reply_post_id,status,error,permalink,attempts,post_id
                                              FROM jobs WHERE batch_id=? ORDER BY card_index,account_id''', (batch_id,))]
    for job in jobs:
        job['media'] = json.loads(job.pop('media_json'))
    heartbeat = get_db().execute("SELECT value FROM runtime WHERE key='heartbeat'").fetchone()
    return jsonify(jobs=jobs, worker_online=bool(heartbeat and time.time() - heartbeat['value'] < 180))


@bp.post('/api/jobs/<job_id>/retry')
@login_required
def retry(job_id):
    with transaction() as conn:
        job = conn.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not job:
            abort(404)
        batch = own_batch(job['batch_id'])
        if not can_use(job['account_id']):
            abort(403)
        if not can_use(job['account_id'], batch['user_id']):
            return jsonify(error='原操作者已無此帳號的發布權限，請重新建立發布。'), 409
        if job['status'] != 'failed':
            return jsonify(error='只能重試已確認失敗的項目。'), 409
        conn.execute("UPDATE jobs SET status='pending',phase='queued',container_id=NULL,error='',updated_at=? WHERE id=?", (time.time(), job_id))
    return jsonify(queued=True)


@bp.post('/api/jobs/<job_id>/reply/<action>')
@login_required
def reply_action(job_id, action):
    with transaction() as conn:
        job = conn.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not job:
            abort(404)
        batch = own_batch(job['batch_id'])
        if not can_use(job['account_id']) or not can_use(job['account_id'], batch['user_id']):
            abort(403)
        if job['status'] != 'success':
            abort(409)
        if action == 'retry' and job['reply_status'] == 'failed':
            conn.execute("UPDATE jobs SET reply_status='pending',reply_phase='queued',reply_error='',reply_container_id=NULL,updated_at=? WHERE id=?", (time.time(), job_id))
        elif action == 'reconcile' and job['reply_status'] == 'uncertain':
            from .publishing import reconcile_reply
            reconcile_reply(job)
        else:
            abort(409)
    return jsonify(ok=True)


@bp.post('/api/jobs/<job_id>/reconcile')
@login_required
def reconcile(job_id):
    from .publishing import reconcile_job
    job = get_db().execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    if not job:
        abort(404)
    own_batch(job['batch_id'])
    if not can_use(job['account_id']):
        abort(403)
    if job['status'] != 'uncertain':
        return jsonify(error='此項目不需要查核。'), 409
    reconcile_job(job)
    return jsonify(checked=True)


@bp.get('/demo/posts/<job_id>')
@login_required
def demo_post(job_id):
    if current_app.config['MODE'] != 'demo':
        abort(404)
    job = get_db().execute("SELECT * FROM jobs WHERE id=? AND status='success'", (job_id,)).fetchone()
    if not job:
        abort(404)
    own_batch(job['batch_id'])
    return render_template('demo_post.html', job=job, media=json.loads(job['media_json']) or ([{'url':job['image_url'],'media_type':job['media_type']}] if job['image_url'] else []))
