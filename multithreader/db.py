import sqlite3
from contextlib import contextmanager

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
 password_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS accounts (
 id INTEGER PRIMARY KEY, external_id TEXT NOT NULL UNIQUE,
 username TEXT NOT NULL, label TEXT NOT NULL, avatar TEXT NOT NULL DEFAULT '',
 token TEXT NOT NULL, expires_at REAL NOT NULL, refreshed_at REAL NOT NULL,
 status TEXT NOT NULL DEFAULT 'connected', refresh_checked REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS members (
 user_id INTEGER NOT NULL REFERENCES users(id),
 account_id INTEGER NOT NULL REFERENCES accounts(id), PRIMARY KEY(user_id,account_id)
);
CREATE TABLE IF NOT EXISTS oauth_pending (
 id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
 payload TEXT NOT NULL, expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS assets (
 id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
 url TEXT NOT NULL, filename TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS drafts (
 user_id INTEGER PRIMARY KEY REFERENCES users(id), payload TEXT NOT NULL,
 updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS batches (
 id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
 request_key TEXT NOT NULL, payload_hash TEXT NOT NULL, mode TEXT NOT NULL,
 created_at REAL NOT NULL, UNIQUE(user_id,request_key)
);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id),
 account_id INTEGER NOT NULL REFERENCES accounts(id), card_index INTEGER NOT NULL,
 account_label TEXT NOT NULL, external_id TEXT NOT NULL,
 text TEXT NOT NULL, image_url TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'pending', phase TEXT NOT NULL DEFAULT 'queued',
 container_id TEXT, post_id TEXT, permalink TEXT,
 error TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
 updated_at REAL NOT NULL, UNIQUE(batch_id,card_index,account_id)
);
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status,updated_at);
CREATE TABLE IF NOT EXISTS runtime (key TEXT PRIMARY KEY,value REAL NOT NULL);
CREATE TABLE IF NOT EXISTS login_attempts (username TEXT PRIMARY KEY, attempts INTEGER NOT NULL, until_at REAL NOT NULL);
"""


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(current_app.config['DATABASE'], timeout=20, isolation_level=None)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys=ON')
        g.db.execute('PRAGMA journal_mode=WAL')
    return g.db


@contextmanager
def transaction():
    conn = get_db()
    conn.execute('BEGIN IMMEDIATE')
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def init_db():
    get_db().executescript(SCHEMA)
    # Additive migration preserves existing users, grants and publication history.
    with transaction() as conn:
        columns = {row['name'] for row in conn.execute('PRAGMA table_info(users)')}
        for name, definition in [('auth_version', 'INTEGER NOT NULL DEFAULT 0'),
                                  ('deleted_at', 'REAL'), ('deleted_username', 'TEXT')]:
            if name not in columns:
                conn.execute(f'ALTER TABLE users ADD COLUMN {name} {definition}')

        for table in ('assets', 'jobs'):
            columns = {row['name'] for row in conn.execute(f'PRAGMA table_info({table})')}
            if 'media_type' not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN media_type TEXT NOT NULL DEFAULT 'IMAGE'")

        columns = {row['name'] for row in conn.execute('PRAGMA table_info(jobs)')}
        for name, definition in [('media_json', "TEXT NOT NULL DEFAULT '[]'"),
                                 ('first_reply', "TEXT NOT NULL DEFAULT ''"),
                                 ('reply_status', "TEXT NOT NULL DEFAULT 'none'"),
                                 ('reply_error', "TEXT NOT NULL DEFAULT ''"),
                                 ('reply_phase', "TEXT NOT NULL DEFAULT 'queued'"),
                                 ('reply_container_id', 'TEXT'), ('reply_post_id', 'TEXT'),
                                 ('reply_attempts', 'INTEGER NOT NULL DEFAULT 0')]:
            if name not in columns:
                conn.execute(f'ALTER TABLE jobs ADD COLUMN {name} {definition}')


def close_db(error=None):
    conn = g.pop('db', None)
    if conn:
        conn.close()
