import json
import os
import sys
import time
from pathlib import Path

from WebProxy import service


def test_cache_sweep_deletes_stale_past_grace_and_keeps_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "SEARCH_TTL_S", 10)
    monkeypatch.setattr(service, "CACHE_STALE_GRACE", 2.0)
    monkeypatch.setattr(service, "CACHE_MAX_BYTES", 1_000_000)
    cache = service.FileCache(tmp_path)

    stale = tmp_path / "search_stale.json"
    fresh = tmp_path / "search_fresh.json"
    now = time.time()
    stale.write_text("{}", encoding="utf-8")
    fresh.write_text("{}", encoding="utf-8")
    os.utime(stale, (now - 25, now - 25))

    result = cache.sweep()

    assert not stale.exists()
    assert fresh.exists()
    assert result["files_deleted"] == 1


def test_cache_sweep_size_cap_evicts_oldest_and_includes_images(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "SEARCH_TTL_S", 10_000)
    monkeypatch.setattr(service, "FETCH_TTL_S", 10_000)
    monkeypatch.setattr(service, "CACHE_STALE_GRACE", 2.0)
    monkeypatch.setattr(service, "CACHE_MAX_BYTES", 15)
    cache = service.FileCache(tmp_path)
    images = tmp_path / "images"
    images.mkdir()

    oldest = tmp_path / "fetch_old.json"
    image = images / "middle.png"
    newest = tmp_path / "search_new.json"
    oldest.write_bytes(b"a" * 10)
    image.write_bytes(b"b" * 10)
    newest.write_bytes(b"c" * 10)
    now = time.time()
    os.utime(oldest, (now - 3, now - 3))
    os.utime(image, (now - 2, now - 2))
    os.utime(newest, (now - 1, now - 1))

    result = cache.sweep()

    assert not oldest.exists()
    assert not image.exists()
    assert newest.exists()
    assert result["bytes_remaining"] == 10
    assert result["bytes_freed"] == 20


def test_cache_sweep_tolerates_file_vanishing_during_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "SEARCH_TTL_S", 1)
    monkeypatch.setattr(service, "CACHE_STALE_GRACE", 1.0)
    monkeypatch.setattr(service, "CACHE_MAX_BYTES", 1_000_000)
    cache = service.FileCache(tmp_path)
    victim = tmp_path / "search_race.json"
    victim.write_text("{}", encoding="utf-8")
    old = time.time() - 10
    os.utime(victim, (old, old))

    original_unlink = Path.unlink

    def unlink_then_report_missing(path, *args, **kwargs):
        if path == victim:
            original_unlink(path, *args, **kwargs)
            raise FileNotFoundError(path)
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink_then_report_missing)

    cache.sweep()

    assert not victim.exists()


