"""Local lifecycle state; the database serializes drain and queue claims."""
import os
import threading
import time

from .db import get_db, transaction


def draining(conn=None):
    row = (conn or get_db()).execute("SELECT value FROM runtime WHERE key='draining'").fetchone()
    return bool(row and row['value'])


def set_draining(value):
    with transaction() as conn:
        conn.execute("INSERT OR REPLACE INTO runtime VALUES ('draining',?)", (int(value),))


def status():
    values = dict(get_db().execute('SELECT key,value FROM runtime'))
    active = get_db().execute("SELECT COUNT(*) FROM jobs WHERE status='processing' OR reply_status='processing'").fetchone()[0]
    return dict(application='MultiThreader', protocol=1, pid=os.getpid(),
                draining=bool(values.get('draining')), active=active,
                worker_online=time.time() - values.get('heartbeat', 0) < 15,
                worker_pid=int(values.get('worker_pid', 0)))


def heartbeat(app, stop):
    # Independent of slow remote API calls; each iteration owns its connection.
    while not stop.is_set():
        with app.app_context():
            with transaction() as conn:
                conn.execute("INSERT OR REPLACE INTO runtime VALUES ('heartbeat',?)", (time.time(),))
                conn.execute("INSERT OR REPLACE INTO runtime VALUES ('worker_pid',?)", (os.getpid(),))
        stop.wait(3)


def start_heartbeat(app):
    stop = threading.Event()
    thread = threading.Thread(target=heartbeat, args=(app, stop), daemon=True)
    thread.start()
    return stop, thread
