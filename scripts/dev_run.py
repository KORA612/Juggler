"""Run a second Juggler next to a running one, for development and testing.

    python scripts/dev_run.py --offset 100 [--log-connections]   # dashboard :8865, proxy :20908/:20909

Every port is shifted by --offset, so the real instance (and its phone
subscription) is never disturbed. Data and binaries are shared.
"""
import os
import runpy
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from juggler import paths  # noqa: E402

args = sys.argv[1:]
offset = 100
if "--offset" in args:
    i = args.index("--offset")
    offset = int(args[i + 1])
    del args[i:i + 2]
for k in ("WEB_PORT", "SOCKS_PORT", "HTTP_PORT", "TOR_HTTP_PORT", "SELF_HTTP_PORT", "TOR_SOCKS_PORT", "API_PORT"):
    setattr(paths, k, getattr(paths, k) + offset)
paths.TEST_BASE_PORT += offset * 10
sys.argv = ["run.py", "--no-browser", *args]     # other flags pass through
runpy.run_path(os.path.join(ROOT, "run.py"), run_name="__main__")
