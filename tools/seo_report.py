# -*- coding: utf-8 -*-
"""Google Search Console 周报：拉取最近 7 天的自然搜索查询表现。

认证二选一（未配置时优雅跳过）：
  1) OAuth 刷新令牌（推荐）：GSC_OAUTH_CLIENT_ID / GSC_OAUTH_CLIENT_SECRET /
     GSC_OAUTH_REFRESH_TOKEN
  2) 服务账号 JSON：GSC_SERVICE_ACCOUNT_JSON（需组织允许创建 SA 密钥）
报告写入 GITHUB_STEP_SUMMARY（本地运行时打印）和 seo/reports/。

用法：python3 tools/seo_report.py
"""
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent.parent
REPORTS = BASE / 'seo' / 'reports'
SITES = ['https://pdf.my99ai.com/', 'https://pdf.my99ai.com', 'sc-domain:pdf.my99ai.com']
TOKEN_URL = 'https://oauth2.googleapis.com/token'
SCOPE = 'https://www.googleapis.com/auth/webmasters.readonly'


def get_token_oauth():
    resp = requests.post(TOKEN_URL, data={
        'client_id': os.environ['GSC_OAUTH_CLIENT_ID'],
        'client_secret': os.environ['GSC_OAUTH_CLIENT_SECRET'],
        'refresh_token': os.environ['GSC_OAUTH_REFRESH_TOKEN'],
        'grant_type': 'refresh_token',
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()['access_token']


def get_token_sa(sa_info):
    now = int(time.time())
    import jwt
    assertion = jwt.encode({
        'iss': sa_info['client_email'],
        'scope': SCOPE,
        'aud': TOKEN_URL,
        'iat': now,
        'exp': now + 3600,
    }, sa_info['private_key'], algorithm='RS256')
    resp = requests.post(TOKEN_URL, data={
        'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
        'assertion': assertion,
    }, timeout=30)
    resp.raise_for_status()
    return resp.json()['access_token']


def fetch_site_stats(token, site, start, end):
    url = (f'https://searchconsole.googleapis.com/webmasters/v3/sites/'
           f'{requests.utils.quote(site, safe="")}/searchAnalytics/query')
    resp = requests.post(url, headers={'Authorization': f'Bearer {token}'}, json={
        'startDate': start.isoformat(),
        'endDate': end.isoformat(),
        'dimensions': ['query'],
        'rowLimit': 25,
    }, timeout=60)
    if resp.status_code != 200:
        return None
    return resp.json().get('rows', [])


def main():
    token = None
    if os.getenv('GSC_OAUTH_REFRESH_TOKEN'):
        token = get_token_oauth()
    elif os.getenv('GSC_SERVICE_ACCOUNT_JSON'):
        sa_raw = os.environ['GSC_SERVICE_ACCOUNT_JSON']
        try:
            sa_info = json.loads(sa_raw)
        except json.JSONDecodeError:
            import base64
            sa_info = json.loads(base64.b64decode(sa_raw))
        token = get_token_sa(sa_info)
    else:
        print('未配置 GSC OAuth 刷新令牌或服务账号，跳过 SEO 周报。配置方法见 seo/SECRETS-SETUP.md')
        return 0

    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=6)

    rows = None
    used_site = None
    for site in SITES:
        rows = fetch_site_stats(token, site, start, end)
        if rows is not None:
            used_site = site
            break
    if rows is None:
        raise SystemExit('GSC API 访问失败：请确认已在 Search Console 中把服务账号邮箱添加为网站协作者')

    clicks = sum(r['clicks'] for r in rows)
    imps = sum(r['impressions'] for r in rows)
    lines = [
        f'## SEO 周报（{start} ~ {end}）',
        '',
        f'属性：`{used_site}` ｜ Top {len(rows)} 查询合计：**{clicks:.0f} 次点击 / {imps:.0f} 次展现**',
        '',
        '| 查询词 | 点击 | 展现 | CTR | 平均排名 |',
        '|---|---|---|---|---|',
    ]
    for r in sorted(rows, key=lambda x: -x['clicks']):
        keys = r['keys'][0]
        lines.append(f"| {keys} | {r['clicks']:.0f} | {r['impressions']:.0f} "
                     f"| {r.get('ctr', 0)*100:.1f}% | {r.get('position', 0):.1f} |")

    report = '\n'.join(lines) + '\n'
    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / f'{start.isoformat()}_{end.isoformat()}.md'
    out.write_text(report, encoding='utf-8')
    print(report)

    summary = os.getenv('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as f:
            f.write(report)
    print(f'报告已写入 {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
