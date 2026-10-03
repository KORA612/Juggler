"""Fetch config sources with a fallback cascade: direct -> own proxy -> Tor -> disk cache.

Telegram web previews (t.me/s/<channel>) only show the last ~20 posts, so they are
paginated with ?before=<oldest post id> until TG_PAGES pages or TG_MAX_AGE is hit.
Their links come back newest first.
"""
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from . import links, log
from .paths import CACHE, HTTP_PORT, TOR_HTTP_PORT

TAG = "SRC"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"}
TIMEOUT = {"direct": 12, "proxy": 20, "tor": 60}
TG_PAGES = 3                 # ~60 posts per channel
TG_MAX_AGE = 48 * 3600       # don't page into posts older than this
FETCH_WORKERS = 10
STATIC_MIN_AGE = 60 * 60     # GitHub dumps change every ~3-6 h: don't even ask more often than this

# Channels picked 2026-10-03 by probing ~90 handles found in config names: public
# preview, posted within the last day, many configs per page, mostly VLESS+REALITY/TLS.
TELEGRAM_CHANNELS = [
    "ConfigsHUB", "farah_vpn", "meliproxyy", "soskeynet", "v2ray313", "icv2ray", "i10vpn",
    "v2ray_confiig", "outline_vpn", "prrofile_purple", "v2ray_alpha", "shh_proxy",
    "proxymtalpha", "marambashi", "farazv2ray", "freakconfig", "v2rayng_matsuri",
    "oneclickvpnkeys", "v2ray_phoenix", "v2raysaman", "vconfing", "hex_proxy", "v2raybaaz",
    "iran_v2ray1", "chillguy_vpn", "tootffarangi", "filembad", "v2rayng_fast", "nexus_panell",
    "vasl_bashim",
]
GITHUB_SOURCES = [
    "https://raw.githubusercontent.com/barry-far/V2ray-Config/main/All_Configs_Sub.txt",
    "https://raw.githubusercontent.com/MatinGhanbari/v2ray-configs/main/subscriptions/v2ray/all_sub.txt",
    "https://raw.githubusercontent.com/Epodonios/v2ray-configs/main/All_Configs_base64_Sub.txt",
]
DEFAULT_SOURCES = [f"https://t.me/s/{c}" for c in TELEGRAM_CHANNELS] + GITHUB_SOURCES
# What sources.txt contained before the curated list existed; upgraded automatically.
LEGACY_DEFAULT = ["https://t.me/s/ConfigsHUB"] + GITHUB_SOURCES


def is_telegram(url):
    return "t.me/s/" in url


def _opener(path):
    if path == "direct":
        # An explicit empty ProxyHandler: on Windows urllib would otherwise silently
        # pick up the system proxy (which may be us), making "direct" a lie.
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    port = HTTP_PORT if path == "proxy" else TOR_HTTP_PORT
    p = f"http://127.0.0.1:{port}"
    return urllib.request.build_opener(urllib.request.ProxyHandler({"http": p, "https": p}))


def _cache_file(url):
    return os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest()[:16] + ".txt")


def _label(url):
    return url.split("://", 1)[-1][:60]


def _get(url, path, etag=None):
    """-> (text, etag); text is None if the server says it hasn't changed (304)."""
    headers = dict(UA, **({"If-None-Match": etag} if etag else {}))
    try:
        with _opener(path).open(urllib.request.Request(url, headers=headers), timeout=TIMEOUT[path]) as r:
            return r.read(30_000_000).decode("utf-8", "replace"), r.headers.get("ETag")
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return None, etag
        raise


def _meta_file(url):
    return _cache_file(url)[:-4] + ".meta.json"


