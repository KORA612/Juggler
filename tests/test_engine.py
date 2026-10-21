import time

import pytest

from juggler import engine, links, scoring


class FakeMain:
    def __init__(self):
        self.syncs, self.pinned, self.ids = [], None, []

    def sync(self, nodes, primary=None):
        self.syncs.append(([n.id for n in nodes], primary.id if primary else None))
        added = len([n for n in nodes if n.id not in self.ids])
        removed = len([i for i in self.ids if i not in {n.id for n in nodes}])
        self.ids = [n.id for n in nodes]
        self.pinned = primary.id if primary else None
        return added, removed

    def alive(self):
        return True

    def active_id(self):
        return self.pinned


class FakeTor:
    def available(self): return False
    def start(self, *a): return False
    def stop(self, *a): pass
    def is_ready(self): return False
    def running(self): return False


@pytest.fixture
def eng(tmp_path, monkeypatch):
    for name in ("STATE_JSON", "SOURCES_TXT", "WHITELIST_TXT", "BRIDGES_TXT"):
        monkeypatch.setattr(engine, name, str(tmp_path / name.lower()))
    e = engine.Engine()
    e.main, e.tor = FakeMain(), FakeTor()
    return e


def mk(i):
    return links.parse(f"trojan://pw{i}@h{i}.com:443?security=tls")


def proven(e, i, ms=500, t=None):
    n = mk(i)
    for k in range(4):
        scoring.record(n, {"ok": True, "ms": ms, "sites": {"yt": True}}, (t or time.time()) - 400 + k)
    e.pool[n.id] = n
    return n


def test_outage_does_not_count_against_configs(eng, monkeypatch):
    nodes = [proven(eng, i) for i in range(4)]
    before = [(n.checks, n.fails) for n in nodes]
    monkeypatch.setattr(eng, "online", lambda: False)
    assert eng._record(nodes, {n.id: {"ok": False} for n in nodes}, "test") is False
    assert [(n.checks, n.fails) for n in nodes] == before


def test_real_failures_do_count_when_online(eng, monkeypatch):
    nodes = [proven(eng, i) for i in range(4)]
    monkeypatch.setattr(eng, "online", lambda: True)
    assert eng._record(nodes, {n.id: {"ok": False} for n in nodes}, "test") is True
    assert all(n.fails == 1 for n in nodes)


def test_primary_is_sticky_then_switches_after_two_failures_live(eng):
    a, b = proven(eng, 1, ms=900), proven(eng, 2, ms=200)
    eng._settle()
    first = eng.primary
    assert first is not None and eng.main.pinned == first.id
    other = b if first is a else a
    scoring.record(first, {"ok": False}, time.time())
    eng._settle()
    assert eng.primary is first                      # one blip is forgiven
    scoring.record(first, {"ok": False}, time.time())
    eng._settle()
    assert eng.primary is other                      # second failure in a row: switch
    assert eng.main.pinned == other.id               # moved live through sync(), no restart
    assert eng.state["last_switch"]["to"] == other.id


def test_retired_config_gets_backoff_and_keeps_its_link(eng):
    keep = proven(eng, 1)
    dead = mk(2)
    for k in range(scoring.DEAD_FAILS):
        scoring.record(dead, {"ok": False}, time.time() - 1000 + k)
    eng.pool[dead.id] = dead
    eng._settle()
    assert dead.id not in eng.pool and keep.id in eng.pool
    entry = eng.state["dead"][dead.id]
    assert entry["strikes"] == 1 and entry["link"] == dead.link
    assert entry["until"] > time.time() + scoring.BACKOFF[0] - 5


def test_add_manual_parses_and_queues(eng):
    text = f"{mk(1).link}\nnot a link\n{mk(2).link}\n{mk(1).link}"
    found, unique = eng.add_manual(text)
    assert (found, unique) == (3, 2)
    name, (nodes,) = eng.jobs.get_nowait()
    assert name == "manual" and len(nodes) == 2


def test_manual_pin_is_respected(eng):
    a, b = proven(eng, 1, ms=200), proven(eng, 2, ms=1500)
    eng._settle()
    assert eng.pin(b.id)
    eng._settle()
    assert eng.primary is b and eng.main.pinned == b.id


def test_pin_switches_at_once_without_waiting_for_the_scheduler(eng):
    a, b = proven(eng, 1, ms=200), proven(eng, 2, ms=1500)
    eng._settle()
    assert eng.pin(b.id)                       # no scheduler running: nothing else would settle
    assert eng.primary is b and eng.main.pinned == b.id
    assert eng.pin(None)                       # back to auto: b still works, so it stays (sticky)
    assert eng.state["pinned"] is None and eng.primary is b


class FakeTesterXray:
    def __init__(self, nodes): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def port(self, i): return 0


def test_speed_test_with_no_download_is_not_counted_as_a_passed_check(eng, monkeypatch):
    a = proven(eng, 1)
    scoring.record(a, {"ok": False}, time.time())          # one failure: one more triggers failover
    health, fails = a.ok_ewma, a.fails
    monkeypatch.setattr(engine.tester, "_Tester", FakeTesterXray)
    monkeypatch.setattr(engine.tester, "_speed_once", lambda port, url: 0)
    eng._job_speed()
    assert (a.ok_ewma, a.fails) == (health, fails)
    monkeypatch.setattr(engine.tester, "_speed_once", lambda port, url: 4200)
    eng._job_speed()
    assert a.fails == 0 and a.ok_ewma > health and a.speed_kbps == 4200
