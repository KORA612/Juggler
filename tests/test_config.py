import base64
import json
import os

import pytest

from juggler import engine, links, sysproxy, xray
from juggler.paths import XRAY, ensure_dirs

UUID = "055a1ce8-2a16-4a0d-a2c2-22826c9b2413"
PBK = "Svl81isn16RPAFnjtmYw7A6TPnsEPLHuYYaJht65Rzc"
SAMPLES = [
    f"vless://{UUID}@1.2.3.4:443?security=reality&pbk={PBK}&sid=ab&fp=chrome&type=tcp&flow=xtls-rprx-vision&sni=a.com",
    f"vless://{UUID}@h.com:443?security=tls&type=ws&path=%2Fws&host=h.com",
    f"vless://{UUID}@h.com:443?security=tls&type=xhttp&path=%2Fx&mode=auto",
    f"vless://{UUID}@h.com:443?security=tls&type=httpupgrade&path=%2Fu",
    f"vless://{UUID}@h.com:8080?type=tcp&headerType=http&host=h.com&path=%2F",
    "trojan://pw@t.com:443?type=grpc&serviceName=g&sni=t.com",
    "vmess://" + base64.b64encode(json.dumps({"add": "v.com", "port": 443, "id": UUID, "aid": 0,
                                              "net": "ws", "path": "/", "tls": "tls"}).encode()).decode(),
    "ss://" + base64.b64encode(b"chacha20-ietf-poly1305:pw").decode() + "@5.5.5.5:8388",
    "ss://2022-blake3-aes-128-gcm:AAAAAAAAAAAAAAAAAAAAAA==@5.5.5.5:8389",
    "hy2://auth@h.com:443/?sni=h.com&obfs=salamander&obfs-password=pw",
    "socks://" + base64.b64encode(b"u:p").decode() + "@1.2.3.4:1080",
]
WL = ["geosite:ir", "geosite:category-ir", "regexp:\\.ir$", "geoip:ir", "geoip:private",
      "localhost", "example.com", "1.2.3.0/24"]


def nodes():
    out = [links.parse(l) for l in SAMPLES]
    assert all(out), [l for l, n in zip(SAMPLES, out) if not n]
    return out


def test_split_whitelist():
    d, ip = xray.split_whitelist(WL)
    assert "domain:example.com" in d and "domain:localhost" in d and "geosite:ir" in d
    assert ip == ["geoip:ir", "geoip:private", "1.2.3.0/24"]


def test_build_main_routes_whitelist_direct_then_balancer():
    cfg = xray.build_main(nodes(), WL, tor=True)
    rules = cfg["routing"]["rules"]
    assert rules[0] == {"inboundTag": ["torhttp"], "outboundTag": "tor"}
    assert rules[1]["outboundTag"] == "direct" and "domain" in rules[1]
    assert rules[2]["outboundTag"] == "direct" and "ip" in rules[2]
    assert rules[-1]["balancerTag"] == "best"
    assert cfg["routing"]["balancers"][0]["fallbackTag"] == "tor"
    assert cfg["routing"]["domainStrategy"] == "AsIs"  # poisoned DNS must not hit geoip:private


def test_build_main_without_nodes_uses_tor_or_direct():
    assert xray.build_main([], WL, tor=True)["routing"]["rules"][-1]["outboundTag"] == "tor"
    cfg = xray.build_main([], WL, tor=False)
    assert cfg["routing"]["rules"][-1]["outboundTag"] == "direct"
    assert "balancers" not in cfg["routing"]


def test_build_tester_maps_each_inbound_to_its_outbound():
    cfg = xray.build_tester(nodes()[:3], 21000)
    assert [i["port"] for i in cfg["inbounds"]] == [21000, 21001, 21002]
    assert cfg["routing"]["rules"][2] == {"inboundTag": ["t2"], "outboundTag": "o2"}


def test_bypass_list():
    b = sysproxy.bypass_list(WL)
    assert "*.ir" in b and "192.168.*" in b and "example.com" in b and "*.example.com" in b
    assert "1.2.3.*" in b
    assert len(b) == len(set(b))


def test_pick_candidates_telegram_first_then_github():
    gh = [links.parse(f"trojan://p{i}@a{i}.com:443") for i in range(10)]
    t1 = [links.parse(f"trojan://q{i}@t{i}.com:443") for i in range(3)]
    t2 = [links.parse(f"trojan://r{i}@u{i}.com:443") for i in range(2)]
    pick = engine.pick_candidates([t1, t2], [gh], 8)
    assert len(pick) == 8
    # Telegram channels interleaved, newest (list head) first, before any GitHub link
    assert [n.id for n in pick[:5]] == [t1[0].id, t2[0].id, t1[1].id, t2[1].id, t1[2].id]
    assert {n.id for n in pick[5:]} <= {n.id for n in gh}
    assert len(engine.pick_candidates([t1], [gh], 2)) == 2


def test_rank_key_speed_then_ping():
    from juggler.tester import rank_key
    ns = [links.parse(f"trojan://p{i}@a{i}.com:443") for i in range(3)]
    (ns[0].speed_kbps, ns[0].ping_ms) = (-1, 100)
    (ns[1].speed_kbps, ns[1].ping_ms) = (5000, 900)
    (ns[2].speed_kbps, ns[2].ping_ms) = (-1, 50)
    assert [n.id for n in sorted(ns, key=rank_key)] == [ns[1].id, ns[2].id, ns[0].id]


needs_xray = pytest.mark.skipif(not os.path.exists(XRAY), reason="run `python -m juggler.setup` first")


@needs_xray
def test_every_sample_passes_xray_test():
    ensure_dirs()
    ns = nodes()
    assert len(xray.validate(ns)) == len(ns)
    ok, err = xray.test_config(xray.build_main(ns, WL, tor=True))
    assert ok, err


@needs_xray
def test_validate_drops_only_the_bad_one():
    ensure_dirs()
    good = nodes()[:4]
    bad = links.parse(f"vless://{UUID}@h.com:443?security=reality&pbk=not-a-key&sni=a.com")
    assert bad is not None
    kept = xray.validate(good[:2] + [bad] + good[2:])
    assert [n.id for n in kept] == [n.id for n in good]


def test_watchdog_triggers_after_consecutive_failures_with_cooldown():
    w = engine.Watch()
    n = engine.WATCH_FAILS
    assert not any(w.observe(False, t) for t in range(n - 1))      # not yet
    assert w.observe(False, 1000)                                   # n-th failure in a row
    assert not any(w.observe(False, 1000 + i) for i in range(1, n + 1))   # cooldown holds
    later = 1000 + engine.WATCH_COOLDOWN
    w.fails = 0
    assert [w.observe(False, later + i) for i in range(n)][-1]      # a fresh streak after cooldown
    w2 = engine.Watch()
    w2.observe(False, 0); w2.observe(True, 1)                       # a success resets the streak
    assert not any(w2.observe(False, 2 + i) for i in range(n - 1))
    w3 = engine.Watch()
    w3.observe(False, 0); w3.observe(None, 1)                       # refresh running: reset too
    assert not any(w3.observe(False, 2 + i) for i in range(n - 1))
