import time
import json

from cryptography.fernet import InvalidToken
from flask import current_app

from .db import get_db, transaction
from .threads import APIError, ThreadsAPI


def update_job(job_id, **values):
    values['updated_at'] = time.time()
    get_db().execute('UPDATE jobs SET ' + ','.join(f'{key}=?' for key in values) + ' WHERE id=?', (*values.values(), job_id))


def account_token(account):
    if not account or account['status'] != 'connected' or not account['token'] or account['expires_at'] <= time.time():
        raise APIError('帳號授權已失效或解除連結，請重新連結。', reconnect=True)
    try:
        return current_app.extensions['cipher'].decrypt(account['token'].encode()).decode()
    except InvalidToken:
        raise APIError('無法解密授權，請檢查金鑰備份或重新連結。', reconnect=True) from None


def refresh_due_accounts():
    if current_app.config['MODE'] == 'demo':
        return
    now = time.time()
    get_db().execute("UPDATE accounts SET status='expired' WHERE status='connected' AND expires_at<=?", (now,))
    rows = get_db().execute("""SELECT * FROM accounts WHERE status='connected' AND expires_at<?
        AND refreshed_at<? AND refresh_checked<?""", (now + 7 * 86400, now - 86400, now - 3600)).fetchall()
    for account in rows:
        get_db().execute('UPDATE accounts SET refresh_checked=? WHERE id=?', (now, account['id']))
        try:
            data = ThreadsAPI(current_app.config).refresh(account_token(account))
            encrypted = current_app.extensions['cipher'].encrypt(data['access_token'].encode()).decode()
            # Do not undo a concurrent disconnect or a newer OAuth authorization.
            get_db().execute('''UPDATE accounts SET token=?,expires_at=?,refreshed_at=?
                               WHERE id=? AND token=? AND status='connected' ''',
                             (encrypted, now + float(data['expires_in']), now, account['id'], account['token']))
        except APIError as error:
            if error.reconnect:
                get_db().execute("UPDATE accounts SET status='expired' WHERE id=? AND token=?", (account['id'], account['token']))


def claim_job():
    now = time.time()
    with transaction() as conn:
        conn.execute("INSERT OR REPLACE INTO runtime VALUES ('heartbeat',?)", (now,))
        # A crashed process may already have sent the publish request. Never resend automatically.
        conn.execute("""UPDATE jobs SET status='uncertain',error='處理中斷，請先查核平台結果。',updated_at=?
                        WHERE status='processing' AND updated_at<?""", (now, now - 600))
        row = conn.execute("SELECT * FROM jobs WHERE status='pending' ORDER BY updated_at,id LIMIT 1").fetchone()
        if not row:
            return None
        conn.execute("UPDATE jobs SET status='processing',attempts=attempts+1,phase='checking',updated_at=? WHERE id=?", (now, row['id']))
        return conn.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone()


