#!/usr/bin/env python3
"""Local OOTD review desk. Run: python3 server.py"""
import json
import os
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
FEED = ROOT.parent / 'ootd-monitor.json'
REVIEWS = ROOT / 'reviews.json'
LOCK = threading.Lock()
PORT = 8765

def read(path, fallback):
    return json.loads(path.read_text()) if path.exists() else fallback

def feed():
    data = read(FEED, {})
    today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
    start = today - timedelta(days=29)
    items, seen = [], set()
    for group in ('qualified', 'candidates', 'archive', 'rejected'):
        for original in data.get(group, []):
            item = dict(original)
            key = item['platform'] + ':' + item['post_id']
            if key in seen:
                continue
            seen.add(key)
            try:
                published = datetime.fromisoformat(item.get('published_at', '')).date()
                in_window = start <= published <= today
                expired = published < start
            except (ValueError, TypeError):
                in_window, expired = False, False
            valid = (in_window and isinstance(item.get('likes'), (int, float))
                     and item['likes'] > data.get('rules', {}).get('min_likes_exclusive_by_platform', {}).get(item['platform'], 1000) and item.get('content_verified') is True
                     and item.get('playback_verified') is True
                     and item.get('metrics_verified') is True)
            item.update(key=key, bucket='rejected' if group == 'rejected' else ('qualified' if valid else ('archive' if expired else 'candidates')))
            items.append(item)
    data['items'] = items
    data['window'] = {'from': str(start), 'through': str(today), 'days': 30}
    data['reviews'] = read(REVIEWS, {})
    return data

class Handler(BaseHTTPRequestHandler):
    def permitted(self):
        return self.headers.get('Host') in (f'127.0.0.1:{PORT}', f'localhost:{PORT}')

    def send(self, value, code=200, kind='application/json; charset=utf-8'):
        raw = value.encode() if isinstance(value, str) else json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if not self.permitted():
            return self.send({'error': '仅限本机访问'}, 403)
        path = urlparse(self.path).path
        try:
            if path == '/api/feed':
                return self.send(feed())
            if path == '/':
                return self.send((ROOT / 'index.html').read_text(), kind='text/html; charset=utf-8')
            if path == '/favicon.ico':
                return self.send('', 204)
            return self.send({'error': '页面不存在'}, 404)
        except (OSError, ValueError, KeyError):
            return self.send({'error': '清单暂时不可读取，稍后重试'}, 503)

    def do_POST(self):
        if not self.permitted() or self.headers.get('Origin') != 'http://' + self.headers.get('Host', ''):
            return self.send({'error': '仅允许本机审核页保存'}, 403)
        if self.path != '/api/reviews':
            return self.send({'error': '接口不存在'}, 404)
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length < 8192:
                raise ValueError()
            body = json.loads(self.rfile.read(length))
            if body.get('decision') not in ('shortlist', 'reject', 'none'):
                raise ValueError()
            if body.get('key') not in {i['key'] for i in feed()['items']}:
                raise ValueError()
            note = body.get('note', '')
            if not isinstance(note, str) or len(note) > 2000:
                raise ValueError()
            with LOCK:
                reviews = read(REVIEWS, {})
                reviews[body['key']] = {'decision': body['decision'], 'note': note,
                                       'updated_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()}
                temp = REVIEWS.with_suffix('.tmp')
                temp.write_text(json.dumps(reviews, ensure_ascii=False, indent=2))
                os.replace(temp, REVIEWS)
            self.send({'ok': True})
        except (ValueError, TypeError, KeyError):
            self.send({'error': '审核内容无效'}, 400)
        except OSError:
            self.send({'error': '保存失败，请重试'}, 500)

if __name__ == '__main__':
    print(f'Local: http://127.0.0.1:{PORT}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
