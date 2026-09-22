"""Isolated browser-test server. Never uses the user's database or Meta credentials."""
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cryptography.fernet import Fernet
from flask import request
from waitress import serve
from werkzeug.security import generate_password_hash
from manage import seed_demo
from multithreader import create_app
from multithreader.db import get_db
from multithreader.publishing import run_once

temp = tempfile.TemporaryDirectory()
app = create_app(dict(MODE='demo', DATABASE=str(Path(temp.name) / 'ui.db'), DATA_DIR=temp.name,
                      SECRET_KEY='browser-test-only', ENCRYPTION_KEY=Fernet.generate_key().decode(), SESSION_COOKIE_SECURE=False))
with app.app_context():
    get_db().execute('INSERT INTO users(username,password_hash,is_admin) VALUES (?,?,1)',
                     ('ui-test', generate_password_hash('ui-test-password-only')))
    seed_demo(app)


@app.post('/__test__/oauth-mode')
def oauth_mode():
    # Only this isolated test server exposes this route; never registered by create_app.
    app.config.update(MODE=request.json['mode'], THREADS_APP_ID='browser-test-app',
                      THREADS_APP_SECRET='browser-test-secret',
                      THREADS_REDIRECT_URI='https://example.test/threads/callback')
    return {'ok': True}


def work():
    while True:
        with app.app_context():
            run_once()
        time.sleep(0.15)


threading.Thread(target=work, daemon=True).start()
serve(app, host='127.0.0.1', port=5099)
