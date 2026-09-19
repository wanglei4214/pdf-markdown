# -*- coding: utf-8 -*-
"""无人值守内容流水线：取词 -> LLM 生成文章 -> 质量门槛 -> 渲染入库 -> 更新索引/sitemap/RSS。

环境变量：
  LLM_API_KEY   必需（未设置时本脚本直接跳过，退出码 0，便于 CI 无凭据时优雅跳过）
  LLM_API_BASE  可选，默认 https://api.openai.com/v1（兼容 DeepSeek/智谱等 OpenAI 格式接口）
  LLM_MODEL     可选，默认 gpt-4o-mini

用法：python3 tools/gen_article.py [--selftest]
  --selftest 使用内置样例内容走完整渲染/门槛/索引流程，不调用 LLM。
"""
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent.parent
SEO = BASE / 'seo'
BLOG = BASE / 'static' / 'blog'
SITE = 'https://pdf.my99ai.com'
OWN_DOMAIN = 'pdf.my99ai.com'

TEMPLATE = '''<!DOCTYPE html>
<html lang="en">
<head>
  <!-- Google tag (gtag.js) -->
  <script async src="https://www.googletagmanager.com/gtag/js?id=G-YCN8ZMT5R0"></script>
  <script>
    window.dataLayer = window.dataLayer || [];
    function gtag(){{dataLayer.push(arguments);}}
    gtag('js', new Date());
    gtag('config', 'G-YCN8ZMT5R0');
  </script>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} | PDF to Markdown</title>
  <meta name="description" content="{description}">
  <link rel="canonical" href="{site}/blog/{slug}.html">
  <meta property="og:title" content="{title}">
  <meta property="og:description" content="{description}">
  <meta property="og:url" content="{site}/blog/{slug}.html">
  <meta property="og:type" content="article">
  <meta property="og:image" content="{site}/og-image.png">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{title}">
  <meta name="twitter:description" content="{description}">
  <meta name="twitter:image" content="{site}/og-image.png">
  <meta name="article:published_time" content="{date}">
  <meta name="article:author" content="PDF to Markdown">
  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "BlogPosting",
    "headline": "{title}",
    "description": "{description}",
    "image": "{site}/og-image.png",
    "datePublished": "{date}",
    "dateModified": "{date}",
    "author": {{"@type": "Organization", "name": "PDF to Markdown", "url": "{site}"}},
    "publisher": {{"@type": "Organization", "name": "PDF to Markdown", "url": "{site}"}}
  }}
  </script>
  <link rel="stylesheet" href="../tailwind.css">
  <link rel="stylesheet" href="../style.css">
</head>
<body class="bg-gray-50 text-gray-800 min-h-screen flex flex-col">
  <div class="max-w-3xl mx-auto px-4 py-8 md:py-12 flex-1 w-full">
    <header class="flex items-start justify-between gap-4 mb-8">
      <a href="/" class="block">
        <p class="eyebrow">DOCUMENT CONVERTER</p>
        <h1 class="text-2xl md:text-3xl font-bold text-indigo-700">PDF &rarr; Markdown</h1>
      </a>
      <a href="/" class="px-3 py-1.5 rounded-lg bg-white border border-gray-200 text-gray-600 hover:bg-gray-100 text-sm whitespace-nowrap">Back to tool</a>
    </header>

    <nav class="mb-6 text-sm" aria-label="Breadcrumb">
      <ol class="flex gap-2 text-gray-500">
        <li><a href="/" class="hover:text-indigo-600">Home</a></li>
        <li>/</li>
        <li><a href="/blog/" class="hover:text-indigo-600">Blog</a></li>
        <li>/</li>
        <li class="text-gray-700" aria-current="page">{short_title}</li>
      </ol>
    </nav>

    <article class="bg-white rounded-xl border border-gray-200 shadow-sm p-6 md:p-8">
      <div class="mb-6">
        <span class="text-xs bg-indigo-100 text-indigo-700 px-3 py-1 rounded-full">Guide</span>
        <h2 class="text-3xl md:text-4xl font-bold text-gray-900 mt-3 mb-3">{title}</h2>
        <p class="text-gray-600">{description}</p>
        <p class="text-sm text-gray-400 mt-3">{date} &middot; {read_min} min read</p>
      </div>

      <div class="prose prose-indigo max-w-none">
{content}
      </div>

      <div class="mt-8 pt-6 border-t border-gray-200">
        <div class="bg-indigo-50 rounded-lg p-4">
          <p class="text-sm text-gray-700">
            <strong>Ready to try it?</strong> <a href="/" class="text-indigo-600 hover:underline">Convert your first PDF for free</a> — 200 pages per month, no credit card required. More questions? Check the <a href="/#faq" class="text-indigo-600 hover:underline">FAQ</a> or the <a href="/blog/" class="text-indigo-600 hover:underline">blog</a>.
          </p>
        </div>
      </div>

      <nav class="mt-8 pt-6 border-t border-gray-200 flex justify-between">
        <a href="/blog/" class="text-indigo-600 hover:text-indigo-700 font-medium">&larr; Back to Blog</a>
        <a href="/" class="text-indigo-600 hover:text-indigo-700 font-medium">Try the converter &rarr;</a>
      </nav>
    </article>
  </div>

  <footer class="border-t border-gray-200 bg-white mt-auto">
    <div class="max-w-3xl mx-auto px-4 py-8 text-sm text-gray-500">
      <p>&copy; PDF to Markdown. All rights reserved.</p>
    </div>
  </footer>
</body>
</html>
'''

