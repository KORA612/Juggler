"""Share links <-> Xray outbounds. Pure functions, no I/O.

parse(link) -> Node | None   (None = unsupported / malformed / insecure)
"""
import base64
import binascii
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from urllib.parse import parse_qs, quote, unquote, urlsplit

LINK_RE = re.compile(
    r"(?:vless|vmess|trojan|ss|hysteria2|hy2|wireguard|wg|socks5?|https?)://[^\s<>\"'`]+")

TRANSPORTS = {"raw", "ws", "grpc", "httpupgrade", "xhttp", "kcp"}
NET_ALIASES = {"tcp": "raw", "": "raw", "none": "raw", "splithttp": "xhttp", "mkcp": "kcp",
               "gun": "grpc"}
SS_METHODS = {
    "aes-128-gcm", "aes-256-gcm", "chacha20-poly1305", "chacha20-ietf-poly1305",
    "xchacha20-poly1305", "xchacha20-ietf-poly1305", "2022-blake3-aes-128-gcm",
    "2022-blake3-aes-256-gcm", "2022-blake3-chacha20-poly1305", "none", "plain",
}
FINGERPRINTS = {"chrome", "firefox", "safari", "ios", "android", "edge", "360", "qq",
                "random", "randomized", "randomizednoalpn"}


class Unsupported(ValueError):
    pass


@dataclass
class Node:
    id: str
    link: str
    proto: str
    host: str
    port: int
    name: str
    net: str
    sec: str
    outbound: dict = field(repr=False)
    source: str = ""
    first_seen: float = 0.0
    ping_ms: int = -1
    speed_kbps: int = -1
    country: str = ""
    exit_ip: str = ""
    tested_at: float = 0.0

    @property
    def flag(self):
        return flag(self.country)

    def to_dict(self):
        d = asdict(self)
        d.pop("outbound")
        d["flag"] = self.flag
        return d


def flag(cc):
    cc = (cc or "").upper()
    if len(cc) != 2 or not cc.isalpha():
        return "🏳️"
    return chr(0x1F1E6 + ord(cc[0]) - 65) + chr(0x1F1E6 + ord(cc[1]) - 65)


# ---------- helpers ----------

def b64decode(s):
    s = s.strip().replace("-", "+").replace("_", "/")
    s = re.sub(r"\s+", "", s)
    return base64.b64decode(s + "=" * (-len(s) % 4))


def looks_b64(text):
    t = text.strip()
    return len(t) > 16 and "://" not in t[:200] and re.fullmatch(r"[A-Za-z0-9+/=_\-\s]+", t[:4000]) is not None


