"""Windows Firewall diagnosis for phone access. No-op elsewhere.

Windows asks "allow on which networks?" only once per program. If that answer was
Public only and the home Wi-Fi is classed Private (or vice versa), inbound
connections from the phone are dropped silently - v2rayNG only says "Failure".
"""
import base64
import json
import subprocess
import sys
import threading
import time

from .paths import IS_WIN, XRAY

RULE_NAME = "Juggler phone access"
CACHE_SECS = 120
NO_WINDOW = 0x08000000

_cache = {"at": 0, "data": None}
_lock = threading.Lock()


def _ps(script, timeout=20):
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", enc],
                       capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW)
    return r.stdout.strip()


def programs():
    return [sys.executable, XRAY]


def _query(ip):
    progs = [p.lower() for p in programs()]
    script = f"""
$ErrorActionPreference = 'SilentlyContinue'
$cat = (Get-NetIPAddress -IPAddress '{ip}' | Get-NetConnectionProfile).NetworkCategory
$progs = @({",".join(f"'{p}'" for p in progs)})
$rules = Get-NetFirewallApplicationFilter | Where-Object {{ $progs -contains $_.Program.ToLower() }} | ForEach-Object {{
  $r = $_ | Get-NetFirewallRule
  [pscustomobject]@{{ program = $_.Program.ToLower(); profile = "$($r.Profile)"; action = "$($r.Action)";
                      direction = "$($r.Direction)"; enabled = "$($r.Enabled)" }}
}}
@{{ category = "$cat"; rules = @($rules) }} | ConvertTo-Json -Depth 3 -Compress
"""
    return json.loads(_ps(script) or "{}")


def evaluate(category, rules, progs):
    """-> {program: 'allowed' | 'blocked' | 'no rule'} for the given network category."""
    cat = {"DomainAuthenticated": "Domain"}.get(category, category)
    out = {}
    for p in progs:
        mine = [r for r in rules if r.get("program") == p.lower()
                and r.get("direction") == "Inbound" and r.get("enabled") == "True"
                and (r.get("profile") == "Any" or cat in r.get("profile", ""))]
        if any(r.get("action") == "Block" for r in mine):
            out[p] = "blocked"
        elif any(r.get("action") == "Allow" for r in mine):
            out[p] = "allowed"
        else:
            out[p] = "no rule"
    return out


def status(ip, force=False):
    """-> {'supported', 'category', 'ok', 'detail'}; cached, refreshed in the background."""
    if not IS_WIN:
        return {"supported": False}
    with _lock:
        fresh = time.time() - _cache["at"] < CACHE_SECS
        if (fresh and not force) or _cache.get("running"):
            return _cache["data"] or {"supported": True, "checking": True}
        _cache["running"] = True

    def work():
        data = {"supported": True}
        try:
            q = _query(ip)
            rules = q.get("rules") or []
            if isinstance(rules, dict):
                rules = [rules]
            verdict = evaluate(q.get("category", ""), rules, programs())
            data.update(category=q.get("category") or "unknown",
                        ok=all(v == "allowed" for v in verdict.values()),
                        detail={("python" if p == sys.executable else "xray"): v for p, v in verdict.items()})
        except Exception as e:  # noqa: BLE001 - diagnosis is best effort
            data.update(error=f"{type(e).__name__}: {e}")
        with _lock:
            _cache.update(at=time.time(), data=data, running=False)

    threading.Thread(target=work, daemon=True).start()
    return _cache["data"] or {"supported": True, "checking": True}


def allow():
    """Add inbound allow rules (local subnet only, Private + Public) via a UAC prompt.
    -> True if the elevated helper was launched (the user may still decline)."""
    if not IS_WIN:
        return False
    import ctypes
    lines = [f"Remove-NetFirewallRule -DisplayName '{RULE_NAME}' -ErrorAction SilentlyContinue"]
    for p in programs():
        lines.append(f"New-NetFirewallRule -DisplayName '{RULE_NAME}' -Direction Inbound -Action Allow "
                     f"-Program '{p}' -Profile Private,Public -RemoteAddress LocalSubnet -Protocol TCP")
    enc = base64.b64encode("\n".join(lines).encode("utf-16-le")).decode()
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe",
                                             f"-NoProfile -WindowStyle Hidden -EncodedCommand {enc}",
                                             None, 0)
    with _lock:
        _cache["at"] = 0  # re-check soon
    return rc > 32
