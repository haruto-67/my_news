#!/usr/bin/env python3
"""data/YYYY-MM-DD.json の各記事に、元記事のOGP画像URL(image)を付け足す。

元記事のHTMLの先頭だけを読み、og:image / twitter:image を探す。
取れなかった記事は image を付けない（ページ側は写真なしの表示になる）。
1記事の失敗で全体を止めない。
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 my-news-bot/1.0"
)
TIMEOUT_SEC = 12
MAX_BYTES = 512 * 1024  # <head> 内にあるはずなので先頭だけ読めば足りる
WORKERS = 6

META_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
ATTR_RE = re.compile(r"""([a-zA-Z:_-]+)\s*=\s*("([^"]*)"|'([^']*)'|([^\s>]+))""")
IMAGE_KEYS = ("og:image:secure_url", "og:image", "og:image:url", "twitter:image", "twitter:image:src")


def read_head(url: str) -> tuple[str, str]:
    with requests.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        timeout=TIMEOUT_SEC,
        stream=True,
        allow_redirects=True,
    ) as resp:
        resp.raise_for_status()
        if "html" not in resp.headers.get("Content-Type", "html"):
            return "", resp.url
        chunks, size = [], 0
        for chunk in resp.iter_content(16384):
            chunks.append(chunk)
            size += len(chunk)
            if size >= MAX_BYTES or b"</head>" in chunk.lower():
                break
        raw = b"".join(chunks)
        encoding = resp.encoding if resp.encoding and resp.encoding.lower() != "iso-8859-1" else "utf-8"
        return raw.decode(encoding, errors="replace"), resp.url


def find_image(page: str, base_url: str) -> str | None:
    found: dict[str, str] = {}
    for tag in META_RE.findall(page):
        attrs = {}
        for m in ATTR_RE.finditer(tag):
            attrs[m.group(1).lower()] = m.group(3) or m.group(4) or m.group(5) or ""
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        if key in IMAGE_KEYS and attrs.get("content") and key not in found:
            found[key] = html.unescape(attrs["content"].strip())
    for key in IMAGE_KEYS:
        if key in found:
            image = urljoin(base_url, found[key])
            if image.startswith("http://"):
                image = "https://" + image[len("http://"):]
            if image.startswith("https://"):
                return image
    return None


def image_for(url: str) -> str | None:
    try:
        page, final_url = read_head(url)
        return find_image(page, final_url) if page else None
    except Exception as exc:  # noqa: BLE001 - 1記事の失敗で全体を止めない
        print(f"[fetch_images] WARN: {url}: {exc}", file=sys.stderr)
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("digest", type=Path, help="data/YYYY-MM-DD.json")
    parser.add_argument("--force", action="store_true", help="image が既にある記事も取り直す")
    args = parser.parse_args()

    digest = json.loads(args.digest.read_text(encoding="utf-8"))
    targets = [
        a
        for c in digest["categories"]
        for a in c["articles"]
        if a.get("url") and (args.force or not a.get("image"))
    ]

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        images = list(pool.map(lambda a: image_for(a["url"]), targets))

    hit = 0
    for article, image in zip(targets, images):
        if image:
            article["image"] = image
            hit += 1

    args.digest.write_text(json.dumps(digest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[fetch_images] {hit}/{len(targets)} articles got an image", file=sys.stderr)


if __name__ == "__main__":
    main()
