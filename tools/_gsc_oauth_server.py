# -*- coding: utf-8 -*-
"""一次性本地回环服务：只捕获 Google OAuth 授权码（换令牌在 GitHub Actions 内完成）。
用完即删。"""
import os
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

PORT = 8734
CODE_FILE = os.path.join(os.path.dirname(__file__), '..', '.gsc_code')

HTML_OK = ('<html><body style="font-family:sans-serif;padding:40px">'
           '<h2>授权码已捕获 ✓</h2><p>回到终端继续操作，可以关闭此页面。</p></body></html>')
HTML_ERR = '<html><body style="font-family:sans-serif;padding:40px"><h2>授权失败</h2><pre>{}</pre></body></html>'


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        code = (q.get('code') or [None])[0]
        err = (q.get('error') or [None])[0]
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        if code:
            with open(CODE_FILE, 'w', encoding='utf-8') as f:
                f.write(code)
            subprocess.run(['clip.exe'], input=code.encode('utf-8'), check=False)
            print(f'CODE CAPTURED (len={len(code)}), copied to clipboard', flush=True)
            self.wfile.write(HTML_OK.encode('utf-8'))
        else:
            print(f'AUTH ERROR: {err}', flush=True)
            self.wfile.write(HTML_ERR.format(err or 'no code').encode('utf-8'))

    def log_message(self, *a):
        pass


server = HTTPServer(('127.0.0.1', PORT), Handler)
print(f'listening on http://localhost:{PORT} ...', flush=True)
server.serve_forever()
