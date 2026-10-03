"""Flask dashboard + JSON API. Everything except /sub is localhost-only."""
import json
import logging
import queue

import qrcode
import qrcode.image.svg
from flask import Flask, Response, abort, jsonify, render_template, request

from . import firewall, log


def create_app(engine):
    app = Flask(__name__)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    @app.before_request
    def local_only():
        if request.path == "/sub":
            return None
        if request.remote_addr not in ("127.0.0.1", "::1"):
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

    @app.post("/api/pin")
    def pin():
        ok = engine.pin(body().get("id"))
        return jsonify(ok=ok, msg="" if ok else "node is not in the live balancer"), (200 if ok else 400)

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
