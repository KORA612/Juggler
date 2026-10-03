"""One-time downloads: Xray-core, Iran geo data, Tor Expert Bundle.

Uses urllib's default opener on purpose: it honours the system / env proxy, so
an already-running VPN (e.g. v2rayN) can be used to bootstrap from Iran.
"""
import io
import json
import os
import platform
import shutil
import tarfile
import urllib.request
import zipfile

from . import log
from .paths import BIN, IS_WIN, TOR_DIR, XRAY, exe

TAG = "SETUP"
UA = {"User-Agent": "Mozilla/5.0"}

XRAY_URL = "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-{plat}-64.zip"
GEO_URLS = [
    "https://raw.githubusercontent.com/Chocolate4U/Iran-v2ray-rules/release/{f}",
    "https://cdn.jsdelivr.net/gh/chocolate4u/Iran-v2ray-rules@release/{f}",
]
TOR_VERSIONS = "https://aus1.torproject.org/torbrowser/update_3/release/downloads.json"
TOR_URLS = [
    "https://dist.torproject.org/torbrowser/{v}/tor-expert-bundle-{plat}-x86_64-{v}.tar.gz",
    "https://archive.torproject.org/tor-package-archive/torbrowser/{v}/tor-expert-bundle-{plat}-x86_64-{v}.tar.gz",
]


def _get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _get_any(urls):
    last = None
    for u in urls:
        try:
            return _get(u)
        except Exception as e:  # noqa: BLE001 - try the next mirror
            last = e
            log.warn(TAG, f"download failed: {u} ({e})")
    raise RuntimeError(f"all mirrors failed: {last}")


def _plat():
    if platform.machine().lower() not in ("amd64", "x86_64"):
        raise RuntimeError(f"unsupported CPU {platform.machine()} (only x86_64 is wired up)")
    return "windows" if IS_WIN else "linux"


def _exe(name, plat):
    return name + ".exe" if plat == "windows" else name


# The download_* functions take an explicit target so the release builder can
# fetch Windows binaries on Linux. ensure_* install for this machine into bin/.

def download_xray(plat, dest_dir):
    url = XRAY_URL.format(plat=plat)
    log.info(TAG, f"downloading Xray-core: {url}")
    target = os.path.join(dest_dir, _exe("xray", plat))
    with zipfile.ZipFile(io.BytesIO(_get(url, timeout=180))) as z:
        with z.open(_exe("xray", plat)) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
    os.chmod(target, 0o755)
    log.ok(TAG, "Xray-core installed")


def download_geo(dest_dir):
    for f in ("geosite.dat", "geoip.dat"):
        path = os.path.join(dest_dir, f)
        log.info(TAG, f"downloading Iran geo data: {f}")
        data = _get_any([u.format(f=f) for u in GEO_URLS])
        with open(path + ".tmp", "wb") as out:
            out.write(data)
        os.replace(path + ".tmp", path)
        log.ok(TAG, f"{f} installed ({len(data) // 1024} KB)")


def download_tor(plat, tor_dir):
    v = json.loads(_get(TOR_VERSIONS))["version"]
    log.info(TAG, f"downloading Tor Expert Bundle {v} ({plat})")
    blob = _get_any([u.format(v=v, plat=plat) for u in TOR_URLS])
    shutil.rmtree(tor_dir, ignore_errors=True)
    os.makedirs(tor_dir)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as t:
        t.extractall(tor_dir, filter="data")
    for root, _, files in os.walk(tor_dir):
        for f in files:
            os.chmod(os.path.join(root, f), 0o755)
    log.ok(TAG, "Tor installed")
    return v


def ensure_xray(force=False):
    if force or not os.path.exists(XRAY):
        download_xray(_plat(), BIN)


def ensure_geo(force=False):
    if force or not all(os.path.exists(os.path.join(BIN, f)) for f in ("geosite.dat", "geoip.dat")):
        download_geo(BIN)


def tor_exe():
    return os.path.join(TOR_DIR, "tor", exe("tor"))


def ensure_tor(force=False):
    """Best effort: without Tor the app still works, just without the fallback."""
    if os.path.exists(tor_exe()) and not force:
        return True
    try:
        download_tor(_plat(), TOR_DIR)
        return True
    except Exception as e:  # noqa: BLE001
        log.warn(TAG, f"Tor not installed ({e}); Tor fallback disabled. "
                      f"Extract a tor-expert-bundle into {TOR_DIR} to enable it.")
        return False


def ensure_all():
    ensure_xray()
    ensure_geo()
    return ensure_tor()


if __name__ == "__main__":
    ensure_all()
