import io
import json
import tempfile
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch, Mock

from cryptography.fernet import Fernet
from PIL import Image
from werkzeug.security import generate_password_hash

from manage import seed_demo
from multithreader import create_app
from multithreader.db import get_db
from multithreader.publishing import claim_job, publish_job, refresh_due_accounts, run_once
from multithreader.threads import APIError, ThreadsAPI


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = dict(TESTING=True, MODE='demo', DATABASE=str(Path(self.temp.name) / 'test.db'),
                           DATA_DIR=self.temp.name, SECRET_KEY='test-session-secret',
                           ENCRYPTION_KEY=Fernet.generate_key().decode(), WTF_CSRF_ENABLED=False,
                           SESSION_COOKIE_SECURE=False)
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        with self.app.app_context():
            get_db().execute('INSERT INTO users(id,username,password_hash,is_admin) VALUES (1,?,?,1)', ('admin', generate_password_hash('test-password-long')))
            get_db().execute('INSERT INTO users(id,username,password_hash,is_admin) VALUES (2,?,?,0)', ('editor', generate_password_hash('test-password-long')))
            seed_demo(self.app)
            get_db().execute('INSERT INTO members VALUES (2,1)')
        self.login()

    def tearDown(self):
        self.temp.cleanup()

    def login(self, name='admin', client=None):
        return (client or self.client).post('/login', data={'username': name, 'password': 'test-password-long'})

    def payload(self, text='甲', accounts=None, mode='same', cards=None):
        return dict(request_key=str(uuid.uuid4()), mode=mode, cards=cards or [dict(text=text, accounts=accounts or [1, 2, 3], asset_id='')])

    def submit(self, payload=None):
        response = self.client.post('/api/batches', json=payload or self.payload())
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return response.json['id']

    def drain(self):
        with self.app.app_context():
            while run_once():
                pass

    def jobs(self, batch_id):
        return self.client.get('/api/batches/' + batch_id).json['jobs']

    def test_history_delete_preserves_pending_and_replay_protection(self):
        payload = self.payload()
        batch = self.submit(payload)
        self.client.post('/history/' + batch + '/delete')
        self.assertEqual(len(self.jobs(batch)), 3)
        self.drain()
        self.assertEqual(self.client.get('/history/' + batch + '/delete').status_code, 200)
        self.assertEqual(len(self.jobs(batch)), 3)
        self.client.post('/history/' + batch + '/delete')
        self.assertEqual(self.jobs(batch), [])
        replay = self.client.post('/api/batches', json=payload)
        self.assertEqual(replay.json['id'], batch)
        self.assertEqual(self.jobs(batch), [])

    def test_history_clear_permissions_and_uncertain_protection(self):
        admin_batch = self.submit()
        editor = self.app.test_client()
        self.login('editor', editor)
        other = editor.post('/api/batches', json=self.payload(accounts=[1])).json['id']
        self.drain()
        self.assertEqual(editor.post('/history/' + admin_batch + '/delete').status_code, 404)
        with self.app.app_context():
            get_db().execute("UPDATE jobs SET status='uncertain' WHERE batch_id=?", (admin_batch,))
        editor.post('/history/clear')
        self.assertEqual(self.jobs(other), [])
        self.client.post('/history/clear')
        self.assertEqual(len(self.jobs(admin_batch)), 3)

    def test_live_local_media_is_public_and_supports_range(self):
        self.app.config.update(MODE='live', THREADS_REDIRECT_URI='https://example.test/threads/callback')
        buffer = io.BytesIO()
        Image.new('RGBA', (10, 10), (0, 0, 0, 0)).save(buffer, format='PNG')
        result = self.client.post('/api/assets', data={'image': (io.BytesIO(buffer.getvalue()), 'alpha.png')})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['media_type'], 'IMAGE')
        path = '/media/' + result.json['id']
        anonymous = self.app.test_client()
        response = anonymous.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Image.open(io.BytesIO(response.data)).getpixel((0,0)), (255,255,255))
        response.close()
        ranged = anonymous.get(path, headers={'Range':'bytes=0-9'})
        self.assertEqual(ranged.status_code, 206)
        ranged.close()

    def test_video_upload_snapshot_and_api_parameter(self):
        import subprocess
        video = Path(self.temp.name) / 'sample.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=320x240:d=1',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video)], check=True,
                       capture_output=True)
        result = self.client.post('/api/assets', data={'image': (io.BytesIO(video.read_bytes()), 'sample.mp4')})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json['media_type'], 'VIDEO')
        denied = self.app.test_client().get('/media/' + result.json['id'])
        self.assertEqual(denied.status_code, 404)
        batch = self.submit(self.payload(cards=[dict(text='movie',accounts=[1],asset_id=result.json['id'])]))
        self.assertEqual(self.jobs(batch)[0]['media_type'], 'VIDEO')
        api = ThreadsAPI({})
        with patch.object(api, 'request', return_value={'id':'container'}) as call:
            api.create('secret', '123', 'movie', 'https://example.test/movie.mp4', media_type='VIDEO')
            self.assertEqual(call.call_args.kwargs['media_type'], 'VIDEO')
            self.assertIn('video_url', call.call_args.kwargs)
            self.assertNotIn('image_url', call.call_args.kwargs)
        bad = self.client.post('/api/assets', data={'image': (io.BytesIO(b'not a movie'), 'bad.mp4')})
        self.assertEqual(bad.status_code, 400)

    def test_carousel_order_and_first_reply_per_account(self):
        assets = []
        for color in ['red', 'blue']:
            buf = io.BytesIO(); Image.new('RGB',(8,8),color).save(buf,format='PNG')
            assets.append(self.client.post('/api/assets', data={'image':(io.BytesIO(buf.getvalue()),color+'.png')}).json['id'])
        card = dict(text='輪播', asset_ids=list(reversed(assets)), accounts=[1,2], first_reply='補充說明')
        batch = self.submit(self.payload(cards=[card]))
        self.assertEqual([m['id'] for m in self.jobs(batch)[0]['media']], list(reversed(assets)))
        self.drain()
        self.assertTrue(all(j['reply_status']=='success' and j['status']=='success' for j in self.jobs(batch)))
        self.assertEqual(len({j['reply_post_id'] for j in self.jobs(batch)}), 2)
        self.assertEqual(self.client.post('/api/batches',json=self.payload(cards=[dict(card, asset_ids=assets*11)])).status_code,400)
        self.assertEqual(self.client.post('/api/batches',json=self.payload(cards=[dict(card, first_reply='x'*501)])).status_code,400)
        api = ThreadsAPI({})
        with patch.object(api,'request',return_value={'id':'parent'}) as request:
            api.carousel('token','1','text',['child2','child1'])
            self.assertEqual(request.call_args.kwargs['children'],'child2,child1')

    def test_reply_failure_does_not_republish_main_and_uncertain_not_retryable(self):
        from multithreader.publishing import claim_reply, publish_reply
        batch = self.submit(self.payload(cards=[dict(text='主貼文',accounts=[1],first_reply='回覆')]))
        with self.app.app_context():
            publish_job(claim_job())
            self.app.config['MODE']='live'
            api = Mock(); api.create.side_effect=APIError('拒絕建立')
            with patch('multithreader.publishing.ThreadsAPI',return_value=api):
                publish_reply(claim_reply())
            job = get_db().execute('SELECT * FROM jobs WHERE batch_id=?',(batch,)).fetchone()
            self.assertEqual(job['status'],'success'); self.assertEqual(job['reply_status'],'failed')
            post_id = job['post_id']; jid=job['id']
        self.assertEqual(self.client.post('/api/jobs/'+jid+'/reply/retry').status_code,200)
        self.client.post('/history/clear')
        self.assertEqual(len(self.jobs(batch)),1)
        with self.app.app_context():
            api = Mock(); api.create.return_value='reply-container'; api.status.return_value='FINISHED'
            api.publish.side_effect=APIError('逾時',uncertain=True)
            with patch('multithreader.publishing.ThreadsAPI',return_value=api):
                publish_reply(claim_reply())
            self.assertEqual(api.create.call_args.kwargs['reply_to_id'],post_id)
            job=get_db().execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
            self.assertEqual(job['post_id'],post_id); self.assertEqual(job['reply_status'],'uncertain')
        self.assertEqual(self.client.post('/api/jobs/'+jid+'/reply/retry').status_code,409)
        self.assertEqual(self.client.post('/api/jobs/'+jid+'/retry').status_code,409)

    def test_live_carousel_builds_children_then_parent(self):
        batch = self.submit(self.payload(accounts=[1]))
        with self.app.app_context():
            media = [{'id':'one','url':'https://example.test/1.jpg','media_type':'IMAGE'},
                     {'id':'two','url':'https://example.test/2.jpg','media_type':'IMAGE'}]
            get_db().execute("UPDATE jobs SET media_json=?,media_type='CAROUSEL' WHERE batch_id=?", (json.dumps(media),batch))
            self.app.config['MODE']='live'
            api=Mock(); api.create.side_effect=['child1','child2']; api.carousel.return_value='parent'
            api.status.return_value='FINISHED'; api.publish.return_value='post'; api.permalink.return_value='https://example.test/post'
            with patch('multithreader.publishing.ThreadsAPI',return_value=api):
                publish_job(claim_job())
            self.assertEqual(api.create.call_count,2)
            self.assertTrue(all(c.kwargs['is_carousel_item'] for c in api.create.call_args_list))
            self.assertEqual(api.carousel.call_args.args[-1], ['child1','child2'])
            self.assertEqual(api.publish.call_count,1)
            self.assertEqual(api.publish.call_args.args[-1], 'parent')
            self.assertEqual(get_db().execute('SELECT status FROM jobs WHERE batch_id=?',(batch,)).fetchone()[0],'success')

    def test_reply_oauth_scope_is_opt_in(self):
        from urllib.parse import urlparse, parse_qs
        api = ThreadsAPI({'THREADS_APP_ID':'id','THREADS_REDIRECT_URI':'https://example.test/threads/callback'})
        normal = parse_qs(urlparse(api.authorization_url('state')).query)['scope'][0]
        reply = parse_qs(urlparse(api.authorization_url('state', include_reply=True)).query)['scope'][0]
        self.assertNotIn('threads_manage_replies',normal)
        self.assertIn('threads_manage_replies',reply)

    def test_same_post_three_accounts_and_duplicate_submit(self):
        payload = self.payload()
        batch = self.submit(payload)
        repeat = self.client.post('/api/batches', json=payload)
        self.assertEqual(repeat.json['id'], batch)
        self.drain()
        jobs = self.jobs(batch)
        self.assertEqual(len(jobs), 3)
        self.assertEqual([r['status'] for r in jobs], ['success'] * 3)
        self.assertEqual([r['text'] for r in jobs], ['甲'] * 3)
        self.assertEqual([r['attempts'] for r in jobs], [1] * 3)
        self.assertEqual(self.client.post('/api/jobs/' + jobs[0]['id'] + '/retry').status_code, 409)
        payload['cards'][0]['text'] = '不同內容'
        self.assertEqual(self.client.post('/api/batches', json=payload).status_code, 409)

    def image(self, color):
        output = io.BytesIO()
        Image.new('RGB', (30, 20), color).save(output, 'PNG')
        output.seek(0)
        response = self.client.post('/api/assets', data={'image': (output, 'picture.png')})
        self.assertEqual(response.status_code, 200)
        return response.json

    def test_different_images_remain_mapped_and_immutable(self):
        a, b = self.image('red'), self.image('blue')
        payload = self.payload(mode='different', cards=[
            dict(text='甲', accounts=[1], asset_id=a['id']), dict(text='乙', accounts=[2], asset_id=b['id'])])
        batch = self.submit(payload)
        payload['cards'][0]['text'] = '草稿修改'
        self.client.put('/api/draft', json=payload)
        self.drain()
        jobs = self.jobs(batch)
        self.assertEqual([(j['account_label'], j['text'], j['image_url']) for j in jobs],
                         [('品牌 A', '甲', a['url']), ('品牌 B', '乙', b['url'])])

    def test_partial_failure_retries_only_failed_job(self):
        batch = self.submit(self.payload('[模擬失敗]'))
        self.drain()
        jobs = self.jobs(batch)
        self.assertEqual([j['status'] for j in jobs], ['success', 'failed', 'success'])
        self.assertEqual(self.client.post(f'/api/jobs/{jobs[1]["id"]}/retry').status_code, 200)
        self.assertEqual(self.client.post(f'/api/jobs/{jobs[1]["id"]}/retry').status_code, 409)
        self.drain()
        self.assertEqual([j['attempts'] for j in self.jobs(batch)], [1, 2, 1])

    def test_timeout_requires_reconciliation(self):
        batch = self.submit(self.payload('[模擬逾時]', [1]))
        self.drain()
        job = self.jobs(batch)[0]
        self.assertEqual(job['status'], 'uncertain')
        self.assertEqual(self.client.post(f'/api/jobs/{job["id"]}/retry').status_code, 409)
        self.client.post(f'/api/jobs/{job["id"]}/reconcile')
        self.assertEqual(self.jobs(batch)[0]['status'], 'success')
        self.assertEqual(self.jobs(batch)[0]['attempts'], 1)

    def test_permissions_and_assets_are_enforced(self):
        asset = self.image('red')
        batch = self.submit()
        editor = self.app.test_client()
        self.login('editor', editor)
        self.assertEqual(editor.get('/api/batches/' + batch).status_code, 404)
        self.assertEqual(editor.post('/api/batches', json=self.payload(accounts=[2])).status_code, 400)
        self.assertEqual(editor.post('/api/batches', json=self.payload(cards=[dict(text='x',accounts=[1],asset_id=asset['id'])])).status_code, 400)
        self.assertEqual(editor.get(asset['url']).status_code, 404)
        self.assertEqual(editor.post('/threads/connect').status_code, 403)
        good = editor.post('/api/batches', json=self.payload(accounts=[1]))
        self.assertEqual(good.status_code, 201)
        with self.app.app_context():
            get_db().execute('DELETE FROM members WHERE user_id=2')
        self.drain()
        self.assertEqual(self.jobs(good.json['id'])[0]['status'], 'failed')

    def test_accounts_survive_restart_and_tokens_never_exposed(self):
        second = create_app(self.config).test_client()
        self.login(client=second)
        content = second.get('/accounts').get_data(as_text=True)
        self.assertIn('品牌 A', content)
        self.assertNotIn('demo-token', content)
        with self.app.app_context():
            token = get_db().execute('SELECT token FROM accounts LIMIT 1').fetchone()[0]
            self.assertNotEqual(token, 'demo-token')
            self.assertNotIn(token, content)
        response = second.post('/api/batches', json=self.payload())
        self.assertEqual(response.status_code, 201)

    def test_concurrent_claims_and_idempotency(self):
        batch = self.submit()
        def claim():
            with self.app.app_context():
                row = claim_job()
                return row['id'] if row else None
        with ThreadPoolExecutor(max_workers=4) as pool:
            claims = list(pool.map(lambda _: claim(), range(4)))
        self.assertEqual(len(set(c for c in claims if c)), 3)
        self.assertEqual(claims.count(None), 1)

    def test_simultaneous_duplicate_requests_create_one_batch(self):
        payload = self.payload()
        clients = [self.app.test_client(), self.app.test_client()]
        for client in clients:
            self.login(client=client)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda client: client.post('/api/batches', json=payload), clients))
        self.assertEqual(sorted(r.status_code for r in responses), [200, 201])
        self.assertEqual(responses[0].json['id'], responses[1].json['id'])
        self.assertEqual(len(self.jobs(responses[0].json['id'])), 3)

    def test_password_change_requires_current_password(self):
        self.client.post('/password', data={'old_password':'wrong','new_password':'new-password-long','confirm_password':'new-password-long'})
        self.assertEqual(self.login().status_code, 302)
        self.client.post('/password', data={'old_password':'test-password-long','new_password':'new-password-long','confirm_password':'new-password-long'})
        self.client.post('/logout')
        self.assertEqual(self.login().status_code, 200)
        self.assertEqual(self.client.post('/login', data={'username':'admin','password':'new-password-long'}).status_code, 302)

    def test_stale_processing_not_blindly_republished(self):
        batch = self.submit(self.payload(accounts=[1]))
        with self.app.app_context():
            job = claim_job()
            get_db().execute("UPDATE jobs SET updated_at=?,phase='publishing' WHERE id=?", (time.time() - 1000, job['id']))
            self.assertIsNone(claim_job())
        self.assertEqual(self.jobs(batch)[0]['status'], 'uncertain')

    def test_invalid_content_and_image(self):
        self.assertEqual(self.client.post('/api/batches', json=self.payload('x' * 501)).status_code, 400)
        self.assertEqual(self.client.post('/api/batches', json=self.payload(mode='different')).status_code, 400)
        self.assertEqual(self.client.post('/api/assets', data={'image': (io.BytesIO(b'<script>bad</script>'), 'x.png')}).status_code, 400)
        self.assertEqual(self.client.post('/api/batches', json={'cards': 'bad'}).status_code, 400)

    def test_csrf_is_required(self):
        app = create_app({**self.config, 'WTF_CSRF_ENABLED': True})
        client = app.test_client()
        self.assertEqual(client.post('/login', data={'username':'admin','password':'test-password-long'}).status_code, 400)

    def test_oauth_state_and_account_confirmation(self):
        self.app.config.update(MODE='live', THREADS_APP_ID='app', THREADS_APP_SECRET='secret', THREADS_REDIRECT_URI='https://example.test/threads/callback')
        self.client.post('/threads/connect')
        self.client.get('/threads/callback?state=wrong&code=secret-code')
        with patch.object(ThreadsAPI, 'exchange') as exchange:
            self.client.get('/threads/callback?state=wrong&code=secret-code')
            exchange.assert_not_called()
        self.client.post('/threads/connect')
        with self.client.session_transaction() as session:
            state = session['oauth']['state']
        with patch.object(ThreadsAPI, 'exchange', return_value=({'access_token':'private-token','expires_in':5184000}, {'id':'123','username':'brand_real'})):
            response = self.client.get(f'/threads/callback?state={state}&code=code')
        self.assertIn('/threads/confirm/', response.location)
        html = self.client.get(response.location).get_data(as_text=True)
        self.assertIn('brand_real', html)
        self.assertNotIn('private-token', html)
        self.client.post(response.location, data={'action':'confirm'})
        self.assertEqual(self.client.get(response.location).status_code, 404)
        with self.app.app_context():
            account = get_db().execute("SELECT * FROM accounts WHERE external_id='123'").fetchone()
            self.assertEqual(self.app.extensions['cipher'].decrypt(account['token'].encode()), b'private-token')

    def test_refresh_saves_new_token_and_expiry(self):
        self.app.config['MODE'] = 'live'
        with self.app.app_context():
            get_db().execute('UPDATE accounts SET expires_at=?,refreshed_at=? WHERE id=1', (time.time()+3000,time.time()-172800))
            with patch.object(ThreadsAPI, 'refresh', return_value={'access_token':'new-token','expires_in':5184000}):
                refresh_due_accounts()
            row = get_db().execute('SELECT * FROM accounts WHERE id=1').fetchone()
            self.assertEqual(self.app.extensions['cipher'].decrypt(row['token'].encode()), b'new-token')
            self.assertGreater(row['expires_at'], time.time()+50*86400)

    def test_real_adapter_success_and_ambiguous_publish(self):
        batch = self.submit(self.payload(accounts=[1]))
        self.app.config['MODE'] = 'live'
        with self.app.app_context(), patch('multithreader.publishing.ThreadsAPI') as adapter:
            api = adapter.return_value
            api.create.return_value = 'container'
            api.status.return_value = 'FINISHED'
            api.publish.side_effect = APIError('timeout', uncertain=True)
            run_once()
            self.assertEqual(api.publish.call_count, 1)
        self.assertEqual(self.jobs(batch)[0]['status'], 'uncertain')
        batch2 = self.submit(self.payload(accounts=[2]))
        with self.app.app_context(), patch('multithreader.publishing.ThreadsAPI') as adapter:
            api = adapter.return_value
            api.create.return_value = 'container2'
            api.status.return_value = 'FINISHED'
            api.publish.return_value = 'post123'
            api.permalink.side_effect = APIError('details unavailable')
            run_once()
        self.assertEqual(self.jobs(batch2)[0]['status'], 'success')
        self.assertEqual(self.jobs(batch2)[0]['post_id'], 'post123')

    def test_disconnect_prevents_pending_publication(self):
        batch = self.submit()
        self.client.post('/accounts/2', data={'action':'disconnect'})
        self.drain()
        self.assertEqual([j['status'] for j in self.jobs(batch)], ['success','failed','success'])
        with self.app.app_context():
            self.assertEqual(get_db().execute('SELECT token FROM accounts WHERE id=2').fetchone()[0], '')

    def test_admin_reset_password_revokes_sessions_and_keeps_grants(self):
        editor = self.app.test_client()
        self.login('editor', editor)
        response = self.client.post('/users/2/password', data={'new_password':'replacement-password','confirm_password':'replacement-password'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(editor.get('/api/draft').status_code, 401)
        self.assertEqual(editor.post('/login', data={'username':'editor','password':'test-password-long'}).status_code, 200)
        self.assertEqual(editor.post('/login', data={'username':'editor','password':'replacement-password'}).status_code, 302)
        self.assertEqual(editor.post('/api/batches', json=self.payload(accounts=[1])).status_code, 201)
        with self.app.app_context():
            self.assertTrue(get_db().execute('SELECT 1 FROM members WHERE user_id=2 AND account_id=1').fetchone())

    def test_delete_user_preserves_history_and_cancels_waiting_jobs(self):
        editor = self.app.test_client()
        self.login('editor', editor)
        published = editor.post('/api/batches', json=self.payload(accounts=[1])).json['id']
        self.drain()
        pending = editor.post('/api/batches', json=self.payload(accounts=[1])).json['id']
        self.assertEqual(self.client.post('/users/2/delete', data={'confirm_username':'wrong'}).status_code, 200)
        self.assertEqual(editor.get('/api/draft').status_code, 200)
        self.assertEqual(self.client.post('/users/2/delete', data={'confirm_username':'editor'}).status_code, 302)
        self.assertEqual(editor.get('/api/draft').status_code, 401)
        self.assertEqual(editor.post('/login', data={'username':'editor','password':'test-password-long'}).status_code, 200)
        self.assertEqual(self.jobs(published)[0]['status'], 'success')
        self.assertEqual(self.jobs(pending)[0]['status'], 'failed')
        self.assertIn('editor（已刪除）', self.client.get('/history').get_data(as_text=True))
        self.assertEqual(self.client.post('/api/jobs/' + self.jobs(pending)[0]['id'] + '/retry').status_code, 409)
        self.drain()
        self.assertEqual(self.jobs(pending)[0]['status'], 'failed')
        self.client.post('/users', data={'username':'editor','password':'another-password-long'})
        # Reusing a login name must never resurrect the deleted user's session or grants.
        self.assertEqual(editor.get('/api/draft').status_code, 401)
        fresh = self.app.test_client()
        self.assertEqual(fresh.post('/login', data={'username':'editor','password':'another-password-long'}).status_code, 302)
        self.assertEqual(fresh.post('/api/batches', json=self.payload(accounts=[1])).status_code, 400)
        with self.app.app_context():
            self.assertEqual(get_db().execute('SELECT COUNT(*) FROM accounts').fetchone()[0], 3)
            self.assertFalse(get_db().execute('SELECT 1 FROM members WHERE user_id=2').fetchone())

    def test_user_management_requires_admin_and_cannot_delete_self(self):
        editor = self.app.test_client()
        self.login('editor', editor)
        for path in ['/users/1/password', '/users/1/delete']:
            self.assertEqual(editor.get(path).status_code, 403)
            self.assertEqual(editor.post(path, data={'confirm_username':'admin'}).status_code, 403)
        self.client.post('/users/1/delete', data={'confirm_username':'admin'})
        self.assertEqual(self.client.get('/accounts').status_code, 200)
        with self.app.app_context():
            self.assertIsNone(get_db().execute('SELECT deleted_at FROM users WHERE id=1').fetchone()[0])

    def test_password_reset_validates_confirmation_and_csrf(self):
        self.client.post('/users/2/password', data={'new_password':'replacement-password','confirm_password':'different'})
        editor = self.app.test_client()
        self.assertEqual(self.login('editor', editor).status_code, 302)
        secure_app = create_app({**self.config, 'WTF_CSRF_ENABLED':True})
        client = secure_app.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = '1'
            session['_fresh'] = True
        self.assertEqual(client.post('/users/2/delete', data={'confirm_username':'editor'}).status_code, 400)
        self.assertEqual(client.post('/users/2/password', data={'new_password':'replacement-password','confirm_password':'replacement-password'}).status_code, 400)


class TransportTests(unittest.TestCase):
    def test_publish_error_is_ambiguous_and_redacted(self):
        response = Mock(ok=False, status_code=500)
        response.json.return_value = {'error': {'message':'SECRET-TOKEN', 'code': 2}}
        with patch('requests.request', return_value=response):
            with self.assertRaises(APIError) as caught:
                ThreadsAPI({}).publish('SECRET-TOKEN', '123', '456')
        self.assertTrue(caught.exception.uncertain)
        self.assertNotIn('SECRET-TOKEN', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