_ENTITIES = (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&#39;", "'"), ("&#039;", "'"))


def unescape(text):
    """Undo Telegram's HTML escaping. Not html.unescape: it turns '&note=' into '¬e='."""
    prev = None
    while prev != text:
        prev, text = text, text.replace("&amp;", "&")
    for a, b in _ENTITIES:
        text = text.replace(a, b)
    return text


def extract_links(text):
    """All share links in a blob: plain text, base64 subscription or HTML page."""
    if looks_b64(text):
        try:
            text = b64decode(text).decode("utf-8", "replace")
        except (binascii.Error, ValueError):
            pass
    return LINK_RE.findall(unescape(text))


def _q(query):
    out = {}
    for k, v in parse_qs(query, keep_blank_values=True).items():
        out[k] = v[0].strip()
    return out


def _port(p):
    try:
        port = int(str(p).strip())
    except (TypeError, ValueError):
        raise Unsupported(f"bad port {p!r}")
    if not 0 < port < 65536:
        raise Unsupported(f"bad port {port}")
    return port


def _truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes")


def _clean(v):
    # Telegram reposts glue junk like "none@ChannelName" onto the last value.
    return (v or "").split("@")[0].strip()


def _split_hostport(netloc):
    """'user@host:port' -> (user, host, port); handles [ipv6]."""
    userinfo, _, hp = netloc.rpartition("@")
    if hp.startswith("["):
        host, _, rest = hp[1:].partition("]")
        port = rest.lstrip(":")
    else:
        host, _, port = hp.rpartition(":")
    if not host:
        raise Unsupported("no host")
    return unquote(userinfo), host, _port(port.split("/")[0])


# ---------- stream settings ----------

def _stream(net, sec, q, address):
    net = NET_ALIASES.get((net or "").lower(), (net or "").lower())
    if net not in TRANSPORTS:
        raise Unsupported(f"transport {net}")
    host = q.get("host", "")
    path = q.get("path", "")
    ss = {"network": net}
    header = _clean(q.get("headerType", "")).lower()

    if net == "raw" and header == "http":
        req = {"path": [p for p in path.split(",") if p] or ["/"]}
        if host:
            req["headers"] = {"Host": [h for h in host.split(",") if h]}
        ss["rawSettings"] = {"header": {"type": "http", "request": req}}
    elif net == "ws":
        ss["wsSettings"] = {"path": path or "/", **({"host": host} if host else {})}
    elif net == "httpupgrade":
        ss["httpupgradeSettings"] = {"path": path or "/", **({"host": host} if host else {})}
    elif net == "grpc":
        g = {"serviceName": q.get("serviceName") or path.lstrip("/")}
        if _clean(q.get("mode", "")).lower() == "multi":
            g["multiMode"] = True
        if q.get("authority"):
            g["authority"] = q["authority"]
        ss["grpcSettings"] = g
    elif net == "xhttp":
        x = {"path": path or "/", "mode": _clean(q.get("mode", "")) or "auto"}
        if host:
            x["host"] = host
        if q.get("extra"):
            try:
                extra = json.loads(q["extra"])
                if isinstance(extra, dict):
                    x["extra"] = extra
            except ValueError:
                pass
        ss["xhttpSettings"] = x
    elif net == "kcp":
        # Xray 26 moved mKCP header/seed into finalmask; old-style links can't be mapped.
        if q.get("seed") or (header and header != "none"):
            raise Unsupported("legacy mKCP header/seed")
        ss["kcpSettings"] = {}

    sec = _clean(sec).lower()
    if sec in ("", "false", "0", "none"):
        sec = "none"
    if sec == "xtls":
        raise Unsupported("legacy xtls")
    if sec in ("tls", "reality"):
        if _truthy(q.get("allowInsecure")) or _truthy(q.get("insecure")):
            raise Unsupported("allowInsecure (rejected by Xray)")
        fp = _clean(q.get("fp", "")).lower()
        sni = q.get("sni") or q.get("peer") or (host.split(",")[0] if host else "") or address
        if sec == "tls":
            t = {"serverName": sni}
            if fp:
                t["fingerprint"] = fp if fp in FINGERPRINTS else "chrome"
            alpn = [a for a in q.get("alpn", "").split(",") if a]
            if alpn:
                t["alpn"] = alpn
            ss["security"] = "tls"
            ss["tlsSettings"] = t
        else:
            pbk = _clean(q.get("pbk", ""))
            if not pbk:
                raise Unsupported("reality without pbk")
            r = {"serverName": sni, "fingerprint": fp if fp in FINGERPRINTS else "chrome",
                 "publicKey": pbk}
            if q.get("sid"):
                r["shortId"] = _clean(q["sid"])
            if q.get("spx"):
                r["spiderX"] = q["spx"]
            if q.get("pqv"):
                r["mldsa65Verify"] = q["pqv"]
            ss["security"] = "reality"
            ss["realitySettings"] = r
    elif sec != "none":
        raise Unsupported(f"security {sec}")
    return ss, net, sec


# ---------- protocols ----------

def _vless_like(proto, link):
    u = urlsplit(link)
    cred, host, port = _split_hostport(u.netloc)
    if not cred:
        raise Unsupported("no credential")
    q = _q(u.query)
    default_sec = "tls" if proto == "trojan" else "none"
    ss, net, sec = _stream(_clean(q.get("type", "tcp")), q.get("security", default_sec), q, host)
    if proto == "vless":
        user = {"id": cred, "encryption": _clean(q.get("encryption", "")) or "none"}
        flow = _clean(q.get("flow", ""))
        if flow and net == "raw" and sec in ("tls", "reality"):
            user["flow"] = flow
        settings = {"vnext": [{"address": host, "port": port, "users": [user]}]}
    elif proto == "trojan":
        settings = {"servers": [{"address": host, "port": port, "password": cred}]}
    else:  # vmess in URI form
        settings = {"vnext": [{"address": host, "port": port,
                               "users": [{"id": cred, "security": q.get("encryption") or "auto"}]}]}
    return proto, host, port, unquote(u.fragment), net, sec, {
        "protocol": proto, "settings": settings, "streamSettings": ss}


def _vmess(link):
    body = link[len("vmess://"):]
    if "@" in body.split("?")[0]:
        return _vless_like("vmess", link)
    try:
        j = json.loads(b64decode(body.split("#")[0]).decode("utf-8", "replace"))
    except (ValueError, binascii.Error):
        raise Unsupported("bad vmess json")
    if str(j.get("aid", "0")).strip() not in ("0", ""):
        raise Unsupported("legacy alterId")
    host, port = str(j.get("add", "")).strip(), _port(j.get("port"))
    if not host or not j.get("id"):
        raise Unsupported("incomplete vmess")
    q = {k: str(v) for k, v in {
        "host": j.get("host", ""), "path": j.get("path", ""), "sni": j.get("sni", ""),
        "alpn": j.get("alpn", ""), "fp": j.get("fp", ""), "headerType": j.get("type", ""),
        "serviceName": j.get("path", "") if j.get("net") == "grpc" else "",
        "mode": j.get("type", "") if j.get("net") == "grpc" else j.get("mode", ""),
        "allowInsecure": j.get("allowInsecure", j.get("skip-cert-verify", "")),
        "pbk": j.get("pbk", ""), "sid": j.get("sid", ""), "spx": j.get("spx", ""),
    }.items() if v not in (None, "")}
    if j.get("net") == "grpc":
        q.pop("headerType", None)
    ss, net, sec = _stream(j.get("net", "tcp"), j.get("tls", "") or "none", q, host)
    user = {"id": str(j["id"]).strip(), "security": j.get("scy") or "auto"}
    return "vmess", host, port, str(j.get("ps", "")), net, sec, {
        "protocol": "vmess",
        "settings": {"vnext": [{"address": host, "port": port, "users": [user]}]},
        "streamSettings": ss}


def _ss(link):
    body = link[len("ss://"):]
    body, _, frag = body.partition("#")
    name = unquote(frag)
    if "@" not in body:  # legacy: ss://base64(method:pass@host:port)
        try:
            body = b64decode(body.split("?")[0].split("/")[0]).decode("utf-8", "replace")
        except (ValueError, binascii.Error):
            raise Unsupported("bad legacy ss")
    main, _, query = body.partition("?")
    main = main.rstrip("/")
    userinfo, host, port = _split_hostport(main)
    q = _q(query)
    if q.get("plugin"):
        raise Unsupported("ss plugin")
    if ":" not in userinfo:
        try:
            userinfo = b64decode(userinfo).decode("utf-8", "replace")
        except (ValueError, binascii.Error):
            raise Unsupported("bad ss userinfo")
    method, _, password = userinfo.partition(":")
    method = method.lower().strip()
    if method not in SS_METHODS:
        raise Unsupported(f"ss method {method}")
    return "ss", host, port, name, "raw", ("none" if method in ("none", "plain") else method), {
        "protocol": "shadowsocks",
        "settings": {"servers": [{"address": host, "port": port, "method": method,
                                  "password": password}]}}


def _hysteria2(link):
    u = urlsplit(link)
    auth, host, port = _split_hostport(u.netloc.split(",")[0])
    q = _q(u.query)
    if _truthy(q.get("insecure")) or _truthy(q.get("allowInsecure")):
        raise Unsupported("insecure hysteria2 (rejected by Xray)")
    tls = {"serverName": q.get("sni") or host, "alpn": ["h3"]}
    ss = {"network": "hysteria", "security": "tls", "tlsSettings": tls,
          "hysteriaSettings": {"version": 2, "auth": auth}}
    if q.get("obfs") == "salamander":
        ss["finalmask"] = {"udp": [{"type": "salamander",
                                    "settings": {"password": q.get("obfs-password", "")}}]}
    elif q.get("obfs"):
        raise Unsupported(f"hysteria2 obfs {q['obfs']}")
    return "hysteria2", host, port, unquote(u.fragment), "hysteria", "tls", {
        "protocol": "hysteria", "settings": {"version": 2, "address": host, "port": port},
        "streamSettings": ss}


def _wireguard(link):
    u = urlsplit(link)
    secret, host, port = _split_hostport(u.netloc)
    q = _q(u.query)
    pub = q.get("publickey") or q.get("publicKey") or q.get("peerPublicKey")
    if not secret or not pub:
        raise Unsupported("incomplete wireguard")
    peer = {"publicKey": pub, "endpoint": f"{host}:{port}"}
    if q.get("presharedkey") or q.get("preSharedKey"):
        peer["preSharedKey"] = q.get("presharedkey") or q.get("preSharedKey")
    settings = {"secretKey": secret, "peers": [peer],
                "address": [a for a in (q.get("address") or q.get("ip") or "10.0.0.2/32").split(",") if a]}
    if q.get("mtu"):
        settings["mtu"] = int(q["mtu"])
    if q.get("reserved"):
        try:
            settings["reserved"] = [int(x) for x in q["reserved"].split(",")]
        except ValueError:
            pass
    return "wireguard", host, port, unquote(u.fragment), "udp", "none", {
        "protocol": "wireguard", "settings": settings}


def _socks_http(scheme, link):
    u = urlsplit(link)
    if u.path not in ("", "/") or u.query:
        raise Unsupported("not a proxy link")  # ordinary URL, not a share link
    cred, host, port = _split_hostport(u.netloc)
    server = {"address": host, "port": port}
    if cred:
        if ":" not in cred:
            try:
                cred = b64decode(cred).decode("utf-8", "replace")
            except (ValueError, binascii.Error):
                pass
        user, _, pw = cred.partition(":")
        if user:
            server["users"] = [{"user": user, "pass": pw}]
    proto = "socks" if scheme.startswith("socks") else "http"
    ob = {"protocol": proto, "settings": {"servers": [server]}}
    sec = "none"
    if scheme == "https":
        sec = "tls"
        ob["streamSettings"] = {"network": "raw", "security": "tls",
                                "tlsSettings": {"serverName": host}}
    return proto, host, port, unquote(u.fragment), "raw", sec, ob


def parse(link):
    link = unescape(link.strip())
    scheme = link.split("://", 1)[0].lower()
    try:
        if scheme in ("vless", "trojan"):
            r = _vless_like(scheme, link)
        elif scheme == "vmess":
            r = _vmess(link)
        elif scheme == "ss":
            r = _ss(link)
        elif scheme in ("hysteria2", "hy2"):
            r = _hysteria2(link)
        elif scheme in ("wireguard", "wg"):
            r = _wireguard(link)
        elif scheme in ("socks", "socks5", "http", "https"):
            r = _socks_http(scheme, link)
        else:
            return None
    except (Unsupported, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return None
    proto, host, port, name, net, sec, ob = r
    key = json.dumps(ob, sort_keys=True)
    return Node(id=hashlib.sha1(key.encode()).hexdigest()[:12], link=link, proto=proto,
                host=host, port=port, name=name.strip(), net=net, sec=sec, outbound=ob)


def is_safe(node):
    """Safe mode: refuse configs that send traffic unencrypted over the wire."""
    if node.proto in ("vless", "trojan", "socks", "http") and node.sec == "none":
        return False
    if node.proto == "ss" and node.sec == "none":
        return False
    return True


def to_outbound(node, tag):
    return {"tag": tag, **json.loads(json.dumps(node.outbound))}


def rename(link, name):
    """Same link with a new display name (used for the phone subscription)."""
    if link.startswith("vmess://") and "@" not in link[8:].split("?")[0]:
        try:
            j = json.loads(b64decode(link[8:].split("#")[0]).decode("utf-8"))
            j["ps"] = name
            return "vmess://" + base64.b64encode(json.dumps(j, ensure_ascii=False).encode()).decode()
        except (ValueError, binascii.Error):
            return link
    return link.split("#")[0] + "#" + quote(name, safe="")