def _read_meta(url):
    try:
        with open(_meta_file(url), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_meta(url, **meta):
    with open(_meta_file(url), "w", encoding="utf-8") as f:
        json.dump(meta, f)


def _read_cache(url):
    try:
        with open(_cache_file(url), encoding="utf-8") as f:
            return f.read().split(SEP)
    except OSError:
        return None


def tg_page_info(html):
    """-> (oldest post id on the page or None, oldest post time as epoch or None)."""
    ids = [int(i) for i in re.findall(r'data-post="[^"/]+/(\d+)"', html)]
    times = []
    for t in re.findall(r'<time[^>]+datetime="([^"]+)"', html):
        try:
            times.append(datetime.fromisoformat(t).timestamp())
        except ValueError:
            pass
    return (min(ids) if ids else None), (min(times) if times else None)


def tg_links(pages):
    """Pages are newest-page-first, each page oldest-post-first -> links newest first."""
    out = []
    for html in pages:
        out += reversed(links.extract_links(html))
    return out


def _fetch_pages(url, path, first):
    """Follow ?before= pagination using the path that worked for page 1."""
    pages = [first]
    base = url.split("?")[0]
    for _ in range(TG_PAGES - 1):
        oldest_id, oldest_t = tg_page_info(pages[-1])
        if not oldest_id or (oldest_t and time.time() - oldest_t > TG_MAX_AGE):
            break
        try:
            pages.append(_get(f"{base}?before={oldest_id}", path)[0])
        except Exception:  # noqa: BLE001 - keep what we have
            break
    return pages


SEP = "\n<!-- juggler-page -->\n"


def fetch_one(url, paths):
    """-> (list of page texts or None, path used). Static sources (GitHub dumps) are
    re-checked at most every STATIC_MIN_AGE and with an ETag, so an unchanged
    multi-MB file costs one tiny 304 instead of a full download ("kept")."""
    static = not is_telegram(url)
    meta = _read_meta(url) if static else {}
    cached = _read_cache(url)
    if static and cached and time.time() - meta.get("at", 0) < STATIC_MIN_AGE:
        return cached, "kept"
    for path in paths:
        try:
            text, etag = _get(url, path, etag=meta.get("etag") if cached else None)
            if text is None:  # 304 Not Modified
                _write_meta(url, at=time.time(), etag=meta.get("etag"))
                return cached, "kept"
            pages = _fetch_pages(url, path, text) if not static else [text]
            with open(_cache_file(url), "w", encoding="utf-8") as f:
                f.write(SEP.join(pages))
            if static:
                _write_meta(url, at=time.time(), etag=etag)
            return pages, path
        except Exception:  # noqa: BLE001 - try the next path
            pass
    return (cached, "cache") if cached else (None, "none")


def fetch_all(urls, proxy_ok, tor_ok):
    """-> ({source_url: [links]}, report). Telegram links are newest first."""
    paths = ["direct"] + (["proxy"] if proxy_ok else []) + (["tor"] if tor_ok else [])
    result, report = {}, {}

    def job(url):
        pages, path = fetch_one(url, paths)
        if not pages:
            return url, [], path, 0
        found = tg_links(pages) if is_telegram(url) else links.extract_links("\n".join(pages))
        return url, found, path, len(pages)

    t0 = time.time()
    with ThreadPoolExecutor(FETCH_WORKERS) as ex:
        for url, found, path, npages in ex.map(job, urls):
            result[url] = found
            report[url] = {"path": path, "count": len(found), "pages": npages, "at": time.time()}
    by_path = {}
    for url, r in report.items():
        by_path.setdefault(r["path"], []).append(url)
    total = sum(r["count"] for r in report.values())
    log.ok(TAG, f"{len(urls)} sources in {time.time() - t0:.0f}s · {total} links · "
                + " · ".join(f"{len(u)} via {p}" for p, u in sorted(by_path.items())))
    for p, lvl in (("cache", log.warn), ("none", log.error)):
        if by_path.get(p):
            names = ", ".join(short_name(u) for u in by_path[p][:12])
            more = f" +{len(by_path[p]) - 12} more" if len(by_path[p]) > 12 else ""
            lvl(TAG, f"{'from cache' if p == 'cache' else 'unreachable, no cache'}: {names}{more}")
    return result, report


def short_name(url):
    if is_telegram(url):
        return "@" + url.split("t.me/s/", 1)[1].split("?")[0]
    return url.split("/")[3] if url.count("/") > 3 else _label(url)
