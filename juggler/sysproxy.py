"""Windows system proxy (WinINet / Internet Settings). No-op elsewhere.

Connect points Windows at a proxy script (PAC) served by Juggler, not at a fixed proxy
address. If Juggler stops for any reason (shutdown, End task, crash, power cut) the script
can't load and Windows goes direct, so the internet never depends on Juggler running.
While it runs, the script answers "PROXY 127.0.0.1:<http>; DIRECT", so a dead proxy also
falls back to direct.

The user's original settings are snapshotted to state before the first change
and restored on exit, on console close, and on the next start after a crash.
"""
import json
import time

from .paths import IS_WIN, WEB_PORT

KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
SUPPORTED = IS_WIN


def _refresh():
    import ctypes
    wininet = ctypes.windll.wininet
    wininet.InternetSetOptionW(None, 39, None, 0)  # INTERNET_OPTION_SETTINGS_CHANGED
    wininet.InternetSetOptionW(None, 37, None, 0)  # INTERNET_OPTION_REFRESH


def read():
    if not IS_WIN:
        return None
    import winreg
    out = {}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as k:
        for name in ("ProxyEnable", "ProxyServer", "ProxyOverride", "AutoConfigURL"):
            try:
                out[name] = winreg.QueryValueEx(k, name)[0]
            except FileNotFoundError:
                out[name] = None
    return out


def write(values):
    if not IS_WIN:
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as k:
        for name, v in values.items():
            if v is None:
                try:
                    winreg.DeleteValue(k, name)
                except FileNotFoundError:
                    pass
            elif name == "ProxyEnable":
                winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, int(v))
            else:
                winreg.SetValueEx(k, name, 0, winreg.REG_SZ, str(v))
    _refresh()


PAC_BASE = f"http://127.0.0.1:{WEB_PORT}/proxy.pac"


def is_ours(port, cur=None):
    """Windows currently goes through Juggler: our proxy script, or the fixed address
    that versions before the script set."""
    cur = read() if cur is None else cur
    if not cur:
        return False
    if str(cur.get("AutoConfigURL") or "").startswith(PAC_BASE):
        return True
    return bool(cur.get("ProxyEnable") and cur.get("ProxyServer") == f"127.0.0.1:{port}")


def enable():
    """Point Windows at the proxy script. A new ?v= on every call makes Windows and the
    browsers fetch it again instead of using a cached copy."""
    write({"ProxyEnable": 0, "AutoConfigURL": f"{PAC_BASE}?v={int(time.time())}"})


def reload():
    """The script changed (whitelist): make Windows fetch it again."""
    enable()


def clear_leftover(port):
    """Undo our proxy when there is no snapshot to restore (state lost or stale)."""
    cur = read()
    if not is_ours(port, cur):
        return False
    values = {}
    if str(cur.get("AutoConfigURL") or "").startswith(PAC_BASE):
        values["AutoConfigURL"] = None
    if cur.get("ProxyServer") == f"127.0.0.1:{port}":
        values["ProxyEnable"] = 0
    write(values)
    return True


def pac(port, bypass):
    """The proxy script: whitelist, plain names and the LAN go direct, the rest through
    Juggler, and direct if Juggler's proxy doesn't answer."""
    patterns = json.dumps(["localhost", "127.*", *bypass])
    return (
        "function FindProxyForURL(url, host) {\n"
        "  if (isPlainHostName(host)) return \"DIRECT\";\n"
        f"  var direct = {patterns};\n"
        "  for (var i = 0; i < direct.length; i++)\n"
        "    if (shExpMatch(host, direct[i])) return \"DIRECT\";\n"
        f"  return \"PROXY 127.0.0.1:{port}; DIRECT\";\n"
        "}\n")


def bypass_list(whitelist):
    """Whitelist entries -> WinINet ProxyOverride patterns (best effort; xray still
    routes everything correctly for entries that can't be expressed here)."""
    out = []
    for e in whitelist:
        e = e.strip()
        if e in ("geosite:ir", "geosite:category-ir", "regexp:\\.ir$", "domain:ir", ".ir", "ir"):
            out.append("*.ir")
        elif e == "geoip:private":
            out += ["10.*", "192.168.*", "127.*", "169.254.*"] + [f"172.{i}.*" for i in range(16, 32)]
        elif e.startswith(("geosite:", "geoip:", "regexp:", "keyword:")):
            continue
        elif e.startswith("full:"):
            out.append(e[5:])
        elif "/" in e:
            ip, _, bits = e.partition("/")
            parts = ip.split(".")
            keep = {"8": 1, "16": 2, "24": 3, "32": 4}.get(bits)
            if keep and len(parts) == 4:
                out.append(".".join(parts[:keep]) + (".*" if keep < 4 else ""))
        else:
            d = e.removeprefix("domain:").lstrip("*.")
            if d:
                out += [d, f"*.{d}"]
    seen = set()
    return [x for x in out if not (x in seen or seen.add(x))]
