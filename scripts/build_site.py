#!/usr/bin/env python3
"""Build a static, subpath-safe documentation site for GitHub Pages.

No network requests or secrets are needed. Source Markdown lives in
``site_docs/``; generated HTML goes to the ignored ``site/`` directory.
"""
from __future__ import annotations
import html
import json
import re
import shutil
import tomllib
from pathlib import Path

try:
    import mistune
except ImportError as exc:
    raise SystemExit("Install the docs extra: python -m pip install -e '.[docs]'") from exc

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
SRC = ROOT / "site_docs"
DEST = ROOT / "site"
PAGES = [
    ("Overview", "index.md"),
    ("Getting started", "getting-started.md"),
    ("Architecture", "architecture.md"),
    ("Profiles & setup", "profiles.md"),
    ("CLI", "cli.md"),
    ("Python SDK", "sdk.md"),
    ("Agent execution", "agent-execution.md"),
    ("Coding agent", "coding-agents.md"),
    ("Repair loops", "repair-loops.md"),
    ("Multi-agent orchestration", "orchestration.md"),
    ("Durable background service", "durable-service.md"),
    ("Native adapters", "native-adapters.md"),
    ("Sandboxing", "sandboxing.md"),
    ("MCP integration", "mcp-integration.md"),
    ("Verification", "verification.md"),
    ("API reference", "api-reference.md"),
    ("Security", "security.md"),
    ("Research", "research.md"),
    ("TransferBench runner", "transferbench-runner.md"),
    ("Contributing", "contributing.md"),
    ("Roadmap", "roadmap.md"),
    ("Supervised coding", "supervised-coding.md"),
    ("Human patch review", "review-console.md"),
    ("Local clients & VS Code", "local-clients.md"),
    ("Setup and diagnostics", "setup-diagnostics.md"),
    ("Host certification", "host-certification.md"),
    ("State backup & recovery", "state-recovery.md"),
    ("Schema upgrades", "schema-upgrades.md"),
    ("Production readiness", "production-readiness.md"),
    ("Signed release verification", "release-verification.md"),
    ("GitHub Pages", "pages.md"),
]

CSS = r"""
:root{color-scheme:dark;--bg:#080d18;--surface:#10192a;--border:#27344c;--text:#e8f0fb;--muted:#9aaac2;--accent:#7bd7ef;--good:#8ee0b6}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--text);font-family:Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif;line-height:1.7}
body::before{content:"";position:fixed;inset:0;z-index:-1;background:radial-gradient(ellipse at 80% 0,rgba(45,120,166,.16),transparent 47%)}
.header{height:68px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:18px;padding:0 26px;background:rgba(8,13,24,.94);position:sticky;top:0;z-index:5;backdrop-filter:blur(20px)}
.brand{font-size:19px;font-weight:750;letter-spacing:-.04em;color:#fff;text-decoration:none}.brand span{color:var(--accent)}.stage{border:1px solid var(--border);padding:2px 9px;border-radius:30px;font-size:11px;color:var(--muted)}
.header a.navlink{color:var(--muted);text-decoration:none;font-size:13px;margin-left:auto}.header a.navlink:hover{color:var(--accent)}
.layout{display:grid;grid-template-columns:250px minmax(0,880px);gap:56px;max-width:1320px;margin:0 auto;align-items:start;padding:35px 30px 100px}
.sidebar{position:sticky;top:98px;height:calc(100vh - 120px);overflow-y:auto;padding-right:14px}.sidebar label{display:block;text-transform:uppercase;letter-spacing:.13em;font-size:11px;color:var(--muted);margin-bottom:12px}
.sidebar a{display:block;text-decoration:none;padding:6px 12px;border-left:2px solid transparent;font-size:14px;color:var(--muted);border-radius:0 8px 8px 0}.sidebar a:hover,.sidebar a.active{color:var(--accent);background:rgba(123,215,239,.08);border-color:var(--accent)}
.search{background:#111c2e;color:var(--text);border:1px solid var(--border);border-radius:8px;padding:10px;width:100%;font:inherit;font-size:13px;margin-bottom:16px}
main{min-width:0}main h1{font-size:clamp(2.1rem,3.5vw,3.3rem);letter-spacing:-.055em;line-height:1.12;margin:20px 0 24px}main h2{font-size:1.65rem;letter-spacing:-.03em;margin-top:54px;border-top:1px solid var(--border);padding-top:28px}main h3{font-size:1.27rem;margin-top:32px}main h4{font-size:1.05rem;margin-top:25px}
main p,main li{color:#c5d1e0}main strong{color:#fff}main a{color:var(--accent)}main a:hover{text-decoration:underline}
main blockquote{padding:16px 20px;margin:24px 0;border-left:3px solid var(--accent);background:rgba(123,215,239,.065);border-radius:0 9px 9px 0}main blockquote p{margin:0}
main pre{padding:18px 20px;background:#0b1424;border:1px solid var(--border);border-radius:10px;overflow-x:auto;font-size:13px;line-height:1.55}main code{background:#172538;border-radius:4px;padding:.14rem .3rem;font:13px ui-monospace,SFMono-Regular,Menlo,monospace;color:#a3e4e4}main pre code{background:transparent;padding:0;color:#d6e5f6}
main table{border-collapse:collapse;width:100%;font-size:13px;display:block;overflow-x:auto}main th,main td{border-bottom:1px solid var(--border);padding:12px 16px;text-align:left;vertical-align:top}main th{background:var(--surface);font-weight:700;white-space:nowrap}main hr{border:0;border-top:1px solid var(--border);margin:40px 0}
main img{max-width:100%}main p:first-child{font-size:1.1rem} .breadcrumbs{font-size:12px;color:var(--muted);letter-spacing:.04em;text-transform:uppercase}
.footer{border-top:1px solid var(--border);padding:30px 0;margin-top:62px;font-size:12px;color:var(--muted)}#search-results{display:none;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:10px;font-size:13px}
#search-results a{display:block;padding:6px;color:var(--accent);text-decoration:none}
@media(max-width:820px){.layout{display:block;padding:22px 18px}.sidebar{position:static;height:auto;overflow:visible;padding:8px 0 18px;border-bottom:1px solid var(--border);margin-bottom:28px}.sidebar nav{display:flex;overflow-x:auto;gap:2px}.sidebar a{white-space:nowrap;border-left:0;border-bottom:2px solid transparent}.sidebar label{display:none}main h1{margin-top:14px}.header{padding:0 18px}.stage{display:none}}
"""
JS = r"""
async function initSearch(){let pages=[];try{pages=await(await fetch('search-index.json')).json()}catch{return}
const box=document.getElementById('search'),result=document.getElementById('search-results');
box.addEventListener('input',()=>{const q=box.value.trim().toLowerCase();if(q.length<2){result.style.display='none';return}
const found=pages.filter(p=>(p.title+' '+p.text).toLowerCase().includes(q)).slice(0,8);
result.replaceChildren();for(const p of found){const a=document.createElement('a');a.href=p.href;a.textContent=p.title;result.append(a)}
result.style.display=found.length?'block':'none'})}initSearch();
"""


