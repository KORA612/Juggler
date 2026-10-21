import pytest

from juggler import links, scoring

T0 = 1_000_000.0


def node(i=0, **kw):
    n = links.parse(f"trojan://pw{i}@h{i}.com:443?security=tls")
    for k, v in kw.items():
        setattr(n, k, v)
    return n


def ok(ms=500, **sites):
    return {"ok": True, "ms": ms, "sites": sites}


FAIL = {"ok": False}


def test_one_failure_does_not_kill_a_proven_config():
    n = node()
    for i in range(5):
        scoring.record(n, ok(), T0 + i * 60)
    scoring.record(n, FAIL, T0 + 400)
    assert n.fails == 1
    assert not scoring.is_dead(n, T0 + 400)
    assert scoring.score(n) > 0.5


def test_dead_needs_several_failures_spread_over_time():
    n = node()
    scoring.record(n, ok(), T0)
    for i in range(1, 4):
        scoring.record(n, FAIL, T0 + i * 30)        # 3 fails within 90 s of the last success
    assert not scoring.is_dead(n, T0 + 90)          # too soon after it last worked
    assert scoring.is_dead(n, T0 + scoring.DEAD_MIN_AGE + 1)


def test_never_working_config_dies_after_the_fail_count():
    n = node()
    for i in range(scoring.DEAD_FAILS):
        scoring.record(n, FAIL, T0 + i)
    assert scoring.is_dead(n, T0 + 10)


def test_manual_configs_are_never_dead():
    n = node(manual=True)
    for i in range(10):
        scoring.record(n, FAIL, T0 + i * 600)
    assert not scoring.is_dead(n, T0 + 10_000)


def test_reliability_beats_speed():
    steady = node(1)
    flashy = node(2)
    for i in range(6):
        scoring.record(steady, ok(ms=900, yt=True, google=True), T0 + i * 60)
        scoring.record(flashy, ok(ms=300, yt=i % 2 == 0, google=False) if i % 2 == 0 else FAIL, T0 + i * 60)
    steady.kbps_ewma, flashy.kbps_ewma = 800, 12_000      # the "12 Mbps that doesn't load YouTube"
    assert scoring.score(steady) > scoring.score(flashy)


def test_youtube_failure_gates_the_score():
    good, no_yt = node(1), node(2)
    for i in range(4):
        scoring.record(good, ok(yt=True, google=True, ig=True), T0 + i)
        scoring.record(no_yt, ok(yt=False, google=True, ig=True), T0 + i)
    assert scoring.score(no_yt) < scoring.score(good) * 0.6
    assert not scoring.eligible(no_yt)
    assert scoring.eligible(good)


def test_backoff_grows_and_caps():
    assert [scoring.backoff(s) for s in range(6)] == [1800, 7200, 21600, 86400, 86400, 86400]


def test_sticky_primary_keeps_a_healthy_config():
    cur, better = node(1), node(2)
    for i in range(5):
        scoring.record(cur, ok(ms=900, yt=True), T0 + i)
        scoring.record(better, ok(ms=200, yt=True), T0 + i)
    better.kbps_ewma = 20_000
    chosen, reason = scoring.choose_primary(cur, [better, cur], None)
    assert chosen is cur and reason == ""


def test_primary_tolerates_one_failure_then_switches():
    cur, alt = node(1), node(2)
    for i in range(5):
        scoring.record(cur, ok(yt=True), T0 + i)
        scoring.record(alt, ok(yt=True), T0 + i)
    scoring.record(cur, FAIL, T0 + 10)
    assert scoring.choose_primary(cur, [alt, cur], None)[0] is cur
    scoring.record(cur, FAIL, T0 + 20)
    chosen, reason = scoring.choose_primary(cur, [alt, cur], None)
    assert chosen is alt and "failed" in reason


def test_manual_pin_wins_until_it_is_really_broken():
    cur, pinned = node(1), node(2)
    for i in range(4):
        scoring.record(cur, ok(ms=200, yt=True), T0 + i)
        scoring.record(pinned, ok(ms=1500, yt=True), T0 + i)
    assert scoring.choose_primary(cur, [cur, pinned], pinned)[0] is pinned
    for i in range(scoring.DEAD_FAILS):
        scoring.record(pinned, FAIL, T0 + 100 + i)
    chosen, reason = scoring.choose_primary(pinned, [cur, pinned], pinned)
    assert chosen is cur and "pinned" in reason


def test_rank_orders_by_score_and_skips_ineligible():
    a, b, c = node(1), node(2), node(3)
    for i in range(4):
        scoring.record(a, ok(ms=400, yt=True), T0 + i)
        scoring.record(b, ok(ms=1200, yt=True), T0 + i)
        scoring.record(c, ok(ms=100, yt=False), T0 + i)
    assert [n.id for n in scoring.rank([c, b, a])] == [a.id, b.id]


def test_node_scoring_fields_round_trip():
    n = node()
    scoring.record(n, ok(ms=321, yt=True, tg=False), T0)
    d = n.to_dict()
    m = links.parse(d["link"])
    links.restore(m, d)
    assert (m.checks, m.lat_ewma, m.sites, m.last_ok) == (1, 321, {"yt": 1.0, "tg": 0.0}, T0)


def test_ping_shows_the_latest_measurement_while_ranking_uses_the_average():
    n = node()
    scoring.record(n, ok(800), T0)
    scoring.record(n, ok(300), T0 + 1)
    assert n.ping_ms == 300
    assert n.lat_ewma == pytest.approx(800 + 0.35 * (300 - 800))


def test_health_is_pass_rate_times_site_coverage():
    n = links.parse("trojan://pw@h.com:443?security=tls")
    scoring.record(n, {"ok": True, "ms": 300}, 1000)
    assert scoring.health(n) == 1.0                          # no site evidence yet: not penalised
    scoring.record(n, {"ok": True, "ms": 300, "sites": {"yt": True, "google": True, "ig": False, "tg": True}}, 1001)
    assert scoring.health(n) == pytest.approx(0.8)           # Instagram fails; YouTube counts double: 4/5
    scoring.record(n, {"ok": False}, 1002)
    assert scoring.health(n) == pytest.approx(0.65 * 0.8)
