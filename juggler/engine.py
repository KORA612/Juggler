"""The refresh loop and everything the dashboard can ask for."""
import base64
import json
import os
import random
import socket
import threading
import time
import urllib.request

from . import firewall, links, log, sources, sysproxy, tester
from .links import Node
from .paths import (BRIDGES_TXT, HTTP_PORT, SOCKS_PORT, SOURCES_TXT, STATE_JSON, WEB_PORT,
                    WHITELIST_TXT, read_lines, write_lines)
from .tor import Tor
from .xray import MainXray, build_main, test_config, validate

TAG = "JUGGLER"
INTERVAL = 15 * 60
MAX_CANDIDATES = 800     # tested per cycle: survivors, then newest Telegram, then GitHub
DEAD_TTL = 6 * 3600      # a config that failed isn't retried for this long
KEEP = 50                # alive nodes remembered between cycles
ACTIVE = 20              # nodes inside the live balancer
TOR_WAIT = 90
WATCH_EVERY = 30         # seconds between end-to-end checks through our own proxy
WATCH_FAILS = 3          # consecutive failures before forcing a refresh (~90 s)
WATCH_COOLDOWN = 180     # don't force refreshes more often than this (internet may be down)

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


class Engine:
    def __init__(self):
        self.main = MainXray()
        self.tor = Tor()
        self.state_lock = threading.RLock()
        self.refresh_lock = threading.Lock()
        self.wake = threading.Event()
        self.stopping = False
        self.phase = "starting"
        self.nodes = []          # alive, best first
        self.active = []         # what the main balancer currently holds
        self.state = {"safe_mode": True, "sysproxy": {"on": False, "saved": None},
                      "last_refresh": 0, "next_refresh": 0, "report": {}, "stats": {},
                      "pinned": None, "dead": {}, "last_sub": None}
        self._ensure_files()
        self._load()

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
        self.state.update({k: v for k, v in saved.items() if k != "nodes"})
        for d in saved.get("nodes", []):
            n = links.parse(d.get("link", ""))
            if n:
                for k in ("source", "first_seen", "ping_ms", "speed_kbps", "country",
                          "exit_ip", "tested_at"):
                    setattr(n, k, d.get(k, getattr(n, k)))
                self.nodes.append(n)

    def save(self):
        with self.state_lock:
            data = dict(self.state, nodes=[n.to_dict() for n in self.nodes])
            tmp = STATE_JSON + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, STATE_JSON)

    # ---------- lifecycle ----------

    def start(self):
        sp = self.state["sysproxy"]
        if sp.get("on") and sysproxy.is_ours(HTTP_PORT):
            log.warn("PROXY", "restoring system proxy left on by a previous crash")
            self.set_sysproxy(False)
        if not self.tor.available():
            log.warn("TOR", "Tor not installed; the Tor fallback is off")
        if self.nodes:
            log.info(TAG, f"starting with {len(self.nodes)} cached nodes from last run")
        self._apply(self.nodes[:ACTIVE])
        if not self.nodes:
            self.tor.start("no configs yet")
        threading.Thread(target=self._loop, name="scheduler", daemon=True).start()
        threading.Thread(target=self._watchdog, name="watchdog", daemon=True).start()

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
            self.save()
        except OSError:
            pass

    def _loop(self):
        while not self.stopping:
            try:
                self.refresh()
            except Exception as e:  # noqa: BLE001 - never let the loop die
                log.error(TAG, f"refresh crashed: {type(e).__name__}: {e}")
                self.phase = "idle"
            self.state["next_refresh"] = time.time() + INTERVAL
            self.wake.wait(INTERVAL)
            self.wake.clear()

    def _probe(self):
        """Real use, end to end: YouTube's 204 through our own HTTP proxy port."""
        p = f"http://127.0.0.1:{HTTP_PORT}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": p, "https": p}))
        try:
            req = urllib.request.Request(tester.YT_204, headers=tester.UA)
            with opener.open(req, timeout=10) as r:
                return r.status == 204
        except Exception:  # noqa: BLE001
            return False

    def _watchdog(self):
        """Don't wait up to 15 minutes when every config in the balancer has died."""
        watch = Watch()
        while not self.stopping:
            time.sleep(WATCH_EVERY)
            if self.stopping:
                return
            busy = self.refresh_lock.locked() or not self.active
            if watch.observe(None if busy else self._probe(), time.time()):
                if not self.main.alive():   # xray itself died: bring it back right away
                    log.warn(TAG, "main xray is not running → restarting it with the current configs")
                    self._apply(self.active)
                log.warn(TAG, f"connection lost: {WATCH_FAILS} checks in a row failed through the "
                              "balancer → refreshing now")
                self.refresh_now()

    def refresh_now(self):
        if self.refresh_lock.locked():
            return False
        self.wake.set()
        return True

    # ---------- the cycle ----------

    def _apply(self, nodes):
        tor_on = self.tor.available()
        if self.main.apply(nodes, read_lines(WHITELIST_TXT), tor_on):
            self.active = list(nodes)
            if self.state.get("pinned") and self.state["pinned"] not in [n.id for n in nodes]:
                self.state["pinned"] = None
            elif self.state.get("pinned"):
                self.pin(self.state["pinned"])

    def refresh(self):
        if not self.refresh_lock.acquire(blocking=False):
            return
        try:
            self._refresh()
        finally:
            self.refresh_lock.release()
            self.phase = "idle"

    def _refresh(self):
        t0 = time.time()
        log.info(TAG, "── refresh cycle ──────────────────────────")
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

        # candidates: survivors, then fresh links (Telegram newest first, GitHub sampled),
        # skipping anything that failed within DEAD_TTL so each cycle explores new configs
        safe = self.state.get("safe_mode", True)
        now = time.time()
        dead = {k: t for k, t in self.state.get("dead", {}).items() if now - t < DEAD_TTL}
        known = {n.id for n in self.nodes}
        tg, other, total_links, parsed, skipped = [], [], 0, 0, 0
        for url, found in fetched.items():
            total_links += len(found)
            fresh = []
            for link in found:
                n = links.parse(link)
                if not n:
                    continue
                parsed += 1
                if n.id in known or (safe and not links.is_safe(n)):
                    continue
                known.add(n.id)
                if n.id in dead:
                    skipped += 1
                    continue
                n.source = url
                n.first_seen = now
                fresh.append(n)
            (tg if sources.is_telegram(url) else other).append(fresh)
        survivors = [n for n in self.nodes if not safe or links.is_safe(n)]
        pool = sum(len(l) for l in tg + other)
        fresh_pick = pick_candidates(tg, other, max(MAX_CANDIDATES - len(survivors), 0))
        candidates = survivors + fresh_pick

        self.phase = "testing"
        valid = validate(candidates)
        stats = {"links": total_links, "parsed": parsed, "fresh": pool,
                 "candidates": len(candidates), "valid": len(valid)}
        n_tg = sum(1 for n in fresh_pick if sources.is_telegram(n.source))
        log.info(TAG, f"{total_links} links → {parsed} parsed → {pool} new unique"
                      + (f" ({skipped} failed recently, skipped)" if skipped else "")
                      + f" → testing {len(valid)}: {len(survivors)} survivors + {n_tg} Telegram"
                      f" + {len(fresh_pick) - n_tg} other")

        def on_stage1(alive):
            self.phase = "speed"
            with self.state_lock:
                self.nodes = alive[:KEEP]
            self._apply(alive[:ACTIVE])
            self.save()

        final = tester.run(valid, on_stage1=on_stage1, stats=stats)
        alive_ids = {n.id for n in final}
        for n in valid:
            if n.id not in alive_ids:
                dead[n.id] = now
        self.state["dead"] = dead
        with self.state_lock:
            self.nodes = final[:KEEP]
            stats["alive"] = len(final)
            self.state["stats"] = stats
            self.state["last_refresh"] = time.time()
        if {n.id for n in final[:ACTIVE]} != {n.id for n in self.active}:
            self._apply(final[:ACTIVE])
        self.save()
        if final:
            b = final[0]
            log.ok(TAG, f"cycle done in {time.time() - t0:.0f}s · {len(final)} alive · best "
                        f"{b.flag} {b.country} {b.ping_ms}ms {b.speed_kbps / 1000:.1f}Mbps")
        else:
            log.warn(TAG, f"cycle done in {time.time() - t0:.0f}s · no working nodes"
                          + (" · traffic goes through Tor" if self.tor.available() else ""))
        # Tor is a fallback, not a service: keep it only while nothing else carries traffic.
        if final:
            self.tor.stop("configs are working again")
        else:
            self.tor.start("no working configs, traffic falls back to Tor")

    # ---------- dashboard actions ----------

    def set_safe_mode(self, on):
        self.state["safe_mode"] = bool(on)
        self.save()
        log.info(TAG, f"safe mode {'on' if on else 'off'}")
        if on:
            with self.state_lock:
                self.nodes = [n for n in self.nodes if links.is_safe(n)]
            if any(not links.is_safe(n) for n in self.active):
                self._apply(self.nodes[:ACTIVE])

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
        self.save()
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
        self._apply(self.active)
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
        if not node_id:
            self.main.pin(None)
            self.state["pinned"] = None
            log.info(TAG, "balancer back on auto (lowest ping)")
            return True
        for i, n in enumerate(self.active):
            if n.id == node_id:
                if self.main.pin(f"node-{i:02d}"):
                    self.state["pinned"] = node_id
                    log.ok(TAG, f"pinned {n.flag} {n.country} {n.proto} {n.host}")
                    return True
        return False

    # ---------- views ----------

    def subscription(self, client_ip=""):
        self.state["last_sub"] = {"at": time.time(), "ip": client_ip, "count": len(self.nodes)}
        if not self.nodes:
            log.warn("WEB", f"subscription fetched by {client_ip} but there are no working configs yet "
                            "- v2rayNG will report Failure")
        else:
            log.ok("WEB", f"subscription fetched by {client_ip} · {len(self.nodes)} configs")
        lines = []
        for n in self.nodes:
            sp = f" · {n.speed_kbps / 1000:.1f}Mbps" if n.speed_kbps > 0 else ""
            lines.append(links.rename(n.link, f"{n.flag} {n.country} · {n.proto} · {n.ping_ms}ms{sp}"))
        return base64.b64encode("\n".join(lines).encode()).decode()

    def snapshot(self):
        active_tag = self.main.active_tag()
        active = None
        if active_tag:
            i = int(active_tag.split("-")[1])
            if i < len(self.active):
                active = self.active[i].id
        if self.state.get("pinned"):
            active = self.state["pinned"]
        in_balancer = {n.id for n in self.active}
        with self.state_lock:
            nodes = [dict(n.to_dict(), active=n.id == active, in_balancer=n.id in in_balancer)
                     for n in self.nodes]
        ip = lan_ip()
        return {
            "phase": self.phase,
            "now": time.time(),
            "last_refresh": self.state["last_refresh"],
            "next_refresh": self.state["next_refresh"],
            "interval": INTERVAL,
            "nodes": nodes,
            "active": active,
            "pinned": self.state.get("pinned"),
            "mode": "nodes" if self.active else ("tor" if self.tor.available() else "direct"),
            "xray": self.main.alive(),
            "tor": {"installed": self.tor.available(), "ready": self.tor.is_ready(),
                    "status": self.tor.status(), "progress": self.tor.progress,
                    "bridges": len(self.text_file("bridges"))},
            "safe_mode": self.state.get("safe_mode", True),
            "sysproxy": {"supported": sysproxy.SUPPORTED, "on": self.state["sysproxy"].get("on", False)},
            "report": self.state.get("report", {}),
            "stats": self.state.get("stats", {}),
            "lan": {"ip": ip, "sub": f"http://{ip}:{WEB_PORT}/sub",
                    "socks": f"{ip}:{SOCKS_PORT}", "http": f"{ip}:{HTTP_PORT}"},
            "phone": {"last_sub": self.state.get("last_sub"), "firewall": firewall.status(ip)},
        }


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
    sampled so successive cycles explore different parts of them."""
    out = _round_robin(tg_lists, room)
    shuffled = [random.sample(l, len(l)) for l in other_lists]
    return out + _round_robin(shuffled, room - len(out))


class Watch:
    """Watchdog decision: trigger after WATCH_FAILS consecutive failed checks, at most
    once per WATCH_COOLDOWN. observe(None) means "not checked" (refresh running or
    no configs) and resets the streak."""

    def __init__(self):
        self.fails = 0
        self.last = 0

    def observe(self, ok, now):
        if ok is None or ok:
            self.fails = 0
            return False
        self.fails += 1
        if self.fails >= WATCH_FAILS and now - self.last >= WATCH_COOLDOWN:
            self.fails, self.last = 0, now
            return True
        return False
