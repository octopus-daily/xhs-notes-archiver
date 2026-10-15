#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小红书笔记链接提取与规范化工具

用途：从任意文本（聊天记录、笔记、导出的 JSON）里提取小红书链接，
      去重、补全 xsec_token，输出可直接喂给下载器的干净链接列表。

为什么需要它：
  小红书的 note 链接不带 xsec_token 时打不开（或跳登录）。
  从「分享」按钮复制的链接是带 token 的，但从页面 DOM 抓下来的往往没有。
  本工具负责把这些链接规范化，并和已有的 note_id 清单做交叉校验。
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys

NOTE_ID_RE = re.compile(r"/(?:item|explore)/([0-9a-fA-F]{24})")
TOKEN_RE = re.compile(r"[?&]xsec_token=([^&\s]+)")
URL_RE = re.compile(r"https?://(?:www\.)?(?:xiaohongshu\.com|rednote\.com)/\S+")
SHORT_RE = re.compile(r"https?://xhslink\.com/\S+")


def extract(text: str) -> list[tuple[str, str | None]]:
    """从任意文本里提取 (note_id, xsec_token)。同一 note_id 只保留一条。"""
    found: dict[str, str | None] = {}
    for url in URL_RE.findall(text) + SHORT_RE.findall(text):
        m = NOTE_ID_RE.search(url)
        if not m:
            continue
        nid = m.group(1)
        t = TOKEN_RE.search(url)
        token = t.group(1) if t else None
        if nid not in found or (found[nid] is None and token):
            found[nid] = token
    return list(found.items())


def build_url(nid: str, token: str | None) -> str:
    base = f"https://www.xiaohongshu.com/discovery/item/{nid}"
    return f"{base}?xsec_token={token}&xsec_source=pc_share" if token else base


def main() -> int:
    ap = argparse.ArgumentParser(description="小红书笔记链接提取与规范化")
    ap.add_argument("input", nargs="?", help="输入文件（文本或 JSON）；省略则读 stdin")
    ap.add_argument("--out", default="links.txt", help="输出链接清单（默认 links.txt）")
    ap.add_argument("--csv", help="额外输出 CSV（note_id,token,url）")
    ap.add_argument("--check", help="交叉校验用的 JSON（含 note_id 清单）")
    args = ap.parse_args()

    raw = open(args.input, encoding="utf-8", errors="replace").read() if args.input else sys.stdin.read()
    pairs = extract(raw)
    if not pairs:
        print("未提取到任何小红书链接", file=sys.stderr)
        return 1

    print(f"提取到 {len(pairs)} 条（去重后）｜ 带 xsec_token {sum(1 for _, t in pairs if t)} 条")
    with open(args.out, "w", encoding="utf-8") as f:
        for nid, token in pairs:
            f.write(build_url(nid, token) + "\n")
    print(f"已写出: {args.out}")

    if args.csv:
        with open(args.csv, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["note_id", "xsec_token", "url"])
            for nid, token in pairs:
                w.writerow([nid, token or "", build_url(nid, token)])
        print(f"已写出: {args.csv}")

    if args.check:
        data = json.load(open(args.check, encoding="utf-8"))
        items = data.get("items") if isinstance(data, dict) else data
        mine = {x.get("note_id") for x in items if isinstance(x, dict)}
        have = {nid for nid, _ in pairs}
        print()
        print(f"清单共 {len(mine)} 条 ｜ 链接覆盖 {len(mine & have)} 条")
        print(f"  还缺 {len(mine - have)} 条 ｜ 多余 {len(have - mine)} 条")
        for m in sorted(mine - have)[:20]:
            print(f"    缺: {m}")
        for m in sorted(have - mine)[:10]:
            print(f"    多: {m}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
