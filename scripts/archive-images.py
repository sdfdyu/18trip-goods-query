#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
18TRIP 谷子查询 · 图片归档

把数据里还挂在外站（http/https）的图片下载下来，转成 webp 存进 img/，
并把数据里的地址改成本地 img/xxx.webp。

文件名 = sha1(原图地址)[:16] + '.webp'，和之前 1870 张的命名规则一致。

用法（一般由 GitHub Actions 调用）：
  python scripts/archive-images.py --proxy "https://18trip-goods-query.pages.dev/p?u="
  python scripts/archive-images.py --dry-run true
"""

import argparse
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(BASE, "goods-cloud.json")
IMG_DIR = os.path.join(BASE, "img")
PROVENANCE_PATH = os.path.join(BASE, "img-来源对照.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

MAX_BYTES = 8 * 1024 * 1024      # 单张超过 8MB 就跳过，避免仓库被撑爆
MIN_BYTES = 512                  # 太小多半是占位/错误图
TIMEOUT = 45
WEBP_QUALITY = 82


def is_external(u):
    return isinstance(u, str) and re.match(r"^https?://", u, re.I) is not None


def name_for(url):
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16] + ".webp"


def local_rel(name):
    return "img/" + name


def fetch(url, proxy):
    """通过代理（如果有）或直连下载。返回 (bytes, content_type)。"""
    target = (proxy + urllib.parse.quote(url, safe="")) if proxy else url
    req = urllib.request.Request(target, headers={
        "User-Agent": UA,
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        data = resp.read(MAX_BYTES + 1)
    return data, ctype


def to_webp(data, ctype):
    """已经是 webp 就原样存，其他格式转成 webp。"""
    if "webp" in ctype:
        return data
    try:
        from PIL import Image
    except ImportError:
        return None
    im = Image.open(io.BytesIO(data))
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
    else:
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=WEBP_QUALITY, method=6)
    return buf.getvalue()


def collect_urls(goods):
    """收集所有需要处理的外链图片地址（去重，保持顺序）。"""
    seen = []
    index = {}

    def add(u):
        if is_external(u) and u not in index:
            index[u] = len(seen)
            seen.append(u)

    for it in goods:
        add(it.get("image"))
        for u in (it.get("imgs") or []):
            add(u)
    return seen


def rewrite(goods, mapping):
    """把数据里的外链地址换成本地路径。"""
    changed = 0

    def sub(u):
        nonlocal changed
        if isinstance(u, str) and u in mapping:
            changed += 1
            return mapping[u]
        return u

    for it in goods:
        if "image" in it:
            it["image"] = sub(it.get("image"))
        if isinstance(it.get("imgs"), list):
            it["imgs"] = [sub(u) for u in it["imgs"]]
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", default="", help="图片代理前缀，如 https://xxx/p?u=")
    ap.add_argument("--dry-run", default="false", help="true = 只检查不写入")
    ap.add_argument("--limit", type=int, default=0, help="最多处理几张（调试用，0=不限）")
    ap.add_argument("--data", default="", help="指定数据文件（默认仓库根目录的 goods-cloud.json）")
    args = ap.parse_args()

    dry = str(args.dry_run).lower() == "true"
    proxy = (args.proxy or "").strip()
    data_path = args.data or DATA_PATH

    if not os.path.exists(data_path):
        print("找不到数据文件：%s" % data_path, file=sys.stderr)
        return 2

    raw = open(data_path, encoding="utf-8").read()
    payload = json.loads(raw)
    if isinstance(payload, list):
        goods = payload
        payload = {"goods": goods}
    else:
        goods = payload.get("goods") or []
        payload["goods"] = goods

    # 保持原有缩进风格，避免整份文件被重排
    m = re.match(r"\{\s*\n(\s+)\"", raw)
    indent = len(m.group(1)) if m else 2

    os.makedirs(IMG_DIR, exist_ok=True)
    prov = {}
    if os.path.exists(PROVENANCE_PATH):
        try:
            prov = json.load(open(PROVENANCE_PATH, encoding="utf-8"))
        except Exception:
            prov = {}

    urls = collect_urls(goods)
    todo = [u for u in urls if not os.path.exists(os.path.join(IMG_DIR, name_for(u)))]
    if args.limit:
        todo = todo[:args.limit]

    print("外链图片共 %d 张，其中 %d 张还没归档" % (len(urls), len(todo)))
    print("代理：%s" % (proxy or "（直连）"))
    print("模式：%s" % ("DRY RUN" if dry else "实际写入"))
    print("")

    ok, skipped, failed = 0, 0, []
    mapping = {}

    for i, url in enumerate(todo, 1):
        name = name_for(url)
        dest = os.path.join(IMG_DIR, name)
        if os.path.exists(dest):
            mapping[url] = local_rel(name)
            skipped += 1
            continue
        try:
            data, ctype = fetch(url, proxy)
            if not data or len(data) < MIN_BYTES:
                raise ValueError("内容太小 %d 字节" % (len(data or b"")))
            if len(data) > MAX_BYTES:
                raise ValueError("超过 %d MB" % (MAX_BYTES // 1024 // 1024))
            if ctype and not ctype.startswith("image/"):
                raise ValueError("返回的不是图片（%s）" % ctype)
            webp = to_webp(data, ctype)
            if not webp:
                raise ValueError("无法转换（缺 Pillow？）")
            if not dry:
                with open(dest, "wb") as f:
                    f.write(webp)
                prov[local_rel(name)] = url
            mapping[url] = local_rel(name)
            ok += 1
            print("[%d/%d] OK   %-22s %6d KB  %s" % (i, len(todo), name, len(webp) // 1024, url[:70]))
            time.sleep(0.15)
        except Exception as e:
            failed.append((url, str(e)))
            print("[%d/%d] FAIL %-22s %s  %s" % (i, len(todo), name, e, url[:70]))

    changed = 0
    if not dry and mapping:
        changed = rewrite(goods, mapping)
        payload["goods"] = goods
        payload["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=indent)
        if prov:
            with open(PROVENANCE_PATH, "w", encoding="utf-8") as f:
                json.dump(prov, f, ensure_ascii=False, indent=1)

    print("")
    print("=" * 60)
    print("成功 %d 张 · 已存在跳过 %d 张 · 失败 %d 张" % (ok, skipped, len(failed)))
    if not dry:
        print("数据里替换了 %d 处地址" % changed)
    if failed:
        print("")
        print("失败的图（前 10 条）：")
        for u, e in failed[:10]:
            print("  %s  ←  %s" % (e, u[:80]))

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## 图片归档结果\n\n")
            f.write("| 项目 | 数量 |\n|---|---|\n")
            f.write("| 外链图片总数 | %d |\n" % len(urls))
            f.write("| 本次成功下载 | %d |\n" % ok)
            f.write("| 已存在跳过 | %d |\n" % skipped)
            f.write("| 失败 | %d |\n" % len(failed))
            f.write("| 数据地址替换 | %d 处 |\n" % changed)
            if failed:
                f.write("\n失败明细（前 10）：\n\n```\n")
                for u, e in failed[:10]:
                    f.write("%s ← %s\n" % (e, u))
                f.write("```\n")

    # 全军覆没才算失败；有个别死链（比如已经挂掉的老外链）是正常现象
    if todo and ok == 0 and skipped == 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
