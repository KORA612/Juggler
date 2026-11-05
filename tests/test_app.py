import pytest

from juggler import app as appmod


class FakeEngine:
    def __init__(self):
        self.refreshes = 0

    def snapshot(self):
        return {"ok": True}

    def refresh_now(self):
        self.refreshes += 1
        return True

    def pac(self):
        return "function FindProxyForURL(url, host) { return \"DIRECT\"; }"

    def subscription(self, ip=""):
        return "c3Vi"


@pytest.fixture
def eng():
    return FakeEngine()


@pytest.fixture
def client(eng):
    return appmod.create_app(eng).test_client()


LOCAL = {"REMOTE_ADDR": "127.0.0.1"}


@pytest.mark.parametrize("host", ["localhost:8765", "127.0.0.1:8765", "[::1]:8765", "localhost",
                                  "192.168.1.5:8765", "LOCALHOST:8765"])
def test_host_ok_accepts_ips_and_localhost(host):
    assert appmod.host_ok(host)


@pytest.mark.parametrize("host", ["evil.com", "evil.com:8765", "127.0.0.1.nip.io:8765",
                                  "localhost.evil.com", ""])
def test_host_ok_rejects_names(host):
    assert not appmod.host_ok(host)


def test_dns_rebinding_is_refused(client):
    r = client.get("/api/state", headers={"Host": "rebind.evil.com:8765"}, environ_base=LOCAL)
    assert r.status_code == 403


def test_dashboard_reads_work(client):
    r = client.get("/api/state", headers={"Host": "localhost:8765"}, environ_base=LOCAL)
    assert r.status_code == 200 and r.json == {"ok": True}


def test_post_without_header_is_refused(client, eng):
    r = client.post("/api/refresh", headers={"Host": "localhost:8765"}, environ_base=LOCAL)
    assert r.status_code == 403 and eng.refreshes == 0


def test_post_with_header_works(client, eng):
    r = client.post("/api/refresh", headers={"Host": "localhost:8765", "X-Juggler": "1"}, environ_base=LOCAL)
    assert r.status_code == 200 and eng.refreshes == 1


def test_api_is_still_localhost_only(client, eng):
    r = client.post("/api/refresh", headers={"Host": "192.168.1.5:8765", "X-Juggler": "1"},
                    environ_base={"REMOTE_ADDR": "192.168.1.9"})
    assert r.status_code == 403 and eng.refreshes == 0


def test_phone_reaches_sub_by_ip(client):
    r = client.get("/sub", headers={"Host": "192.168.1.5:8765"}, environ_base={"REMOTE_ADDR": "192.168.1.9"})
    assert r.status_code == 200


def test_proxy_pac_is_served_locally(client):
    r = client.get("/proxy.pac?v=1", headers={"Host": "127.0.0.1:8765"}, environ_base=LOCAL)
    assert r.status_code == 200 and r.mimetype == "application/x-ns-proxy-autoconfig"
    assert b"FindProxyForURL" in r.data
