"""The perpetual engine: a scored pool of configs that is checked continuously.

There is no 15-minute reset any more. Small jobs run back to back, each within a
budget, and only ever *add evidence* to the pool:

  fetch      every 15 min: read sources, queue configs we haven't tested
  explore    every 90 s:   test a small batch of queued configs, keep those that work
  active     every 75 s:   quick check of the configs xray currently holds
  reserve    every 10 min: full check (YouTube/Google/Instagram/Telegram) of the rest
  speed      every 30 min: sustained speed test of the best few
  sample     every hour:   re-test retired configs, to measure false negatives
  watch      every 20 s:   (own thread) real traffic through the config in use

A config leaves the pool only after repeated failures over time (scoring.py), the
config in use is sticky, and xray is updated in place, so nothing is cut off at a
fixed moment. When every check in a batch fails, a direct connectivity probe
decides whether that's the configs or simply our own internet being down.
"""
import base64
import json
import os
import queue
import random
import socket
import threading
import time
import urllib.request

from . import connlog, firewall, links, log, scoring, sources, sysproxy, tester
from .paths import (BRIDGES_TXT, HTTP_PORT, SELF_HTTP_PORT, SOCKS_PORT, SOURCES_TXT, STATE_JSON, WEB_PORT,
                    WHITELIST_TXT, read_lines, write_lines)
from .tor import Tor
from .xray import MainXray, build_main, test_config, validate

TAG = "JUGGLER"
SOURCE_INTERVAL = 15 * 60
EXPLORE_EVERY, EXPLORE_BATCH = 90, 48
ACTIVE_EVERY = 75
RESERVE_EVERY, RESERVE_BATCH = 10 * 60, 40
SPEED_EVERY, SPEED_TOP = 30 * 60, 5
SAMPLE_EVERY, SAMPLE_SIZE = 60 * 60, 30
WATCH_EVERY = 20
POOL_MAX = 150           # scored configs kept
ACTIVE = 20              # configs loaded into xray
QUEUE_MAX = 6000
DEAD_MAX = 6000          # retired configs remembered (for backoff and false-negative sampling)
TOR_WAIT = 90
SUB_MAX = 50
OUTAGE_HOSTS = ("www.digikala.com", "www.aparat.com", "www.google.com")   # any one = we're online

DEFAULT_WHITELIST = ["geosite:ir", "geosite:category-ir", "regexp:\\.ir$", "geoip:ir",
                     "geoip:private", "localhost"]
WHITELIST_HEADER = "# Direct-route whitelist: these bypass the proxy. One per line.\n" \
                   "# domain (example.com), full:x.com, regexp:, keyword:, geosite:xx, geoip:xx, IP or CIDR"
SOURCES_HEADER = "# Config sources: Telegram web previews (https://t.me/s/<channel>) or subscription URLs"
BRIDGES_HEADER = "# Tor bridges, one per line (from @GetBridgesBot / bridges.torproject.org).\n" \
                 "# webtunnel / obfs4 / snowflake / meek_lite. Built-in Snowflake + meek are always added."


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def lan_ips():
    """Every private IPv4 address of this machine, the default-route one first."""
    def private(ip):
        a, b = (int(x) for x in ip.split(".")[:2])
        return a == 10 or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31)

    first = lan_ip()
    found = [first] if first != "127.0.0.1" else []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found and private(ip):
                found.append(ip)
    except (OSError, ValueError):
        pass
    return found or ["127.0.0.1"]


def _round_robin(lists, room):
    out, i = [], 0
    while len(out) < room and any(i < len(l) for l in lists):
        for l in lists:
            if i < len(l) and len(out) < room:
                out.append(l[i])
        i += 1
    return out


def pick_candidates(tg_lists, other_lists, room):
    """Fresh configs are the ones most likely to work, so Telegram channels (each
    newest first, interleaved) come first. Big GitHub dumps fill the rest, randomly
    sampled so successive fetches explore different parts of them."""
    out = _round_robin(tg_lists, room)
    shuffled = [random.sample(l, len(l)) for l in other_lists]
    return out + _round_robin(shuffled, room - len(out))


