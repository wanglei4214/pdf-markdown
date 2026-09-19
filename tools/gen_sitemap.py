# -*- coding: utf-8 -*-
"""重新生成 static/sitemap.xml：扫描 static 下所有 HTML，lastmod 取 git 提交日期（新文件回退文件时间）。

用法：python3 tools/gen_sitemap.py
"""
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

BASE = Path(__file__).resolve().parent.parent
STATIC = BASE / 'static'
SITE = 'https://pdf.my99ai.com'


def lastmod(path: Path) -> str:
    """git 提交日期（YYYY-MM-DD）；未入库的文件回退为文件修改时间。"""
    try:
        out = subprocess.run(
            ['git', 'log', '-1', '--format=%cs', '--', str(path.relative_to(BASE))],
            cwd=BASE, capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        if out:
            return out
    except Exception:
        pass
    return datetime.fromtimestamp(path.stat().st_mtime).strftime('%Y-%m-%d')


def url_for(path: Path) -> str:
    rel = path.relative_to(STATIC).as_posix()
    if rel == 'index.html':
        return SITE + '/'
    if rel.endswith('index.html'):
        return SITE + '/' + rel[:-len('index.html')]
    return SITE + '/' + rel


def priority_changefreq(url: str):
    if url == SITE + '/':
        return '1.0', 'weekly'
    if url.rstrip('/').endswith(('blog', 'pricing')):
        return '0.8', 'weekly' if url.endswith('blog/') else 'monthly'
    if '/blog/' in url:
        return '0.7', 'monthly'
    return '0.3', 'yearly'


def main():
    pages = sorted(STATIC.rglob('*.html'))
    rows = []
    for p in pages:
        url = url_for(p)
        pri, freq = priority_changefreq(url)
        rows.append((url, lastmod(p), freq, pri))

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for url, mod, freq, pri in rows:
        lines.append('  <url>')
        lines.append(f'    <loc>{escape(url)}</loc>')
        lines.append(f'    <lastmod>{mod}</lastmod>')
        lines.append(f'    <changefreq>{freq}</changefreq>')
        lines.append(f'    <priority>{pri}</priority>')
        lines.append('  </url>')
    lines.append('</urlset>')
    out = STATIC / 'sitemap.xml'
    out.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'sitemap.xml regenerated: {len(rows)} urls -> {out}')


if __name__ == '__main__':
    sys.exit(main())
