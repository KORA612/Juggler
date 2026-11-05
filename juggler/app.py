"""Flask dashboard + JSON API. Everything except /sub and /ping is localhost-only."""
import ipaddress
import json
import logging
import queue

import qrcode
import qrcode.image.svg
from flask import Flask, Response, abort, jsonify, render_template, request

from . import firewall, log

CSRF_HEADER = "X-Juggler"


def host_ok(host):
    """True for an IP address or localhost (any port), the only names Juggler is reached by."""
    name = host.strip().lower()
    if name.startswith("["):                       # [::1]:8765
        name = name[1:].partition("]")[0]
    elif name.count(":") == 1:                     # 127.0.0.1:8765 / localhost:8765
        name = name.partition(":")[0]
    if name == "localhost":
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


def create_app(engine):
    app = Flask(__name__)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    @app.before_request
    def guard():
        # DNS rebinding: a website can point its own name at 127.0.0.1, and then the browser
        # treats Juggler as that site. Its requests still carry the site's name in Host, so
        # only an IP address or "localhost" is accepted.
        if not host_ok(request.host):
            abort(403)
        if request.path in ("/sub", "/ping"):
            return None
        if request.remote_addr not in ("127.0.0.1", "::1"):
            abort(403)
        # Cross-site requests: any page can send a plain POST to localhost, but it can't add a
        # custom header without a CORS preflight, which Juggler never approves.
        if request.method not in ("GET", "HEAD") and request.headers.get(CSRF_HEADER) != "1":
            abort(403)

    @app.after_request
    def no_cache(resp):
        resp.headers["Cache-Control"] = "no-store"
        return resp

    def body():
        return request.get_json(silent=True) or {}

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/state")
    def state():
        return jsonify(engine.snapshot())

    @app.post("/api/refresh")
    def refresh():
        started = engine.refresh_now()
        return jsonify(ok=True, started=started,
                       msg="refresh started" if started else "a refresh is already running")

    @app.post("/api/sysproxy")
    def sysproxy():
        ok, err = engine.set_sysproxy(bool(body().get("on")))
        return jsonify(ok=ok, msg=err), (200 if ok else 400)

    @app.post("/api/safemode")
    def safemode():
        engine.set_safe_mode(bool(body().get("on")))
        return jsonify(ok=True)

    @app.post("/api/minspeed")
    def minspeed():
        ok = engine.set_min_speed(body().get("kbps"))
        return jsonify(ok=ok, msg="" if ok else "unknown speed"), (200 if ok else 400)

    @app.post("/api/pin")
    def pin():
        ok = engine.pin(body().get("id"))
        return jsonify(ok=ok, msg="" if ok else "node is not in the live balancer"), (200 if ok else 400)

    @app.post("/api/connlog")
    def connlog_toggle():
        engine.set_connlog(bool(body().get("on")))
        return jsonify(ok=True)

    @app.post("/api/sitecheck")
    def sitecheck():
        ok, what = engine.site_check(body().get("url", ""))
        return jsonify(ok=ok, host=what if ok else None, msg="" if ok else what), (200 if ok else 400)

    @app.delete("/api/sitecheck")
    def sitecheck_forget():
        return jsonify(ok=engine.site_forget(body().get("host", "")))

    def ids():
        b = body()
        return b.get("ids") if isinstance(b.get("ids"), list) else [b.get("id", "")]

    @app.post("/api/test")
    def test_some():
        n = engine.test(ids())
        return jsonify(ok=bool(n), count=n, msg=f"testing {n}…" if n else "nothing to test"), (200 if n else 400)

    @app.post("/api/test_all")
    def test_all():
        engine.test_all()
        return jsonify(ok=True, msg="testing every config…")

    @app.post("/api/manual")
    def manual():
        found, unique = engine.add_manual(body().get("text", ""))
        if not unique:
            return jsonify(ok=False, msg="no config links found in the pasted text"), 400
        return jsonify(ok=True, found=found, added=unique,
                       msg=f"{unique} config(s) added, testing now")

    @app.post("/api/remove")
    def remove():
        n = engine.remove(ids())
        return jsonify(ok=bool(n), count=n), (200 if n else 400)

    @app.get("/api/whitelist")
    def whitelist_get():
        return jsonify(entries=engine.whitelist())

    @app.put("/api/whitelist")
    def whitelist_put():
        ok, err = engine.set_whitelist(body().get("entries", []))
        return jsonify(ok=ok, msg=err, entries=engine.whitelist()), (200 if ok else 400)

    @app.get("/api/<any(sources, bridges):which>")
    def text_get(which):
        return jsonify(lines=engine.text_file(which))

    @app.put("/api/<any(sources, bridges):which>")
    def text_put(which):
        engine.set_text_file(which, body().get("lines", []))
        return jsonify(ok=True, lines=engine.text_file(which))

    @app.get("/api/qr")
    def qr():
        data = request.args.get("d", "")[:2500]
        if not data:
            abort(400)
        img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
        return Response(img.to_string(encoding="unicode"), mimetype="image/svg+xml")

    @app.get("/api/log/stream")
    def log_stream():
        backlog, q = log.subscribe()

        def gen():
            try:
                for e in backlog[-300:]:
                    yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
                while True:
                    try:
                        e = q.get(timeout=15)
                        yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
                    except queue.Empty:
                        yield ": ping\n\n"
            finally:
                log.unsubscribe(q)

        return Response(gen(), mimetype="text/event-stream",
                        headers={"X-Accel-Buffering": "no"})

    @app.get("/ping")
    def ping():
        """Open on the phone: proves it can reach this PC (separates 'can't reach' from
        'reached, but no configs')."""
        log.info("WEB", f"phone check from {request.remote_addr}")
        return Response("<!doctype html><meta name=viewport content='width=device-width'>"
                        "<body style='font:20px system-ui;padding:24px;background:#0b1020;color:#e8ebf4'>"
                        "<h2 style='color:#34d399'>✓ This device can reach Juggler</h2>"
                        "<p>If v2rayNG still says <b>Failure</b>, wait for the first configs "
                        "to be found, then press Update again.</p>", mimetype="text/html")

    @app.get("/proxy.pac")
    def proxy_pac():
        """Windows' system proxy points here while connected. When Juggler isn't running the
        script can't load, so Windows goes direct instead of losing the internet."""
        return Response(engine.pac(), mimetype="application/x-ns-proxy-autoconfig")

    @app.get("/sub")
    def sub():
        return Response(engine.subscription(request.remote_addr), mimetype="text/plain")

    @app.post("/api/firewall/allow")
    def firewall_allow():
        ok = firewall.allow()
        log.info("WEB", "firewall: asked Windows for permission (UAC)" if ok
                 else "firewall: UAC prompt was declined or unavailable")
        return jsonify(ok=ok, msg="" if ok else "Windows did not run the change (UAC declined?)"), (200 if ok else 400)

    return app
