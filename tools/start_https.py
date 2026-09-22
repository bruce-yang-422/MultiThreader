"""Download verified official cloudflared and start a temporary HTTPS tunnel."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlparse

import requests
from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parent.parent
DIRECTORY = ROOT / 'tools'


def download():
    binary = DIRECTORY / 'cloudflared.exe'
    if binary.exists():
        return binary
    print('Downloading cloudflared from the official Cloudflare GitHub release...', flush=True)
    response = requests.get('https://api.github.com/repos/cloudflare/cloudflared/releases/latest', timeout=30)
    response.raise_for_status()
    asset = next(a for a in response.json()['assets'] if a['name'] == 'cloudflared-windows-amd64.exe')
    expected = asset.get('digest', '')
    if not expected.startswith('sha256:'):
        raise RuntimeError('Official release has no SHA256 digest; download was not started.')
    response = requests.get(asset['browser_download_url'], timeout=(15, 120))
    response.raise_for_status()
    if hashlib.sha256(response.content).hexdigest() != expected.split(':', 1)[1]:
        raise RuntimeError('SHA256 verification failed.')
    binary.write_bytes(response.content)
    print('Official SHA256 verified.', flush=True)
    return binary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--download-only', action='store_true')
    args = parser.parse_args()
    binary = download()
    if args.download_only:
        return
    envfile = ROOT / '.env'
    if not envfile.exists():
        envfile.write_text((ROOT / '.env.example').read_text(encoding='utf-8'), encoding='utf-8')
    settings = dotenv_values(envfile)
    callback_host = urlparse(settings.get('THREADS_REDIRECT_URI') or '').hostname or ''
    if callback_host and not callback_host.endswith('.trycloudflare.com') and callback_host not in ('your-domain.example', 'localhost'):
        raise SystemExit('A fixed HTTPS callback is configured. Use the existing named tunnel; configuration was not changed.')
    port = int(settings.get('PORT') or 18473)
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    process = subprocess.Popen([str(binary), 'tunnel', '--url', f'http://127.0.0.1:{port}',
                                '--no-autoupdate', '--protocol', 'http2'],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               encoding='utf-8', errors='replace', creationflags=flags)
    try:
        with (DIRECTORY / 'tunnel.log').open('w', encoding='utf-8') as log:
            found = False
            for line in process.stdout:
                log.write(line); log.flush()
                match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', line)
                if match and not found:
                    found = True
                    url = match.group(0)
                    set_key(str(envfile), 'THREADS_REDIRECT_URI', url + '/threads/callback')
                    set_key(str(envfile), 'COOKIE_SECURE', 'true')
                    (ROOT / 'instance').mkdir(exist_ok=True)
                    (ROOT / 'instance' / 'https-url.txt').write_text(url, encoding='utf-8')
                    print(f'HTTPS: {url}\nMeta OAuth callback: {url}/threads/callback', flush=True)
                    print('Saved to .env. Restart app.py and use the HTTPS URL for login. Keep this process running.', flush=True)
                if 'Registered tunnel connection' in line:
                    print('HTTPS tunnel connected.', flush=True)
            if not found:
                raise RuntimeError('Tunnel did not provide a URL. See tools/tunnel.log.')
    except KeyboardInterrupt:
        print('Stopping temporary HTTPS tunnel.', flush=True)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == '__main__':
    main()
