#!/usr/bin/env python3
"""Check local HTML asset links in the concept docs. No external network requests."""
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, unquote

DOCS = Path(__file__).resolve().parents[1] / 'docs'

class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.links=[]
    def handle_starttag(self, tag, attrs):
        if tag not in {'a','link','script','img'}: return
        data=dict(attrs)
        attr='href' if tag in {'a','link'} else 'src'
        if data.get(attr): self.links.append(data[attr])

failures=[]
for page in DOCS.rglob('*.html'):
    parser=Links(); parser.feed(page.read_text(encoding='utf-8'))
    for href in parser.links:
        parsed=urlsplit(href)
        if parsed.scheme or href.startswith(('#','//')): continue
        target=(page.parent/unquote(parsed.path)).resolve()
        if parsed.path and not target.is_file(): failures.append(f'{page.relative_to(DOCS)}: missing {href}')
if failures:
    print('\n'.join(failures)); raise SystemExit(1)
print(f'PASS: {len(list(DOCS.rglob("*.html")))} HTML pages with valid local paths')
