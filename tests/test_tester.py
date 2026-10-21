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
