"""Checking configs the way they're actually used.

check(nodes, "quick")   YouTube generate_204 -> alive + latency. One retry with a
                        longer timeout before a failure counts (Iranian RTT spikes).
check(nodes, "full")    quick, then on a *fresh* connection: ~64 KB of the real
                        youtube.com page (DPI often lets one small request through and
                        kills the flow afterwards), small real requests to Google,
                        Instagram and Telegram, and the exit country.
check(nodes, "speed")   sustained download for a few seconds, timed from the first byte.

Cloudflare is deliberately not the liveness target: Workers-based configs can't
reach Cloudflare and would look dead.
"""
import json
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from functools import partial

from . import log, procs, xray
from .paths import RUN, TEST_BASE_PORT, XRAY

TAG = "TEST"
YT_204 = "https://www.youtube.com/generate_204"
YT_PAGE = "https://www.youtube.com/"
TRACE_URL = "https://www.cloudflare.com/cdn-cgi/trace"
SITE_URLS = {   # site -> (url, minimum bytes of a real answer)
    "google": ("https://www.google.com/search?q=juggler&hl=en", 4096),
    "ig": ("https://www.instagram.com/", 4096),
    "tg": ("https://web.telegram.org/k/", 2048),
}
SPEED_URLS = ("https://speed.cloudflare.com/__down?bytes={n}",
              "https://cachefly.cachefly.net/10mb.test")  # fallback for Workers-based nodes

QUICK_TIMEOUT, RETRY_TIMEOUT = 6, 12          # seconds; relaxed mode uses 12 / 18
VERIFY_BYTES, SITE_BYTES = 64_000, 16_000
PAGE_TIMEOUT = 10
SPEED_SECONDS, SPEED_BYTES = 4, 3_000_000   # speed is 5 % of the score: cap the cost
WORKERS = {"quick": 64, "full": 32, "speed": 4, "site": 32}
PORT_BASES = (TEST_BASE_PORT, 31000, 41000)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"}  # urllib's default UA gets 403s

# One TLS context for every test request. urllib's default builds a new one per
# connection, loading the whole CA store into OpenSSL each time; at ~2,000 requests
# per cycle that native memory piled up to ~1 GB that was never returned.
TLS = ssl.create_default_context()

_bytes = 0
_bytes_lock = threading.Lock()


def bytes_used():
    """Body bytes downloaded by tests since start (for the bandwidth readout)."""
    return _bytes


def _count(n):
    global _bytes
    with _bytes_lock:
        _bytes += n


def _opener(port):
    proxy = f"http://127.0.0.1:{port}"
    return urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
                                       urllib.request.HTTPSHandler(context=TLS))


def _wait_port(port, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.1)
    return False


def _get(port, url, limit, timeout, deadline=None, headers=None):
    """-> (status, body). Raises on any network error. Stops at `limit` bytes or at
    `deadline` (perf_counter), so a slow trickle can't stall a worker."""
    req = urllib.request.Request(url, headers=dict(UA, **(headers or {})))
    with _opener(port).open(req, timeout=timeout) as r:
        chunks, got = [], 0
        while got < limit:
            chunk = r.read(min(16384, limit - got))
            if not chunk:
                break
            chunks.append(chunk)
            got += len(chunk)
            if deadline and time.perf_counter() > deadline:
                break
        _count(got)
        return r.status, b"".join(chunks)


def _alive(port, relaxed=False):
    """-> latency ms or None. Only a genuine 204 counts (a block page is a 200)."""
    for timeout in ((RETRY_TIMEOUT, 18) if relaxed else (QUICK_TIMEOUT, RETRY_TIMEOUT)):
        t = time.perf_counter()
        try:
            status, _ = _get(port, YT_204, 1024, timeout)
            if status == 204:
                return int((time.perf_counter() - t) * 1000)
            return None                                # answered, but not YouTube: no retry
        except Exception:  # noqa: BLE001 - try once more with a longer timeout
            continue
    return None


def _page_ok(port, url, need, timeout=PAGE_TIMEOUT):
    try:
        status, body = _get(port, url, need, timeout, deadline=time.perf_counter() + timeout)
        return status == 200 and len(body) >= need
    except Exception:  # noqa: BLE001
        return False


