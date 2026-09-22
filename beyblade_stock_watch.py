"""
Beyblade stock watcher — 孤注一扭 (lastchancetoy.com) + 玩具反斗城 (toysrus.com.hk)

用法:
  pip install requests
  python beyblade_stock_watch.py              # 檢查一次
  python beyblade_stock_watch.py --digest     # 檢查 + 推「今日可買」清單
  python beyblade_stock_watch.py --loop 60    # 每 60 秒檢查一次

可選 Discord:
  DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/xxxx/yyyy
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8"}
TIMEOUT = 25

STATE_PATH = Path(__file__).with_name("beyblade_stock_state.json")

LASTCHANCE_PAGES = 5

SHOPIFY_SOURCES = [
    {
        "store": "孤注一扭",
        "json": "https://lastchancetoy.com/collections/beybladex/products.json",
        "product": "https://lastchancetoy.com/products/{handle}",
        "id_prefix": "lc",
        "pages": 5,
    },
    {
        "store": "Hobby Pocket",
        "json": "https://hobbypocket.com/collections/beyblade/products.json",
        "product": "https://hobbypocket.com/products/{handle}",
        "id_prefix": "hp",
        "pages": 4,
    },
    {
        "store": "T Club",
        "json": "https://www.tclub.com.hk/collections/beybladex/products.json",
        "product": "https://www.tclub.com.hk/products/{handle}",
        "id_prefix": "tclub",
        "pages": 3,
    },
    {
        "store": "Mobile Garage",
        "json": "https://www.mobilegaragehk.com/collections/beyblade-x/products.json",
        "product": "https://www.mobilegaragehk.com/products/{handle}",
        "id_prefix": "mg",
        "pages": 3,
    },
    {
        "store": "Moonroad",
        "json": "https://www.moonroadhk.com/collections/戰鬥陀螺x-beyblade-x/products.json",
        "product": "https://www.moonroadhk.com/products/{handle}",
        "id_prefix": "mr",
        "pages": 4,
    },
]

TRU_URLS = [
    "https://www.toysrus.com.hk/zh-hk/beyblade/",
    "https://www.toysrus.com.hk/zh-hk/search/?q=Beyblade+X",
    "https://www.toysrus.com.hk/zh-hk/search/?q=BeybladeX",
]


def now_hkt() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def discord_send(text: str) -> None:
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        return
    chunks = [text[i : i + 1900] for i in range(0, len(text), 1900)] or [text]
    try:
        for chunk in chunks:
            r = requests.post(url, json={"content": chunk}, timeout=20)
            r.raise_for_status()
    except requests.RequestException as exc:
        print(f"[warn] Discord 發送失敗: {exc}", file=sys.stderr)


def fetch_shopify(src: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    pages = int(src.get("pages") or 3)
    for page in range(1, pages + 1):
        url = f"{src['json']}?limit=30&page={page}"
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        products = r.json().get("products") or []
        if not products:
            break
        for p in products:
            title = p.get("title") or ""
            handle = p.get("handle") or ""
            variants = p.get("variants") or []
            available = any(v.get("available") for v in variants)
            price = variants[0].get("price") if variants else ""
            sku = variants[0].get("sku") if variants else ""
            items.append(
                {
                    "store": src["store"],
                    "id": f"{src['id_prefix']}-{p.get('id')}",
                    "title": title,
                    "sku": sku,
                    "price": price,
                    "available": bool(available),
                    "url": src["product"].format(handle=handle),
                }
            )
    return items


def fetch_lastchance() -> list[dict[str, Any]]:
    return fetch_shopify(SHOPIFY_SOURCES[0])


def _tru_parse_html(html: str, base: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    tile_re = re.compile(
        r'<div[^>]+class="[^"]*product-tile[^"]*"[^>]*>[\s\S]{0,4000}?</div>\s*</div>',
        re.I,
    )
    tiles = tile_re.findall(html)
    if not tiles:
        for m in re.finditer(
            r'href="([^"]+)"[^>]*>\s*([^<]{8,160})\s*<',
            html,
            re.I,
        ):
            href, name = m.group(1), re.sub(r"\s+", " ", m.group(2)).strip()
            blob = href + name
            if "beyblade" not in blob.lower() and "爆旋" not in name and "陀螺" not in name:
                continue
            url = urljoin(base, href)
            items.append(
                {
                    "store": "反斗城",
                    "id": f"tru-{url}",
                    "title": name,
                    "sku": "",
                    "price": "",
                    "available": "unavailable"
                    not in html[max(0, m.start() - 200) : m.end() + 200].lower(),
                    "url": url.split("?")[0],
                }
            )
        seen: set[str] = set()
        uniq = []
        for it in items:
            if it["url"] in seen:
                continue
            seen.add(it["url"])
            uniq.append(it)
        return uniq

    for tile in tiles:
        name_m = re.search(r'class="[^"]*pdp-link[^"]*"[^>]*>\s*([^<]+)', tile, re.I) or re.search(
            r'<a[^>]+class="[^"]*name[^"]*"[^>]*>\s*([^<]+)', tile, re.I
        )
        href_m = re.search(r'href="([^"]+)"', tile, re.I)
        pid_m = re.search(r'data-pid="([^"]+)"', tile, re.I)
        price_m = re.search(r"HK\$\s*([\d.,]+)", tile)
        low = tile.lower()
        unavailable = any(k in low for k in ("unavailable", "售罄", "缺貨", "out of stock", "暫時缺貨"))
        preorder = "pre-order" in low or "預購" in tile or "預訂" in tile
        name = re.sub(r"\s+", " ", name_m.group(1)).strip() if name_m else (pid_m.group(1) if pid_m else "unknown")
        href = href_m.group(1) if href_m else base
        items.append(
            {
                "store": "反斗城",
                "id": f"tru-{pid_m.group(1) if pid_m else href}",
                "title": name,
                "sku": pid_m.group(1) if pid_m else "",
                "price": price_m.group(1) if price_m else "",
                "available": (not unavailable) or preorder,
                "url": urljoin(base, href),
            }
        )
    return items


def fetch_tru() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for url in TRU_URLS:
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
        except requests.RequestException as exc:
            print(f"[warn] 反斗城讀取失敗 {url}: {exc}", file=sys.stderr)
            continue
        for it in _tru_parse_html(r.text, url):
            key = it["id"]
            if key in seen:
                continue
            seen.add(key)
            items.append(it)
    return items


def snapshot(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {it["id"]: it for it in items}


def diff(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    old_ids = set(old)
    new_ids = set(new)
    for _id in sorted(new_ids - old_ids):
        it = new[_id]
        flag = "有貨/可訂" if it.get("available") else "已上架（暫未能買）"
        lines.append(f"[新商品][{it['store']}] {flag}  {it['title']}  {it.get('url','')}")
    for _id in sorted(old_ids & new_ids):
        a, b = old[_id], new[_id]
        if bool(a.get("available")) != bool(b.get("available")):
            if b.get("available"):
                lines.append(f"[返貨][{b['store']}] {b['title']}  {b.get('url','')}")
            else:
                lines.append(f"[售罄][{b['store']}] {b['title']}")
    return lines


def build_digest(items: list[dict[str, Any]]) -> str:
    buy = [it for it in items if it.get("available")]
    lines = [
        f"今日可買陀螺（{now_hkt()} HKT）",
        f"合共 {len(buy)} 件有貨/可訂（香港網店）",
    ]
    by_store: dict[str, list[dict[str, Any]]] = {}
    for it in buy:
        by_store.setdefault(str(it["store"]), []).append(it)
    for store, rows in by_store.items():
        lines.append(f"\n【{store}】")
        for it in rows:
            extra = f"  HK${it['price']}" if it.get("price") else ""
            lines.append(f"• {it['title']}{extra}")
            if it.get("url"):
                lines.append(f"  {it['url']}")
    if not buy:
        lines.append("而家兩間都未掃到可買貨。")
    return "\n".join(lines)


def check_once(state: dict[str, Any], digest: bool = False) -> dict[str, Any]:
    print(f"\n=== {now_hkt()} 開始檢查 ===")
    items: list[dict[str, Any]] = []
    for src in SHOPIFY_SOURCES:
        try:
            got = fetch_shopify(src)
            print(f"{src['store']}：讀到 {len(got)} 件")
            items.extend(got)
        except Exception as exc:
            print(f"[error] {src['store']}: {exc}", file=sys.stderr)
    try:
        tru = fetch_tru()
        print(f"反斗城：讀到 {len(tru)} 件（頁面解析，可能唔完整）")
        items.extend(tru)
    except Exception as exc:
        print(f"[error] 反斗城: {exc}", file=sys.stderr)

    new_snap = snapshot(items)
    old_snap = state.get("items") or {}
    changes = diff(old_snap, new_snap)

    if not old_snap:
        print("第一次執行，已建立基準。之後有變動先會通知。")
        avail = [it for it in items if it.get("available")]
        print(f"而家標記為可買/可訂：{len(avail)} 件")
        for it in avail[:15]:
            print(f"  - [{it['store']}] {it['title'][:80]}")
        if len(avail) > 15:
            print(f"  ... 仲有 {len(avail) - 15} 件")
    elif not changes:
        print("冇新貨、冇返貨。")
    else:
        print("*** 發現變動 ***")
        msg = f"Beyblade 庫存更新 {now_hkt()}\n" + "\n".join(changes)
        print(msg)
        discord_send(msg)

    if digest:
        text = build_digest(items)
        print("--- 00 分摘要 ---")
        print(text)
        discord_send(text)

    state["items"] = new_snap
    state["checked_at"] = now_hkt()
    save_state(state)
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description="檢查孤注一扭 + 反斗城 Beyblade 上架/返貨")
    parser.add_argument("--loop", type=int, default=0, metavar="SEC", help="每隔幾秒再查一次，0 = 只查一次")
    parser.add_argument("--digest", action="store_true", help="額外推送而家可買清單")
    args = parser.parse_args()

    digest = args.digest or datetime.now().minute == 0
    state = load_state()
    interval = max(0, args.loop)
    if interval and interval < 30:
        print("為咗唔好洗爆對方網站，最短建議 30 秒，已幫你改做 30。")
        interval = 30

    try:
        while True:
            state = check_once(state, digest=digest)
            if not interval:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
