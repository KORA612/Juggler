"""Xray config builders, validation and the long-running main instance."""
import json
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import links, log, procs
from .paths import (API_PORT, HTTP_PORT, IS_WIN, RUN, SOCKS_PORT, TOR_HTTP_PORT,
                    TOR_SOCKS_PORT, VALID_CACHE, XRAY)

TAG = "XRAY"
NO_WINDOW = 0x08000000 if IS_WIN else 0
PROBE_URL = "https://www.gstatic.com/generate_204"

_version = None


def version():
    global _version
    if _version is None:
        out = subprocess.run([XRAY, "version"], capture_output=True, text=True,
                             creationflags=NO_WINDOW).stdout
        m = re.search(r"Xray (\S+)", out)
        _version = m.group(1) if m else "unknown"
    return _version


# ---------- validation ----------

def test_config(cfg, name="test"):
    """`xray run -test`; returns (ok, error_text)."""
    path = os.path.join(RUN, f"{name}-{threading.get_ident()}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    try:
        r = subprocess.run([XRAY, "run", "-test", "-c", path], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60,
                           creationflags=NO_WINDOW)
        return r.returncode == 0, (r.stdout + r.stderr)[-600:]
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _load_cache():
    try:
        with open(VALID_CACHE, encoding="utf-8") as f:
            data = json.load(f)
        return data.get(version(), {})
    except (OSError, ValueError):
        return {}


def _save_cache(cache):
    if len(cache) > 50000:  # keep it bounded; it is only a speed-up
        cache = dict(list(cache.items())[-30000:])
    tmp = VALID_CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({version(): cache}, f)
    os.replace(tmp, VALID_CACHE)


def validate(nodes, chunk=150, workers=6):
    """Keep only nodes whose outbound Xray accepts. Bad ones are found by bisection;
    verdicts are cached per Xray version, so only new links cost anything."""
    cache = _load_cache()
    todo = [n for n in nodes if n.id not in cache]
    verdict = {}

    def ok(group):
        cfg = {"log": {"loglevel": "none"},
               "outbounds": [links.to_outbound(n, f"o{i}") for i, n in enumerate(group)]}
        return test_config(cfg, "validate")[0]

    def check(group):
        if ok(group):
            verdict.update({n.id: True for n in group})
        elif len(group) == 1:
            verdict[group[0].id] = False
        else:
            mid = len(group) // 2
            check(group[:mid])
            check(group[mid:])

    if todo:
        with ThreadPoolExecutor(workers) as ex:
            list(ex.map(check, [todo[i:i + chunk] for i in range(0, len(todo), chunk)]))
        cache.update(verdict)
        _save_cache(cache)
    return [n for n in nodes if cache.get(n.id)]


# ---------- config builders ----------

def build_tester(nodes, base_port):
    """One HTTP inbound per node, so plain urllib can test each through its own port."""
    return {
        "log": {"loglevel": "none"},
        "inbounds": [{"tag": f"t{i}", "listen": "127.0.0.1", "port": base_port + i,
                      "protocol": "http"} for i in range(len(nodes))],
        "outbounds": [links.to_outbound(n, f"o{i}") for i, n in enumerate(nodes)],
        "routing": {"rules": [{"inboundTag": [f"t{i}"], "outboundTag": f"o{i}"}
                              for i in range(len(nodes))]},
    }


DOMAIN_PREFIXES = ("geosite:", "domain:", "full:", "regexp:", "keyword:")
IP_RE = re.compile(r"^[0-9a-fA-F:.]+(/\d{1,3})?$")


def split_whitelist(entries):
    """whitelist.txt entries -> (domain rules, ip rules) in Xray syntax."""
    domains, ips = [], []
    for e in entries:
        e = e.strip()
        if not e:
            continue
        if e.startswith("geoip:") or (IP_RE.match(e) and any(c.isdigit() for c in e) and ("." in e or ":" in e)):
            ips.append(e)
        elif e.startswith(DOMAIN_PREFIXES):
            domains.append(e)
        else:
            domains.append("domain:" + e.lstrip(".*"))
    return domains, ips


def build_main(nodes, whitelist, tor):
    tags = [f"node-{i:02d}" for i in range(len(nodes))]
    sniff = {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": True}
    outbounds = [links.to_outbound(n, t) for n, t in zip(nodes, tags)]
    outbounds += [{"tag": "direct", "protocol": "freedom"},
                  {"tag": "block", "protocol": "blackhole"}]
    if tor:
        outbounds.append({"tag": "tor", "protocol": "socks",
                          "settings": {"servers": [{"address": "127.0.0.1", "port": TOR_SOCKS_PORT}]}})
    last_resort = "tor" if tor else "direct"

    domains, ips = split_whitelist(whitelist)
    rules = [{"inboundTag": ["torhttp"], "outboundTag": last_resort}]
    if domains:
        rules.append({"domain": domains, "outboundTag": "direct"})
    if ips:
        rules.append({"ip": ips, "outboundTag": "direct"})
    routing = {"domainStrategy": "AsIs", "rules": rules}
    cfg = {
        "log": {"loglevel": "error", "access": "none"},
        "api": {"tag": "api", "listen": f"127.0.0.1:{API_PORT}", "services": ["RoutingService"]},
        "inbounds": [
            {"tag": "socks", "listen": "0.0.0.0", "port": SOCKS_PORT, "protocol": "socks",
             "settings": {"udp": True}, "sniffing": sniff},
            {"tag": "http", "listen": "0.0.0.0", "port": HTTP_PORT, "protocol": "http",
             "sniffing": sniff},
            {"tag": "torhttp", "listen": "127.0.0.1", "port": TOR_HTTP_PORT, "protocol": "http"},
        ],
        "outbounds": outbounds,
        "routing": routing,
    }
    if nodes:
        routing["balancers"] = [{"tag": "best", "selector": ["node-"], "fallbackTag": last_resort,
                                 "strategy": {"type": "leastPing"}}]
        rules.append({"network": "tcp,udp", "balancerTag": "best"})
        cfg["observatory"] = {"subjectSelector": ["node-"], "probeUrl": PROBE_URL,
                              "probeInterval": "1m", "enableConcurrency": True}
    else:
        rules.append({"network": "tcp,udp", "outboundTag": last_resort})
    return cfg


# ---------- main instance ----------

class MainXray:
    def __init__(self):
        self.proc = None
        self.cfg = None
        self.tags = []
        self.lock = threading.Lock()
        self.started_at = 0

    def apply(self, nodes, whitelist, tor):
        """Validate and (re)start. On a bad config the running instance is kept."""
        cfg = build_main(nodes, whitelist, tor)
        ok, err = test_config(cfg, "main-check")
        if not ok:
            log.error(TAG, f"new config rejected, keeping the old one: {err.strip()[-300:]}")
            return False
        with self.lock:
            if self.proc and self.proc.poll() is None and cfg == self.cfg:
                return True
            procs.stop(self.proc)
            path = os.path.join(RUN, "main.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=1)
            self.proc = procs.spawn("xray-main", [XRAY, "run", "-c", path])
            self.cfg = cfg
            self.tags = [f"node-{i:02d}" for i in range(len(nodes))]
            self.started_at = time.time()
            threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()
        time.sleep(0.4)
        if self.proc.poll() is not None:
            log.error(TAG, "main xray exited right after start")
            return False
        mode = f"{len(nodes)} nodes" if nodes else ("Tor only" if tor else "direct only")
        log.ok(TAG, f"main xray up · {mode} · socks :{SOCKS_PORT} · http :{HTTP_PORT}")
        return True

    def _pump(self, p):
        for line in p.stdout:
            line = line.strip()
            # Startup banner, [Info] and the "feature X is deprecated" nags are noise.
            if "[Error]" in line or "panic" in line or "Failed to start" in line:
                log.warn(TAG, line.split("]", 1)[-1].strip()[-200:])

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def _api(self, *args):
        # --server must precede positional args: Go's flag parser stops at the first one.
        cmd, *rest = args
        r = subprocess.run([XRAY, "api", cmd, f"--server=127.0.0.1:{API_PORT}", *rest],
                           capture_output=True, text=True, timeout=5, creationflags=NO_WINDOW)
        return r.returncode == 0, r.stdout

    def active_tag(self):
        """The outbound the balancer currently selects, or None."""
        if not self.tags or not self.alive():
            return None
        try:
            ok, out = self._api("bi", "best")
        except (subprocess.TimeoutExpired, OSError):
            return None
        if not ok:
            return None
        # "- Selecting Override:\n  1  node-03" (if pinned) then "- Selects:\n  1  node-02"
        for section in ("Override:", "Selects:"):
            m = re.search(re.escape(section) + r"\s*\n\s*\d+\s+(node-\d+)", out)
            if m:
                return m.group(1)
        return None

    def pin(self, tag):
        if tag:
            return self._api("bo", "-b", "best", tag)[0]
        return self._api("bo", "-b", "best", "-r")[0]

    def stop(self):
        with self.lock:
            procs.stop(self.proc)
            self.proc = None