class Engine:
    def __init__(self):
        self.main = MainXray()
        self.tor = Tor()
        self.lock = threading.RLock()        # pool, queue, dead, primary
        self.settling = threading.Lock()     # one _settle at a time
        self.jobs = queue.Queue()            # on-demand work from the dashboard
        self.wake = threading.Event()
        self.stopping = False
        self.phase = "starting"
        self.pool = {}                       # id -> Node, scored
        self.queue = []                      # untested candidates, best first
        self.primary = None                  # Node traffic is pinned to
        self.active = []                     # nodes loaded in xray
        self.due = {}                        # job -> next run (epoch)
        self._online = (0, True)
        self._bw = []                        # (time, bytes) samples for MB/h
        self._saved_at = 0
        self.state = {"safe_mode": True, "sysproxy": {"on": False, "saved": None},
                      "last_fetch": 0, "report": {}, "stats": {}, "pinned": None,
                      "dead": {}, "last_sub": None, "fn": None, "last_switch": None,
                      "connlog": False}
        self._ensure_files()
        self._load()
        self.connlog = connlog.ConnLog(self._route_label)
        self.connlog.enabled = bool(self.state.get("connlog"))
        self.main.on_line = self.connlog.feed

    # ---------- persistence ----------

    def _ensure_files(self):
        if not os.path.exists(SOURCES_TXT) or read_lines(SOURCES_TXT) == sources.LEGACY_DEFAULT:
            write_lines(SOURCES_TXT, sources.DEFAULT_SOURCES, SOURCES_HEADER)
        if not os.path.exists(WHITELIST_TXT):
            write_lines(WHITELIST_TXT, DEFAULT_WHITELIST, WHITELIST_HEADER)
        if not os.path.exists(BRIDGES_TXT):
            write_lines(BRIDGES_TXT, [], BRIDGES_HEADER)

    def _load(self):
        try:
            with open(STATE_JSON, encoding="utf-8") as f:
                saved = json.load(f)
        except (OSError, ValueError):
            return
        self.state.update({k: v for k, v in saved.items() if k not in ("nodes", "pool")})
        # v1.4 stored {"dead": {id: timestamp}}; now {id: {"until", "strikes", "link"}}
        self.state["dead"] = {k: (v if isinstance(v, dict) else {"until": 0, "strikes": 0})
                              for k, v in self.state.get("dead", {}).items()}
        for d in saved.get("pool", saved.get("nodes", [])):
            n = links.parse(d.get("link", ""))
            if n:
                self.pool[n.id] = links.restore(n, d)

    def save(self, force=False):
        if not force and time.time() - self._saved_at < 20:
            return
        with self.lock:
            data = dict(self.state, pool=[n.to_dict() for n in self.pool.values()])
        tmp = STATE_JSON + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, STATE_JSON)
        self._saved_at = time.time()

    # ---------- lifecycle ----------

    def start(self):
        sp = self.state["sysproxy"]
        if sp.get("on") and sysproxy.is_ours(HTTP_PORT):
            log.warn("PROXY", "restoring system proxy left on by a previous crash")
            self.set_sysproxy(False)
        if not self.tor.available():
            log.warn("TOR", "Tor not installed; the Tor fallback is off")
        warm = sorted(self.pool.values(), key=scoring.score, reverse=True)[:ACTIVE]
        if warm:
            log.info(TAG, f"starting with {len(self.pool)} configs remembered from last run; "
                          "re-checking them first")
        # Unpinned until checked: the balancer picks among the remembered configs meanwhile.
        self.main.apply(warm, read_lines(WHITELIST_TXT), self.tor.available())
        self.active = warm
        if not warm:
            self.tor.start("no configs yet")
        now = time.time()
        self.due = {"startup": now, "fetch": now, "explore": now + 20, "active": now + ACTIVE_EVERY,
                    "reserve": now + RESERVE_EVERY, "speed": now + 120, "sample": now + SAMPLE_EVERY}
        threading.Thread(target=self._loop, name="scheduler", daemon=True).start()
        threading.Thread(target=self._watch, name="watch", daemon=True).start()

    def shutdown(self):
        if self.stopping:
            return
        self.stopping = True
        self.wake.set()
        if self.state["sysproxy"].get("on"):
            self.set_sysproxy(False)
        self.main.stop()
        self.tor.stop()
        try:
            self.save(force=True)
        except OSError:
            pass

    # ---------- scheduler ----------

    JOBS = ("startup", "fetch", "active", "explore", "reserve", "speed", "sample")
    INTERVALS = {"fetch": SOURCE_INTERVAL, "explore": EXPLORE_EVERY, "active": ACTIVE_EVERY,
                 "reserve": RESERVE_EVERY, "speed": SPEED_EVERY, "sample": SAMPLE_EVERY}

    def _loop(self):
        while not self.stopping:
            try:
                ran = self._run_next()
            except Exception as e:  # noqa: BLE001 - never let the loop die
                log.error(TAG, f"{self.phase} crashed: {type(e).__name__}: {e}")
                ran = True
            if ran:
                try:
                    self._settle()
                except Exception as e:  # noqa: BLE001
                    log.error(TAG, f"settle crashed: {type(e).__name__}: {e}")
            self.phase = "idle"
            if not ran:
                self.wake.wait(2)
                self.wake.clear()

    def _run_next(self):
        try:
            name, arg = self.jobs.get_nowait()       # dashboard requests go first
            getattr(self, "_job_" + name)(*arg)
            return True
        except queue.Empty:
            pass
        now = time.time()
        for name in self.JOBS:
            if name in self.due and now >= self.due[name]:
                if name == "startup":
                    del self.due[name]
                else:
                    self.due[name] = now + self.INTERVALS[name]
                getattr(self, "_job_" + name)()
                return True
        return False

    def request(self, name, *arg):
        self.jobs.put((name, arg))
        self.wake.set()

    # ---------- evidence ----------

    def online(self):
        """Is *our* internet up? Direct TCP to a few big sites, cached 30 s."""
        at, ok = self._online
        if time.time() - at < 30:
            return ok
        ok = False
        for host in OUTAGE_HOSTS:
            try:
                with socket.create_connection((host, 443), timeout=5):
                    ok = True
                    break
            except OSError:
                continue
        self._online = (time.time(), ok)
        return ok

    def _record(self, nodes, results, what):
        """Fold check results into the pool. A batch where *everything* failed is
        first blamed on our own connection if the direct probe fails too."""
        if nodes and results and not any(r.get("ok") for r in results.values()) \
                and len(results) >= 3 and not self.online():
            log.warn(TAG, f"{what}: every check failed and the internet itself looks down "
                          "→ not counting this against the configs")
            return False
        now = time.time()
        with self.lock:
            for n in nodes:
                r = results.get(n.id)
                if r is None:
                    continue                          # ran out of time: unknown, not failed
                scoring.record(n, r, now)
                if r.get("country"):
                    n.country, n.exit_ip = r["country"], r.get("ip", "")
        return True

    # ---------- jobs ----------

    def _job_startup(self):
        """Re-check what we remembered before trusting it with traffic."""
        nodes = list(self.pool.values())
        if not nodes:
            return
        self.phase = "checking"
        res = tester.check(nodes, "full", budget=90)
        if self._record(nodes, res, "startup check"):
            good = sum(1 for n in nodes if res.get(n.id, {}).get("ok"))
            log.ok(TAG, f"startup check · {good}/{len(nodes)} remembered configs still work")

    def _job_fetch(self):
        self.phase = "fetching"
        urls = read_lines(SOURCES_TXT)
        fetched, report = sources.fetch_all(urls, proxy_ok=bool(self.active),
                                            tor_ok=self.tor.is_ready())
        failed = [u for u, r in report.items() if r["path"] in ("cache", "none")]
        if failed and not self.tor.is_ready() and self.tor.ensure(
                TOR_WAIT, f"{len(failed)} source(s) unreachable directly and via proxy"):
            again, rep2 = sources.fetch_all(failed, proxy_ok=False, tor_ok=True)
            for u in failed:
                if rep2[u]["path"] == "tor":
                    fetched[u], report[u] = again[u], rep2[u]
        self.state["report"] = report
        self.state["last_fetch"] = time.time()

        safe = self.state.get("safe_mode", True)
        now = time.time()
        with self.lock:
            dead = self.state["dead"]
            seen = set(self.pool)
            tg, other, total, parsed, skipped = [], [], 0, 0, 0
            for url, found in fetched.items():
                total += len(found)
                fresh = []
                for link in found:
                    n = links.parse(link)
                    if not n:
                        continue
                    parsed += 1
                    if n.id in seen or (safe and not links.is_safe(n)):
                        continue
                    seen.add(n.id)
                    if dead.get(n.id, {}).get("until", 0) > now:
                        skipped += 1
                        continue
                    n.source, n.first_seen = url, now
                    fresh.append(n)
                (tg if sources.is_telegram(url) else other).append(fresh)
            self.queue = pick_candidates(tg, other, QUEUE_MAX)
            stats = self.state.setdefault("stats", {})
            stats.update(links=total, parsed=parsed, queued=len(self.queue))
        log.info(TAG, f"{total} links → {parsed} parsed → {len(self.queue)} untested configs queued"
                      + (f" · {skipped} retired ones still on backoff" if skipped else ""))
        self.due["explore"] = min(self.due.get("explore", now), now + 2)

    def _take(self, k):
        with self.lock:
            batch, self.queue = self.queue[:k], self.queue[k:]
            self.state["stats"]["queued"] = len(self.queue)
        return batch

    def _retire(self, n, now, why=""):
        d = self.state["dead"].get(n.id, {"strikes": 0})
        self.state["dead"][n.id] = {"until": now + scoring.backoff(d["strikes"]),
                                    "strikes": d["strikes"] + 1, "link": n.link}

    def _job_explore(self):
        batch = self._take(EXPLORE_BATCH)
        if not batch:
            return
        self.phase = "exploring"
        batch = validate(batch)
        quick = tester.check(batch, "quick")
        if not self._record_new(batch, quick):
            return
        answered = [n for n in batch if quick.get(n.id, {}).get("ok")]
        full = tester.check(answered, "full")
        now = time.time()
        kept, fake = 0, 0
        with self.lock:
            for n in batch:
                q, f = quick.get(n.id), full.get(n.id)
                if q is None or (q.get("ok") and f is None):
                    self.queue.append(n)                  # out of time: unknown, try again later
                    continue
                if not q.get("ok") or not (f and f.get("ok")):
                    fake += bool(q.get("ok"))
                    self._retire(n, now)
                    continue
                scoring.record(n, q, now)
                scoring.record(n, f, now)
                if f.get("country"):
                    n.country, n.exit_ip = f["country"], f.get("ip", "")
                if n.country.upper() == "IR":            # an exit inside Iran is useless
                    self._retire(n, now)
                    continue
                self.pool[n.id] = n
                kept += 1
        log.info(TAG, f"explore · {len(batch)} new → {len(answered)} answered → {kept} kept"
                      + (f" · {fake} false positive(s) dropped" if fake else "")
                      + f" · {len(self.queue)} still queued")

    def _record_new(self, batch, quick):
        """Outage guard for untested candidates: don't retire a whole batch for our own outage."""
        if batch and quick and not any(r.get("ok") for r in quick.values()) \
                and len(quick) >= 3 and not self.online():
            log.warn(TAG, "explore: nothing answered and the internet itself looks down → retry later")
            with self.lock:
                self.queue = batch + self.queue
            return False
        return True

    def _job_active(self):
        nodes = [n for n in self.active if n.id in self.pool]
        if not nodes:
            return
        self.phase = "checking"
        res = tester.check(nodes, "quick", budget=40)
        if self._record(nodes, res, "active check"):
            bad = [n for n in nodes if res.get(n.id) and not res[n.id].get("ok")]
            if bad:
                log.warn(TAG, f"active check · {len(nodes) - len(bad)}/{len(nodes)} answered · "
                              + ", ".join(f"{scoring.label(n)} ({n.fails}× in a row)" for n in bad[:4]))

    def _job_reserve(self):
        with self.lock:
            nodes = sorted(self.pool.values(), key=lambda n: n.last_check)[:RESERVE_BATCH]
        if not nodes:
            return
        self.phase = "checking"
        res = tester.check(nodes, "full", budget=90)
        if self._record(nodes, res, "reserve check"):
            ok = sum(1 for n in nodes if res.get(n.id, {}).get("ok"))
            log.info(TAG, f"full check · {ok}/{len(nodes)} configs load YouTube · "
                          + " · ".join(f"{scoring.SITE_NAMES[s]} {sum(1 for r in res.values() if r.get('sites', {}).get(s))}"
                                       for s in ("google", "ig", "tg")))

    def _job_speed(self):
        with self.lock:
            ranked = scoring.rank(self.pool.values())[:SPEED_TOP]
        if not ranked:
            return
        self.phase = "speed"
        res = tester.check(ranked, "speed", budget=60)
        self._record(ranked, res, "speed test")
        best = max((r.get("kbps", 0) for r in res.values()), default=0)
        log.info(TAG, f"speed · top {len(ranked)} measured · best {best / 1000:.1f} Mbps")

    def _job_sample(self):
        """False-negative estimate: how many configs we retired actually work?"""
        with self.lock:
            dead = [(k, v) for k, v in self.state["dead"].items() if v.get("link")]
        if len(dead) < 5:
            return
        sample = [links.parse(v["link"]) for _, v in random.sample(dead, min(SAMPLE_SIZE, len(dead)))]
        sample = validate([n for n in sample if n])
        if not sample:
            return
        self.phase = "sampling"
        quick = tester.check(sample, "quick", relaxed=True, budget=60)
        if not self._record_new(sample, quick):
            return
        alive = [n for n in sample if quick.get(n.id, {}).get("ok")]
        full = tester.check(alive, "full")
        now = time.time()
        revived = 0
        with self.lock:
            for n in alive:
                f = full.get(n.id)
                if f and f.get("ok"):
                    scoring.record(n, quick[n.id], now)
                    scoring.record(n, f, now)
                    if f.get("country"):
                        n.country, n.exit_ip = f["country"], f.get("ip", "")
                    self.pool[n.id] = n
                    self.state["dead"].pop(n.id, None)
                    revived += 1
            self.state["fn"] = {"sampled": len(sample), "revived": revived, "at": now}
        log.info(TAG, f"false-negative check · {len(sample)} retired configs re-tested with relaxed "
                      f"timeouts → {revived} actually work ({100 * revived // len(sample)} %)")

    # dashboard-requested jobs

    def _job_test(self, ids):
        with self.lock:
            nodes = [self.pool[i] for i in ids if i in self.pool]
        if not nodes:
            return
        self.phase = "testing"
        res = tester.check(nodes, "full", budget=60)
        self._record(nodes, res, "manual test")
        for n in nodes:
            r = res.get(n.id, {})
            s = " ".join(f"{k}{'✓' if v else '✗'}" for k, v in r.get("sites", {}).items())
            log.info(TAG, f"test {scoring.label(n)} · {'OK' if r.get('ok') else 'FAILED'}"
                          + (f" · {r.get('ms')} ms · {s}" if r.get("ok") else ""))

    def _job_test_all(self):
        with self.lock:
            nodes = list(self.pool.values())
        self.phase = "testing"
        for i in range(0, len(nodes), RESERVE_BATCH):
            part = nodes[i:i + RESERVE_BATCH]
            res = tester.check(part, "full", budget=90)
            self._record(part, res, "test all")
            self._settle()
        ok = sum(1 for n in nodes if n.fails == 0 and n.last_check)
        log.ok(TAG, f"test all · {ok}/{len(nodes)} configs working")

    def _job_manual(self, nodes):
        self.phase = "testing"
        valid = validate(nodes)
        bad = len(nodes) - len(valid)
        now = time.time()
        with self.lock:
            for n in valid:
                n.manual, n.source, n.first_seen = True, "manual", now
                self.pool[n.id] = self.pool.get(n.id, n)
                self.pool[n.id].manual = True
                self.state["dead"].pop(n.id, None)
        res = tester.check(valid, "full", budget=60)
        self._record(valid, res, "added configs")
        ok = sum(1 for n in valid if res.get(n.id, {}).get("ok"))
        log.ok(TAG, f"added {len(valid)} config(s) by hand · {ok} working now"
                    + (f" · {bad} rejected by xray" if bad else ""))

    # ---------- settle: pool -> xray ----------

    def _settle(self):
        """Pool -> xray. Runs after every scheduler job, and straight from the dashboard
        and the watchdog so a switch never waits behind a test job that takes 10+ s."""
        with self.settling:
            now = time.time()
            changed = False
            with self.lock:
                retired = [n for n in self.pool.values() if scoring.is_dead(n, now) and n is not self.primary]
                for n in retired:
                    self._retire(n, now)
                    del self.pool[n.id]
                if retired:
                    log.info(TAG, f"retired {len(retired)} config(s) after repeated failures: "
                                  + ", ".join(scoring.label(n) for n in retired[:4]))
                if len(self.pool) > POOL_MAX:
                    spare = sorted((n for n in self.pool.values() if not n.manual and n is not self.primary),
                                   key=scoring.score)[:len(self.pool) - POOL_MAX]
                    for n in spare:
                        del self.pool[n.id]
                if len(self.state["dead"]) > DEAD_MAX:
                    keep = sorted(self.state["dead"].items(), key=lambda kv: kv[1].get("until", 0))[-DEAD_MAX:]
                    self.state["dead"] = dict(keep)

                ranked = scoring.rank(self.pool.values())
                current = self.primary if self.primary and self.primary.id in self.pool else None
                manual = self.pool.get(self.state.get("pinned") or "")
                chosen, reason = scoring.choose_primary(current, ranked, manual)
                if chosen is not current:
                    changed = True
                    if current is not None:                 # a real switch, not the first pick
                        self.state["last_switch"] = {"at": now, "reason": reason,
                                                     "to": chosen.id if chosen else None}
                    if chosen is not None:
                        log.ok(TAG, f"now using {scoring.label(chosen)} · {reason}")
                    else:
                        log.warn(TAG, "no working config right now")
                self.primary = chosen
                active = ranked[:ACTIVE]
                if chosen is not None and chosen not in active:
                    active = [chosen] + active[:ACTIVE - 1]
                stats = self.state.setdefault("stats", {})
                stats.update(pool=len(self.pool), eligible=len(ranked), active=len(active),
                             dead=len(self.state["dead"]))
            if [n.id for n in active] != [n.id for n in self.active] or changed \
                    or (chosen is not None and self.main.pinned != chosen.id):
                added, removed = self.main.sync(active, primary=chosen)
                if added or removed:
                    log.info("XRAY", f"updated live · +{added} −{removed} configs · no restart")
                self.active = active
            # Tor is a fallback, not a service: only while nothing else can carry traffic.
            if ranked:
                self.tor.stop("configs are working again")
            elif self.pool or self.state.get("last_fetch"):
                self.tor.start("no working configs, traffic falls back to Tor")
            self._bw.append((now, tester.bytes_used()))
            self._bw = [s for s in self._bw if now - s[0] <= 3600]
            self.save()

    # ---------- watch: the config in use, end to end ----------

    def _route_label(self, tag):
        n = self.pool.get(tag[5:]) if tag.startswith("node-") else None
        return scoring.label(n) if n is not None else tag

    def set_connlog(self, on):
        self.connlog.enabled = self.state["connlog"] = bool(on)
        log.info(TAG, "connection log on: every new connection is shown (in memory only)" if on
                 else "connection log off")
        self.save(force=True)

    def _probe(self):
        """Real use, end to end: YouTube's 204 through xray (its internal port, so these
        checks stay out of the connection log)."""
        p = f"http://127.0.0.1:{SELF_HTTP_PORT}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": p, "https": p}),
                                             urllib.request.HTTPSHandler(context=tester.TLS))
        try:
            req = urllib.request.Request(tester.YT_204, headers=tester.UA)
            t = time.perf_counter()
            with opener.open(req, timeout=10) as r:
                return r.status == 204, int((time.perf_counter() - t) * 1000)
        except Exception:  # noqa: BLE001
            return False, None

    def _watch(self):
        """Every 20 s, real traffic through the config in use. Two failures in a row
        (~40 s) make choose_primary move to the next best config, live, no restart."""
        while not self.stopping:
            time.sleep(WATCH_EVERY)
            if self.stopping or self.primary is None:
                continue
            if not self.main.alive():
                log.warn(TAG, "main xray is not running → restarting it")
                self.main.apply(self.active, read_lines(WHITELIST_TXT), self.tor.available(), self.primary)
                continue
            ok, ms = self._probe()
            if ok:
                with self.lock:
                    if self.primary is not None:
                        scoring.record(self.primary, {"ok": True, "ms": ms}, time.time())
                continue
            if not self.online():
                continue                                   # our internet, not the config
            with self.lock:
                n = self.primary
                if n is None:
                    continue
                scoring.record(n, {"ok": False}, time.time())
                fails = n.fails
            if fails >= 2:
                log.warn(TAG, f"{scoring.label(n)} stopped working ({fails} checks in a row) → switching")
                try:
                    self._settle()
                except Exception as e:  # noqa: BLE001 - the watchdog must not die; the scheduler retries
                    log.error(TAG, f"settle crashed: {type(e).__name__}: {e}")

    # ---------- dashboard actions ----------

    def refresh_now(self):
        self.due["fetch"] = 0
        self.wake.set()
        return True

    def test(self, node_id):
        if node_id not in self.pool:
            return False
        self.request("test", [node_id])
        return True

    def test_all(self):
        self.request("test_all")
        return True

    def add_manual(self, text):
        found = links.extract_links(text or "")
        nodes = {}
        for l in found:
            n = links.parse(l)
            if n:
                nodes[n.id] = n
        if nodes:
            self.request("manual", list(nodes.values()))
        return len(found), len(nodes)

    def remove(self, node_id):
        with self.lock:
            n = self.pool.get(node_id)
            if n is None:
                return False
            if self.state.get("pinned") == node_id:
                self.state["pinned"] = None
            del self.pool[node_id]
            if not n.manual:
                self._retire(n, time.time())
        log.info(TAG, f"removed {scoring.label(n)}")
        self._settle()
        self.save(force=True)
        return True

    def set_safe_mode(self, on):
        self.state["safe_mode"] = bool(on)
        log.info(TAG, f"safe mode {'on' if on else 'off'}")
        if on:
            with self.lock:
                for k in [k for k, n in self.pool.items() if not links.is_safe(n) and not n.manual]:
                    del self.pool[k]
                self.queue = [n for n in self.queue if links.is_safe(n)]
        self._settle()
        self.save(force=True)

    def set_sysproxy(self, on):
        if not sysproxy.SUPPORTED:
            return False, "system proxy toggle is only available on Windows"
        sp = self.state["sysproxy"]
        if on:
            if not sp.get("on"):
                sp["saved"] = sysproxy.read()
            sysproxy.enable(HTTP_PORT, sysproxy.bypass_list(read_lines(WHITELIST_TXT)))
            sp["on"] = True
            log.ok("PROXY", f"Windows system proxy → 127.0.0.1:{HTTP_PORT}")
        else:
            saved = sp.get("saved") or {"ProxyEnable": 0}
            sysproxy.write(saved)
            sp["on"] = False
            log.ok("PROXY", "Windows system proxy restored")
        self.save(force=True)
        return True, ""

    def whitelist(self):
        return read_lines(WHITELIST_TXT)

    def set_whitelist(self, entries):
        entries = [e.strip() for e in entries if e.strip()]
        ok, err = test_config(build_main([], entries, False), "wl-check")
        if not ok:
            reason = err.strip().splitlines()[-1] if err.strip() else "rejected by xray"
            return False, reason[-200:]
        write_lines(WHITELIST_TXT, entries, WHITELIST_HEADER)
        log.ok(TAG, f"whitelist saved · {len(entries)} entries")
        self.main.apply(self.active, entries, self.tor.available(), self.primary)
        if self.state["sysproxy"].get("on"):
            sysproxy.set_bypass(sysproxy.bypass_list(entries))
        return True, ""

    def text_file(self, which):
        path = {"sources": SOURCES_TXT, "bridges": BRIDGES_TXT}[which]
        return read_lines(path)

    def set_text_file(self, which, lines):
        path, header = {"sources": (SOURCES_TXT, SOURCES_HEADER),
                        "bridges": (BRIDGES_TXT, BRIDGES_HEADER)}[which]
        write_lines(path, [l.strip() for l in lines if l.strip()], header)
        log.ok(TAG, f"{which} saved")
        if which == "bridges" and self.tor.running():
            self.tor.stop()
            self.tor.start("bridges changed")

    def pin(self, node_id):
        """Manual choice from the dashboard; None = back to automatic."""
        with self.lock:
            if node_id and node_id not in self.pool:
                return False
            self.state["pinned"] = node_id or None
        log.info(TAG, f"pinned {scoring.label(self.pool[node_id])} by hand" if node_id
                 else "back to automatic choice")
        self._settle()
        return True

    # ---------- views ----------

    def _ranked_view(self):
        with self.lock:
            ranked = scoring.rank(self.pool.values())
            rest = sorted((n for n in self.pool.values() if n not in ranked), key=scoring.score, reverse=True)
            return ranked, rest

    def subscription(self, client_ip=""):
        ranked, _ = self._ranked_view()
        nodes = ranked[:SUB_MAX]
        self.state["last_sub"] = {"at": time.time(), "ip": client_ip, "count": len(nodes)}
        if not nodes:
            log.warn("WEB", f"subscription fetched by {client_ip} but there are no working configs yet "
                            "- v2rayNG will report Failure")
        else:
            log.ok("WEB", f"subscription fetched by {client_ip} · {len(nodes)} configs")
        lines = []
        for n in nodes:
            sp = f" · {n.speed_kbps / 1000:.1f}Mbps" if n.speed_kbps > 0 else ""
            lines.append(links.rename(n.link, f"{n.flag} {n.country} · {n.proto} · {n.ping_ms}ms{sp}"))
        return base64.b64encode("\n".join(lines).encode()).decode()

    def snapshot(self):
        ranked, rest = self._ranked_view()
        loaded = {n.id for n in self.active}
        primary = self.primary.id if self.primary else self.main.active_id()
        nodes = []
        for n in ranked + rest:
            d = n.to_dict()
            d.update(active=n.id == primary, in_balancer=n.id in loaded, eligible=n in ranked,
                     score=round(100 * scoring.score(n)), health=round(100 * scoring.health(n)),
                     sites={k: round(v, 2) for k, v in n.sites.items()})
            nodes.append(d)
        now = time.time()
        bw = 0
        if len(self._bw) >= 2 and self._bw[-1][0] > self._bw[0][0]:
            (t0, b0), (t1, b1) = self._bw[0], self._bw[-1]
            bw = (b1 - b0) / 1e6 * 3600 / max(t1 - t0, 60)
        ips = lan_ips()
        ip = ips[0]
        stats = dict(self.state.get("stats", {}), bandwidth_mb_h=round(bw), fn=self.state.get("fn"))
        return {
            "phase": self.phase,
            "now": now,
            "last_refresh": self.state.get("last_fetch", 0),
            "next_refresh": self.due.get("fetch", 0),
            "interval": SOURCE_INTERVAL,
            "nodes": nodes,
            "active": primary,
            "pinned": self.state.get("pinned"),
            "last_switch": self.state.get("last_switch"),
            "mode": "nodes" if ranked else ("tor" if self.tor.available() else "direct"),
            "xray": self.main.alive(),
            "tor": {"installed": self.tor.available(), "ready": self.tor.is_ready(),
                    "status": self.tor.status(), "progress": self.tor.progress,
                    "bridges": len(self.text_file("bridges"))},
            "safe_mode": self.state.get("safe_mode", True),
            "connlog": {"on": self.connlog.enabled, "count": self.connlog.count},
            "sysproxy": {"supported": sysproxy.SUPPORTED, "on": self.state["sysproxy"].get("on", False)},
            "report": self.state.get("report", {}),
            "stats": stats,
            "lan": {"ip": ip, "ips": ips, "sub": f"http://{ip}:{WEB_PORT}/sub",
                    "socks": f"{ip}:{SOCKS_PORT}", "http": f"{ip}:{HTTP_PORT}"},
            "phone": {"last_sub": self.state.get("last_sub"), "firewall": firewall.status(ip)},
        }
