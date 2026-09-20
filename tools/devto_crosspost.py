# -*- coding: utf-8 -*-
"""把站内文章转发到 DEV Community（带 canonical 指向原文，白帽外链）。

pdf-markdown 版：文章为 HTML 片段，先转 Markdown 再提交。

环境变量 DEVTO_API_KEY 未设置时优雅跳过。

用法：python3 tools/devto_crosspost.py --slug <slug>
      （省略 --slug 时取 published.json 中最新一篇）
"""
import json
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

SITE = 'https://pdf.my99ai.com'
BASE = Path(__file__).resolve().parent.parent
TAGS = ['tutorial', 'productivity', 'beginners']


def load_article(slug):
    html = (BASE / 'static' / 'blog' / f'{slug}.html').read_text(encoding='utf-8')
    title = re.search(r'<title>(.*?)\s*\|\s*PDF to Markdown</title>', html)
    desc = re.search(r'<meta name="description" content="(.*?)"', html)
    body = re.search(r'<div class="prose prose-indigo max-w-none">\n?(.*?)\n\s*</div>', html, re.S)
    if not (title and body):
        raise SystemExit(f'无法从 {slug}.html 解析出标题或正文')
    return title.group(1).strip(), (desc.group(1) if desc else ''), body.group(1)


class _HTML2MD(HTMLParser):
    """极简 HTML->Markdown 转换器（覆盖本文集使用的标签子集）。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self._list_stack = []

    def handle_starttag(self, tag, attrs):
        if tag in ('h1', 'h2', 'h3', 'h4'):
            self.out.append('\n' + '#' * int(tag[1]) + ' ')
        elif tag == 'p':
            self.out.append('\n')
        elif tag in ('ul', 'ol'):
            self._list_stack.append(tag)
            self.out.append('\n')
        elif tag == 'li':
            self.out.append('\n' + ('- ' if self._list_stack[-1:] == ['ul'] else '1. '))
        elif tag == 'strong':
            self.out.append('**')
        elif tag == 'em':
            self.out.append('*')
        elif tag == 'code':
            self.out.append('`')
        elif tag == 'br':
            self.out.append('\n')
        elif tag == 'a':
            href = dict(attrs).get('href', '')
            self.out.append('[')
            self._href = href

    def handle_endtag(self, tag):
        if tag in ('h1', 'h2', 'h3', 'h4'):
            self.out.append('\n')
        elif tag in ('ul', 'ol'):
            self._list_stack.pop()
        elif tag == 'strong':
            self.out.append('**')
        elif tag == 'em':
            self.out.append('*')
        elif tag == 'code':
            self.out.append('`')
        elif tag == 'a':
            href = getattr(self, '_href', '')
            if href:
                self.out.append(f']({href})')
            self._href = ''

    def handle_data(self, data):
        self.out.append(data)

    def result(self):
        md = ''.join(self.out)
        md = re.sub(r'\n{3,}', '\n\n', md)
        return md.strip()


def html_to_markdown(html):
    p = _HTML2MD()
    p.feed(html)
    return p.result()


def main():
    api_key = os.getenv('DEVTO_API_KEY', '')
    if not api_key:
        print('未配置 DEVTO_API_KEY，跳过 Dev.to 转发。配置方法见 seo/SECRETS-SETUP.md')
        return 0

    args = sys.argv
    slug = args[args.index('--slug') + 1] if '--slug' in args else None
    if not slug:
        published = json.loads((BASE / 'seo' / 'published.json').read_text(encoding='utf-8'))
        slug = published['published'][-1]

    title, description, body_html = load_article(slug)
    md = html_to_markdown(body_html)
    canonical = f'{SITE}/blog/{slug}'
    md += (f'\n\n---\n\n*This was originally published on [my blog]({canonical}). '
           f'Try the free [PDF to Markdown converter]({SITE}) — 200 pages/month free.*\n'
           f'> {description}')

    import requests
    resp = requests.post('https://dev.to/api/articles', headers={'api-key': api_key}, json={
        'article': {
            'title': title,
            'body_markdown': md,
            'published': True,
            'description': description,
            'tags': TAGS,
            'canonical_url': canonical,
            'main_image': f'{SITE}/og-image.png',
        }
    }, timeout=60)
    if resp.status_code in (200, 201):
        url = resp.json().get('url')
        print(f'Dev.to 转发成功: {url}')
        return 0
    print(f'Dev.to 转发失败 HTTP {resp.status_code}: {resp.text[:300]}')
    return 1


if __name__ == '__main__':
    sys.exit(main())
