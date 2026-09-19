# -*- coding: utf-8 -*-
"""生成 static/rss.xml：收录 static/blog 下的全部文章（按发布时间倒序）。

用法：python3 tools/gen_rss.py
"""
import email.utils
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

BASE = Path(__file__).resolve().parent.parent
BLOG = BASE / 'static' / 'blog'
SITE = 'https://pdf.my99ai.com'


def meta(path: Path, pattern: str) -> str:
    m = re.search(pattern, path.read_text(encoding='utf-8'), re.S)
    return m.group(1).strip() if m else ''


def git_date(path: Path) -> datetime:
    try:
        out = subprocess.run(
            ['git', 'log', '-1', '--format=%cI', '--', str(path.relative_to(BASE))],
            cwd=BASE, capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        if out:
            return datetime.fromisoformat(out)
    except Exception:
        pass
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def main():
    posts = [p for p in BLOG.glob('*.html') if p.name != 'index.html']
    items = []
    for p in posts:
        title = meta(p, r'<title>(.*?)\s*\|\s*PDF to Markdown</title>') or meta(p, r'<title>(.*?)</title>')
        desc = meta(p, r'<meta name="description" content="(.*?)"')
        pub = meta(p, r'<meta name="article:published_time" content="(\d{4}-\d{2}-\d{2})')
        dt = (datetime.strptime(pub, '%Y-%m-%d').replace(tzinfo=timezone.utc)
              if pub else git_date(p))
        url = meta(p, r'<link rel="canonical" href="(.*?)"') or f'{SITE}/blog/{p.name}'
        items.append((dt, title, url, desc))

    items.sort(key=lambda t: t[0], reverse=True)
    now = email.utils.format_datetime(datetime.now(timezone.utc))
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        '  <channel>',
        '    <title>PDF to Markdown Blog</title>',
        f'    <link>{SITE}/blog/</link>',
        '    <description>Guides and best practices for converting PDFs to Markdown with OCR</description>',
        '    <language>en</language>',
        f'    <lastBuildDate>{now}</lastBuildDate>',
        f'    <atom:link href="{SITE}/rss.xml" rel="self" type="application/rss+xml"/>',
    ]
    for dt, title, url, desc in items:
        lines += [
            '    <item>',
            f'      <title>{escape(title)}</title>',
            f'      <link>{escape(url)}</link>',
            f'      <guid>{escape(url)}</guid>',
            f'      <pubDate>{email.utils.format_datetime(dt)}</pubDate>',
            f'      <description>{escape(desc)}</description>',
            '    </item>',
        ]
    lines += ['  </channel>', '</rss>']
    out = BASE / 'static' / 'rss.xml'
    out.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'rss.xml regenerated: {len(items)} posts -> {out}')


if __name__ == '__main__':
    sys.exit(main())
