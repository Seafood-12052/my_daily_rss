import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import random
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import feedparser
import requests
import trafilatura
from openai import OpenAI

ROOT = Path(__file__).resolve().parent
STATE = ROOT / 'reading_history.json'
OUT = ROOT / 'reading_site'
HEADERS = {'User-Agent': 'PersonalReadingRSS/1.0'}


def sources():
    data = json.loads((ROOT / 'sources' / 'sources.json').read_text(encoding='utf-8'))
    return [feed for category in data['categories'] for feed in category['feeds'] if feed.get('rss')]


def classic():
    books = [p for p in (ROOT / 'sources').iterdir() if p.is_file() and p.suffix != '.json']
    random.shuffle(books)
    for book in books:
        chapters = [x.strip() for x in re.split(r'(?m)^\s*---\s*$', book.read_text(encoding='utf-8-sig')) if x.strip()]
        random.shuffle(chapters)
        for chapter in chapters:
            paragraphs = [p.strip() for p in re.split(r'\n\s*\n', chapter) if len(p.strip()) >= 30]
            if paragraphs:
                passage = random.choice(paragraphs)
                if len(passage) > 700:
                    sentences = re.split(r'(?<=[。！？])', passage)
                    passage = ''.join(sentences[:max(1, next((i for i, _ in enumerate(sentences) if len(''.join(sentences[:i+1])) >= 500), len(sentences)-1)+1)])
                return book.stem, passage
    raise RuntimeError('没有可用的古文段落')


def candidates(seen):
    result, links = [], set(seen)
    for source in sources():
        try:
            response = requests.get(source['rss'], headers=HEADERS, timeout=20)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            for entry in feed.entries:
                link = entry.get('link', '')
                if link.startswith(('https://', 'http://')) and link not in links:
                    links.add(link)
                    result.append((source['name'], entry.get('title', ''), link))
        except requests.RequestException as exc:
            print(f"来源不可用 {source['name']}: {exc}")
    random.shuffle(result)
    return result


def generate():
    key = os.environ.get('DEEPSEEK_API_KEY')
    if not key:
        raise RuntimeError('请设置 DEEPSEEK_API_KEY')
    state = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {'items': [], 'seen': []}
    client = OpenAI(api_key=key, base_url='https://api.deepseek.com', timeout=120, max_retries=0)
    for source, title, link in candidates(state['seen']):
        try:
            response = requests.get(link, headers=HEADERS, timeout=30)
            response.raise_for_status()
            body = trafilatura.extract(response.text, url=link, include_tables=True)
            if not body or len(body) < 200:
                continue
            if len(body) > 24000:
                print(f'正文过长，跳过: {link}')
                continue
            break
        except Exception as exc:
            print(f'文章抓取失败 {link}: {exc}')
    else:
        raise RuntimeError('没有成功提取文章，本次不发布')
    result = client.chat.completions.create(model='deepseek-flash', temperature=0.2,
        extra_body={'thinking': {'type': 'disabled'}},
        messages=[{'role': 'system', 'content': '请将给定文章标题和正文忠实完整翻译为简体中文，保留段落。只输出 JSON 对象，字段为 title 和 body。若原文已是中文则保留原文。'},
                  {'role': 'user', 'content': json.dumps({'title': title, 'body': body}, ensure_ascii=False)}],
        response_format={'type': 'json_object'})
    translated = json.loads(result.choices[0].message.content)
    if not all(isinstance(translated.get(k), str) and translated[k].strip() for k in ('title', 'body')):
        raise ValueError('翻译结果为空')
    book, passage = classic()
    result = client.chat.completions.create(model='deepseek-flash', temperature=0.2,
        extra_body={'thinking': {'type': 'disabled'}},
        messages=[{'role': 'system', 'content': '为给定古籍选段提供简明阅读辅助。只输出 JSON 对象，字段为 translation（白话理解）和 notes（重点词语、背景或阅读提示）。忠于提供的文字，不确定处明确说明，不要编造出处；如果选段本身含注释或现代文字，不要将其误作古文。'},
                  {'role': 'user', 'content': json.dumps({'book': book, 'passage': passage}, ensure_ascii=False)}],
        response_format={'type': 'json_object'})
    guide = json.loads(result.choices[0].message.content)
    if not all(isinstance(guide.get(k), str) and guide[k].strip() for k in ('translation', 'notes')):
        raise ValueError('古籍阅读辅助为空')
    now = dt.datetime.now(dt.timezone.utc)
    identifier = hashlib.sha256((link + now.isoformat()).encode()).hexdigest()
    article = {'id': identifier + ':article', 'kind': 'article', 'title': translated['title'],
               'source': source, 'link': link, 'body': translated['body'], 'date': now.isoformat()}
    classical = {'id': identifier + ':classic', 'kind': 'classic', 'title': '古籍｜' + book,
                 'book': book, 'passage': passage, 'translation': guide['translation'],
                 'notes': guide['notes'], 'date': now.isoformat()}
    state['items'].extend([article, classical])
    state['items'] = state['items'][-100:]
    state['seen'].append(link)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    render(state)


def render(state):
    site = os.environ.get('FEED_SITE_URL', 'https://github.com/').rstrip('/')
    OUT.mkdir(exist_ok=True)
    pages = OUT / 'articles'
    pages.mkdir(exist_ok=True)
    ET.register_namespace('content', 'http://purl.org/rss/1.0/modules/content/')
    rss = ET.Element('rss', version='2.0')
    channel = ET.SubElement(rss, 'channel')
    for name, value in [('title', '每日阅读'), ('link', site), ('description', '随机文章译文与古文'), ('language', 'zh-CN')]:
        ET.SubElement(channel, name).text = value
    for row in reversed(state['items']):
        item = ET.SubElement(channel, 'item')
        def paragraphs(value):
            return ''.join('<p>' + html.escape(p) + '</p>' for p in re.split(r'\n+', value) if p.strip())
        content = ''
        if row.get('kind') != 'classic':
            content = '<p>来源：' + html.escape(row['source']) + ' · <a href="' + html.escape(row['link'], quote=True) + '">原文</a></p>'
            content += paragraphs(row['body'])
        if row.get('kind') != 'article':
            content += '<h2>古籍选段｜' + html.escape(row['book']) + '</h2>' + paragraphs(row['passage'])
            if row.get('kind') == 'classic':
                content += '<h2>白话理解</h2>' + paragraphs(row['translation']) + '<h2>阅读提示</h2>' + paragraphs(row['notes'])
                content += '<p>白话理解和阅读提示由 AI 生成，仅供参考。</p>'
        page_name = hashlib.sha256(row['id'].encode('utf-8')).hexdigest() + '.html'
        page = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>' + html.escape(row['title']) + '</title>'
                '<style>body{max-width:46rem;margin:2rem auto;padding:0 1rem;line-height:1.8}a{overflow-wrap:anywhere}</style>'
                '</head><body><main><article><h1>' + html.escape(row['title']) + '</h1>' + content + '</article></main></body></html>')
        (pages / page_name).write_text(page, encoding='utf-8')
        for name, value in [('title', row['title']), ('link', site + '/articles/' + page_name), ('description', content), ('pubDate', email.utils.format_datetime(dt.datetime.fromisoformat(row['date'])))]:
            ET.SubElement(item, name).text = value
        ET.SubElement(item, '{http://purl.org/rss/1.0/modules/content/}encoded').text = content
        ET.SubElement(item, 'guid', isPermaLink='false').text = row['id']
    ET.ElementTree(rss).write(OUT / 'feed.xml', encoding='utf-8', xml_declaration=True)


if __name__ == '__main__':
    generate()
