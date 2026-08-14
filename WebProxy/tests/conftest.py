"""Shared test isolation for WebProxy's filesystem-backed state."""

import pytest

from WebProxy import service


@pytest.fixture(autouse=True)
def isolate_service_paths(tmp_path, monkeypatch):
    """Keep every test's cache, stats, paper, and image state under tmp_path."""
    cache_dir = tmp_path / "web_proxy_cache"
    paper_artifact_dir = tmp_path / "paper_artifacts"

    # Scope the import-time inputs too: a test that reloads service must not
    # reconstitute the deployed checkout's paths during that same test.
    monkeypatch.setenv("ATLASFORGE_WEB_PROXY_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("ATLASFORGE_WEB_PROXY_PAPER_DIR", str(paper_artifact_dir))

    monkeypatch.setattr(service, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(service, "STATS_PATH", tmp_path / "web_proxy_stats.json")
    monkeypatch.setattr(service, "PAPER_ARTIFACT_DIR", paper_artifact_dir)
    monkeypatch.setattr(service, "IMAGE_CACHE_DIR", cache_dir / "images")