def publish_job(job):
    account = get_db().execute('SELECT * FROM accounts WHERE id=?', (job['account_id'],)).fetchone()
    published = False
    try:
        from .web import can_use
        batch = get_db().execute('SELECT user_id FROM batches WHERE id=?', (job['batch_id'],)).fetchone()
        if not can_use(job['account_id'], batch['user_id']):
            raise APIError('操作者已無此帳號的發布權限。')
        token = account_token(account)
        if account['external_id'] != job['external_id']:
            raise APIError('目標帳號身分已變更，請重新建立發布。')
        if current_app.config['MODE'] == 'demo':
            if '[模擬失敗]' in job['text'] and account['external_id'] == 'demo-B' and job['attempts'] == 1:
                raise APIError('模擬：B 帳號首次發布失敗，可重試此項目。')
            if '[模擬逾時]' in job['text'] and job['attempts'] == 1:
                update_job(job['id'], container_id='demo-' + job['id'], phase='publishing')
                raise APIError('模擬：平台回應逾時，請按「查核結果」。', uncertain=True)
            update_job(job['id'], status='success', phase='done', post_id='demo-' + job['id'],
                       permalink='/demo/posts/' + job['id'], error='')
            return
        api = ThreadsAPI(current_app.config)
        update_job(job['id'], phase='creating')
        media = json.loads(job['media_json'])
        if len(media) > 1:
            children = []
            for item in media:
                update_job(job['id'], phase='creating')
                child = api.create(token, job['external_id'], '', item['url'], media_type='IMAGE', is_carousel_item=True)
                wait_ready(api, token, child, job['id'])
                children.append(child)
            container = api.carousel(token, job['external_id'], job['text'], children)
        else:
            container = api.create(token, job['external_id'], job['text'], job['image_url'], media_type=job['media_type'])
        update_job(job['id'], container_id=container, phase='waiting')
        for _ in range(150 if job['media_type'] == 'VIDEO' else 30):
            state = api.status(token, container)
            update_job(job['id'], phase='waiting')
            if state == 'FINISHED':
                break
            if state == 'PUBLISHED':
                raise APIError('平台顯示已發布，請查核結果。', uncertain=True)
            if state in ('ERROR', 'EXPIRED'):
                raise APIError('素材處理失敗或容器到期，尚未送出發布。')
            time.sleep(2)
        else:
            raise APIError('素材處理尚未完成，未送出發布，可稍後重試。')
        # Recheck revocation right before the irreversible request.
        latest = get_db().execute('SELECT * FROM accounts WHERE id=?', (account['id'],)).fetchone()
        token = account_token(latest)
        if not can_use(job['account_id'], batch['user_id']):
            raise APIError('操作者已無此帳號的發布權限。')
        update_job(job['id'], phase='publishing')
        published = True
        post_id = api.publish(token, job['external_id'], container)
        # Persist known success before optional permalink lookup.
        update_job(job['id'], status='success', phase='done', post_id=post_id, error='')
        try:
            update_job(job['id'], permalink=api.permalink(token, post_id))
        except APIError:
            pass
    except APIError as error:
        if error.reconnect and account:
            get_db().execute("UPDATE accounts SET status='expired' WHERE id=? AND token=? AND status='connected'", (account['id'], account['token']))
        update_job(job['id'], status='uncertain' if error.uncertain or published else 'failed', error=str(error))
    except Exception:
        # Do not leak raw network exceptions (which can contain credentials).
        status = get_db().execute('SELECT status FROM jobs WHERE id=?', (job['id'],)).fetchone()['status']
        if status != 'success':
            update_job(job['id'], status='uncertain' if published else 'failed', error='處理中斷，請檢查服務狀態後再試。')


def reconcile_job(job):
    if current_app.config['MODE'] == 'demo' and job['container_id']:
        update_job(job['id'], status='success', phase='done', post_id='demo-' + job['id'],
                   permalink='/demo/posts/' + job['id'], error='模擬查核：已發布。')
        return
    account = get_db().execute('SELECT * FROM accounts WHERE id=?', (job['account_id'],)).fetchone()
    try:
        token = account_token(account)
        api = ThreadsAPI(current_app.config)
        if job['post_id']:
            link = api.permalink(token, job['post_id'])
            update_job(job['id'], status='success', phase='done', permalink=link, error='已取得貼文資料。')
        elif job['container_id'] and api.status(token, job['container_id']) == 'PUBLISHED':
            update_job(job['id'], status='success', phase='done', error='平台確認已發布；連結未回傳，請至 Threads 查看。')
        elif job['phase'] in ('queued', 'checking', 'creating', 'waiting'):
            # Persisted phase proves the publish endpoint was never called.
            update_job(job['id'], status='failed', error='確認尚未進入發布步驟，可重試。')
        else:
            update_job(job['id'], error='仍無法確認發布結果，請至 Threads 核對；為避免重複發文，暫不開放重試。')
    except APIError as error:
        update_job(job['id'], error='查核未完成：' + str(error))


