"""One logger feeding both the terminal (ANSI colors) and the dashboard (SSE)."""
import queue
import sys
import threading
import time
from collections import deque

from .paths import IS_WIN

_RESET, _DIM, _BOLD = "\033[0m", "\033[2m", "\033[1m"
TAG_COLORS = {
    "JUGGLER": "\033[38;5;213m",
    "SRC": "\033[38;5;81m",
    "TEST": "\033[38;5;221m",
    "XRAY": "\033[38;5;141m",
    "TOR": "\033[38;5;177m",
    "WEB": "\033[38;5;114m",
    "PROXY": "\033[38;5;209m",
    "SETUP": "\033[38;5;110m",
}
LEVELS = {  # level -> (symbol, color)
    "info": ("•", "\033[38;5;250m"),
    "ok": ("✓", "\033[38;5;84m"),
    "warn": ("!", "\033[38;5;214m"),
    "error": ("✗", "\033[38;5;203m"),
}

_lock = threading.Lock()
_backlog = deque(maxlen=1000)
_subscribers = set()
_seq = 0


def _setup_console():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    if IS_WIN:
        try:
            import ctypes
            k = ctypes.windll.kernel32
            h = k.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if k.GetConsoleMode(h, ctypes.byref(mode)):
                k.SetConsoleMode(h, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        except Exception:
            pass


_setup_console()


def log(tag, msg, level="info"):
    global _seq
    now = time.time()
    with _lock:
        _seq += 1
        entry = {"seq": _seq, "t": now, "tag": tag, "level": level, "msg": msg}
        _backlog.append(entry)
        subs = list(_subscribers)
    for q in subs:
        try:
            q.put_nowait(entry)
        except queue.Full:
            pass
    sym, lcol = LEVELS.get(level, LEVELS["info"])
    tcol = TAG_COLORS.get(tag, "")
    line = (f"{_DIM}{time.strftime('%H:%M:%S', time.localtime(now))}{_RESET}  "
            f"{tcol}{_BOLD}{tag:<7}{_RESET} {lcol}{sym}{_RESET} {msg}")
    with _lock:
        try:
            print(line, flush=True)
        except Exception:
            pass


def info(tag, msg): log(tag, msg, "info")
def ok(tag, msg): log(tag, msg, "ok")
def warn(tag, msg): log(tag, msg, "warn")
def error(tag, msg): log(tag, msg, "error")


def subscribe():
    """Returns (backlog, queue) for a new SSE client."""
    q = queue.Queue(maxsize=500)
    with _lock:
        _subscribers.add(q)
        return list(_backlog), q


def unsubscribe(q):
    with _lock:
        _subscribers.discard(q)


def banner(lines):
    width = max(len(l) for l in lines) + 4
    c = TAG_COLORS["JUGGLER"]
    print(f"\n{c}╭{'─' * width}╮{_RESET}")
    for l in lines:
        print(f"{c}│{_RESET}  {l.ljust(width - 2)}{c}│{_RESET}")
    print(f"{c}╰{'─' * width}╯{_RESET}\n", flush=True)
