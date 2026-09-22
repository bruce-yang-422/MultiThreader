"""Validated local media, exposed by unguessable asset IDs for Meta fetching."""
import io
import json
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from flask import current_app, url_for
from PIL import Image, ImageOps, UnidentifiedImageError


def save_media(file):
    asset_id = str(uuid.uuid4())
    video = Path(file.filename or '').suffix.lower() in ('.mp4', '.mov')
    limit = (90 if video else 8) * 1024 * 1024
    raw = file.read(limit + 1)
    if not raw or len(raw) > limit:
        raise ValueError('圖片限 8 MB；影片限 90 MB，且不可為空檔案。')
    directory = Path(current_app.config['DATA_DIR']) / 'media'
    directory.mkdir(exist_ok=True)
    if video:
        path = directory / (asset_id + '.mp4')
        path.write_bytes(raw)
        try:
            result = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)],
                                    capture_output=True, timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            data = json.loads(result.stdout)
            streams = data.get('streams', [])
            videos = [s for s in streams if s.get('codec_type') == 'video']
            audios = [s for s in streams if s.get('codec_type') == 'audio']
            if result.returncode or not videos or 'mp4' not in data.get('format', {}).get('format_name', ''):
                raise ValueError('請使用 MP4 或 MOV 影片。')
            if not 0 < float(data['format']['duration']) <= 300:
                raise ValueError('影片長度須在 5 分鐘以內。')
            if any(s.get('codec_name') not in ('h264', 'hevc') for s in videos) or any(s.get('codec_name') != 'aac' for s in audios):
                raise ValueError('影片需使用 H.264／H.265 編碼，音訊需為 AAC；請重新匯出 MP4。')
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, KeyError, TypeError):
            path.unlink(missing_ok=True)
            raise ValueError('無法檢查影片，請確認 FFmpeg 已安裝且影片完整。') from None
        except ValueError:
            path.unlink(missing_ok=True)
            raise
        media_type = 'VIDEO'
    else:
        try:
            image = Image.open(io.BytesIO(raw))
            if image.format not in ('JPEG', 'PNG', 'WEBP') or image.width * image.height > 25_000_000:
                raise ValueError('請使用 2500 萬像素以下的 JPG、PNG 或 WebP。')
            image = ImageOps.exif_transpose(image).convert('RGBA')
            background = Image.new('RGB', image.size, 'white')
            background.paste(image, mask=image.getchannel('A'))
            background.thumbnail((1920, 1920))
            path = directory / (asset_id + '.jpg')
            background.save(path, format='JPEG', quality=90)
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
            raise ValueError('圖片無法讀取，請重新選擇 JPG、PNG 或 WebP。') from None
        media_type = 'IMAGE'
    route = url_for('web.public_media', asset_id=asset_id)
    if current_app.config['MODE'] == 'live':
        base = current_app.config.get('MEDIA_BASE_URL') or current_app.config['THREADS_REDIRECT_URI']
        parsed = urlsplit(base)
        if parsed.scheme != 'https' or not parsed.netloc:
            path.unlink(missing_ok=True)
            raise ValueError('請先設定 HTTPS 回呼網址或 MEDIA_BASE_URL。')
        route = f'https://{parsed.netloc}' + route
    return asset_id, route, media_type
