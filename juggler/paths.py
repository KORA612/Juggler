"""Filesystem layout and platform switches."""
import os
import sys

IS_WIN = sys.platform == "win32"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "bin")
DATA = os.path.join(ROOT, "data")
CACHE = os.path.join(DATA, "cache")
RUN = os.path.join(DATA, "run")
TOR_DATA = os.path.join(DATA, "tor")
TOR_DIR = os.path.join(BIN, "tor")

SOURCES_TXT = os.path.join(ROOT, "sources.txt")
BRIDGES_TXT = os.path.join(ROOT, "bridges.txt")
WHITELIST_TXT = os.path.join(ROOT, "whitelist.txt")
STATE_JSON = os.path.join(DATA, "state.json")
VALID_CACHE = os.path.join(DATA, "valid.json")

# Ports
WEB_PORT = 8765
SOCKS_PORT = 20808
HTTP_PORT = 20809
TOR_HTTP_PORT = 20810   # xray http inbound that routes straight into Tor
TOR_SOCKS_PORT = 20850  # tor's own SocksPort
API_PORT = 20890        # xray API (balancer info / override)
TEST_BASE_PORT = 21000  # tester inbounds: 21000 + i


def exe(name):
    return name + ".exe" if IS_WIN else name


XRAY = os.path.join(BIN, exe("xray"))


def ensure_dirs():
    for d in (BIN, DATA, CACHE, RUN, TOR_DATA):
        os.makedirs(d, exist_ok=True)


def read_lines(path):
    """Non-empty, non-comment lines of a user-editable text file."""
    try:
        with open(path, encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip() and not l.lstrip().startswith("#")]
    except FileNotFoundError:
        return []


def write_lines(path, lines, header=""):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if header:
            f.write(header.rstrip() + "\n")
        f.write("\n".join(lines) + ("\n" if lines else ""))
    os.replace(tmp, path)