def wait_ready(api, token, container, job_id, reply=False):
    for _ in range(30):
        update_job(job_id, **({'reply_phase': 'waiting'} if reply else {'phase': 'waiting'}))
        state = api.status(token, container)
        if state == 'FINISHED':
            return
        if state in ('ERROR', 'EXPIRED'):
            raise APIError('素材處理失敗，尚未送出發布。')
        if state == 'PUBLISHED':
            raise APIError('平台顯示已發布，請查核結果。', uncertain=True)
        time.sleep(2)
    raise APIError('平台處理逾時，尚未送出發布。')


def claim_reply():
    now = time.time()
    with transaction() as conn:
        conn.execute("UPDATE jobs SET reply_status='uncertain',reply_error='回覆處理中斷，請查核後再操作。',updated_at=? WHERE reply_status='processing' AND updated_at<?", (now, now - 600))
        row = conn.execute("SELECT * FROM jobs WHERE status='success' AND reply_status='pending' ORDER BY updated_at,id LIMIT 1").fetchone()
        if not row:
            return None
        conn.execute("UPDATE jobs SET reply_status='processing',reply_phase='checking',reply_attempts=reply_attempts+1,updated_at=? WHERE id=?", (now, row['id']))
        return conn.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone()


def publish_reply(job):
    sent = False
    try:
        from .web import can_use
        batch = get_db().execute('SELECT user_id FROM batches WHERE id=?', (job['batch_id'],)).fetchone()
        account = get_db().execute('SELECT * FROM accounts WHERE id=?', (job['account_id'],)).fetchone()
        token = account_token(account)
        if not can_use(job['account_id'], batch['user_id']) or account['external_id'] != job['external_id']:
            raise APIError('操作者已無此帳號的發布權限。')
        if not job['post_id']:
            raise APIError('主貼文 ID 尚未取得，無法自動回覆。請至 Threads 確認。')
        if current_app.config['MODE'] == 'demo':
            update_job(job['id'], reply_status='success', reply_phase='done', reply_post_id='demo-reply-' + job['id'], reply_error='')
            return
        api = ThreadsAPI(current_app.config)
        update_job(job['id'], reply_phase='creating')
        container = api.create(token, job['external_id'], job['first_reply'], '', media_type='TEXT', reply_to_id=job['post_id'])
        update_job(job['id'], reply_container_id=container, reply_phase='waiting')
        wait_ready(api, token, container, job['id'], reply=True)
        account = get_db().execute('SELECT * FROM accounts WHERE id=?', (job['account_id'],)).fetchone()
        token = account_token(account)
        if not can_use(job['account_id'], batch['user_id']):
            raise APIError('操作者已無此帳號的發布權限。')
        update_job(job['id'], reply_phase='publishing')
        sent = True
        post_id = api.publish(token, job['external_id'], container)
        update_job(job['id'], reply_status='success', reply_phase='done', reply_post_id=post_id, reply_error='')
    except APIError as error:
        update_job(job['id'], reply_status='uncertain' if sent or error.uncertain else 'failed', reply_error=str(error))
    except Exception:
        update_job(job['id'], reply_status='uncertain' if sent else 'failed', reply_error='回覆處理中斷，請檢查結果。')


def reconcile_reply(job):
    try:
        account = get_db().execute('SELECT * FROM accounts WHERE id=?', (job['account_id'],)).fetchone()
        api = ThreadsAPI(current_app.config)
        if job['reply_post_id'] or (job['reply_container_id'] and api.status(account_token(account), job['reply_container_id']) == 'PUBLISHED'):
            update_job(job['id'], reply_status='success', reply_phase='done', reply_error='平台確認回覆已發布。')
        elif job['reply_phase'] in ('queued', 'checking', 'creating', 'waiting'):
            update_job(job['id'], reply_status='failed', reply_error='確認尚未送出回覆，可單獨重試。')
        else:
            update_job(job['id'], reply_error='仍無法確認回覆，請至 Threads 核對；暫不開放重試以避免重複。')
    except APIError as error:
        update_job(job['id'], reply_error='查核未完成：' + str(error))


def run_once():
    job = claim_job()
    if job:
        publish_job(job)
    reply = claim_reply()
    if reply:
        publish_reply(reply)
    return bool(job or reply)