def build() -> None:
    """Render Markdown pages, a local search index and a simulation subsite."""
    # Repo-provided Markdown is reviewed source, not untrusted user input.
    renderer = mistune.create_markdown(escape=True, plugins=["table", "strikethrough"])
    DEST.mkdir(parents=True, exist_ok=True)
    (DEST / "assets").mkdir(exist_ok=True)
    (DEST / "assets" / "site.css").write_text(CSS, encoding="utf-8")
    (DEST / "assets" / "site.js").write_text(JS, encoding="utf-8")
    search_index = []
    for title, filename in PAGES:
        src = SRC / filename
        if not src.is_file():
            raise FileNotFoundError(src)
        md = src.read_text(encoding="utf-8")
        body = renderer(md)
        # Internal Markdown navigation becomes local HTML; no site-root URLs.
        body = re.sub(r'(href=")([^"#]+)\.md(#[^\"]*)?(\")',
                      lambda m: m.group(1) + m.group(2) + '.html' + (m.group(3) or '') + m.group(4), body)
        html_file = filename.replace(".md", ".html")
        sidebar = "".join(f'<a class="{"active" if f == filename else ""}" href="{html.escape(f.replace(".md", ".html"))}">{html.escape(t)}</a>' for t, f in PAGES)
        page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="description" content="DecisionBound documentation: {html.escape(title)}"><title>{html.escape(title)} · DecisionBound</title><link rel="stylesheet" href="assets/site.css"><script defer src="assets/site.js"></script></head><body><header class="header"><a href="index.html" class="brand">Decision<span>Bound</span></a><span class="stage">ALPHA {html.escape(PACKAGE_VERSION)} · RESEARCH</span><a class="navlink" href="concept/index.html">Concept demo</a></header><div class="layout"><aside class="sidebar"><label>Documentation</label><input id="search" class="search" type="search" aria-label="Search documentation" placeholder="Search docs…"><div id="search-results" aria-live="polite"></div><nav>{sidebar}</nav></aside><main><div class="breadcrumbs">Documentation / {html.escape(title)}</div>{body}<footer class="footer">DecisionBound · Experimental developer alpha · Documentation is not an agent-execution service.</footer></main></div></body></html>'''
        (DEST / html_file).write_text(page, encoding="utf-8")
        text = re.sub(r"[`*#>\[\]()]", "", md)
        search_index.append({"title": title, "href": html_file, "text": text[:4000]})
    (DEST / "search-index.json").write_text(json.dumps(search_index), encoding="utf-8")
    # Retain earlier interactive concept, always explicitly marked as simulation.
    concept = DEST / "concept"
    concept.mkdir(exist_ok=True)
    original = ROOT / "docs"
    for file in ("index.html", "styles.css", "demo.js"):
        if (original / file).exists():
            shutil.copyfile(original / file, concept / file)
    print(f"Generated {len(PAGES)} documentation pages and concept assets in {DEST}")


if __name__ == "__main__":
    build()
