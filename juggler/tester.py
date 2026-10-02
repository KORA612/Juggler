"""Two-stage node testing, built to stay fast.

Stage 1: every candidate in parallel, one HTTPS request each -> real delay + exit
         country. Failures (-1) and Iranian exits are dropped.
Stage 2: download speed for the best few only, with hard per-node and total caps.
"""
import socket
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed, wait

from . import log, procs, xray
from .paths import RUN, TEST_BASE_PORT, XRAY

TAG = "TEST"
TRACE_URL = "https://www.cloudflare.com/cdn-cgi/trace"
SPEED_URL = "https://speed.cloudflare.com/__down?bytes={n}"

PING_TIMEOUT = 5        # seconds per node
PING_WORKERS = 128
STAGE1_BUDGET = 35      # seconds for the whole ping stage
SPEED_TOP = 10          # only this many get a download test
SPEED_BYTES = 1_000_000
SPEED_NODE_CAP = 6      # seconds per node; partial downloads still count
SPEED_WORKERS = 5       # 10 nodes -> 2 rounds -> ~12s worst case
STAGE2_BUDGET = 20
PORT_BASES = (TEST_BASE_PORT, 31000, 41000)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0"}  # urllib's default UA gets 403


def _opener(port):
    proxy = f"http://127.0.0.1:{port}"
    return urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))


def _wait_port(port, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.1)
    return False


def _ping(port):
    """-> (ms, country, exit_ip) or None."""
    t = time.perf_counter()
    try:
        with _opener(port).open(urllib.request.Request(TRACE_URL, headers=UA),
                                timeout=PING_TIMEOUT) as r:
            body = r.read(2048).decode("ascii", "replace")
    except Exception:  # noqa: BLE001 - any failure means dead
        return None
    ms = int((time.perf_counter() - t) * 1000)
    kv = dict(l.split("=", 1) for l in body.splitlines() if "=" in l)
    if "loc" not in kv:
        return None  # some "proxies" return a captive / block page
    return ms, kv.get("loc", ""), kv.get("ip", "")


def _speed(port):
    """-> kbps (0 if nothing arrived). Timed from the first body byte, so the
    proxy's connection setup (already measured as ping) doesn't drag it down."""
    got = 0
    t0 = time.perf_counter()
    t_first = None
    try:
        req = urllib.request.Request(SPEED_URL.format(n=SPEED_BYTES), headers=UA)
        with _opener(port).open(req, timeout=SPEED_NODE_CAP) as r:
            while got < SPEED_BYTES and time.perf_counter() - t0 < SPEED_NODE_CAP:
                chunk = r.read(16384)
                if not chunk:
                    break
                if t_first is None:
                    t_first = time.perf_counter()
                got += len(chunk)
    except Exception:  # noqa: BLE001 - keep whatever arrived
        pass
    if t_first is None or got < 32768:
        return 0
    secs = max(time.perf_counter() - t_first, 0.05)
    return int(got * 8 / 1000 / secs)


class _Tester:
    """A temporary xray with one HTTP inbound per node."""

    def __init__(self, nodes):
        self.nodes = nodes
        self.proc = None
        self.base = None

    def __enter__(self):
        import json
        import os
        for base in PORT_BASES:
            path = os.path.join(RUN, "tester.json")
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


def run(nodes, on_stage1=None):
    """Tests nodes in place and returns the alive ones, best first.
    on_stage1(alive_by_ping) is called as soon as pings are known."""
    if not nodes:
        return []
    t0 = time.time()
    with _Tester(nodes) as tst:
        log.info(TAG, f"stage 1 · pinging {len(nodes)} nodes ({PING_WORKERS} at a time)")
        ex = ThreadPoolExecutor(PING_WORKERS)
        futs = {ex.submit(_ping, tst.port(i)): n for i, n in enumerate(nodes)}
        done, _ = wait(futs, timeout=STAGE1_BUDGET)
        ex.shutdown(wait=False, cancel_futures=True)
        now = time.time()
        alive, dead, iran = [], 0, 0
        for f, n in futs.items():
            res = f.result() if f in done else None
            n.tested_at = now
            n.speed_kbps = -1
            if res is None:
                n.ping_ms, dead = -1, dead + 1
                continue
            n.ping_ms, n.country, n.exit_ip = res
            if n.country.upper() == "IR":
                iran += 1
                continue
            alive.append(n)
        alive.sort(key=lambda n: n.ping_ms)
        log.ok(TAG, f"stage 1 done in {time.time() - t0:.0f}s · {len(alive)} alive · "
                    f"{dead} dead (-1) removed" + (f" · {iran} Iranian exits dropped" if iran else ""))
        if on_stage1:
            on_stage1(list(alive))

        top = alive[:SPEED_TOP]
        if top:
            t1 = time.time()
            idx = {n.id: i for i, n in enumerate(nodes)}
            log.info(TAG, f"stage 2 · speed test on top {len(top)} (≤{SPEED_BYTES // 1000} KB each)")
            ex = ThreadPoolExecutor(SPEED_WORKERS)
            futs = {ex.submit(_speed, tst.port(idx[n.id])): n for n in top}
            try:
                for f in as_completed(futs, timeout=STAGE2_BUDGET):
                    futs[f].speed_kbps = f.result()
            except TimeoutError:
                log.warn(TAG, "stage 2 hit its time budget; unfinished nodes keep no speed")
            ex.shutdown(wait=False, cancel_futures=True)
            best = max((n.speed_kbps for n in top), default=0)
            log.ok(TAG, f"stage 2 done in {time.time() - t1:.0f}s · best {best / 1000:.1f} Mbps")
    alive.sort(key=rank_key)
    return alive


def rank_key(n):
    """Fastest download first, then lowest ping."""
    return (-max(n.speed_kbps, 0), n.ping_ms)
