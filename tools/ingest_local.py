#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地文章入库：处理浏览器下载的 HTML/PDF，转 Markdown 存入 knowledge/archive/。

流程：
    1. 用户在浏览器打开文章，Ctrl+S 保存（选"仅HTML"）或打印为PDF，
       存入 knowledge/inbox/
    2. 运行本脚本：文件被转换为 Markdown（带元信息头）存入 knowledge/archive/，
       原件移入 knowledge/inbox/done/
    3. 之后交给 agent 按 knowledge-intake 流程提炼（三层分流）

用法（在仓库根目录）：
    python tools/ingest_local.py            # 处理 inbox/ 里全部待处理文件
    python tools/ingest_local.py --list     # 只查看待处理列表
"""
from __future__ import annotations

import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

KB = Path(__file__).resolve().parents[1] / "knowledge"
INBOX = KB / "inbox"
ARCHIVE = KB / "archive"
DONE = INBOX / "done"
SUPPORTED = {".html", ".htm", ".mht", ".pdf"}


def html_to_markdown(html: str) -> str:
    try:
        import html2text
        h = html2text.HTML2Text()
        h.body_width = 0
        h.ignore_images = True
        return h.handle(html)
    except ImportError:
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
        text = re.sub(r"</p>|</div>|</h[1-6]>", "\n\n", text, flags=re.I)
        text = re.sub(r"<[^>]+>", "", text)
        import html as ihtml
        return ihtml.unescape(text)


def extract_html(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    for enc in ("utf-8", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="ignore")
    m = re.search(r"<title>(.*?)</title>", text, re.S | re.I)
    title = m.group(1).strip().split("|")[0].split("_")[0].strip() if m else path.stem
    return title, html_to_markdown(text)


def extract_pdf(path: Path) -> tuple[str, str]:
    import pymupdf
    doc = pymupdf.open(path)
    title = doc.metadata.get("title") or path.stem
    return title, "\n\n".join(page.get_text() for page in doc)


def ingest(path: Path) -> Path:
    if path.suffix.lower() in (".html", ".htm", ".mht"):
        title, body = extract_html(path)
    elif path.suffix.lower() == ".pdf":
        title, body = extract_pdf(path)
    else:
        raise ValueError(f"不支持的格式: {path.suffix}")

    ARCHIVE.mkdir(parents=True, exist_ok=True)
    out = ARCHIVE / f"{path.stem}-{re.sub(chr(92) + 'W+', '-', title)[:50].strip('-')}.md"
    header = (f"---\n标题: {title}\n来源: 浏览器下载 ({path.name})\n"
              f"入库时间: {datetime.now():%Y-%m-%d %H:%M}\n用途: 个人学习提炼，版权归原作者\n---\n\n")
    out.write_text(header + body, encoding="utf-8")
    DONE.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), DONE / path.name)
    return out


def main() -> int:
    if not INBOX.exists():
        print(f"收件夹不存在: {INBOX}（创建后放入浏览器下载的文章）")
        INBOX.mkdir(parents=True)
        return 0
    files = [f for f in INBOX.iterdir()
             if f.is_file() and f.suffix.lower() in SUPPORTED]
    if not files:
        print("inbox/ 里没有待处理文件。")
        return 0
    if "--list" in sys.argv:
        for f in files:
            print(f)
        return 0
    ok = fail = 0
    for f in files:
        try:
            out = ingest(f)
            print(f"[OK] {f.name} -> {out.relative_to(KB.parent)}")
            ok += 1
        except Exception as e:
            print(f"[FAIL] {f.name}: {type(e).__name__} {e}")
            fail += 1
    print(f"完成: 成功 {ok} / 失败 {fail}。接下来可让 agent 按 knowledge-intake 提炼 archive/ 里的文章。")
    return 0 if ok or not files else 1


if __name__ == "__main__":
    sys.exit(main())
