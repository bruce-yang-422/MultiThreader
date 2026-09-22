"""Threads adapter. No tokens, request URLs or raw API errors are logged.

Flow references: Meta's Threads Postman collection and Insights/threads_client.py.
"""
from urllib.parse import urlencode

import requests


class APIError(Exception):
    def __init__(self, message, *, uncertain=False, reconnect=False):
        super().__init__(message)
        self.uncertain = uncertain
        self.reconnect = reconnect


class ThreadsAPI:
    host = 'https://graph.threads.net'

    def __init__(self, config):
        self.config = config

    def authorization_url(self, state, include_reply=False):
        return 'https://www.threads.net/oauth/authorize?' + urlencode({
            'client_id': self.config['THREADS_APP_ID'],
            'redirect_uri': self.config['THREADS_REDIRECT_URI'],
            'scope': 'threads_basic,threads_content_publish' + (',threads_manage_replies' if include_reply else ''),
            'response_type': 'code', 'state': state,
        })

    def request(self, method, path, *, token=None, publish=False, **params):
        headers = {'Authorization': f'Bearer {token}'} if token else {}
        try:
            response = requests.request(method, self.host + path, headers=headers,
                                        timeout=(10, 30), allow_redirects=False,
                                        **({'params': params} if method == 'GET' else {'data': params}))
        except requests.RequestException:
            raise APIError('連線逾時或中斷，請檢查網路。', uncertain=publish) from None
        try:
            data = response.json()
        except ValueError:
            raise APIError('平台回應無法辨識。', uncertain=publish) from None
        if not isinstance(data, dict):
            raise APIError('平台回應格式不符。', uncertain=publish)
        if not response.ok or 'error' in data:
            error = data.get('error', {})
            code = error.get('code') if isinstance(error, dict) else None
            if code == 190:
                message = '帳號授權已失效，請由管理者重新連結。'
            elif response.status_code == 429 or code in (4, 32, 613):
                message = '平台使用量已達限制，請稍後重試。'
            else:
                message = f'平台未接受此操作（HTTP {response.status_code}）。請檢查權限與素材格式。'
            # Any failed publish response is conservative: an error may follow a commit.
            raise APIError(message, reconnect=code == 190, uncertain=publish)
        return data

    def exchange(self, code):
        short = self.request('POST', '/oauth/access_token', client_id=self.config['THREADS_APP_ID'],
                             client_secret=self.config['THREADS_APP_SECRET'], code=code,
                             grant_type='authorization_code', redirect_uri=self.config['THREADS_REDIRECT_URI'])
        if not short.get('access_token') or not short.get('user_id'):
            raise APIError('授權回應缺少帳號或憑證，請重新連結。')
        long = self.request('GET', '/access_token', grant_type='th_exchange_token',
                            client_secret=self.config['THREADS_APP_SECRET'], access_token=short['access_token'])
        if not long.get('access_token') or not long.get('expires_in'):
            raise APIError('未取得長效授權，請重新連結。')
        profile = self.request('GET', '/me', token=long['access_token'],
                               fields='id,username,threads_profile_picture_url')
        if str(profile.get('id')) != str(short['user_id']):
            raise APIError('授權帳號身分不一致，請重新連結。')
        return long, profile

    def refresh(self, token):
        data = self.request('GET', '/refresh_access_token', token=token, grant_type='th_refresh_token')
        if not data.get('access_token') or not data.get('expires_in'):
            raise APIError('更新授權回應不完整，稍後再試。')
        return data

    def create(self, token, external_id, text, image_url, media_type="IMAGE", is_carousel_item=False, reply_to_id=None):
        params = {'media_type': media_type if image_url else 'TEXT', 'text': text}
        if is_carousel_item:
            params['is_carousel_item'] = 'true'
        if reply_to_id:
            params['reply_to_id'] = reply_to_id
        if image_url:
            params['video_url' if media_type == 'VIDEO' else 'image_url'] = image_url
        data = self.request('POST', f'/{external_id}/threads', token=token, **params)
        if not data.get('id'):
            raise APIError('未取得貼文容器，尚未發布。')
        return str(data['id'])

    def carousel(self, token, external_id, text, children):
        data = self.request('POST', f'/{external_id}/threads', token=token,
                            media_type='CAROUSEL', text=text, children=','.join(children))
        if not data.get('id'):
            raise APIError('未取得輪播容器，尚未發布。')
        return str(data['id'])

    def status(self, token, container_id):
        return self.request('GET', f'/{container_id}', token=token, fields='status').get('status')

    def publish(self, token, external_id, container_id):
        data = self.request('POST', f'/{external_id}/threads_publish', token=token,
                            publish=True, creation_id=container_id)
        if not data.get('id'):
            raise APIError('發布回應缺少貼文 ID，請先查核。', uncertain=True)
        return str(data['id'])

    def permalink(self, token, post_id):
        data = self.request('GET', f'/{post_id}', token=token, fields='permalink')
        url = data.get('permalink', '')
        return url if url.startswith('https://') else ''

