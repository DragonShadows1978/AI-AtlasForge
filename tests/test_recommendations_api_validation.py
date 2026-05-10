#!/usr/bin/env python3
"""
Functional tests for /api/recommendations validation hardening (mission_caca28ec).

Covers the five validation gaps closed in this mission:

  1. POST mission_type: invalid string → HTTP 400 (was: silent EXPANSION coerce).
  2. PUT mission_type: null → HTTP 400 (was: double-coerce via "NONE" string).
  3. POST/PUT mission_description / rationale: non-string → HTTP 400.
  4. POST/PUT execution_profile: strict 400 pattern, mirrors mission_type.
  5. suggestion_storage.add(): NULL mission_type defaults to EXPANSION at storage layer.

Tests run a real Flask app with the core blueprint registered, backed by an
isolated SQLite database in a tmp dir. No mocking of internals — the storage
backend, validation layer, and HTTP layer all execute end-to-end.
"""
import json
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

AF_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AF_ROOT))

import suggestion_storage
from suggestion_storage import SQLiteSuggestionStorage


def _build_app(tmp_db_path):
    """Build a minimal Flask app with the core blueprint registered.

    Storage singletons are overridden to point at an isolated tmp DB so each
    test class runs against a fresh schema.
    """
    from flask import Flask
    from dashboard_modules import core as core_mod

    app = Flask(__name__)
    app.register_blueprint(core_mod.core_bp)

    test_storage = SQLiteSuggestionStorage(db_path=tmp_db_path)
    suggestion_storage._storage_instance = test_storage
    core_mod._suggestion_storage = test_storage

    return app, test_storage


