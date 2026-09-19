# -*- coding: utf-8 -*-
"""IndexNow 主动推送：把站点 URL 通知 Bing/Yahoo/DuckDuckGo 等即时索引。

Google 不支持 IndexNow（Google 只能靠 sitemap + 站内链接自然重抓）。

环境变量 INDEXNOW_KEY：在 Bing Webmaster Tools -> IndexNow 中获取。
key 文件 static/<KEY>.txt 由 deploy 流程自动写入，无需手工维护。

用法：
  python3 tools/indexnow_ping.py                # 推送最近更新的 5 个 URL
  python3 tools/indexnow_ping.py --all          # 推送 sitemap 全部 URL
  python3 tools/indexnow_ping.py --dry-run      # 只打印不发送
"""
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ENDPOINT = 'https://api.indexnow.org/indexnow'
SITEMAP = Path(__file__).resolve().parent.parent / 'static' / 'sitemap.xml'


def sitemap_urls():
    xml = SITEMAP.read_text(encoding='utf-8')
    return re.findall(r'<loc>(.*?)</loc>', xml)


def main():
    key = os.getenv('INDEXNOW_KEY', '')
    dry = '--dry-run' in sys.argv
    if not key and not dry:
        print('未配置 INDEXNOW_KEY，跳过 IndexNow 推送。配置方法见 seo/SECRETS-SETUP.md')
        return 0

    urls = sitemap_urls()
    if '--all' not in sys.argv:
        urls = urls[:5]
    if not urls:
        print('没有可推送的 URL')
        return 0

    payload = {
        'host': 'pdf.my99ai.com',
        'key': key or 'DRY-RUN-KEY',
        'urlList': urls,
    }
    body = json.dumps(payload).encode('utf-8')
    if dry:
        print(f'[dry-run] 将推送 {len(urls)} 个 URL:')
        for u in urls:
            print(' ', u)
        return 0

    req = urllib.request.Request(
        ENDPOINT, data=body,
        headers={'Content-Type': 'application/json; charset=utf-8'},
        method='POST')
    with urllib.request.urlopen(req, timeout=30) as resp:
        print(f'IndexNow 推送完成：HTTP {resp.status}，{len(urls)} 个 URL')
        if resp.status not in (200, 202):
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
