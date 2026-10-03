from datetime import datetime, timezone

from juggler import sources

UUID = "055a1ce8-2a16-4a0d-a2c2-22826c9b2413"


def page(ids_times_links):
    """A minimal t.me/s/ page: posts oldest first, like Telegram renders them."""
    out = []
    for pid, ts, link in ids_times_links:
        out.append(f'<div class="tgme_widget_message" data-post="chan/{pid}">'
                   f'<div class="tgme_widget_message_text">{link}</div>'
                   f'<time datetime="{ts}" class="time">x</time></div>')
    return "\n".join(out)


def link(n):
    return f"trojan://pw{n}@h{n}.com:443?security=tls"


def test_tg_page_info():
    html = page([(101, "2026-10-01T10:00:00+00:00", link(1)), (105, "2026-10-02T10:00:00+00:00", link(2))])
    oldest_id, oldest_t = sources.tg_page_info(html)
    assert oldest_id == 101
    assert oldest_t == datetime(2026, 10, 1, 10, tzinfo=timezone.utc).timestamp()


def test_tg_links_newest_first_across_pages():
    newest_page = page([(10, "2026-10-02T00:00:00+00:00", link(3)), (11, "2026-10-02T01:00:00+00:00", link(4))])
    older_page = page([(8, "2026-10-01T00:00:00+00:00", link(1)), (9, "2026-10-01T01:00:00+00:00", link(2))])
    got = sources.tg_links([newest_page, older_page])
    assert got == [link(4), link(3), link(2), link(1)]


def test_tg_html_escaped_links():
    html = page([(1, "2026-10-01T00:00:00+00:00",
                  f"vless://{UUID}@h.com:443?security=tls&amp;type=ws&amp;note=x")])
    assert sources.tg_links([html]) == [f"vless://{UUID}@h.com:443?security=tls&type=ws&note=x"]


def test_defaults_are_telegram_heavy_and_legacy_is_upgradeable():
    tg = [u for u in sources.DEFAULT_SOURCES if sources.is_telegram(u)]
    assert len(tg) >= 20 and "https://t.me/s/ConfigsHUB" in tg
    assert sources.LEGACY_DEFAULT != sources.DEFAULT_SOURCES


def test_short_name():
    assert sources.short_name("https://t.me/s/farah_vpn") == "@farah_vpn"
    assert sources.short_name(sources.GITHUB_SOURCES[0]) == "barry-far"


def test_static_source_kept_when_young_and_on_304(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "CACHE", str(tmp_path))
    url = "https://raw.githubusercontent.com/x/y/main/sub.txt"
    calls = []

    def fake_get(u, path, etag=None):
        calls.append(etag)
        return (None, etag) if etag else (link(1), '"v1"')

    monkeypatch.setattr(sources, "_get", fake_get)
    pages, path = sources.fetch_one(url, ["direct"])
    assert path == "direct" and calls == [None]                  # first fetch: full download
    pages, path = sources.fetch_one(url, ["direct"])
    assert path == "kept" and len(calls) == 1                    # young cache: no request at all
    monkeypatch.setattr(sources, "STATIC_MIN_AGE", 0)
    pages, path = sources.fetch_one(url, ["direct"])
    assert path == "kept" and calls[-1] == '"v1"' and pages == [link(1)]   # 304 via ETag
