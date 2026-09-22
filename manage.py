import argparse
import getpass
import secrets
import time
from pathlib import Path

from werkzeug.security import generate_password_hash

from multithreader import create_app
from multithreader.db import get_db, transaction


def seed_demo(app):
    if app.config['MODE'] != 'demo':
        raise ValueError('Only available in demo mode')
    with transaction() as conn:
        for letter in 'ABC':
            conn.execute('''INSERT INTO accounts(external_id,username,label,token,expires_at,refreshed_at)
                VALUES (?,?,?,?,?,?) ON CONFLICT(external_id) DO UPDATE SET
                token=excluded.token,expires_at=excluded.expires_at,status='connected' ''',
                (f'demo-{letter}', f'brand_{letter.lower()}', f'品牌 {letter}',
                 app.extensions['cipher'].encrypt(b'demo-token').decode(), time.time() + 60 * 86400, time.time()))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['init', 'create-user', 'reset-password', 'seed-demo'])
    parser.add_argument('--username')
    parser.add_argument('--admin', action='store_true')
    parser.add_argument('--generate-password', action='store_true', help='Save a generated password in instance/initial-login.txt')
    args = parser.parse_args()
    app = create_app()
    with app.app_context():
        if args.command == 'seed-demo':
            seed_demo(app)
        elif args.command == 'init' and get_db().execute('SELECT 1 FROM users').fetchone():
            print('Already initialized. Use create-user or reset-password.')
            return
        else:
            name = (args.username or input('Internal username: ')).strip()
            if not name or len(name) > 80:
                raise SystemExit('Username must be 1-80 characters.')
            password = secrets.token_urlsafe(18) if args.generate_password else getpass.getpass('Password (at least 12 characters): ')
            if len(password) < 12 or (not args.generate_password and password != getpass.getpass('Confirm password: ')):
                raise SystemExit('Passwords must match and contain at least 12 characters.')
            if args.command == 'reset-password':
                changed = get_db().execute('UPDATE users SET password_hash=?,auth_version=auth_version+1 WHERE username=? AND deleted_at IS NULL', (generate_password_hash(password), name)).rowcount
                if not changed:
                    raise SystemExit('User not found.')
            else:
                if get_db().execute('SELECT 1 FROM users WHERE username=?', (name,)).fetchone():
                    raise SystemExit('Username already exists.')
                get_db().execute('INSERT INTO users(username,password_hash,is_admin) VALUES (?,?,?)',
                                 (name, generate_password_hash(password), int(args.admin or args.command == 'init')))
            if args.command == 'init' and app.config['MODE'] == 'demo':
                seed_demo(app)
            if args.generate_password:
                path = Path(app.config['DATA_DIR']) / 'initial-login.txt'
                path.write_text(f'Mode: {app.config["MODE"]}\nUsername: {name}\nPassword: {password}\nChange the password after login, then delete this file.\n', encoding='utf-8')
                print('Initial credentials saved to instance/initial-login.txt (not displayed).')
        print(f'Ready ({app.config["MODE"]}). Run python app.py and python worker.py in separate terminals.')


if __name__ == '__main__':
    main()
