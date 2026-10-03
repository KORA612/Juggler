"""Tor with pluggable-transport bridges (user WebTunnel/obfs4 + built-in Snowflake/meek)."""
import json
import os
import re
import threading
import time
from collections import Counter

from . import log, procs
from .paths import BRIDGES_TXT, IS_WIN, TOR_DATA, TOR_DIR, TOR_SOCKS_PORT, read_lines
from .setup import tor_exe

TAG = "TOR"
PT_TYPES = ("webtunnel", "obfs4", "snowflake", "meek_lite")


def _path(p):
    """A quoted torrc path. Tor's quoted strings use C escapes, so on Windows the
    backslashes must be doubled (forward slashes make Tor call the path relative)."""
    return '"' + (p.replace("\\", "\\\\") if IS_WIN else p) + '"'


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
        f"DataDirectory {_path(TOR_DATA)}",
        f"SocksPort 127.0.0.1:{TOR_SOCKS_PORT}",
        "Log notice stdout",
        "AvoidDiskWrites 1",
        f"GeoIPFile {_path(os.path.join(TOR_DIR, 'data', 'geoip'))}",
        f"GeoIPv6File {_path(os.path.join(TOR_DIR, 'data', 'geoip6'))}",
        "UseBridges 1",
        f"ClientTransportPlugin meek_lite,obfs4,webtunnel exec {lyrebird}",
        f"ClientTransportPlugin snowflake exec {lyrebird}",
    ]
    lines += [f"Bridge {b}" for b in user_bridges() + builtin_bridges()]
    return "\n".join(lines) + "\n"


class Tor:
    """Started on demand only: when sources can't be fetched any other way, or when
    no config works and traffic has nowhere else to go. Stopped again afterwards.
    Its chatty output is summarised instead of streamed into the log."""

    def __init__(self):
        self.proc = None
        self.ready = threading.Event()
        self.progress = 0
        self.started_at = 0
        self._warns = Counter()
        self._lock = threading.Lock()

    def available(self):
        return os.path.exists(tor_exe())

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def status(self):
        if not self.available():
            return "missing"
        if self.is_ready():
            return "ready"
        return "starting" if self.running() else "off"

    def start(self, reason=""):
        if not self.available():
            return False
        with self._lock:
            if self.running():
                return True
            self.ready.clear()
            self.progress = 0
            self._warns = Counter()
            os.makedirs(TOR_DATA, exist_ok=True)
            path = os.path.join(TOR_DATA, "torrc")
            with open(path, "w", encoding="utf-8") as f:
                f.write(torrc())
            log.info(TAG, f"starting Tor fallback{f' ({reason})' if reason else ''} · "
                          f"{len(user_bridges())} bridge(s) + built-in Snowflake/meek")
            self.started_at = time.time()
            self.proc = procs.spawn("tor", [tor_exe(), "-f", path], cwd=os.path.dirname(tor_exe()))
            threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()
        return True

    def ensure(self, timeout, reason=""):
        """Start if needed and wait until it can carry traffic. -> ready?"""
        if not self.start(reason):
            return False
        if self.ready.wait(timeout):
            return True
        log.warn(TAG, f"Tor not ready after {timeout}s (bootstrap {self.progress}%)" + self._warn_summary())
        return False

    def _pump(self, p):
        for line in p.stdout:
            m = re.search(r"Bootstrapped (\d+)%", line)
            if m:
                self.progress = int(m.group(1))
                if self.progress == 100 and not self.ready.is_set():
                    self.ready.set()
                    log.ok(TAG, f"Tor ready in {time.time() - self.started_at:.0f}s" + self._warn_summary())
            elif "[err]" in line:
                log.error(TAG, line.split("]", 1)[-1].strip()[:200])
            elif "[warn]" in line:
                # Bridges failing one by one is normal; count instead of flooding the log.
                self._warns[re.sub(r"\S*\d\S*", "#", line.split("]", 1)[-1].strip())[:90]] += 1
        self.ready.clear()

    def _warn_summary(self):
        n = sum(self._warns.values())
        if not n:
            return ""
        top, _ = self._warns.most_common(1)[0]
        return f" · {n} bridge warning(s), most common: {top}"

    def is_ready(self):
        return self.ready.is_set() and self.running()

    def stop(self, reason=""):
        if self.running() and reason:
            log.info(TAG, f"Tor fallback stopped ({reason})")
        procs.stop(self.proc)
        self.proc = None
        self.ready.clear()
        self.progress = 0
