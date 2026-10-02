import base64
import json

import pytest

from juggler import links

UUID = "055a1ce8-2a16-4a0d-a2c2-22826c9b2413"
PBK = "Svl81isn16RPAFnjtmYw7A6TPnsEPLHuYYaJht65Rzc"


def b64(s):
    return base64.b64encode(s.encode()).decode()


def test_vless_reality_vision():
    n = links.parse(f"vless://{UUID}@1.2.3.4:443?security=reality&encryption=none&pbk={PBK}"
                    f"&sid=ab12&fp=chrome&type=tcp&flow=xtls-rprx-vision&sni=www.example.com#My%20Node")
    assert (n.proto, n.host, n.port, n.net, n.sec, n.name) == ("vless", "1.2.3.4", 443, "raw", "reality", "My Node")
    user = n.outbound["settings"]["vnext"][0]["users"][0]
    assert user == {"id": UUID, "encryption": "none", "flow": "xtls-rprx-vision"}
    r = n.outbound["streamSettings"]["realitySettings"]
    assert r["publicKey"] == PBK and r["shortId"] == "ab12" and r["serverName"] == "www.example.com"


def test_flow_dropped_on_non_raw_transport():
    n = links.parse(f"vless://{UUID}@h.com:443?security=tls&type=ws&path=%2Fws&host=cdn.h.com&flow=xtls-rprx-vision")
    assert "flow" not in n.outbound["settings"]["vnext"][0]["users"][0]
    ss = n.outbound["streamSettings"]
    assert ss["wsSettings"] == {"path": "/ws", "host": "cdn.h.com"}
    assert ss["tlsSettings"]["serverName"] == "cdn.h.com"


def test_telegram_html_escaping_and_junk():
    raw = (f"vless://{UUID}@45.1.1.1:8443?encryption=none&amp;amp;security=reality&amp;sni=a.net"
           f"&amp;pbk={PBK}&amp;sid=d515&amp;type=tcp&amp;headerType=none@ThePackageCom#x")
    n = links.parse(raw)
    assert n and n.sec == "reality" and n.net == "raw"
    assert "rawSettings" not in n.outbound["streamSettings"]


def test_unescape_does_not_eat_note_param():
    # html.unescape would turn "&note=" into "¬e=" and corrupt the transport.
    text = f"<p>vless://{UUID}@h.com:443?type=ws&amp;note=@chan&amp;security=tls#n</p>"
    found = links.extract_links(text)
    assert found and "¬" not in found[0]
    assert links.parse(found[0]).net == "ws"


def test_allow_insecure_is_dropped():
    assert links.parse(f"vless://{UUID}@h.com:443?security=tls&allowInsecure=1") is None
    assert links.parse(f"trojan://pw@h.com:443?security=tls&insecure=1") is None


def test_removed_transports_are_dropped():
    assert links.parse(f"vless://{UUID}@h.com:443?security=tls&type=h2") is None
    assert links.parse(f"vless://{UUID}@h.com:443?security=tls&type=quic") is None


def test_reality_needs_pbk():
    assert links.parse(f"vless://{UUID}@h.com:443?security=reality&sni=a.com") is None


def test_grpc_and_xhttp():
    g = links.parse(f"trojan://pw@h.com:443?type=grpc&serviceName=svc&mode=multi&sni=h.com")
    assert g.outbound["streamSettings"]["grpcSettings"] == {"serviceName": "svc", "multiMode": True}
    assert g.sec == "tls"  # trojan defaults to tls
    x = links.parse(f"vless://{UUID}@h.com:443?type=xhttp&path=%2Fx&mode=packet-up&security=tls"
                    "&extra=%7B%22xPaddingBytes%22%3A%22100-1000%22%7D")
    xs = x.outbound["streamSettings"]["xhttpSettings"]
    assert xs["mode"] == "packet-up" and xs["extra"] == {"xPaddingBytes": "100-1000"}


def test_vmess_json():
    j = {"v": "2", "ps": "vm", "add": "v.com", "port": "8080", "id": UUID, "aid": "0", "net": "ws",
         "type": "none", "host": "v.com", "path": "/p", "tls": "tls", "sni": "v.com"}
    n = links.parse("vmess://" + b64(json.dumps(j)))
    assert (n.proto, n.port, n.net, n.sec, n.name) == ("vmess", 8080, "ws", "tls", "vm")
    assert n.outbound["settings"]["vnext"][0]["users"][0]["security"] == "auto"


