import json
import os
import secrets
from datetime import timedelta
from pathlib import Path

from cryptography.fernet import Fernet
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, session
from flask_login import LoginManager, UserMixin
from flask_wtf.csrf import CSRFError, CSRFProtect
from jinja2 import ChoiceLoader, FileSystemLoader

from .db import close_db, get_db, init_db

ROOT = Path(__file__).resolve().parent.parent


class User(UserMixin):
    def __init__(self, row):
        self.id = row['id']
        self.username = row['username']
        self.is_admin = bool(row['is_admin'])


def create_app(overrides=None):
    load_dotenv(ROOT / '.env')
    app = Flask(__name__, template_folder=str(ROOT / 'templates'), static_folder=str(ROOT / 'static'))
    app.jinja_loader = ChoiceLoader([app.jinja_loader, FileSystemLoader(str(ROOT))])
    mode = os.getenv('THREADS_MODE', 'demo')
    if mode not in ('demo', 'live'):
        raise RuntimeError('THREADS_MODE must be demo or live')
    app.config.update(
        MODE=mode, DATABASE=str(ROOT / 'instance' / f'{mode}.sqlite3'),
        DATA_DIR=str(ROOT / 'instance'), MAX_CONTENT_LENGTH=92 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=os.getenv('COOKIE_SECURE', 'false').lower() == 'true',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        WTF_CSRF_TIME_LIMIT=12 * 60 * 60,
        **{k: os.getenv(k, '') for k in ('MEDIA_BASE_URL', 'THREADS_APP_ID', 'THREADS_APP_SECRET', 'THREADS_REDIRECT_URI',
                                       'CLOUDINARY_CLOUD_NAME', 'CLOUDINARY_API_KEY', 'CLOUDINARY_API_SECRET')},
    )
    if overrides:
        app.config.update(overrides)
    directory = Path(app.config['DATA_DIR'])
    directory.mkdir(parents=True, exist_ok=True)
    if not app.config.get('SECRET_KEY'):
        keyfile = directory / 'keys.json'
        try:
            with keyfile.open('x', encoding='utf-8') as f:
                json.dump({'session': secrets.token_hex(32), 'encryption': Fernet.generate_key().decode()}, f)
        except FileExistsError:
            pass
        keys = json.loads(keyfile.read_text(encoding='utf-8'))
        app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY') or keys['session']
        app.config['ENCRYPTION_KEY'] = os.getenv('TOKEN_ENCRYPTION_KEY') or keys['encryption']
    app.extensions['cipher'] = Fernet(app.config['ENCRYPTION_KEY'].encode())
    login = LoginManager(app)
    login.login_view = 'web.login'
    login.login_message = '請先登入多脆客。'

    @login.user_loader
    def load_user(user_id):
        row = get_db().execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        if not row or row['deleted_at'] is not None or session.get('auth_version', 0) != row['auth_version']:
            return None
        return User(row)

    @login.unauthorized_handler
    def unauthorized():
        if request.path.startswith('/api/'):
            return jsonify(error='登入已到期，請重新登入；已保存草稿不會遺失。'), 401
        from flask import redirect, url_for
        return redirect(url_for('web.login'))

    CSRFProtect(app)
    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()

    from .web import bp
    app.register_blueprint(bp)

    @app.get('/healthz')
    def health():
        from .control import status
        state = status()
        # Public response contains no account, settings, or process metadata.
        return jsonify(application=state['application'], protocol=1,
                       instance=os.getenv('MULTITHREADER_INSTANCE', ''),
                       ready=not state['draining'], worker_online=state['worker_online'])

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        if request.path.startswith('/api/'):
            return jsonify(error='頁面驗證已到期，請重新整理後再試。'), 400
        return render_template('error.html', message='頁面驗證已到期，請重新整理後再試。'), 400

    @app.errorhandler(413)
    def too_large(error):
        return jsonify(error='檔案過大，圖片限 8 MB，影片限 90 MB。'), 413

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store' if not request.path.startswith('/static/') else 'public, max-age=3600'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' https: blob: data:; media-src 'self' https: blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    return app
