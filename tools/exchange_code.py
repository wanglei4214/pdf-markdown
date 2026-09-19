# -*- coding: utf-8 -*-
"""一次性（可重复使用）：用授权码换取刷新令牌，并加密写入仓库 Secret
GSC_OAUTH_REFRESH_TOKEN。设计为在 GitHub Actions 内运行。

环境变量：
  AUTH_CODE                 本地回环服务捕获的授权码（一次性）
  GSC_OAUTH_CLIENT_ID       OAuth 客户端 ID（repo secret）
  GSC_OAUTH_CLIENT_SECRET   OAuth 客户端密钥（repo secret）
  GITHUB_TOKEN              工作流令牌（需 administration: write 权限）
"""
import base64
import json
import os
import sys

import requests

REPO = 'wanglei4214/pdf-markdown'
SECRET_NAME = 'GSC_OAUTH_REFRESH_TOKEN'


def main():
    code = os.environ['AUTH_CODE']
    cid = os.environ['GSC_OAUTH_CLIENT_ID']
    cs = os.environ['GSC_OAUTH_CLIENT_SECRET']
    gh_token = os.environ['GITHUB_TOKEN']

    resp = requests.post('https://oauth2.googleapis.com/token', data={
        'code': code,
        'client_id': cid,
        'client_secret': cs,
        'redirect_uri': 'http://localhost:8734',
        'grant_type': 'authorization_code',
    }, timeout=30)
    data = resp.json()
    rt = data.get('refresh_token')
    if not rt:
        print('exchange failed:', json.dumps(data)[:400])
        return 1
    print(f'exchange ok, refresh token len={len(rt)}')

    gh = requests.Session()
    gh.headers.update({
        'Authorization': f'Bearer {gh_token}',
        'Accept': 'application/vnd.github+json',
    })
    pub = gh.get(f'https://api.github.com/repos/{REPO}/actions/secrets/public-key', timeout=30).json()

    from nacl import encoding, public
    pk = public.PublicKey(pub['key'].encode('utf-8'), encoding.Base64Encoder())
    sealed = public.SealedBox(pk).encrypt(rt.encode('utf-8'))
    put = gh.put(f'https://api.github.com/repos/{REPO}/actions/secrets/{SECRET_NAME}', json={
        'encrypted_value': base64.b64encode(sealed).decode('utf-8'),
        'key_id': pub['key_id'],
    }, timeout=30)
    print(f'secret {SECRET_NAME} put -> HTTP {put.status_code}')
    return 0 if put.status_code in (201, 204) else 1


if __name__ == '__main__':
    sys.exit(main())
