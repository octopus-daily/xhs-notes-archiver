#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小红书笔记归档器：抓正文 + 图片，输出 Markdown 与索引 CSV。

关键点（详见 README）：
  1. 必须用 curl_cffi + impersonate 模拟浏览器指纹，否则只能拿到空壳页面
  2. 数据在 state["note"]["noteDetailMap"][note_id]["note"]（三层）
  3. 请求太快会被风控；用 --min-delay/--max-delay 控制节奏，失败自动退避
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import random
import re
import time

from curl_cffi import requests as creq

HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    "referer": "https://www.xiaohongshu.com/",
}
MARKER = "window.__INITIAL_STATE__="


def safe_name(text: str, limit: int = 40) -> str:
    text = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", (text or "").strip())
    return (re.sub(r"\s+", " ", text) or "无标题")[:limit]


def parse_state(html: str) -> dict | None:
    """取出页面内嵌 __INITIAL_STATE__ 的 JSON。"""
    i = html.find(MARKER)
    if i < 0:
        return None
    body = html[i + len(MARKER):]
    body = body[: body.find("</script>")].strip().rstrip(";")
    body = body.replace("undefined", "null")  # JS 字面量在 JSON 里不合法
    try:
        return json.loads(body)
    except Exception:
        return None


def pick_note(state: dict) -> dict | None:
    """取出笔记主体（注意：是 state['note']['noteDetailMap'][id]['note'] 三层）。"""
    detail = (state.get("note") or {}).get("noteDetailMap") or {}
    for _, v in detail.items():
        if isinstance(v, dict):
            note = v.get("note")
            if isinstance(note, dict) and (note.get("noteId") or note.get("title") or note.get("desc")):
                return note
    for _, v in (state.get("noteDetailMap") or {}).items():  # 兜底
        if isinstance(v, dict):
            note = v.get("note") or v
            if isinstance(note, dict) and note.get("noteId"):
                return note
    return None


def fetch_note(url: str, impersonate: str, timeout: int) -> tuple[dict | None, str]:
    try:
        r = creq.get(url, headers=HEADERS, impersonate=impersonate, timeout=timeout, verify=False)
    except Exception as e:
        return None, f"请求异常 {type(e).__name__}"
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    if MARKER not in r.text:
        return None, "空壳页面（风控）"
    state = parse_state(r.text)
    if not state:
        return None, "__INITIAL_STATE__ 解析失败"
    note = pick_note(state)
    if not note:
        return None, "未找到笔记主体"
    return note, ""


def download_images(urls, img_dir, prefix, impersonate, timeout):
    os.makedirs(img_dir, exist_ok=True)
    saved = []
    for i, url in enumerate(urls, 1):
        ext = ".jpg"
        m = re.search(r"\.(jpg|jpeg|png|webp|gif)", url.lower())
        if m:
            ext = "." + m.group(1)
        name = f"{prefix}_{i:02d}{ext}"
        path = os.path.join(img_dir, name)
        if os.path.exists(path):
            saved.append(name)
            continue
        try:
            r = creq.get(url, headers=HEADERS, impersonate=impersonate, timeout=timeout, verify=False)
            if r.status_code == 200 and len(r.content) > 1000:
                with open(path, "wb") as f:
                    f.write(r.content)
                saved.append(name)
        except Exception:
            pass
        time.sleep(0.3)
    return saved