def test_endpoint_counters_persist_across_simulated_restart(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    stats_path = tmp_path / "web_proxy_stats.json"
    monkeypatch.setattr(service, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(service, "IMAGE_CACHE_DIR", cache_dir / "images")
    monkeypatch.setattr(service, "STATS_PATH", stats_path)
    monkeypatch.setattr(service, "CACHE_SWEEP_INTERVAL_S", 0)

    def fake_search(self, query, count=5, provider="auto"):
        return {"provider": "offline", "query": query, "results": [], "count": 0}

    def fake_fetch(url, max_chars=-1, timeout_s=20):
        return {"url": url, "text": "offline", "text_length": 7}

    monkeypatch.setattr(service.SearchProvider, "search", fake_search)
    monkeypatch.setattr(service, "fetch_page", fake_fetch)
    client = service.create_app().test_client()

    assert client.post("/search", json={"query": "counter-search"}).status_code == 200
    assert client.post("/search", json={"query": "counter-search"}).status_code == 200
    assert client.post(
        "/fetch", json={"url": "https://counter.example/page"}
    ).status_code == 200
    assert client.post(
        "/fetch", json={"url": "https://counter.example/page"}
    ).status_code == 200

    reloaded = service.WebProxyStats(stats_path).snapshot()
    assert reloaded["searches_total"] == 2
    assert reloaded["fetches_total"] == 2
    assert reloaded["search_cache_hits"] == 1
    assert reloaded["fetch_cache_hits"] == 1


def test_corrupt_stats_json_starts_fresh(tmp_path, caplog):
    stats_path = tmp_path / "web_proxy_stats.json"
    stats_path.write_text("{not-json", encoding="utf-8")

    with caplog.at_level("WARNING"):
        stats = service.WebProxyStats(stats_path)

    snapshot = stats.snapshot()
    assert snapshot["searches_total"] == 0
    assert snapshot["provider_breakdown"] == {}
    assert "starting fresh" in caplog.text


def test_provider_call_updates_lifetime_breakdown(tmp_path):
    class FakeResponse:
        text = ""

        def raise_for_status(self):
            return None

    class FakeSession:
        def post(self, *args, **kwargs):
            return FakeResponse()

    stats = service.WebProxyStats(tmp_path / "web_proxy_stats.json")
    provider = service.SearchProvider(session=FakeSession(), stats=stats)

    result = provider.search_duckduckgo("offline")

    assert result["provider"] == "duckduckgo"
    assert stats.snapshot()["provider_breakdown"] == {"duckduckgo": 1}


def test_stats_endpoint_contains_legacy_and_lifetime_keys(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr(service, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(service, "IMAGE_CACHE_DIR", cache_dir / "images")
    monkeypatch.setattr(service, "STATS_PATH", tmp_path / "web_proxy_stats.json")
    monkeypatch.setattr(service, "CACHE_SWEEP_INTERVAL_S", 0)
    cache_dir.mkdir()
    (cache_dir / "search_seed.json").write_text(
        json.dumps({"provider": "offline", "_cached_at": int(time.time())}),
        encoding="utf-8",
    )
    (cache_dir / "fetch_seed.json").write_text(
        json.dumps({"_cached_at": int(time.time())}), encoding="utf-8"
    )
    image_dir = cache_dir / "images"
    image_dir.mkdir()
    (image_dir / "seed.png").write_bytes(b"image")

    body = service.create_app().test_client().get("/stats").get_json()

    for key in (
        "cached_searches",
        "cached_fetches",
        "cached_images",
        "cached_image_searches",
        "providers",
        "cache_dir",
        "searches_total",
        "fetches_total",
        "paper_fetches_total",
        "image_searches_total",
        "research_total",
        "search_cache_hits",
        "fetch_cache_hits",
        "paper_fetch_cache_hits",
        "image_search_cache_hits",
        "provider_breakdown",
    ):
        assert key in body
    assert body["cached_searches"] == 1
    assert body["cached_fetches"] == 1
    assert body["cached_images"] == 1


def test_main_uses_waitress_without_debug(monkeypatch):
    calls = []

    class FakeApp:
        def run(self, **kwargs):
            calls.append(("flask", kwargs))

    class FakeWaitress:
        def serve(self, app, **kwargs):
            calls.append(("waitress", app, kwargs))

    monkeypatch.setattr(service, "create_app", lambda **kwargs: FakeApp())
    monkeypatch.setattr(service, "waitress", FakeWaitress())
    monkeypatch.setattr(service, "WEB_PROXY_THREADS", 3)
    monkeypatch.setattr(sys, "argv", ["web-proxy", "--port", "9876"])

    service.main()

    assert calls == [("waitress", calls[0][1], {
        "host": "127.0.0.1",
        "port": 9876,
        "threads": 3,
    })]


def test_main_uses_flask_in_debug_mode(monkeypatch):
    calls = []

    class FakeApp:
        def run(self, **kwargs):
            calls.append(kwargs)

    class FakeWaitress:
        def serve(self, *args, **kwargs):
            raise AssertionError("waitress must not serve debug mode")

    monkeypatch.setattr(service, "create_app", lambda **kwargs: FakeApp())
    monkeypatch.setattr(service, "waitress", FakeWaitress())
    monkeypatch.setattr(sys, "argv", ["web-proxy", "--debug"])

    service.main()

    assert calls == [{
        "host": "127.0.0.1",
        "port": service.DEFAULT_PORT,
        "debug": True,
        "threaded": True,
    }]
