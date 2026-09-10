#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文章下载器：抓取URL列表，HTML转Markdown存入 knowledge/archive/。

- 礼貌限速：每次请求间隔 5 秒，只用于个人学习精选下载，勿批量爬站
- 每篇头部写入元信息（URL/抓取时间），供 knowledge-intake 提炼时溯源

用法（在仓库根目录）：
    python tools/download_articles.py <url1> <url2> ...
"""
from __future__ import annotations

import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

ARCHIVE = Path(__file__).resolve().parents[1] / "knowledge" / "archive"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
DELAY_SECONDS = 5


def html_to_text(html: str) -> str:
    """HTML -> Markdown。优先 html2text，缺失时退化为正则去标签。"""
    try:
        import html2text
        h = html2text.HTML2Text()
        h.body_width = 0
        h.ignore_images = True
        h.ignore_links = False
        return h.handle(html)
    except ImportError:
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
        text = re.sub(r"</p>", "\n\n", text, flags=re.I)
        text = re.sub(r"<[^>]+>", "", text)
        import html as ihtml
        return ihtml.unescape(text)


def extract_title(html: str) -> str:
    m = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    return m.group(1).strip().split("|")[0].strip() if m else "untitled"


def download(url: str) -> Path | None:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding
    title = extract_title(resp.text)
    slug = re.sub(r"[^\w\-]+", "-", title)[:60].strip("-") or "article"
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    path = ARCHIVE / f"{url.rstrip('/').split('/')[-1]}-{slug}.md"
    header = (f"---\n来源: {url}\n标题: {title}\n"
              f"抓取时间: {datetime.now():%Y-%m-%d %H:%M}\n用途: 个人学习提炼，版权归原作者\n---\n\n")
    path.write_text(header + html_to_text(resp.text), encoding="utf-8")
    return path


def main() -> int:
    urls = [u for u in sys.argv[1:] if u.startswith("http")]
    if not urls:
        print("用法: download_articles.py <url> [...]")
        return 1
    ok = fail = 0
    for i, url in enumerate(urls):
        if i:
            time.sleep(DELAY_SECONDS)
        try:
            path = download(url)
            print(f"[OK] {url} -> {path.name}")
            ok += 1
        except Exception as e:
            print(f"[FAIL] {url}: {type(e).__name__} {e}")
            fail += 1
    print(f"完成: 成功 {ok} / 失败 {fail}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
