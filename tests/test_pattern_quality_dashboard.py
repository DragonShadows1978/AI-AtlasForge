"""Smoke coverage for the Pattern Quality dashboard API."""

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