def _geo(port):
    """-> (country, exit_ip); ('', '') if every lookup fails."""
    try:
        _, body = _get(port, TRACE_URL, 2048, 6)
        kv = dict(l.split("=", 1) for l in body.decode("ascii", "replace").splitlines() if "=" in l)
        if kv.get("loc"):
            return kv["loc"], kv.get("ip", "")
    except Exception:  # noqa: BLE001 - Workers-based nodes can't reach Cloudflare
        pass
    try:
        _, body = _get(port, "https://api.country.is/", 2048, 6)
        j = json.loads(body)
        return j.get("country", ""), j.get("ip", "")
    except Exception:  # noqa: BLE001
        pass
    try:
        _, body = _get(port, "https://ipinfo.io/country", 64, 6)
        return body.decode("ascii", "replace").strip()[:2], ""
    except Exception:  # noqa: BLE001
        return "", ""


def _quick(port, node, relaxed):
    ms = _alive(port, relaxed)
    return {"ok": ms is not None, "ms": ms}


def _full(port, node, relaxed):
    ms = _alive(port, relaxed)
    if ms is None:
        return {"ok": False, "ms": None}
    sites = {"yt": _page_ok(port, YT_PAGE, VERIFY_BYTES)}
    for site, (url, need) in SITE_URLS.items():
        sites[site] = _page_ok(port, url, need)
    res = {"ok": sites["yt"], "ms": ms, "sites": sites}
    if not node.country:
        res["country"], res["ip"] = _geo(port)
    return res


def _speed_once(port, url):
    got, t0, t_first = 0, time.perf_counter(), None
    try:
        req = urllib.request.Request(url, headers=dict(UA, Range=f"bytes=0-{SPEED_BYTES - 1}"))
        with _opener(port).open(req, timeout=8) as r:
            while got < SPEED_BYTES:
                chunk = r.read(32768)
                if not chunk:
                    break
                now = time.perf_counter()
                if t_first is None:
                    t_first = now
                got += len(chunk)
                if now - t_first > SPEED_SECONDS:
                    break
    except Exception:  # noqa: BLE001 - keep whatever arrived
        pass
    _count(got)
    if t_first is None or got < 65536:
        return 0
    return int(got * 8 / 1000 / max(time.perf_counter() - t_first, 0.05))


def _speed(port, node, relaxed):
    for url in SPEED_URLS:
        kbps = _speed_once(port, url.format(n=SPEED_BYTES))
        if kbps:
            return {"ok": True, "kbps": kbps}
    return None   # no reading: no evidence either way, so neither a pass nor a failure


def _site(port, node, relaxed, url):
    """Does `url` answer through this config? Any HTTP status below 500 counts: a 403
    or a login wall still means the site is reachable. -> {"ok", "ms" to first answer}"""
    t = time.perf_counter()
    try:
        status, _ = _get(port, url, 1024, PAGE_TIMEOUT)
    except urllib.error.HTTPError as e:
        status = e.code
    except Exception:  # noqa: BLE001
        return {"ok": False, "ms": None}
    ms = int((time.perf_counter() - t) * 1000)
    return {"ok": status < 500, "ms": ms if status < 500 else None}


KINDS = {"quick": _quick, "full": _full, "speed": _speed, "site": _site}


class _Tester:
    """A temporary xray with one HTTP inbound per node."""

    def __init__(self, nodes):
        self.nodes = nodes
        self.proc = None
        self.base = None

    def __enter__(self):
        import os
        for base in PORT_BASES:
            path = os.path.join(RUN, f"tester-{threading.get_ident()}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(xray.build_tester(self.nodes, base), f)
            self.proc = procs.spawn("xray-tester", [XRAY, "run", "-c", path])
            if _wait_port(base + len(self.nodes) - 1) and self.proc.poll() is None:
                self.base = base
                return self
            out = ""
            if self.proc.poll() is not None:
                out = (self.proc.stdout.read() or "")[-200:]
            procs.stop(self.proc)
            log.warn(TAG, f"tester ports {base}+ unavailable, trying next range {out.strip()}")
        raise RuntimeError("could not start the tester xray")

    def port(self, i):
        return self.base + i

    def __exit__(self, *exc):
        procs.stop(self.proc)


def check(nodes, kind="quick", relaxed=False, budget=60, url=None):
    """-> {node.id: result}. Nodes missing from the result ran out of time or gave no
    evidence (unknown, not failed). Only one tester runs at a time; the engine
    serialises its jobs. `url` is the target of kind "site"."""
    if not nodes:
        return {}
    fn = partial(_site, url=url) if kind == "site" else KINDS[kind]
    with _Tester(nodes) as tst:
        ex = ThreadPoolExecutor(min(WORKERS[kind], len(nodes)))
        futs = {ex.submit(fn, tst.port(i), n, relaxed): n for i, n in enumerate(nodes)}
        done, _ = wait(futs, timeout=budget)
        ex.shutdown(wait=False, cancel_futures=True)
        return {futs[f].id: r for f in done if (r := f.result()) is not None}