def test_vmess_legacy_alterid_dropped():
    j = {"add": "v.com", "port": 443, "id": UUID, "aid": "64", "net": "tcp"}
    assert links.parse("vmess://" + b64(json.dumps(j))) is None


def test_ss_sip002_base64_and_plain_2022():
    n = links.parse("ss://" + b64("chacha20-ietf-poly1305:secret") + "@5.5.5.5:8388#ss1")
    s = n.outbound["settings"]["servers"][0]
    assert (s["method"], s["password"], s["port"]) == ("chacha20-ietf-poly1305", "secret", 8388)
    n2 = links.parse("ss://2022-blake3-aes-256-gcm:AAA=:BBB=@31.57.185.82:36969?type=tcp#k")
    assert n2.outbound["settings"]["servers"][0]["password"] == "AAA=:BBB="


def test_ss_legacy_whole_base64():
    n = links.parse("ss://" + b64("aes-256-gcm:pw@9.9.9.9:443") + "#old")
    assert n.host == "9.9.9.9" and n.port == 443


def test_ss_stream_cipher_and_plugin_dropped():
    assert links.parse("ss://" + b64("aes-256-cfb:pw") + "@1.1.1.1:1") is None
    assert links.parse("ss://" + b64("aes-256-gcm:pw") + "@1.1.1.1:1?plugin=obfs-local;obfs=http") is None


def test_hysteria2():
    n = links.parse("hy2://auth123@h.com:443/?sni=h.com&obfs=salamander&obfs-password=pw#h")
    assert n.proto == "hysteria2"
    assert n.outbound["protocol"] == "hysteria"
    ss = n.outbound["streamSettings"]
    assert ss["hysteriaSettings"] == {"version": 2, "auth": "auth123"}
    assert ss["finalmask"]["udp"][0]["type"] == "salamander"
    assert links.parse("hysteria2://a@h.com:443/?insecure=1") is None


def test_socks_http_and_plain_urls():
    n = links.parse("socks://" + b64("u:p") + "@1.2.3.4:1080#s")
    assert n.outbound["settings"]["servers"][0]["users"] == [{"user": "u", "pass": "p"}]
    assert links.parse("https://t.me/ConfigsHUB") is None          # not a proxy link
    assert links.parse("https://example.com:443/path") is None


def test_wireguard():
    n = links.parse("wireguard://cHJpdmF0ZQ%3D%3D@engage.cloudflareclient.com:2408"
                    "?publickey=cHVi&address=172.16.0.2/32&reserved=1,2,3&mtu=1280#wg")
    s = n.outbound["settings"]
    assert s["secretKey"] == "cHJpdmF0ZQ==" and s["reserved"] == [1, 2, 3] and s["mtu"] == 1280


def test_dedupe_ignores_name():
    a = links.parse(f"vless://{UUID}@h.com:443?security=tls&type=ws#one")
    b = links.parse(f"vless://{UUID}@h.com:443?security=tls&type=ws#two")
    assert a.id == b.id


def test_is_safe():
    assert not links.is_safe(links.parse(f"vless://{UUID}@h.com:80?type=ws"))
    assert links.is_safe(links.parse(f"vless://{UUID}@h.com:443?type=ws&security=tls"))
    assert links.is_safe(links.parse("ss://" + b64("aes-256-gcm:pw") + "@1.1.1.1:1"))


def test_extract_base64_subscription():
    blob = b64(f"vless://{UUID}@h.com:443?security=tls\ntrojan://pw@t.com:443\n")
    assert len(links.extract_links(blob)) == 2


@pytest.mark.parametrize("link", [
    f"vless://{UUID}@h.com:443?security=tls#old name",
    "vmess://" + b64(json.dumps({"add": "v.com", "port": 443, "id": UUID, "aid": 0, "ps": "x"})),
])
def test_rename_keeps_config(link):
    renamed = links.rename(link, "🇩🇪 DE · 120ms")
    a, b = links.parse(link), links.parse(renamed)
    assert a.id == b.id and b.name == "🇩🇪 DE · 120ms"


def test_flag():
    assert links.flag("de") == "🇩🇪"
    assert links.flag("") == "🏳️"
