"""Smoke coverage for the Pattern Quality dashboard API."""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
try:
    sys.path.remove(str(ROOT))
except ValueError:
    pass
sys.path.insert(0, str(ROOT))

from flask import Flask

from dashboard_modules.pattern_quality import init_pattern_quality_blueprint, pattern_quality_bp


class FakePatternBackend:
    def close(self):
        pass

    def get_pattern_quality_summary(self):
        return {
            "total_clusters": 2,
            "canonical_clusters": 1,
            "duplicate_clusters": 1,
            "total_occurrences": 4,
            "average_quality_score": 1.2,
        }

    def get_top_patterns(self, limit=10, canonical_only=False):
        patterns = [
            {
                "cluster_id": "cluster-a",
                "quality_score": 1.5,
                "occurrence_count": 3,
                "distinct_mission_count": 3,
                "is_canonical": True,
                "representative_file_path": "/workspace/mission_a1/core/a.py",
                "decay_factor": 1.0,
                "normalized_code": "FunctionDef Return",
            },
            {
                "cluster_id": "cluster-b",
                "quality_score": 0.8,
                "occurrence_count": 1,
                "distinct_mission_count": 1,
                "is_canonical": False,
                "representative_file_path": "/workspace/mission_b2/core/b.py",
                "decay_factor": 0.8,
                "normalized_code": "Assign Call",
            },
        ]
        if canonical_only:
            patterns = [p for p in patterns if p["is_canonical"]]
        return patterns[:limit]

    def get_duplicate_clusters(self, limit=10):
        return [
            {
                "cluster_id": "cluster-a",
                "quality_score": 1.5,
                "occurrence_count": 3,
                "distinct_mission_count": 3,
                "is_canonical": True,
                "normalized_code": "FunctionDef Return",
                "members": [],
            }
        ][:limit]

    def get_decay_curve(self, cluster_id=None, days=180, points=12):
        return [
            {"age_days": 0, "decay_factor": 1.0, "is_canonical": False},
            {"age_days": days, "decay_factor": 0.25, "is_canonical": False},
        ][:points]


class EmptyPatternBackend:
    def close(self):
        pass

    def get_pattern_quality_summary(self):
        return {
            "total_clusters": 0,
            "canonical_clusters": 0,
            "duplicate_clusters": 0,
            "total_occurrences": 0,
            "average_quality_score": 0.0,
        }

    def get_top_patterns(self, limit=10, canonical_only=False):
        return []

    def get_duplicate_clusters(self, limit=10):
        return []

    def get_decay_curve(self, cluster_id=None, days=180, points=12):
        return []


class BadNumberBackend(FakePatternBackend):
    def get_pattern_quality_summary(self):
        return {
            "total_clusters": float("nan"),
            "canonical_clusters": float("inf"),
            "duplicate_clusters": float("-inf"),
            "total_occurrences": 4,
            "average_quality_score": float("inf"),
        }

    def get_top_patterns(self, limit=10, canonical_only=False):
        return [{"cluster_id": "bad", "quality_score": float("nan"), "decay_factor": float("inf")}]

    def get_duplicate_clusters(self, limit=10):
        return [{"cluster_id": "bad", "quality_score": float("-inf"), "members": [{"similarity_score": float("nan")}]}]

    def get_decay_curve(self, cluster_id=None, days=180, points=12):
        return [{"age_days": 0, "decay_factor": float("inf")}, {"age_days": days, "decay_factor": float("nan")}]


