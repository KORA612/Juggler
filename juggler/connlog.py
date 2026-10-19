"""Connection log: which device opened which site, through which route.

Xray writes one access line per connection to its stdout; MainXray hands every
line to ConnLog.feed. Off by default (it reveals browsing). When on, lines go to
the terminal and the dashboard log only (in memory, never written to disk), with
repeats of the same device + site + route folded for 60 s.
"""
import re
import threading
import time

from . import log

ACCESS_RE = re.compile(
    r"from (?:(?:tcp|udp):)?(\[?[0-9a-fA-F.:]+?\]?):\d+ accepted (\S+) \[(\S+) (?:->|>>) (\S+)\]")
HIDDEN_INBOUNDS = {"self", "torhttp", "api"}   # Juggler's own traffic
FOLD_SECONDS = 60
LOCAL = {"127.0.0.1", "::1", "localhost"}


def parse(line):
    """One xray access-log line -> {src, dest, net, inbound, outbound} or None."""
    m = ACCESS_RE.search(line)
    if not m:
        return None
    src, dest, inbound, outbound = m.groups()
    net = "udp" if dest.startswith("udp:") else "tcp"
    d = re.sub(r"^(tcp|udp):", "", dest)
    if d.startswith("//"):                       # HTTP CONNECT: //host:port
        d = d[2:]
    elif "://" in d:                             # plain HTTP request: http://host/path
        scheme, rest = d.split("://", 1)
        host = rest.split("/", 1)[0]
        d = host if re.search(r":\d+$", host) else f"{host}:{443 if scheme == 'https' else 80}"
    return {"src": src.strip("[]"), "dest": d, "net": net, "inbound": inbound, "outbound": outbound}


def route_name(tag, label_for):
    if tag == "direct":
        return "direct (whitelist)"
    if tag == "tor":
        return "Tor"
    if tag == "block":
        return "blocked"
    return label_for(tag)


class ConnLog:
    def __init__(self, label_for):
        self.enabled = False
        self.label_for = label_for          # outbound tag -> "🇩🇪 DE vless"
        self.count = 0
        self._seen = {}
        self._lock = threading.Lock()

    def feed(self, line):
        """-> True if `line` was an access line (handled), False otherwise."""
        c = parse(line)
        if c is None:
            return False
        if c["inbound"] in HIDDEN_INBOUNDS:
            return True
        with self._lock:
            self.count += 1
            if not self.enabled:
                return True
            now = time.time()
            host = c["dest"].rsplit(":", 1)[0]
            key = (c["src"], host, c["outbound"])
            if now - self._seen.get(key, 0) < FOLD_SECONDS:
                return True
            self._seen[key] = now
            if len(self._seen) > 5000:
                self._seen = {k: t for k, t in self._seen.items() if now - t < FOLD_SECONDS}
        who = "this PC" if c["src"] in LOCAL else c["src"]
        udp = " (udp)" if c["net"] == "udp" else ""
        log.log("CONN", f"{who} → {c['dest']}{udp} · {route_name(c['outbound'], self.label_for)}", "info")
        return True
