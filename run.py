"""Juggler entry point: python run.py [--no-browser]"""
import atexit
import signal
import sys
import threading
import time
import webbrowser

from juggler import log, paths, procs, setup
from juggler.paths import HTTP_PORT, IS_WIN, SOCKS_PORT, WEB_PORT


def _console_qr(text):
    try:
        import qrcode
        q = qrcode.QRCode(border=1)
        q.add_data(text)
        q.make()
        q.print_ascii(invert=True)
    except Exception:
        pass


def main():
    paths.ensure_dirs()
    stale = procs.kill_stale()
    if stale:
        log.warn("JUGGLER", f"killed {stale} leftover process(es) from a previous run")
    try:
        setup.ensure_xray()
        setup.ensure_geo()
    except Exception as e:  # noqa: BLE001
        log.error("SETUP", f"cannot install Xray / geo data: {e}")
        log.error("SETUP", "connect any VPN once (or drop xray + geo .dat files into bin/) and retry")
        input("press Enter to exit")
        return 1
    setup.ensure_tor()

    from juggler.app import create_app
    from juggler.engine import Engine, lan_ip

    engine = Engine()

    def cleanup(*_):
        engine.shutdown()
        procs.stop_all()

    atexit.register(cleanup)
    signal.signal(signal.SIGTERM, lambda *a: (cleanup(), sys.exit(0)))
    if IS_WIN:
        import ctypes
        handler_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)

        @handler_type
        def on_console_event(event):  # Ctrl+C, Ctrl+Break, window close, logoff, shutdown
            cleanup()
            return False  # not "handled": let Python / Windows carry on with the default action
        main.handler = on_console_event  # keep a reference so it isn't garbage-collected
        ctypes.windll.kernel32.SetConsoleCtrlHandler(on_console_event, True)

    engine.start()
    ip = lan_ip()
    log.banner([
        "JUGGLER · self-hosted Xray config juggler",
        "",
        f"Dashboard    http://localhost:{WEB_PORT}",
        f"Phone sub    http://{ip}:{WEB_PORT}/sub",
        f"Proxy        SOCKS {ip}:{SOCKS_PORT} · HTTP {ip}:{HTTP_PORT}",
        "",
        "Ctrl+C or close this window to stop (system proxy is restored)",
    ])
    print("  Scan with v2rayNG → Subscription group → add → URL:\n")
    _console_qr(f"http://{ip}:{WEB_PORT}/sub")
    print()

    if "--no-browser" not in sys.argv:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://localhost:{WEB_PORT}")).start()

    app = create_app(engine)
    import flask.cli
    flask.cli.show_server_banner = lambda *a, **k: None
    try:
        app.run(host="0.0.0.0", port=WEB_PORT, threaded=True, debug=False, use_reloader=False)
    except OSError as e:
        log.error("WEB", f"port {WEB_PORT} busy ({e}); is Juggler already running?")
        time.sleep(5)
        return 1
    finally:
        cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
