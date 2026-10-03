"""Node testing, built to stay fast and to not be fooled.

Stage 1   every candidate in parallel: YouTube generate_204 must answer 204.
          This is liveness + ping. Cloudflare is deliberately not the target:
          Workers-based configs can't reach Cloudflare and would look dead.
Verify    survivors only, on a *fresh* connection: pull ~100 KB of the real
          youtube.com page. Iran's DPI often lets one small request through and
          then kills or throttles the flow; those false positives die here.
          Exit country comes from the Cloudflare trace, with non-Cloudflare
          fallbacks. Iranian exits are dropped.
Stage 2   download speed for the best few only, with hard per-node and total caps.
"""
import json
import socket
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed, wait

from . import log, procs, xray
from .paths import RUN, TEST_BASE_PORT, XRAY

TAG = "TEST"
YT_204 = "https://www.youtube.com/generate_204"
YT_PAGE = "https://www.youtube.com/"
TRACE_URL = "https://www.cloudflare.com/cdn-cgi/trace"
SPEED_URLS = ("https://speed.cloudflare.com/__down?bytes={n}",
              "https://cachefly.cachefly.net/1mb.test")  # fallback for Workers-based nodes

PING_TIMEOUT = 6        # seconds per node
PING_WORKERS = 192
STAGE1_BUDGET = 40      # seconds for the whole ping stage
VERIFY_BYTES = 100_000  # real page bytes a node must deliver
VERIFY_TIMEOUT = 10
VERIFY_WORKERS = 128
VERIFY_BUDGET = 35
SPEED_TOP = 10          # only this many get a download test
SPEED_BYTES = 1_000_000
SPEED_NODE_CAP = 6      # seconds per attempt; partial downloads still count
SPEED_WORKERS = 5
STAGE2_BUDGET = 25
PORT_BASES = (TEST_BASE_PORT, 31000, 41000)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"}  # urllib's default UA gets 403s


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


def _get(port, url, limit, timeout, deadline=None):
    """-> (status, body). Raises on any network error. Stops reading at `limit`
    bytes or at `deadline` (perf_counter) so slow trickles can't stall a worker."""
    with _opener(port).open(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        chunks, got = [], 0
        while got < limit:
            chunk = r.read(min(16384, limit - got))
            if not chunk:
                break
            chunks.append(chunk)
            got += len(chunk)
            if deadline and time.perf_counter() > deadline:
                break
        return r.status, b"".join(chunks)


def _alive(port):
    """-> ping ms, or None. Only a genuine 204 counts (a block page is a 200)."""
    t = time.perf_counter()
    try:
        status, _ = _get(port, YT_204, 1024, PING_TIMEOUT)
    except Exception:  # noqa: BLE001 - any failure means dead
        return None
    return int((time.perf_counter() - t) * 1000) if status == 204 else None


def _verify(port):
    """Real use on a fresh connection: ~100 KB of youtube.com within the time cap."""
    try:
        status, body = _get(port, YT_PAGE, VERIFY_BYTES, VERIFY_TIMEOUT,
                            deadline=time.perf_counter() + VERIFY_TIMEOUT)
    except Exception:  # noqa: BLE001
        return False
    return status == 200 and len(body) >= VERIFY_BYTES


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


def _check(port):
    """Verify stage for one node -> (passed, country, exit_ip)."""
    if not _verify(port):
        return False, "", ""
    return (True, *_geo(port))


def _speed_once(port, url):
    got = 0
    t0 = time.perf_counter()
    t_first = None
    try:
        req = urllib.request.Request(url, headers=dict(UA, Range=f"bytes=0-{SPEED_BYTES - 1}"))
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
    return int(got * 8 / 1000 / max(time.perf_counter() - t_first, 0.05))


def _speed(port):
    """-> kbps. Timed from the first body byte, so connection setup (already
    measured as ping) doesn't drag it down. Falls back to a non-Cloudflare file."""
    for url in SPEED_URLS:
        kbps = _speed_once(port, url.format(n=SPEED_BYTES))
        if kbps:
            return kbps
    return 0


class _Tester:
    """A temporary xray with one HTTP inbound per node."""

    def __init__(self, nodes):
        self.nodes = nodes
        self.proc = None
        self.base = None

    def __enter__(self):
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


def _pool(fn, items, workers, budget):
    """Run fn over items in parallel; -> {item_index: result} for those done in time."""
    ex = ThreadPoolExecutor(workers)
    futs = {ex.submit(fn, it): i for i, it in enumerate(items)}
    done, _ = wait(futs, timeout=budget)
    ex.shutdown(wait=False, cancel_futures=True)
    return {futs[f]: f.result() for f in done}


def run(nodes, on_stage1=None, stats=None):
    """Tests nodes in place and returns the working ones, best first.
    on_stage1(working_by_ping) is called as soon as they are verified.
    `stats` (a dict) receives the per-stage counts."""
    stats = stats if stats is not None else {}
    if not nodes:
        return []
    t0 = time.time()
    with _Tester(nodes) as tst:
        idx = {n.id: i for i, n in enumerate(nodes)}
        log.info(TAG, f"stage 1 · {len(nodes)} nodes → YouTube ({PING_WORKERS} at a time)")
        pings = _pool(_alive, [tst.port(i) for i in range(len(nodes))], PING_WORKERS, STAGE1_BUDGET)
        now = time.time()
        answered = []
        for i, n in enumerate(nodes):
            n.tested_at, n.speed_kbps = now, -1
            n.ping_ms = pings.get(i) or -1
            if n.ping_ms > 0:
                answered.append(n)
        log.ok(TAG, f"stage 1 done in {time.time() - t0:.0f}s · {len(answered)} answered · "
                    f"{len(nodes) - len(answered)} dead (-1) removed")

        t1 = time.time()
        checks = _pool(_check, [tst.port(idx[n.id]) for n in answered], VERIFY_WORKERS, VERIFY_BUDGET)
        alive, fake, iran = [], 0, 0
        unchecked = 0
        for j, n in enumerate(answered):
            if j not in checks:          # ran out of time: unknown, not fake
                n.ping_ms, unchecked = -1, unchecked + 1
                continue
            passed, cc, ip = checks[j]
            if not passed:
                n.ping_ms, fake = -1, fake + 1
                continue
            n.country, n.exit_ip = cc, ip
            if cc.upper() == "IR":
                iran += 1
                continue
            alive.append(n)
        alive.sort(key=lambda n: n.ping_ms)
        log.ok(TAG, f"verify done in {time.time() - t1:.0f}s · {len(alive)} passed real use"
                    + (f" · {fake} false positive(s) removed" if fake else "")
                    + (f" · {iran} Iranian exit(s) dropped" if iran else "")
                    + (f" · {unchecked} not checked in time" if unchecked else ""))
        stats.update(answered=len(answered), verified=len(alive), fake=fake)
        if on_stage1:
            on_stage1(list(alive))

        top = alive[:SPEED_TOP]
        if top:
            t2 = time.time()
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
            log.ok(TAG, f"stage 2 done in {time.time() - t2:.0f}s · best {best / 1000:.1f} Mbps")
    alive.sort(key=rank_key)
    return alive


def rank_key(n):
    """Fastest download first, then lowest ping."""
    return (-max(n.speed_kbps, 0), n.ping_ms)
