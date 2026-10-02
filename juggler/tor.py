"""Tor with pluggable-transport bridges (user WebTunnel/obfs4 + built-in Snowflake/meek)."""
import json
import os
import re
import threading
import time

from . import log, procs
from .paths import BRIDGES_TXT, IS_WIN, TOR_DATA, TOR_DIR, TOR_SOCKS_PORT, read_lines
from .setup import tor_exe

TAG = "TOR"
PT_TYPES = ("webtunnel", "obfs4", "snowflake", "meek_lite")


def _fwd(p):
    return p.replace("\\", "/")


def _pt_dir():
    return os.path.join(TOR_DIR, "tor", "pluggable_transports")


def builtin_bridges():
    try:
        with open(os.path.join(_pt_dir(), "pt_config.json"), encoding="utf-8") as f:
            b = json.load(f).get("bridges", {})
        return b.get("snowflake", []) + b.get("meek", [])
    except (OSError, ValueError):
        return []


def user_bridges():
    out = []
    for line in read_lines(BRIDGES_TXT):
        line = re.sub(r"^bridge\s+", "", line.strip(), flags=re.I)
        if line.split(" ", 1)[0] in PT_TYPES:
            out.append(line)
    return out


def torrc():
    # Tor doesn't unquote the exec path, so a quoted (or spaced) absolute path breaks.
    # Tor runs with cwd = bin/tor/tor, so a relative path sidesteps both problems.
    lyrebird = "pluggable_transports/lyrebird" + (".exe" if IS_WIN else "")
    lines = [
        f'DataDirectory "{_fwd(TOR_DATA)}"',
        f"SocksPort 127.0.0.1:{TOR_SOCKS_PORT}",
        "Log notice stdout",
        "AvoidDiskWrites 1",
        f'GeoIPFile "{_fwd(os.path.join(TOR_DIR, "data", "geoip"))}"',
        f'GeoIPv6File "{_fwd(os.path.join(TOR_DIR, "data", "geoip6"))}"',
        "UseBridges 1",
        f"ClientTransportPlugin meek_lite,obfs4,webtunnel exec {lyrebird}",
        f"ClientTransportPlugin snowflake exec {lyrebird}",
    ]
    lines += [f"Bridge {b}" for b in user_bridges() + builtin_bridges()]
    return "\n".join(lines) + "\n"


class Tor:
    def __init__(self):
        self.proc = None
        self.ready = threading.Event()
        self.progress = self._logged = 0
        self._seen = {}

    def available(self):
        return os.path.exists(tor_exe())

    def start(self):
        if not self.available():
            return False
        self.stop()
        self.ready.clear()
        self.progress = self._logged = 0
        os.makedirs(TOR_DATA, exist_ok=True)
        path = os.path.join(TOR_DATA, "torrc")
        with open(path, "w", encoding="utf-8") as f:
            f.write(torrc())
        n = len(user_bridges())
        log.info(TAG, f"starting Tor · {n} user bridge(s) + built-in Snowflake/meek")
        self.proc = procs.spawn("tor", [tor_exe(), "-f", path], cwd=os.path.dirname(tor_exe()))
        threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()
        return True

    def _pump(self, p):
        for line in p.stdout:
            m = re.search(r"Bootstrapped (\d+)%", line)
            if m:
                pct = int(m.group(1))
                if pct >= self._logged + 25 or (pct == 100 and self._logged < 100):
                    log.info(TAG, f"bootstrap {pct}%")
                    self._logged = pct
                self.progress = pct
                if pct == 100:
                    self.ready.set()
                    log.ok(TAG, "Tor is ready")
            elif "[err]" in line or "[warn]" in line:
                msg = re.sub(r"\d+", "#", line.split("]", 1)[-1].strip())[:200]
                if time.time() - self._seen.get(msg, 0) > 120:  # same warning at most every 2 min
                    self._seen[msg] = time.time()
                    log.warn(TAG, line.split("]", 1)[-1].strip()[:200])
        self.ready.clear()

    def is_ready(self):
        return self.ready.is_set() and self.proc is not None and self.proc.poll() is None

    def stop(self):
        procs.stop(self.proc)
        self.proc = None
        self.ready.clear()
