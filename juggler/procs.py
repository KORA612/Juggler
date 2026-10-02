"""Child processes (xray, tor) that never outlive the app.

Windows: every child joins a Job Object with KILL_ON_JOB_CLOSE, so closing the
console window kills them. Linux: children share our process group (SIGHUP on
terminal close) and are stopped at exit. Pidfiles catch the rest. PDEATHSIG is
deliberately not used: it fires when the spawning *thread* exits, and xray is
restarted from short-lived request threads.
"""
import os
import signal
import subprocess
import threading

from .paths import IS_WIN, RUN

_children = {}
_lock = threading.Lock()
_job = None


def _win_job():
    global _job
    if _job is not None:
        return _job
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.OpenProcess.restype = wintypes.HANDLE
    job = k32.CreateJobObjectW(None, None)
    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info))
    _job = (k32, job)
    return _job


def _assign_to_job(pid):
    try:
        import ctypes
        from ctypes import wintypes
        k32, job = _win_job()
        h = k32.OpenProcess(0x0101, False, pid)  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        if h:
            k32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(h))
            k32.CloseHandle(wintypes.HANDLE(h))
    except Exception:
        pass


def spawn(name, args, **kw):
    """Start a long-lived child; stdout+stderr are merged and piped."""
    opts = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", bufsize=1)
    if IS_WIN:
        opts["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    opts.update(kw)
    p = subprocess.Popen(args, **opts)
    if IS_WIN:
        _assign_to_job(p.pid)
    with _lock:
        _children[p.pid] = (name, p)
    _write_pids()
    return p


def stop(p, timeout=3):
    if p is None:
        return
    if p.poll() is None:
        p.terminate()
        try:
            p.wait(timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(timeout)
    with _lock:
        _children.pop(p.pid, None)
    _write_pids()


def stop_all():
    with _lock:
        procs = [p for _, p in _children.values()]
    for p in procs:
        try:
            stop(p)
        except Exception:
            pass


def _pidfile():
    return os.path.join(RUN, "children.pids")


def _write_pids():
    try:
        with _lock:
            lines = [f"{pid} {name}" for pid, (name, _) in _children.items()]
        with open(_pidfile(), "w") as f:
            f.write("\n".join(lines))
    except OSError:
        pass


def kill_stale():
    """Kill children left behind by a previous crashed run."""
    try:
        with open(_pidfile()) as f:
            entries = [l.split(None, 1) for l in f if l.strip()]
    except OSError:
        return 0
    killed = 0
    for pid, name in entries:
        pid, name = int(pid), name.strip()
        if _is_ours(pid, name):
            try:
                if IS_WIN:
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
                else:
                    os.kill(pid, signal.SIGKILL)
                killed += 1
            except OSError:
                pass
    os.remove(_pidfile())
    return killed


def _is_ours(pid, name):
    """Only kill a stale pid if it is still an xray/tor process (pids get reused)."""
    exe = "tor" if name.startswith("tor") else "xray"
    try:
        if IS_WIN:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                                 capture_output=True, text=True).stdout.lower()
            return f'"{exe}.exe"' in out
        with open(f"/proc/{pid}/comm") as f:
            return f.read().strip() == exe
    except OSError:
        return False