CARD = '''      <!-- article-card-insert-point -->
      <article class="bg-white rounded-xl border border-gray-200 shadow-sm p-6 hover:shadow-md transition">
        <div class="flex flex-col md:flex-row gap-4 md:items-start">
          <div class="flex-1">
            <div class="flex items-center gap-2 mb-2">
              <span class="text-xs bg-indigo-100 text-indigo-700 px-2 py-1 rounded">Guide</span>
              <span class="text-xs text-gray-500">{read_min} min read</span>
            </div>
            <h3 class="text-xl font-semibold text-gray-900 mb-2">
              <a href="{slug}.html" class="hover:text-indigo-600">{title}</a>
            </h3>
            <p class="text-gray-600 mb-4">{description}</p>
            <a href="{slug}.html" class="text-indigo-600 hover:text-indigo-700 font-medium">Read more &rarr;</a>
          </div>
        </div>
      </article>'''


def log(msg):
    print(msg, flush=True)


def load_keywords():
    data = json.loads((SEO / 'keywords.json').read_text(encoding='utf-8'))
    published = json.loads((SEO / 'published.json').read_text(encoding='utf-8')) if (SEO / 'published.json').exists() else {'published': []}
    done = set(published['published'])
    for kw in data.get('keywords', []):
        if kw['slug'] not in done:
            return kw, published
    return None, published


def internal_url_list():
    urls = [('/', 'the free converter homepage'),
            ('/pricing.html', 'the pricing page')]
    for p in sorted(BLOG.glob('*.html')):
        if p.name != 'index.html':
            urls.append((f'/blog/{p.name}', p.stem.replace('-', ' ')))
    return '\n'.join(f'  - {SITE}{u} ({label})' for u, label in urls)


SYSTEM_PROMPT = (
    'You are a senior SEO content writer for a PDF-to-Markdown SaaS website '
    f'({SITE}). You write accurate, practical, genuinely useful articles for an '
    'international (US) audience. Hard rules: never invent statistics, quotes, or '
    'product claims; do not link to any external website; only use the internal URLs '
    'provided; output only the JSON object requested.'
)

USER_PROMPT = '''Write a blog article for the target keyword below.

Target keyword: {keyword}
Suggested angle: {angle}

Requirements:
- 900-1200 words. Practical and actionable, no fluff, no marketing hype.
- Structure: intro paragraph, 3-5 <h2> sections (use <h3> subsections where helpful), and a short FAQ section at the end with 2-3 questions (<h3> question + <p> answer).
- Allowed HTML tags ONLY: <h2> <h3> <p> <ul> <ol> <li> <strong> <em> <code> <table> <thead> <tbody> <tr> <th> <td>. Do NOT output <h1>, <html>, <head>, or <body>.
- Include 2-4 internal links chosen from this list, with natural anchor text:
{urls}
- Write for researchers, students, developers, and Obsidian/Notion/LLM-workflow users.
- US English. No fabricated dates, numbers, or awards.

Respond with ONLY this JSON object (no markdown fences):
{{"title": "...", "description": "...", "html": "..."}}
- title: 30-65 chars, natural, includes the keyword
- description: 120-160 chars meta description
- html: the article body per the rules above'''


def call_llm(keyword, angle):
    api_base = (os.getenv('LLM_API_BASE') or 'https://api.openai.com/v1').rstrip('/')
    model = (os.getenv('LLM_MODEL') or 'gpt-4o-mini')
    resp = requests.post(
        f'{api_base}/chat/completions',
        headers={'Authorization': f"Bearer {os.environ['LLM_API_KEY']}"},
        json={
            'model': model,
            'messages': [
                {'role': 'system', 'content': SYSTEM_PROMPT},
                {'role': 'user', 'content': USER_PROMPT.format(
                    keyword=keyword, angle=angle, urls=internal_url_list())},
            ],
            'temperature': 0.7,
        },
        timeout=300,
    )
    resp.raise_for_status()
    text = resp.json()['choices'][0]['message']['content'].strip()
    text = re.sub(r'^```(?:json)?|```$', '', text.strip(), flags=re.M).strip()
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        raise ValueError(f'LLM did not return JSON: {text[:200]}')
    data = json.loads(text[start:end + 1])
    return {k: (data.get(k) or '').strip() for k in ('title', 'description', 'html')}


def word_count(html):
    return len(re.findall(r"[A-Za-z0-9']+", re.sub(r'<[^>]+>', ' ', html)))


