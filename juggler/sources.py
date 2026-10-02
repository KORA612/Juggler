"""Fetch config sources with a fallback cascade: direct -> own proxy -> Tor -> disk cache."""
import hashlib
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import links, log
from .paths import CACHE, HTTP_PORT, TOR_HTTP_PORT

TAG = "SRC"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"}
TIMEOUT = {"direct": 12, "proxy": 20, "tor": 60}

DEFAULT_SOURCES = [
    "https://t.me/s/ConfigsHUB",
    "https://raw.githubusercontent.com/barry-far/V2ray-Config/main/All_Configs_Sub.txt",
    "https://raw.githubusercontent.com/MatinGhanbari/v2ray-configs/main/subscriptions/v2ray/all_sub.txt",
    "https://raw.githubusercontent.com/Epodonios/v2ray-configs/main/All_Configs_base64_Sub.txt",
]


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


def fetch_one(url, paths):
    """-> (text or None, path used)."""
    for path in paths:
        try:
            req = urllib.request.Request(url, headers=UA)
            with _opener(path).open(req, timeout=TIMEOUT[path]) as r:
                text = r.read(30_000_000).decode("utf-8", "replace")
            with open(_cache_file(url), "w", encoding="utf-8") as f:
                f.write(text)
            return text, path
        except Exception as e:  # noqa: BLE001 - try the next path
            log.info(TAG, f"{_label(url)} via {path} failed: {type(e).__name__}")
    try:
        with open(_cache_file(url), encoding="utf-8") as f:
            return f.read(), "cache"
    except OSError:
        return None, "none"


def fetch_all(urls, proxy_ok, tor_ok):
    """-> ({source_url: [links]}, report). Telegram pages are reversed (newest first)."""
    paths = ["direct"] + (["proxy"] if proxy_ok else []) + (["tor"] if tor_ok else [])
    result, report = {}, {}

    def job(url):
        text, path = fetch_one(url, paths)
        found = links.extract_links(text) if text else []
        if "t.me/s/" in url:
            found.reverse()
        return url, found, path

    with ThreadPoolExecutor(6) as ex:
        for url, found, path in ex.map(job, urls):
            result[url] = found
            report[url] = {"path": path, "count": len(found), "at": time.time()}
            if path == "none":
                log.error(TAG, f"{_label(url)} unreachable and no cache")
            else:
                (log.ok if path != "cache" else log.warn)(
                    TAG, f"{_label(url)} · {len(found)} links · via {path}")
    return result, report
