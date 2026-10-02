"""Windows system proxy (WinINet / Internet Settings). No-op elsewhere.

The user's original settings are snapshotted to state before the first change
and restored on exit, on console close, and on the next start after a crash.
"""
from .paths import IS_WIN

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
        for name in ("ProxyEnable", "ProxyServer", "ProxyOverride"):
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


def is_ours(port):
    cur = read()
    return bool(cur and cur.get("ProxyEnable") and cur.get("ProxyServer") == f"127.0.0.1:{port}")


def enable(port, bypass):
    write({"ProxyEnable": 1, "ProxyServer": f"127.0.0.1:{port}",
           "ProxyOverride": ";".join(bypass + ["<local>"])})


def set_bypass(bypass):
    write({"ProxyOverride": ";".join(bypass + ["<local>"])})


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
