import urllib.error

from juggler import tester


def _raise(code):
    def get(*a, **kw):
        raise urllib.error.HTTPError("https://x.com", code, "", {}, None)
    return get


def test_site_answering_with_a_client_error_is_still_reachable(monkeypatch):
    monkeypatch.setattr(tester, "_get", _raise(403))
    assert tester._site(0, None, False, "https://x.com")["ok"] is True
    monkeypatch.setattr(tester, "_get", _raise(502))
    assert tester._site(0, None, False, "https://x.com")["ok"] is False


def test_site_that_never_answers_is_unreachable(monkeypatch):
    def get(*a, **kw):
        raise TimeoutError
    monkeypatch.setattr(tester, "_get", get)
    assert tester._site(0, None, False, "https://x.com") == {"ok": False, "ms": None}


def test_a_crawling_download_is_a_measurement_a_quick_one_only_a_lower_bound():
    assert tester.reading(64_000, 5.0, False) == {"kbps": 102}       # 64 KB in 5 s: really slow
    assert tester.reading(40_000, 10.0, True) == {"kbps": 32}        # cut off by the deadline
    assert tester.reading(64_000, 0.4, False) == {"kbps_min": 1280}  # slow start dominates
    assert tester.reading(0, 8.0, True) == {"kbps": 0}


def test_full_check_times_the_youtube_page(monkeypatch):
    monkeypatch.setattr(tester, "_alive", lambda port, relaxed: 340)
    monkeypatch.setattr(tester, "_page", lambda port, url, need, timeout=10:
                        (True, {"kbps": 96}) if url == tester.YT_PAGE else (True, {}))
    node = type("N", (), {"country": "DE"})()
    r = tester._full(0, node, False)
    assert r["ok"] and r["ms"] == 340 and r["kbps"] == 96


def test_speed_with_no_body_is_slow_evidence_but_not_a_pass(monkeypatch):
    monkeypatch.setattr(tester, "_speed_once", lambda port, url: {"kbps": 0})
    assert tester._speed(0, None, False) == {"kbps": 0}
    monkeypatch.setattr(tester, "_speed_once", lambda port, url: None)
    assert tester._speed(0, None, False) is None


class _Throttled:
    """A local HTTP server that urllib reaches as its 'proxy': serves `size` bytes,
    `per_chunk` bytes every `gap` seconds."""

    def __init__(self, size, per_chunk, gap):
        import http.server
        import threading

        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(outer.size))
                self.end_headers()
                sent = 0
                try:
                    while sent < outer.size:
                        n = min(outer.per_chunk, outer.size - sent)
                        self.wfile.write(b"x" * n)
                        self.wfile.flush()
                        sent += n
                        time.sleep(outer.gap)
                except OSError:
                    pass

            def log_message(self, *a):
                pass

        self.size, self.per_chunk, self.gap = size, per_chunk, gap
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


import time  # noqa: E402


def test_timed_download_through_a_real_socket():
    slow = _Throttled(64_000, 4_000, 0.25)          # ~128 kbps for 4 s
    try:
        status, got, secs, capped = tester._timed(slow.port, "http://example.test/", 64_000, 5, 1.5)
        assert status == 200 and capped and got < 64_000 and 1.4 < secs < 2.5
        r = tester.reading(got, secs, capped)
        assert 60 < r["kbps"] < 250
    finally:
        slow.close()
    fast = _Throttled(64_000, 64_000, 0)
    try:
        status, got, secs, capped = tester._timed(fast.port, "http://example.test/", 64_000, 5, 10)
        assert got == 64_000 and not capped
        assert "kbps_min" in tester.reading(got, secs, capped)
    finally:
        fast.close()
