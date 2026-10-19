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


def test_build_main_without_nodes_keeps_an_empty_balancer():
    # configs added later through the API must have a balancer to join
    for tor, fallback in ((True, "tor"), (False, "direct")):
        cfg = xray.build_main([], WL, tor=tor)
        assert cfg["routing"]["balancers"][0]["fallbackTag"] == fallback
        assert cfg["routing"]["rules"][-1] == {"network": "tcp,udp", "balancerTag": "best"}


def test_node_tags_are_stable_ids():
    ns = nodes()
    cfg = xray.build_main(ns, WL, tor=False)
    assert [o["tag"] for o in cfg["outbounds"][:len(ns)]] == ["node-" + n.id for n in ns]


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


@needs_xray
def test_live_sync_adds_pins_then_removes(monkeypatch):
    """Against a real xray: hot add/remove through the API, pin moved before removal."""
    ensure_dirs()
    for k, v in (("API_PORT", 29990), ("SOCKS_PORT", 29908), ("HTTP_PORT", 29909), ("TOR_HTTP_PORT", 29910)):
        monkeypatch.setattr(xray, k, v)
    a, b, c = (links.parse(f"trojan://p{i}@t{i}.example.com:443") for i in range(3))
    m = xray.MainXray()
    try:
        assert m.apply([a, b], [], tor=False, primary=a)
        assert m.pinned == a.id
        added, removed = m.sync([b, c], primary=c)
        assert (added, removed) == (1, 1)
        assert m.ids == [b.id, c.id] and m.pinned == c.id
        ok, out = m._api("lso")
        assert ok and "node-" + c.id in out and "node-" + a.id not in out
        assert m.active_id() == c.id
        m.sync([b], primary=None)
        assert m.ids == [b.id] and m.pinned is None
    finally:
        m.stop()


def test_tor_transport_errors_are_summarised_not_printed():
    from juggler import tor
    assert tor._is_transport_noise('Managed proxy "pluggable_transports/lyrebird.exe": Error dialing: x')
    assert tor._is_transport_noise("Proxy Client: unable to connect OR connection")
    assert not tor._is_transport_noise("Could not bind to 127.0.0.1:20850: Address already in use")
