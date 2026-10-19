from juggler import connlog, log

HTTP = "2026/10/19 12:43:17.421918 from 127.0.0.1:41634 accepted //www.youtube.com:443 [http -> node-abc123]"
PLAIN = "2026/10/19 12:43:19.376802 from 192.168.1.23:41652 accepted http://neverssl.com/ [http -> direct]"
SOCKS = "2026/10/19 12:44:49.388006 from tcp:127.0.0.1:35384 accepted tcp:www.gstatic.com:443 [socks -> node-abc123]"
UDP = "2026/10/19 12:44:50.000000 from udp:192.168.1.40:5000 accepted udp:8.8.8.8:53 [socks >> tor]"
SELF = "2026/10/19 12:44:51.000000 from 127.0.0.1:5555 accepted //www.youtube.com:443 [self -> node-abc123]"


def test_parse_every_access_format():
    assert connlog.parse(HTTP) == {"src": "127.0.0.1", "dest": "www.youtube.com:443", "net": "tcp",
                                   "inbound": "http", "outbound": "node-abc123"}
    assert connlog.parse(PLAIN)["dest"] == "neverssl.com:80"
    assert connlog.parse(SOCKS)["dest"] == "www.gstatic.com:443"
    u = connlog.parse(UDP)
    assert (u["net"], u["dest"], u["outbound"], u["src"]) == ("udp", "8.8.8.8:53", "tor", "192.168.1.40")
    assert connlog.parse("2026/10/19 [Error] something else") is None


def logged(monkeypatch):
    lines = []
    monkeypatch.setattr(log, "log", lambda tag, msg, level="info": lines.append((tag, msg)))
    return lines


def test_off_by_default_but_still_counts(monkeypatch):
    lines = logged(monkeypatch)
    c = connlog.ConnLog(lambda tag: tag)
    assert c.feed(HTTP) is True          # recognised as an access line either way
    assert lines == [] and c.count == 1


def test_enabled_logs_labels_and_folds_repeats(monkeypatch):
    lines = logged(monkeypatch)
    c = connlog.ConnLog(lambda tag: "🇩🇪 DE vless" if tag == "node-abc123" else tag)
    c.enabled = True
    c.feed(HTTP)
    c.feed(HTTP)                         # same source, host and route within 60 s: folded
    c.feed(PLAIN)
    assert lines[0] == ("CONN", "this PC → www.youtube.com:443 · 🇩🇪 DE vless")
    assert lines[1] == ("CONN", "192.168.1.23 → neverssl.com:80 · direct (whitelist)")
    assert len(lines) == 2 and c.count == 3


def test_internal_traffic_is_hidden(monkeypatch):
    lines = logged(monkeypatch)
    c = connlog.ConnLog(lambda tag: tag)
    c.enabled = True
    assert c.feed(SELF) is True
    assert lines == [] and c.count == 0


def test_route_names():
    assert connlog.route_name("direct", lambda t: t) == "direct (whitelist)"
    assert connlog.route_name("tor", lambda t: t) == "Tor"
    assert connlog.route_name("block", lambda t: t) == "blocked"
    assert connlog.route_name("node-xyz", lambda t: "🇺🇸 US ss") == "🇺🇸 US ss"