class _RecommendationsApiTestBase(unittest.TestCase):
    """Shared setup: tmp DB + Flask test client."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test_suggestions.sqlite"
        self.app, self.storage = _build_app(self.db_path)
        self.client = self.app.test_client()

    def tearDown(self):
        suggestion_storage._storage_instance = None
        from dashboard_modules import core as core_mod
        core_mod._suggestion_storage = None
        self._tmpdir.cleanup()

    def _post(self, payload):
        return self.client.post(
            "/api/recommendations",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def _put(self, rec_id, payload):
        return self.client.put(
            f"/api/recommendations/{rec_id}",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def _add_seed_record(self, **overrides):
        """Insert a baseline valid record and return its ID."""
        record = {
            "mission_title": "Seed Mission",
            "mission_description": "seed",
            "rationale": "seed rationale",
            "suggested_cycles": 3,
            "source_type": "manual",
            "classification": "EXPANSION",
            "mission_type": "full_rd",
            "execution_profile": "full_rd",
        }
        record.update(overrides)
        return self.storage.add(record)


class TestPostRecommendationValidation(_RecommendationsApiTestBase):

    def test_invalid_mission_type_returns_400(self):
        resp = self._post({
            "mission_title": "Bad type test",
            "mission_type": "NOT_A_TYPE",
        })
        self.assertEqual(resp.status_code, 400)
        body = resp.get_json()
        self.assertFalse(body["success"])
        self.assertIn("Invalid mission_type", body["error"])

    def test_null_mission_type_defaults_to_expansion(self):
        resp = self._post({
            "mission_title": "Null mt test",
            "mission_type": None,
        })
        self.assertEqual(resp.status_code, 200)
        rec = resp.get_json()["recommendation"]
        stored = self.storage.get_by_id(rec["id"])
        self.assertEqual(stored["classification"], "EXPANSION")
        self.assertEqual(stored["mission_type"], "plan_only")

    def test_missing_mission_type_defaults_to_expansion(self):
        resp = self._post({"mission_title": "Missing mt test"})
        self.assertEqual(resp.status_code, 200)
        rec = resp.get_json()["recommendation"]
        stored = self.storage.get_by_id(rec["id"])
        self.assertEqual(stored["classification"], "EXPANSION")
        self.assertEqual(stored["mission_type"], "plan_only")

    def test_valid_mission_type_normalized_to_uppercase(self):
        resp = self._post({
            "mission_title": "Lowercase mt test",
            "mission_type": "bugfix",
        })
        self.assertEqual(resp.status_code, 200)
        rec = resp.get_json()["recommendation"]
        stored = self.storage.get_by_id(rec["id"])
        self.assertEqual(stored["classification"], "BUGFIX")
        self.assertEqual(stored["mission_type"], "bug_hunt")
        self.assertEqual(stored["execution_profile"], "bug_hunt")

    def test_int_mission_type_returns_400(self):
        resp = self._post({
            "mission_title": "Int mt test",
            "mission_type": 42,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("must be a string", resp.get_json()["error"])

    def test_invalid_execution_profile_returns_400(self):
        resp = self._post({
            "mission_title": "Bad ep test",
            "execution_profile": "not_a_profile",
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Invalid execution_profile", resp.get_json()["error"])

    def test_null_execution_profile_defaults_to_full_rd(self):
        resp = self._post({
            "mission_title": "Null ep test",
            "execution_profile": None,
        })
        self.assertEqual(resp.status_code, 200)
        rec = resp.get_json()["recommendation"]
        stored = self.storage.get_by_id(rec["id"])
        self.assertEqual(stored["execution_profile"], "plan_only")

    def test_int_execution_profile_returns_400(self):
        resp = self._post({
            "mission_title": "Int ep test",
            "execution_profile": 7,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("must be a string", resp.get_json()["error"])

    def test_list_mission_description_returns_400(self):
        resp = self._post({
            "mission_title": "List desc test",
            "mission_description": ["not", "a", "string"],
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("mission_description must be a string", resp.get_json()["error"])

    def test_dict_rationale_returns_400(self):
        resp = self._post({
            "mission_title": "Dict rationale test",
            "rationale": {"why": "because"},
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("rationale must be a string", resp.get_json()["error"])

    def test_null_description_treated_as_empty(self):
        resp = self._post({
            "mission_title": "Null desc test",
            "mission_description": None,
        })
        self.assertEqual(resp.status_code, 200)
        rec = resp.get_json()["recommendation"]
        stored = self.storage.get_by_id(rec["id"])
        self.assertEqual(stored["mission_description"], "")

    def test_null_mission_title_returns_400(self):
        resp = self._post({"mission_title": None})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("mission_title cannot be null", resp.get_json()["error"])

    def test_non_string_mission_title_returns_400(self):
        resp = self._post({"mission_title": 123})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("mission_title must be a string", resp.get_json()["error"])

    def test_short_mission_title_returns_400(self):
        resp = self._post({"mission_title": "  x "})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("at least 3 characters", resp.get_json()["error"])

    def test_post_rejects_non_integer_suggested_cycles(self):
        for value in (True, False, 1.5, "5", None):
            with self.subTest(value=value):
                resp = self._post({
                    "mission_title": "Bad cycles test",
                    "suggested_cycles": value,
                })
                self.assertEqual(resp.status_code, 400)
                self.assertIn("suggested_cycles must be an integer 1-10", resp.get_json()["error"])

    def test_post_rejects_non_string_source_metadata(self):
        resp = self._post({
            "mission_title": "Bad source metadata",
            "source_mission_id": ["not", "sqlite-bindable"],
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("source_mission_id must be a string", resp.get_json()["error"])


class TestPutRecommendationValidation(_RecommendationsApiTestBase):

    def test_invalid_mission_type_returns_400(self):
        rec_id = self._add_seed_record()
        resp = self._put(rec_id, {"mission_type": "NOT_A_TYPE"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Invalid mission_type", resp.get_json()["error"])
        self.assertEqual(self.storage.get_by_id(rec_id)["classification"], "EXPANSION")
        self.assertEqual(self.storage.get_by_id(rec_id)["mission_type"], "full_rd")

    def test_null_mission_type_returns_400(self):
        rec_id = self._add_seed_record(classification="BUGFIX", mission_type="bug_hunt", execution_profile="bug_hunt")
        resp = self._put(rec_id, {"mission_type": None})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("cannot be null", resp.get_json()["error"])
        self.assertEqual(self.storage.get_by_id(rec_id)["classification"], "BUGFIX")
        self.assertEqual(self.storage.get_by_id(rec_id)["mission_type"], "bug_hunt")

    def test_valid_mission_type_succeeds(self):
        rec_id = self._add_seed_record()
        resp = self._put(rec_id, {"mission_type": "bugfix"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.storage.get_by_id(rec_id)["classification"], "BUGFIX")
        self.assertEqual(self.storage.get_by_id(rec_id)["mission_type"], "full_rd")

    def test_int_mission_type_returns_400(self):
        rec_id = self._add_seed_record()
        resp = self._put(rec_id, {"mission_type": 99})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("must be a string", resp.get_json()["error"])

    def test_invalid_execution_profile_returns_400(self):
        rec_id = self._add_seed_record()
        resp = self._put(rec_id, {"execution_profile": "not_a_profile"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Invalid execution_profile", resp.get_json()["error"])
        self.assertEqual(self.storage.get_by_id(rec_id)["execution_profile"], "full_rd")

    def test_null_execution_profile_returns_400(self):
        rec_id = self._add_seed_record()
        resp = self._put(rec_id, {"execution_profile": None})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("cannot be null", resp.get_json()["error"])

    def test_int_mission_description_returns_400(self):
        rec_id = self._add_seed_record()
        resp = self._put(rec_id, {"mission_description": 12345})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("mission_description must be a string", resp.get_json()["error"])

    def test_null_rationale_treated_as_empty(self):
        rec_id = self._add_seed_record(rationale="prior")
        resp = self._put(rec_id, {"rationale": None})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.storage.get_by_id(rec_id)["rationale"], "")

    def test_dict_rationale_returns_400(self):
        rec_id = self._add_seed_record()
        resp = self._put(rec_id, {"rationale": {"x": 1}})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("rationale must be a string", resp.get_json()["error"])

    def test_null_mission_title_returns_400(self):
        rec_id = self._add_seed_record()
        resp = self._put(rec_id, {"mission_title": None})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("mission_title cannot be null", resp.get_json()["error"])

    def test_non_string_mission_title_returns_400(self):
        rec_id = self._add_seed_record()
        resp = self._put(rec_id, {"mission_title": 123})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("mission_title must be a string", resp.get_json()["error"])

    def test_put_rejects_non_integer_suggested_cycles(self):
        rec_id = self._add_seed_record()
        for value in (True, False, 2.5, "5", None):
            with self.subTest(value=value):
                resp = self._put(rec_id, {"suggested_cycles": value})
                self.assertEqual(resp.status_code, 400)
                self.assertIn("suggested_cycles must be an integer 1-10", resp.get_json()["error"])

    def test_first_edit_preserves_original_fields_once(self):
        rec_id = self._add_seed_record(
            mission_title="Original Title",
            mission_description="Original description",
            rationale="Original rationale",
            suggested_cycles=4,
        )
        resp = self._put(rec_id, {"mission_description": "Edited description"})
        self.assertEqual(resp.status_code, 200)
        stored = self.storage.get_by_id(rec_id)
        self.assertEqual(stored["original_mission_title"], "Original Title")
        self.assertEqual(stored["original_mission_description"], "Original description")
        self.assertEqual(stored["original_rationale"], "Original rationale")
        self.assertEqual(stored["original_suggested_cycles"], 4)

        resp = self._put(rec_id, {"mission_title": "Second Edit Title"})
        self.assertEqual(resp.status_code, 200)
        stored = self.storage.get_by_id(rec_id)
        self.assertEqual(stored["original_mission_title"], "Original Title")
        self.assertEqual(stored["mission_title"], "Second Edit Title")

    def test_update_returns_404_when_row_disappears_between_read_and_write(self):
        rec_id = self._add_seed_record()
        original_update = self.storage.update
        try:
            self.storage.update = lambda *_args, **_kwargs: False
            resp = self._put(rec_id, {"mission_title": "Changed Title"})
        finally:
            self.storage.update = original_update
        self.assertEqual(resp.status_code, 404)
        self.assertIn("Recommendation not found", resp.get_json()["error"])


class TestMergeRecommendationValidation(_RecommendationsApiTestBase):

    def _merge(self, payload):
        return self.client.post(
            "/api/recommendations/merge",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def _seed_pair(self):
        first = self._add_seed_record(mission_title="Merge Source One")
        second = self._add_seed_record(mission_title="Merge Source Two")
        return first, second

    def test_merge_rejects_non_object_body(self):
        resp = self.client.post(
            "/api/recommendations/merge",
            data=json.dumps(["not", "an", "object"]),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Request body must be a JSON object", resp.get_json()["error"])

    def test_merge_rejects_null_merged_data(self):
        first, second = self._seed_pair()
        resp = self._merge({"source_ids": [first, second], "merged_data": None})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("merged_data must be an object", resp.get_json()["error"])

    def test_merge_rejects_bad_cycles_and_profile(self):
        first, second = self._seed_pair()
        resp = self._merge({
            "source_ids": [first, second],
            "merged_data": {"mission_title": "Merged Title", "suggested_cycles": 2.5},
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("suggested_cycles must be an integer", resp.get_json()["error"])

        resp = self._merge({
            "source_ids": [first, second],
            "merged_data": {"mission_title": "Merged Title", "execution_profile": "bad"},
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Invalid execution_profile", resp.get_json()["error"])

    def test_merge_rejects_bad_description_type(self):
        first, second = self._seed_pair()
        resp = self._merge({
            "source_ids": [first, second],
            "merged_data": {
                "mission_title": "Merged Title",
                "mission_description": ["not", "text"],
            },
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("mission_description must be a string", resp.get_json()["error"])


class TestStorageMissionTypeDefault(_RecommendationsApiTestBase):

    def test_add_with_none_mission_type_defaults_to_expansion_classification(self):
        rec_id = self.storage.add({
            "mission_title": "None mt at storage",
            "mission_type": None,
        })
        stored = self.storage.get_by_id(rec_id)
        self.assertEqual(stored["classification"], "EXPANSION")
        self.assertEqual(stored["mission_type"], "plan_only")

    def test_add_without_mission_type_defaults_to_expansion_classification(self):
        rec_id = self.storage.add({"mission_title": "Missing mt at storage"})
        stored = self.storage.get_by_id(rec_id)
        self.assertEqual(stored["classification"], "EXPANSION")
        self.assertEqual(stored["mission_type"], "plan_only")

    def test_add_with_invalid_mission_type_string_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.storage.add({
                "mission_title": "Invalid mt at storage",
                "mission_type": "GARBAGE",
            })
        self.assertIn("Invalid mission_type", str(ctx.exception))

    def test_add_with_legacy_classification_mission_type_persists_classification(self):
        rec_id = self.storage.add({
            "mission_title": "Valid mt at storage",
            "mission_type": "BUGFIX",
        })
        stored = self.storage.get_by_id(rec_id)
        self.assertEqual(stored["classification"], "BUGFIX")
        self.assertEqual(stored["mission_type"], "bug_hunt")
        self.assertEqual(stored["execution_profile"], "bug_hunt")

    def test_upsert_with_bugfix_defaults_execution_profile(self):
        rec_id = self.storage.upsert({
            "mission_title": "Upsert bugfix default",
            "mission_type": "BUGFIX",
        })
        stored = self.storage.get_by_id(rec_id)
        self.assertEqual(stored["classification"], "BUGFIX")
        self.assertEqual(stored["mission_type"], "bug_hunt")
        self.assertEqual(stored["execution_profile"], "bug_hunt")

    def test_upsert_batch_with_tech_debt_defaults_execution_profile(self):
        count = self.storage.upsert_batch([{
            "id": "batch_tech_debt",
            "mission_title": "Batch tech debt default",
            "mission_type": "TECH_DEBT",
            "execution_profile": None,
        }])
        self.assertEqual(count, 1)
        stored = self.storage.get_by_id("batch_tech_debt")
        self.assertEqual(stored["classification"], "TECH_DEBT")
        self.assertEqual(stored["mission_type"], "build_only")
        self.assertEqual(stored["execution_profile"], "build_only")

    def test_update_all_commits_and_rolls_back_on_validation_error(self):
        existing_id = self.storage.add({"mission_title": "Existing survivor"})

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            count = self.storage.update_all([{
                "id": "replace_bugfix",
                "mission_title": "Replace bugfix",
                "mission_type": "BUGFIX",
            }])
        self.assertEqual(count, 1)
        self.assertIsNone(self.storage.get_by_id(existing_id))
        self.assertEqual(self.storage.get_by_id("replace_bugfix")["execution_profile"], "bug_hunt")
        self.assertEqual(self.storage.get_by_id("replace_bugfix")["classification"], "BUGFIX")
        self.assertEqual(self.storage._write_count, 2)

        with self.assertRaises(ValueError):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                self.storage.update_all([{
                    "id": "invalid_replace",
                    "mission_title": None,
                }])
        self.assertIsNotNone(self.storage.get_by_id("replace_bugfix"))

    def test_migrate_from_json_uses_storage_defaults(self):
        json_path = Path(self._tmpdir.name) / "suggestions.json"
        json_path.write_text(json.dumps({
            "items": [{
                "id": "migrated_bugfix",
                "mission_title": "Migrated bugfix",
                "mission_type": "BUGFIX",
                "execution_profile": None,
            }]
        }))
        result = self.storage.migrate_from_json(json_path)
        self.assertTrue(result["success"])
        self.assertEqual(result["imported"], 1)
        stored = self.storage.get_by_id("migrated_bugfix")
        self.assertEqual(stored["classification"], "BUGFIX")
        self.assertEqual(stored["mission_type"], "bug_hunt")
        self.assertEqual(stored["execution_profile"], "bug_hunt")
        self.assertEqual(result["db_count_before"] + result["imported"], result["expected_db_count"])
        self.assertTrue(result["counts_match"])

    def test_rejects_scalar_json_fields(self):
        with self.assertRaises(ValueError) as ctx:
            self.storage.add({
                "mission_title": "Bad tags",
                "auto_tags": 42,
            })
        self.assertIn("auto_tags must be a list", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            self.storage.add({
                "mission_title": "Bad drift context",
                "drift_context": ["not", "an", "object"],
            })
        self.assertIn("drift_context must be an object", str(ctx.exception))

    def test_rejects_bool_priority_score(self):
        with self.assertRaises(ValueError) as ctx:
            self.storage.add({
                "mission_title": "Bool priority",
                "priority_score": True,
            })
        self.assertIn("priority_score must be numeric", str(ctx.exception))


class _JsonIO:
    @staticmethod
    def atomic_write_json(path, data):
        Path(path).write_text(json.dumps(data))

    @staticmethod
    def atomic_read_json(path, default=None):
        path = Path(path)
        if not path.exists():
            return default
        return json.loads(path.read_text())


class TestSetMissionBuildApprovalGate(_RecommendationsApiTestBase):

    def setUp(self):
        super().setUp()
        from dashboard_modules import core as core_mod
        self.core_mod = core_mod
        self._old_io_utils = core_mod.io_utils
        self._old_base_dir = core_mod.BASE_DIR
        self._old_workspace_dir = core_mod.WORKSPACE_DIR
        self._old_mission_path = core_mod.MISSION_PATH
        tmp_root = Path(self._tmpdir.name) / "atlasforge"
        (tmp_root / "state").mkdir(parents=True, exist_ok=True)
        core_mod.io_utils = _JsonIO
        core_mod.BASE_DIR = tmp_root
        core_mod.WORKSPACE_DIR = tmp_root / "workspace"
        core_mod.MISSION_PATH = tmp_root / "state" / "mission.json"

    def tearDown(self):
        self.core_mod.io_utils = self._old_io_utils
        self.core_mod.BASE_DIR = self._old_base_dir
        self.core_mod.WORKSPACE_DIR = self._old_workspace_dir
        self.core_mod.MISSION_PATH = self._old_mission_path
        super().tearDown()

    def _post_set_mission(self, rec_id, payload):
        return self.client.post(
            f"/api/recommendations/{rec_id}/set-mission",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def _build_gated_rec(self):
        return self.storage.add({
            "mission_title": "Gated build mission",
            "mission_description": "Build from the approved implementation plan.",
            "classification": "COMPLETION",
            "mission_type": "build_only",
            "execution_profile": "build_only",
            "requires_user_build_approval": True,
            "build_approval_status": "pending",
        })

    def test_gated_build_requires_explicit_action(self):
        rec_id = self._build_gated_rec()

        resp = self._post_set_mission(rec_id, {"cycle_budget": 1, "execution_profile": "build_only"})

        self.assertEqual(resp.status_code, 400)
        self.assertIn("explicit approval", resp.get_json()["error"])
        self.assertEqual(self.storage.get_by_id(rec_id)["status"], "open")

    def test_review_action_requires_notes(self):
        rec_id = self._build_gated_rec()

        resp = self._post_set_mission(rec_id, {
            "cycle_budget": 1,
            "execution_profile": "build_only",
            "build_approval_action": "review",
            "build_review_notes": "   ",
        })

        self.assertEqual(resp.status_code, 400)
        self.assertIn("requires user instructions", resp.get_json()["error"])
        self.assertEqual(self.storage.get_by_id(rec_id)["status"], "open")

    def test_review_action_launches_plan_only_with_user_notes(self):
        rec_id = self._build_gated_rec()

        resp = self._post_set_mission(rec_id, {
            "cycle_budget": 1,
            "execution_profile": "build_only",
            "build_approval_action": "review",
            "build_review_notes": "Add a rollback section before implementation.",
        })

        self.assertEqual(resp.status_code, 200)
        mission = json.loads(Path(self.core_mod.MISSION_PATH).read_text())
        self.assertEqual(mission["mission_type"], "plan_only")
        self.assertIn("Add a rollback section before implementation.", mission["problem_statement"])
        stored = self.storage.get_by_id(rec_id)
        self.assertEqual(stored["status"], "queued")
        self.assertEqual(stored["build_approval_status"], "review_requested")


class TestNarrativeMissionValidation(_RecommendationsApiTestBase):

    def setUp(self):
        super().setUp()
        from dashboard_modules import core as core_mod
        self.core_mod = core_mod
        self._old_io_utils = core_mod.io_utils
        self._old_narrative_path = core_mod.NARRATIVE_MISSION_PATH
        core_mod.io_utils = _JsonIO
        core_mod.NARRATIVE_MISSION_PATH = Path(self._tmpdir.name) / "narrative_mission.json"

    def tearDown(self):
        self.core_mod.io_utils = self._old_io_utils
        self.core_mod.NARRATIVE_MISSION_PATH = self._old_narrative_path
        super().tearDown()

    def _post_narrative_mission(self, payload):
        return self.client.post(
            "/api/narrative-autonomous/mission",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_story_genre_must_be_string_when_present(self):
        resp = self._post_narrative_mission({
            "story_number": 1,
            "story_title": "Valid Story",
            "story_genre": ["sci-fi"],
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("story_genre must be a string", resp.get_json()["message"])

    def test_story_logline_must_be_string_when_present(self):
        resp = self._post_narrative_mission({
            "story_number": 1,
            "story_title": "Valid Story",
            "story_logline": {"logline": "bad"},
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("story_logline must be a string", resp.get_json()["message"])


if __name__ == "__main__":
    unittest.main()
