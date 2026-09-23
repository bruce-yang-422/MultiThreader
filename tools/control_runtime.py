"""Private CLI for the local PowerShell controller. Never prints configuration."""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from multithreader import create_app
from multithreader.control import set_draining, status
from multithreader.db import get_db


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['preflight', 'status', 'drain', 'resume'])
    args = parser.parse_args()
    app = create_app()
    with app.app_context():
        if args.command == 'preflight':
            ready = bool(get_db().execute('SELECT 1 FROM users WHERE is_admin=1 AND deleted_at IS NULL').fetchone())
            print(json.dumps({'ready': ready, 'port': int(os.getenv('PORT', '18473')),
                              'host': os.getenv('HOST', '127.0.0.1')}))
            return 0 if ready else 2
        if args.command in ('drain', 'resume'):
            set_draining(args.command == 'drain')
        print(json.dumps(status()))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        print('{"error":"runtime_unavailable"}')
        sys.exit(1)