class MalformedPayloadBackend(FakePatternBackend):
    def get_pattern_quality_summary(self):
        return {
            "total_clusters": "not-a-count",
            "canonical_clusters": ["bad"],
            "duplicate_clusters": {"bad": True},
            "total_occurrences": object(),
            "average_quality_score": object(),
        }

    def get_top_patterns(self, limit=10, canonical_only=False):
        return [{
            "cluster_id": "oversized",
            "quality_score": object(),
            "occurrence_count": "bad",
            "distinct_mission_count": [],
            "is_canonical": "true",
            "decay_factor": object(),
            "duplicate_score": object(),
            "normalized_code": "TOKEN " * 1000,
            "normalized_tokens": [f"token-{i}" for i in range(200)],
        }]

    def get_duplicate_clusters(self, limit=10):
        return [{
            "cluster_id": "cluster",
            "members": [{"similarity_score": object()}],
        }]

    def get_decay_curve(self, cluster_id=None, days=180, points=12):
        return [{"age_days": object(), "decay_factor": object(), "is_canonical": "true"}]


def _client(factory):
    app = Flask(__name__)
    init_pattern_quality_blueprint(backend_factory=factory)
    app.register_blueprint(pattern_quality_bp)
    return app.test_client()


def test_pattern_quality_summary_endpoint_uses_real_backend_contract():
    client = _client(lambda: FakePatternBackend())

    response = client.get("/api/pattern-quality/summary")
    data = response.get_json()

    assert response.status_code == 200
    assert data["available"] is True
    assert data["summary"]["canonical_clusters"] == 1
    assert data["top_patterns"][0]["is_canonical"] is True
    assert data["duplicate_clusters"][0]["occurrence_count"] == 3
    assert len(data["decay_curve"]) == 2


def test_pattern_quality_api_fails_closed_when_backend_unavailable():
    client = _client(lambda: (_ for _ in ()).throw(RuntimeError("offline")))

    response = client.get("/api/pattern-quality/summary")
    data = response.get_json()

    assert response.status_code == 200
    assert data["available"] is False
    assert data["summary"]["total_clusters"] == 0
    assert data["top_patterns"] == []


def test_pattern_quality_api_reports_empty_backend_as_available():
    client = _client(lambda: EmptyPatternBackend())

    for path, key in (
        ("/api/pattern-quality/top", "top_patterns"),
        ("/api/pattern-quality/clusters", "duplicate_clusters"),
        ("/api/pattern-quality/decay", "decay_curve"),
    ):
        response = client.get(path)
        data = response.get_json()
        assert response.status_code == 200
        assert data["available"] is True
        assert data[key] == []


def test_pattern_quality_api_rejects_invalid_bounds():
    client = _client(lambda: FakePatternBackend())

    for path in (
        "/api/pattern-quality/top?limit=abc",
        "/api/pattern-quality/top?limit=-5",
        "/api/pattern-quality/top?limit=999",
        "/api/pattern-quality/decay?days=abc&points=12",
        "/api/pattern-quality/decay?days=-1&points=12",
        "/api/pattern-quality/decay?days=180&points=999",
    ):
        response = client.get(path)
        data = response.get_json()
        assert response.status_code == 400
        assert data["available"] is False
        assert "must" in data["error"]


def test_pattern_quality_api_sanitizes_non_finite_numbers_for_strict_json():
    client = _client(lambda: BadNumberBackend())

    for path in (
        "/api/pattern-quality/summary",
        "/api/pattern-quality/top",
        "/api/pattern-quality/clusters",
        "/api/pattern-quality/decay",
    ):
        response = client.get(path)
        raw = response.get_data(as_text=True)
        assert response.status_code == 200
        assert "NaN" not in raw
        assert "Infinity" not in raw
        data = json.loads(raw)
        assert data["available"] is True