def check_gate(title, description, html):
    """质量门槛：不达标返回原因列表（防 Google 判定批量低质内容的安全带）。"""
    problems = []
    words = word_count(html)
    if words < 700:
        problems.append(f'too short: {words} words (need >=700)')
    internal = len(re.findall(r'href="(?:/|https://pdf\.my99ai\.com)', html))
    if internal < 2:
        problems.append(f'too few internal links: {internal} (need >=2)')
    external = re.findall(r'href="https?://(?!pdf\.my99ai\.com)', html)
    if external:
        problems.append(f'{len(external)} external links not allowed')
    if re.search(r'<h1[\s>]', html, re.I):
        problems.append('h1 not allowed in body')
    if not 30 <= len(title) <= 80:
        problems.append(f'title length {len(title)} out of range 30-80')
    if not 100 <= len(description) <= 170:
        problems.append(f'description length {len(description)} out of range 100-170')
    if not html.lstrip().lower().startswith('<'):
        problems.append('html does not look like HTML')
    return problems, words


def build_page(slug, title, description, html, read_min, date_str):
    page = TEMPLATE.format(
        site=SITE, slug=slug, title=escape_attr(title), description=escape_attr(description),
        short_title=escape_attr(title.split(':')[0].split('|')[0].strip()),
        content=html, read_min=read_min, date=date_str,
    )
    return page


def escape_attr(s):
    return (s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
             .replace('"', '&quot;'))


def insert_card(slug, title, description, read_min):
    index_path = BLOG / 'index.html'
    src = index_path.read_text(encoding='utf-8')
    card = CARD.format(slug=slug, title=escape_attr(title),
                       description=escape_attr(description), read_min=read_min)
    marker = '<!-- article-card-insert-point -->'
    if marker not in src:
        raise RuntimeError('blog index insert marker missing')
    src = src.replace(marker, card, 1)
    index_path.write_text(src, encoding='utf-8')


def refresh_feeds():
    for script in ('gen_sitemap.py', 'gen_rss.py'):
        subprocess.run([sys.executable, str(BASE / 'tools' / script)],
                       check=True, capture_output=True, text=True)


def main():
    selftest = '--selftest' in sys.argv
    kw, published = load_keywords()
    if kw is None:
        log('所有关键词均已发布。请向 seo/keywords.json 添加新词。')
        return 0

    slug, keyword, angle = kw['slug'], kw['keyword'], kw.get('angle', '')
    log(f'选取关键词: {keyword} (slug={slug})')

    if selftest:
        title = 'PDF to Markdown Conversion Guide for Beginners'
        description = ('Learn how to convert PDF documents to clean Markdown step by step, '
                       'including scanned files, tables, and heading structure.')
        html = ('<p>Markdown makes documents portable, diff-friendly, and easy to reuse. '
                'This guide walks through the whole conversion workflow.</p>'
                + '<h2>Why convert PDFs to Markdown</h2><p>Markdown is plain text, so it '
                'works everywhere: Git, Obsidian, Notion, and LLM pipelines.</p>'
                * 1)
        # selftest 内容凑够门槛词数
        html += ''.join(f'<h2>Section {i}</h2><p>This section explains a practical aspect '
                        f'of converting PDF files to Markdown format with clear steps and '
                        f'tips for better results in your daily document workflow.</p>'
                        for i in range(2, 32))
        html += '<p>Try the <a href="/">free converter</a> and read the <a href="/blog/">blog</a>.</p>'
    elif not os.getenv('LLM_API_KEY'):
        log('未配置 LLM_API_KEY，跳过文章生成（CI 中属正常情况）。配置方法见 seo/SECRETS-SETUP.md')
        return 0
    else:
        title = description = html = None
        critique = ''
        for attempt in (1, 2):
            log(f'调用 LLM 生成文章（第 {attempt} 次）...')
            data = call_llm(keyword, angle + ('\n\n改进要求：\n' + critique if critique else ''))
            title, description, html = data['title'], data['description'], data['html']
            problems, words = check_gate(title, description, html)
            log(f'  词数={words}，门槛问题: {problems or "无，通过"}')
            if not problems:
                break
            critique = '\n'.join(problems)
            time.sleep(2)
        else:
            raise SystemExit('文章未通过质量门槛，已放弃（不写入）。请检查 LLM_MODEL 或关键词角度。')

    problems, words = check_gate(title, description, html)
    if problems:
        raise SystemExit(f'质量门槛未通过: {problems}')
    read_min = max(2, math.ceil(words / 220))
    date_str = time.strftime('%Y-%m-%d')

    page = build_page(slug, title, description, html, read_min, date_str)
    out = BLOG / f'{slug}.html'
    out.write_text(page, encoding='utf-8')
    log(f'已写入 {out}')

    insert_card(slug, title, description, read_min)
    log('已更新 blog/index.html')

    published['published'].append(slug)
    (SEO / 'published.json').write_text(
        json.dumps(published, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    refresh_feeds()
    log(f'完成：新文章 /blog/{slug}.html（{words} 词，约 {read_min} 分钟阅读）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
