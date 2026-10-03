"""Build self-contained release archives that install with no internet access.

    python scripts/build_release.py            # both targets, into dist/
    python scripts/build_release.py windows    # just one
    python scripts/build_release.py --fresh    # re-download everything

Each archive holds the app, that OS's Xray / Tor / Iran geo data, the Python
packages as wheels (CPython 3.10-3.14), and on Windows the Python installer too,
so Install.bat works even where GitHub and PyPI are blocked.
"""
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from juggler import __version__, setup  # noqa: E402

DIST = os.path.join(ROOT, "dist")
CACHE = os.path.join(DIST, "cache")
PY_VERSIONS = ("3.10", "3.11", "3.12", "3.13", "3.14")
PY_INSTALLER = "https://www.python.org/ftp/python/3.13.12/python-3.13.12-amd64.exe"
WHEEL_PLATFORMS = {
    "windows": ["win_amd64"],
    "linux": ["manylinux2014_x86_64", "manylinux_2_17_x86_64", "manylinux_2_28_x86_64"],
}
# pip download evaluates environment markers for *this* machine, so dependencies
# that only apply on Windows (click -> colorama) are skipped when building on Linux.
EXTRA_WHEELS = {"windows": ["colorama"], "linux": []}
COMMON = ["juggler", "run.py", "requirements.txt", "README.md", "assets"]
PER_OS = {
    "windows": ["Install.bat", "Juggler.bat", "scripts/install.ps1"],
    "linux": ["install.sh", "juggler.sh"],
}
SKIP_DIRS = {"__pycache__"}


def say(msg):
    print(f"  {msg}", flush=True)


def cached_binaries(plat, fresh):
    """bin/ contents for `plat`, downloaded once into dist/cache/<plat>/bin."""
    bin_dir = os.path.join(CACHE, plat, "bin")
    if fresh:
        shutil.rmtree(bin_dir, ignore_errors=True)
    os.makedirs(bin_dir, exist_ok=True)
    xray = os.path.join(bin_dir, "xray.exe" if plat == "windows" else "xray")
    if not os.path.exists(xray):
        setup.download_xray(plat, bin_dir)
    if not os.path.exists(os.path.join(bin_dir, "geoip.dat")):
        setup.download_geo(bin_dir)
    if not os.path.isdir(os.path.join(bin_dir, "tor")):
        setup.download_tor(plat, os.path.join(bin_dir, "tor"))
    return bin_dir


def cached_wheels(plat, fresh):
    wheels = os.path.join(CACHE, plat, "wheels")
    if fresh:
        shutil.rmtree(wheels, ignore_errors=True)
    if os.path.isdir(wheels) and os.listdir(wheels):
        return wheels
    os.makedirs(wheels, exist_ok=True)
    for v in PY_VERSIONS:
        say(f"wheels for CPython {v} / {plat}")
        cmd = [sys.executable, "-m", "pip", "download", "-q", "--disable-pip-version-check",
               "-r", os.path.join(ROOT, "requirements.txt"), *EXTRA_WHEELS[plat], "-d", wheels,
               "--only-binary=:all:", "--implementation", "cp", "--python-version", v]
        for p in WHEEL_PLATFORMS[plat]:
            cmd += ["--platform", p]
        subprocess.run(cmd, check=True)
    return wheels


def cached_python_installer(fresh):
    path = os.path.join(CACHE, "windows", os.path.basename(PY_INSTALLER))
    if fresh or not os.path.exists(path):
        say(f"downloading {PY_INSTALLER}")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        req = urllib.request.Request(PY_INSTALLER, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=300) as r, open(path + ".tmp", "wb") as f:
            shutil.copyfileobj(r, f)
        os.replace(path + ".tmp", path)
    return path


def copy_tree(src, dst):
    if os.path.isdir(src):
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns(*SKIP_DIRS, "*.pyc"))
    else:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)


def stage(plat, fresh):
    top = os.path.join(DIST, "stage", plat, "Juggler")
    shutil.rmtree(os.path.dirname(top), ignore_errors=True)
    for rel in COMMON + PER_OS[plat]:
        copy_tree(os.path.join(ROOT, rel), os.path.join(top, rel))
    readme = os.path.join(ROOT, "release", "READ ME FIRST.txt")
    with open(readme, encoding="utf-8") as f:
        text = f.read()
    with open(os.path.join(top, "READ ME FIRST.txt"), "w", encoding="utf-8",
              newline="\r\n" if plat == "windows" else "\n") as f:
        f.write(text)
    # Tor's debug symbols and docs are ~40 MB that Juggler never uses
    shutil.copytree(cached_binaries(plat, fresh), os.path.join(top, "bin"),
                    ignore=lambda d, names: [n for n in names if n in ("debug", "docs")
                                             and os.path.basename(d) == "tor"])
    shutil.copytree(cached_wheels(plat, fresh), os.path.join(top, "wheels"))
    if plat == "windows":
        installer = cached_python_installer(fresh)
        os.makedirs(os.path.join(top, "installers"))
        shutil.copy2(installer, os.path.join(top, "installers", os.path.basename(installer)))
    return top


def pack(plat, top):
    base = os.path.join(DIST, f"Juggler-{__version__}-{plat}-x64")
    parent = os.path.dirname(top)
    if plat == "windows":
        out = base + ".zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for root, _, files in os.walk(top):
                for f in files:
                    full = os.path.join(root, f)
                    z.write(full, os.path.relpath(full, parent))
    else:
        out = base + ".tar.gz"   # tar keeps the executable bits zip would lose
        with tarfile.open(out, "w:gz", compresslevel=9) as t:
            t.add(top, arcname="Juggler")
    return out


def main(argv):
    fresh = "--fresh" in argv
    targets = [a for a in argv if not a.startswith("--")] or ["windows", "linux"]
    os.makedirs(DIST, exist_ok=True)
    for plat in targets:
        print(f"\n== {plat} ==", flush=True)
        out = pack(plat, stage(plat, fresh))
        say(f"{os.path.relpath(out, ROOT)}  ({os.path.getsize(out) / 1e6:.1f} MB)")
    shutil.rmtree(os.path.join(DIST, "stage"), ignore_errors=True)


if __name__ == "__main__":
    main(sys.argv[1:])