def render_markdown(note: dict, nid: str, url: str, images: list[str]) -> str:
    title = (note.get("title") or "").strip()
    desc = (note.get("desc") or "").strip()
    ts = note.get("time") or 0
    pub = dt.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d") if ts else "未知"
    info = note.get("interactInfo") or {}
    user = note.get("user") or {}
    tags = " ".join(f"#{t.get('name')}" for t in (note.get("tagList") or []) if isinstance(t, dict))
    lines = [
        f"# {title or '(无标题)'}", "",
        f"- **note_id**：`{nid}`",
        f"- **类型**：{note.get('type') or ''} ｜ **发布**：{pub}",
        f"- **作者**：{user.get('nickname') or ''}（`{user.get('userId') or ''}`）",
        f"- **互动**：赞 {info.get('likedCount', '')} ｜ 藏 {info.get('collectedCount', '')} "
        f"｜ 评 {info.get('commentCount', '')} ｜ 分享 {info.get('shareCount', '')}",
        f"- **标签**：{tags or '（无）'}",
        f"- **原链接**：{url}", "",
    ]
    if desc:
        lines += ["## 正文", "", desc, ""]
    if images:
        lines += ["## 图片", ""] + [f"![](图片/{n})" for n in images] + [""]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="小红书笔记归档器（正文 + 图片）")
    ap.add_argument("--links", required=True, help="链接清单文件，一行一条（需带 xsec_token）")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--min-delay", type=float, default=2.0, help="每条之间的最小间隔秒数")
    ap.add_argument("--max-delay", type=float, default=5.0, help="每条之间的最大间隔秒数")
    ap.add_argument("--backoff", type=float, default=60.0, help="失败后的退避秒数")
    ap.add_argument("--impersonate", default="chrome146", help="curl_cffi 模拟的浏览器版本")
    ap.add_argument("--timeout", type=int, default=40)
    ap.add_argument("--skip-images", action="store_true", help="只抓正文，不下载图片")
    args = ap.parse_args()

    notes_dir = args.out
    img_dir = os.path.join(notes_dir, "图片")
    os.makedirs(notes_dir, exist_ok=True)
    links = [l.strip() for l in open(args.links, encoding="utf-8") if l.strip()]
    print(f"共 {len(links)} 条链接")

    index_path = os.path.join(notes_dir, "笔记正文索引.csv")
    rows: dict[str, dict] = {}
    if os.path.exists(index_path):
        for r in csv.DictReader(open(index_path, encoding="utf-8-sig")):
            rows[r["note_id"]] = r

    existing = {f.split("_")[0] for f in os.listdir(notes_dir) if f.endswith(".md")}
    ok = fail = skipped = 0
    for i, url in enumerate(links, 1):
        m = re.search(r"/(?:item|explore)/([0-9a-fA-F]{24})", url)
        if not m:
            print(f"  [{i}/{len(links)}] 跳过（非笔记链接）: {url[:60]}")
            continue
        nid = m.group(1)
        if nid in existing:
            skipped += 1
            continue

        note, err = fetch_note(url, args.impersonate, args.timeout)
        if err:
            fail += 1
            print(f"  [{i}/{len(links)}] ✗ {nid} {err}；退避 {args.backoff:.0f}s")
            rows[nid] = {"note_id": nid, "标题": "", "字数": 0, "图片数": 0, "失败原因": err}
            time.sleep(args.backoff)
            continue

        title = (note.get("title") or "").strip()
        desc = (note.get("desc") or "").strip()
        urls = []
        for im in note.get("imageList") or []:
            if isinstance(im, dict):
                u = im.get("urlDefault") or im.get("urlPre") or im.get("url")
                if u:
                    urls.append(u)
        images = [] if args.skip_images else download_images(
            urls, img_dir, nid, args.impersonate, args.timeout
        )
        with open(os.path.join(notes_dir, f"{nid}_{safe_name(title)}.md"), "w", encoding="utf-8") as f:
            f.write(render_markdown(note, nid, url, images))

        ts = note.get("time") or 0
        rows[nid] = {
            "note_id": nid, "标题": title, "字数": len(desc), "图片数": len(images),
            "发布": dt.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d") if ts else "",
            "作者": (note.get("user") or {}).get("nickname") or "", "失败原因": "",
        }
        ok += 1
        print(f"  [{i}/{len(links)}] ✔ {title[:34]} | {len(desc)} 字 | {len(images)} 图")
        time.sleep(random.uniform(args.min_delay, args.max_delay))

    fields = ["note_id", "标题", "字数", "图片数", "发布", "作者", "失败原因"]
    with open(index_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for k in sorted(rows):
            w.writerow({x: rows[k].get(x, "") for x in fields})

    print(f"\n成功 {ok} ｜ 失败 {fail} ｜ 跳过（已存在）{skipped}")
    print(f"输出目录: {notes_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
