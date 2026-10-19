"""How much to trust a config: rolling scores instead of one pass/fail test.

Every check feeds exponentially weighted averages (recent checks count more), so a
single failed handshake dents a proven config instead of deleting it. Ranking puts
reliability first, per-site coverage second, latency third and speed last: a
"12 Mbps" config that doesn't load YouTube must never beat one that works.
"""
ALPHA = 0.35             # weight of the newest check in every rolling average
DEAD_FAILS = 3           # consecutive failures before a config can be retired...
DEAD_MIN_AGE = 600       # ...and only if it hasn't worked for this long (seconds)
BACKOFF = (1800, 7200, 21600, 86400)   # retry a retired config after 30 m, 2 h, 6 h, 24 h
SITE_WEIGHT = {"yt": 2.0, "google": 1.0, "ig": 1.0, "tg": 1.0}
SITE_NAMES = {"yt": "YouTube", "google": "Google", "ig": "Instagram", "tg": "Telegram"}


def _ewma(old, new, first):
    return new if first else old + ALPHA * (new - old)


def record(node, result, now):
    """Fold one check into the node. result = {"ok", "ms"?, "sites"?: {site: bool}, "kbps"?}"""
    ok = bool(result.get("ok"))
    node.ok_ewma = _ewma(node.ok_ewma, 1.0 if ok else 0.0, node.checks == 0)
    node.checks += 1
    node.last_check = node.tested_at = now
    if ok:
        node.fails = 0
        node.last_ok = now
        ms = result.get("ms")
        if ms:
            node.lat_ewma = _ewma(node.lat_ewma, float(ms), not node.lat_ewma)
            node.ping_ms = int(node.lat_ewma)
    else:
        node.fails += 1
    for site, passed in (result.get("sites") or {}).items():
        node.sites[site] = _ewma(node.sites.get(site, 0.0), 1.0 if passed else 0.0, site not in node.sites)
    kbps = result.get("kbps")
    if kbps is not None:
        node.kbps_ewma = _ewma(node.kbps_ewma, float(kbps), not node.kbps_ewma)
        node.speed_kbps = int(node.kbps_ewma)


def _site_score(node):
    known = {s: v for s, v in node.sites.items() if s in SITE_WEIGHT}
    if not known:
        return 0.6                                  # untested: neutral, not zero
    total = sum(SITE_WEIGHT[s] for s in known)
    return sum(SITE_WEIGHT[s] * v for s, v in known.items()) / total


def youtube_broken(node):
    return "yt" in node.sites and node.sites["yt"] < 0.5


def score(node):
    """0..1. Reliability 50 %, site coverage 30 %, latency 15 %, speed 5 %."""
    if node.checks == 0:
        return 0.0
    lat = 1.0 / (1.0 + node.lat_ewma / 800.0) if node.lat_ewma else 0.5
    spd = min(node.kbps_ewma / 4000.0, 1.0) if node.kbps_ewma else 0.3
    s = 0.5 * node.ok_ewma + 0.3 * _site_score(node) + 0.15 * lat + 0.05 * spd
    s *= 0.6 + 0.4 * min(node.checks / 4.0, 1.0)  # little history = less trust
    if youtube_broken(node):
        s *= 0.4
    return s


def eligible(node):
    """May carry traffic: has worked, isn't on a failure streak, loads YouTube."""
    return node.last_ok > 0 and node.fails < 2 and not youtube_broken(node)


def is_dead(node, now):
    if node.manual:
        return False                                # yours: flagged, never deleted
    if node.fails < DEAD_FAILS:
        return False
    return node.last_ok == 0 or now - node.last_ok >= DEAD_MIN_AGE


def backoff(strikes):
    return BACKOFF[min(strikes, len(BACKOFF) - 1)]


def rank(nodes):
    return sorted((n for n in nodes if eligible(n)), key=score, reverse=True)


def label(node):
    return f"{node.flag} {node.country or '??'} {node.proto}"


def choose_primary(current, ranked, manual):
    """The config traffic should use -> (node or None, reason). Sticky by design:
    a working config is kept even when a 'faster' one shows up; it only changes
    after a second failure in a row (one blip is forgiven)."""
    if manual is not None:
        if manual.fails < DEAD_FAILS:
            return manual, "" if current is manual else "pinned by you"
        best = next((n for n in ranked if n is not manual and n.fails == 0), None)
        if best is not None:
            return best, f"pinned config {label(manual)} failed {manual.fails} checks"
    if current is not None and current.last_ok and current.fails < 2:
        return current, ""
    best = next((n for n in ranked if n is not current and n.fails == 0), None)
    if best is None:
        return current, ""
    if current is None:
        return best, "best score"
    return best, f"{label(current)} failed {current.fails} checks in a row"