def test_pattern_quality_api_normalizes_malformed_schema_and_truncates_payloads():
    client = _client(lambda: MalformedPayloadBackend())

    summary_payload = client.get("/api/pattern-quality/summary").get_json()
    top_payload = client.get("/api/pattern-quality/top").get_json()
    cluster_payload = client.get("/api/pattern-quality/clusters").get_json()
    decay_payload = client.get("/api/pattern-quality/decay").get_json()

    assert summary_payload["summary"]["total_clusters"] == 0
    assert summary_payload["summary"]["average_quality_score"] == 0.0
    pattern = top_payload["top_patterns"][0]
    assert pattern["quality_score"] == 0.0
    assert pattern["occurrence_count"] == 0
    assert pattern["is_canonical"] is False
    assert len(pattern["normalized_code"]) < 1300
    assert len(pattern["normalized_tokens"]) == 80
    assert cluster_payload["duplicate_clusters"][0]["members"][0]["similarity_score"] == 0.0
    assert decay_payload["decay_curve"][0]["age_days"] == 0.0
    assert decay_payload["decay_curve"][0]["decay_factor"] == 0.0


def test_pattern_quality_widget_renders_real_dom_and_escapes_payloads(tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = tmp_path / "pattern_quality_widget_render.mjs"
    script.write_text(textwrap.dedent(f"""
        import assert from 'node:assert/strict';
        import {{ createRequire }} from 'node:module';
        import {{ pathToFileURL }} from 'node:url';

        const require = createRequire({str(root / "package.json")!r});
        const {{ buildSync }} = require('esbuild');
        const {{ JSDOM }} = require('jsdom');
        const bundled = {str(tmp_path / "pattern_quality_widget.mjs")!r};
        buildSync({{
          entryPoints: [{str(root / "dashboard_static/src/modules/pattern-quality.js")!r}],
          bundle: true,
          format: 'esm',
          platform: 'browser',
          outfile: bundled,
        }});

        const dom = new JSDOM(`<!doctype html>
          <div id="pattern-quality-widget-card">
            <span id="pattern-quality-clusters-count"></span>
            <span id="pattern-quality-canonical-count"></span>
            <span id="pattern-quality-duplicates-count"></span>
            <span id="pattern-quality-occurrences-count"></span>
            <span id="pattern-quality-badge"></span>
            <div id="pattern-quality-top"></div>
            <div id="pattern-quality-clusters"></div>
            <div id="pattern-quality-decay"></div>
          </div>`, {{ url: 'http://localhost:5000' }});

        globalThis.window = dom.window;
        globalThis.document = dom.window.document;
        globalThis.fetch = async () => ({{
          ok: true,
          json: async () => ({{
            available: true,
            summary: {{
              total_clusters: 2,
              canonical_clusters: 1,
              duplicate_clusters: 1,
              total_occurrences: 4,
            }},
            top_patterns: [{{
              quality_score: 1.45,
              occurrence_count: 3,
              distinct_mission_count: 3,
              is_canonical: true,
              representative_file_path: '/workspace/mission_x/core/<script>alert(1)</script>.py',
              decay_factor: 1.0,
            }}],
            duplicate_clusters: [{{
              cluster_id: 'cluster-x',
              occurrence_count: 3,
              distinct_mission_count: 3,
              normalized_code: 'FunctionDef <img src=x onerror=alert(1)> Return',
            }}],
            decay_curve: [
              {{ age_days: 0, decay_factor: 1.0 }},
              {{ age_days: 180, decay_factor: 0.25 }},
            ],
          }}),
        }});

        const widget = await import(pathToFileURL(bundled).href);
        await widget.refreshPatternQualityWidget();

        assert.equal(document.getElementById('pattern-quality-clusters-count').textContent, '2');
        assert.equal(document.getElementById('pattern-quality-canonical-count').textContent, '1');
        assert.equal(document.getElementById('pattern-quality-badge').textContent, 'live');
        assert.match(document.getElementById('pattern-quality-top').textContent, /canonical/);
        assert.match(document.getElementById('pattern-quality-decay').innerHTML, /svg/);
        assert.equal(document.querySelectorAll('script').length, 0);
        assert.equal(document.querySelectorAll('img').length, 0);
        assert.match(document.getElementById('pattern-quality-clusters').textContent, /FunctionDef/);
    """))

    result = subprocess.run(
        ["node", str(script)],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr or result.stdout
