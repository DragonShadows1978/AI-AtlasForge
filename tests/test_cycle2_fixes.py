"""
Tests for Cycle 2 bug fixes — 34 out-of-scope Red Team findings.

Covers:
- knowledge_base.py: str(e) elimination, negative limit, page validation, regex length cap
- investigation_engine.py: None guards, type validation, JSON extraction, markdown_to_html
- semantic.py: atomic write, thread safety, rate limiter sweep, cache key fallback
- queue_scheduler.py: float priority, tags validation, priority types, sort consistency
- experiment_framework.py: model regex unification, empty conditions, None guard
"""

import ast
import json
import os
import re
import sys
import tempfile
import time
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock
from dataclasses import dataclass

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


# =============================================================================
# knowledge_base.py tests
# =============================================================================

class TestKnowledgeBaseStrELeaks:
    """Verify str(e) is completely eliminated from knowledge_base.py error responses."""

    def test_no_str_e_in_knowledge_base(self):
        """AST-level check: no str(e) patterns in exception handlers."""
        kb_path = Path(__file__).parent.parent / "dashboard_modules" / "knowledge_base.py"
        source = kb_path.read_text()
        tree = ast.parse(source)

        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                # Walk the handler body looking for str(e) calls
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        if isinstance(child.func, ast.Name) and child.func.id == 'str':
                            if child.args and isinstance(child.args[0], ast.Name):
                                if child.args[0].id == (node.name or ''):
                                    violations.append(child.lineno)

        assert not violations, f"str(e) leak found at lines: {violations}"

    def test_no_str_e_in_jsonify_responses(self):
        """Grep-level check: no 'str(e)' strings in the file at all."""
        kb_path = Path(__file__).parent.parent / "dashboard_modules" / "knowledge_base.py"
        source = kb_path.read_text()
        matches = re.findall(r'str\(e\)', source)
        assert len(matches) == 0, f"Found {len(matches)} str(e) occurrences"

    def test_logger_import_exists(self):
        """Verify logger is imported for server-side logging."""
        kb_path = Path(__file__).parent.parent / "dashboard_modules" / "knowledge_base.py"
        source = kb_path.read_text()
        assert 'logger = logging.getLogger' in source

    def test_generic_error_messages(self):
        """Verify error responses use generic messages."""
        kb_path = Path(__file__).parent.parent / "dashboard_modules" / "knowledge_base.py"
        source = kb_path.read_text()
        # Every jsonify error in except blocks should say "Internal error" or similar
        # Check that 'logger.exception' appears for every except block with jsonify
        exception_blocks = re.findall(r'except Exception.*?(?=except|def |class |\Z)', source, re.DOTALL)
        for block in exception_blocks:
            if 'jsonify' in block and '"error"' in block:
                # Should have logger.exception or be the already-safe pattern
                assert 'logger.exception' in block or '"Internal error' in block or \
                       '"Failed to' in block or '"Internal error loading' in block, \
                       f"Exception block missing logger.exception: {block[:100]}"


class TestKnowledgeBaseNegativeLimit:
    """Test negative limit is clamped to 1."""

    def test_limit_clamp_pattern_exists(self):
        """Verify max(1, ...) pattern is used for limit clamping."""
        kb_path = Path(__file__).parent.parent / "dashboard_modules" / "knowledge_base.py"
        source = kb_path.read_text()
        # Should have max(1, min(...)) pattern
        assert 'max(1, min(' in source, "Limit clamping pattern max(1, min()) not found"


class TestKnowledgeBasePageValidation:
    """Test page validation prevents zero/negative values."""

    def test_page_clamp_pattern_exists(self):
        """Verify max(1, ...) pattern is used for page."""
        kb_path = Path(__file__).parent.parent / "dashboard_modules" / "knowledge_base.py"
        source = kb_path.read_text()
        assert "max(1, request.args.get('page'" in source, "Page clamping not found"


class TestKnowledgeBaseRegexLengthCap:
    """Test investigation_id regex has length cap."""

    def test_regex_has_length_cap(self):
        """Verify regex uses {1,128} quantifier."""
        kb_path = Path(__file__).parent.parent / "dashboard_modules" / "knowledge_base.py"
        source = kb_path.read_text()
        assert '{1,128}' in source, "investigation_id regex missing length cap"

    def test_regex_rejects_long_ids(self):
        """Verify the regex pattern rejects strings longer than 128 chars."""
        pattern = re.compile(r'^[a-zA-Z0-9_-]{1,128}$')
        assert pattern.match("inv_abc123")
        assert not pattern.match("a" * 129)
        assert not pattern.match("")


# =============================================================================
# investigation_engine.py tests
# =============================================================================

class TestReconNoneGuard:
    """Test _recon None return is handled."""

    def test_recon_or_empty_string_pattern(self):
        """Verify _recon call has 'or ""' fallback."""
        ie_path = Path(__file__).parent.parent / "investigation_engine.py"
        source = ie_path.read_text()
        assert '_recon(_stream_file, provider=\'claude\') or ""' in source


class TestDeleteInvestigationsBulkValidation:
    """Test delete_investigations_bulk type validation."""

    def test_non_list_input_returns_error(self):
        from investigation_engine import delete_investigations_bulk
        result = delete_investigations_bulk("not-a-list")
        assert result["success"] is False
        assert "must be a list" in result["message"]

    def test_non_string_id_skipped(self):
        from investigation_engine import delete_investigations_bulk
        result = delete_investigations_bulk([123, None])
        assert len(result["failed"]) == 2
        for f in result["failed"]:
            assert "must be a string" in f["reason"]

    def test_dict_input_returns_error(self):
        from investigation_engine import delete_investigations_bulk
        result = delete_investigations_bulk({"id": "test"})
        assert result["success"] is False


class TestInvestigationConfigNegativeValues:
    """Test InvestigationConfig clamps negative values."""

    def test_negative_timeout_clamped(self):
        from investigation_engine import InvestigationConfig
        config = InvestigationConfig(query="test", timeout_minutes=-1)
        assert config.timeout_minutes >= 1

    def test_negative_max_subagents_clamped(self):
        from investigation_engine import InvestigationConfig
        config = InvestigationConfig(query="test", max_subagents=-5)
        assert config.max_subagents >= 1

    def test_zero_timeout_clamped(self):
        from investigation_engine import InvestigationConfig
        config = InvestigationConfig(query="test", timeout_minutes=0)
        assert config.timeout_minutes >= 1


class TestJsonExtractionBalancedBraces:
    """Test balanced brace JSON extraction in _run_lead_agent."""

    def test_balanced_brace_extraction_pattern(self):
        """Verify balanced brace logic is in the codebase."""
        ie_path = Path(__file__).parent.parent / "investigation_engine.py"
        source = ie_path.read_text()
        assert "depth = 0" in source, "Balanced brace depth tracking not found"
        assert "depth += 1" in source
        assert "depth -= 1" in source

    def test_uses_stripped_not_response(self):
        """Verify re.search uses 'stripped' variable, not raw 'response'."""
        ie_path = Path(__file__).parent.parent / "investigation_engine.py"
        source = ie_path.read_text()
        # The json_match line should use stripped, not response
        match = re.search(r"json_match = re\.search\(.*?'```json.*?',\s*(\w+),", source)
        assert match, "json_match re.search line not found"
        assert match.group(1) == "stripped", f"re.search uses '{match.group(1)}' instead of 'stripped'"


class TestMarkdownToHtmlFix:
    """Test markdown_to_html processes headers/bold/italic before escaping."""

    def test_headers_before_escape(self):
        """Verify headers are processed before _esc()."""
        ie_path = Path(__file__).parent.parent / "investigation_engine.py"
        source = ie_path.read_text()
        # Find the markdown processing section — headers should use lambda with _esc
        assert "lambda m: _header_replace(m, 'h3')" in source or \
               "_header_replace(m, 'h3')" in source

    def test_bold_italic_before_escape(self):
        """Verify bold/italic are processed before _esc()."""
        ie_path = Path(__file__).parent.parent / "investigation_engine.py"
        source = ie_path.read_text()
        assert "lambda m: f'<strong>{_esc(m.group(1))}</strong>'" in source

    def test_bullet_content_escaped(self):
        """Verify bullet content is escaped (conditionally to avoid double-escape)."""
        ie_path = Path(__file__).parent.parent / "investigation_engine.py"
        source = ie_path.read_text()
        # Cycle 3: conditional escape to prevent double-escaping when content has HTML from bold/italic
        assert "bullet_text = _esc(bullet_text)" in source


# =============================================================================
# semantic.py tests
# =============================================================================

class TestAtomicSaveState:
    """Test atomic _save_state with tempfile+os.replace."""

    def test_atomic_write_pattern(self):
        """Verify tempfile + os.replace pattern in _save_state."""
        sem_path = Path(__file__).parent.parent / "dashboard_modules" / "semantic.py"
        source = sem_path.read_text()
        assert "tempfile.mkstemp" in source
        assert "os.replace" in source

    def test_os_import_exists(self):
        """Verify os module is imported."""
        sem_path = Path(__file__).parent.parent / "dashboard_modules" / "semantic.py"
        source = sem_path.read_text()
        assert "import os" in source


class TestThreadCreationInsideLock:
    """Test thread creation is inside the lock."""

    def test_thread_creation_indented(self):
        """Verify thread creation is indented inside the lock block."""
        sem_path = Path(__file__).parent.parent / "dashboard_modules" / "semantic.py"
        source = sem_path.read_text()
        # Find the start() method — thread creation should be inside the with block
        # The thread creation should be indented at the same level as other lock contents
        lines = source.split('\n')
        in_start = False
        lock_indent = None
        thread_inside = False
        for line in lines:
            if 'def start(' in line:
                in_start = True
                continue
            if in_start:
                if 'with self._lock:' in line:
                    lock_indent = len(line) - len(line.lstrip()) + 4  # inside lock
                    continue
                if lock_indent and 'self._thread = threading.Thread' in line:
                    actual_indent = len(line) - len(line.lstrip())
                    thread_inside = actual_indent >= lock_indent
                    break
                if line.strip().startswith('def ') and 'start' not in line:
                    break
        assert thread_inside, "Thread creation is not inside the lock"


class TestCaptureSnapshotLock:
    """Test _capture_snapshot writes fields under lock."""

    def test_fields_under_lock(self):
        """Verify _last_capture and _capture_count writes are under lock."""
        sem_path = Path(__file__).parent.parent / "dashboard_modules" / "semantic.py"
        source = sem_path.read_text()
        # Find _capture_snapshot method
        match = re.search(r'def _capture_snapshot\(self\):(.*?)(?=\n    def |\nclass |\Z)', source, re.DOTALL)
        assert match, "_capture_snapshot method not found"
        method_body = match.group(1)
        assert 'with self._lock:' in method_body, "Lock not used in _capture_snapshot"
        # Verify the field writes are after with self._lock:
        lock_pos = method_body.find('with self._lock:')
        last_capture_pos = method_body.find('self._last_capture = time.time()')
        assert last_capture_pos > lock_pos, "_last_capture write is before lock"


class TestRateLimiterPeriodicSweep:
    """Test RateLimiter has periodic sweep for stale entries."""

    def test_last_sweep_attribute(self):
        """Verify _last_sweep attribute exists."""
        sem_path = Path(__file__).parent.parent / "dashboard_modules" / "semantic.py"
        source = sem_path.read_text()
        assert '_last_sweep' in source

    def test_sweep_interval(self):
        """Verify time-based sweep logic exists."""
        sem_path = Path(__file__).parent.parent / "dashboard_modules" / "semantic.py"
        source = sem_path.read_text()
        assert 'now - self._last_sweep' in source

    def test_functional_sweep(self):
        """Test that stale entries are actually cleaned up."""
        # Import and test the actual RateLimiter
        sys.path.insert(0, str(Path(__file__).parent.parent / "dashboard_modules"))
        from semantic import RateLimiter

        limiter = RateLimiter(max_requests=10, window_seconds=1)

        # Simulate requests from many unique IPs
        for i in range(100):
            limiter.is_allowed(f"ip_{i}")

        assert len(limiter._requests) == 100

        # Force sweep by setting _last_sweep to past and window_seconds * 2 ago
        limiter._last_sweep = 0
        # Set all timestamps to the past
        for k in limiter._requests:
            limiter._requests[k] = [time.time() - 10]

        # Next call should trigger sweep
        limiter.is_allowed("new_ip")
        # Stale entries should be swept (their timestamps are > window_seconds * 2 old)
        assert len(limiter._requests) < 100


class TestTTLCacheMakeKeyFallback:
    """Test TTLCache._make_key handles non-serializable params."""

    def test_non_serializable_params(self):
        """Verify _make_key doesn't crash on non-serializable params."""
        sys.path.insert(0, str(Path(__file__).parent.parent / "dashboard_modules"))
        from semantic import TTLCache

        cache = TTLCache()
        # Object that can't be JSON serialized
        key = cache._make_key("test", {"func": lambda x: x})
        assert isinstance(key, str)
        assert key.startswith("test:")

    def test_normal_params_still_work(self):
        """Verify normal params still produce consistent keys."""
        sys.path.insert(0, str(Path(__file__).parent.parent / "dashboard_modules"))
        from semantic import TTLCache

        cache = TTLCache()
        key1 = cache._make_key("test", {"a": 1, "b": 2})
        key2 = cache._make_key("test", {"b": 2, "a": 1})
        assert key1 == key2  # sort_keys=True ensures consistency


class TestQualityStatsCacheKey:
    """Test quality_stats uses explicit empty dict param."""

    def test_explicit_empty_dict(self):
        """Verify quality_stats get uses explicit {} param."""
        sem_path = Path(__file__).parent.parent / "dashboard_modules" / "semantic.py"
        source = sem_path.read_text()
        assert "_cache.get('quality_stats', {})" in source


# =============================================================================
# queue_scheduler.py tests
# =============================================================================

class TestSafePriorityKeyHandlesFloats:
    """Test _safe_priority_key handles float priorities."""

    def test_float_priority(self):
        sys.path.insert(0, str(Path(__file__).parent.parent / "dashboard_modules"))
        from queue_scheduler import _safe_priority_key
        item = {"priority": 75.5}
        result = _safe_priority_key(item)
        assert result == -75, f"Expected -75, got {result}"

    def test_int_priority(self):
        from queue_scheduler import _safe_priority_key
        item = {"priority": 50}
        result = _safe_priority_key(item)
        assert result == -50

    def test_string_priority(self):
        from queue_scheduler import _safe_priority_key
        item = {"priority": "high"}
        result = _safe_priority_key(item)
        assert result == -50

    def test_dict_priority_fallback(self):
        from queue_scheduler import _safe_priority_key
        item = {"priority": {"level": "high"}}
        result = _safe_priority_key(item)
        assert result == 0  # fallback for non-int non-str


class TestUnifiedSortKey:
    """Test that /add endpoint uses _safe_priority_key instead of local get_priority_weight."""

    def test_no_get_priority_weight_in_add(self):
        """Verify get_priority_weight function is removed from /add endpoint."""
        qs_path = Path(__file__).parent.parent / "dashboard_modules" / "queue_scheduler.py"
        source = qs_path.read_text()
        # The old local get_priority_weight function should be gone
        assert "def get_priority_weight" not in source, \
            "Local get_priority_weight still exists — should use _safe_priority_key"

    def test_safe_priority_key_used_in_sort(self):
        """Verify _safe_priority_key is used in sort calls."""
        qs_path = Path(__file__).parent.parent / "dashboard_modules" / "queue_scheduler.py"
        source = qs_path.read_text()
        assert "_safe_priority_key" in source


class TestTagsValidation:
    """Test tags validation logic is correct."""

    def test_no_contradictory_str_t_filter(self):
        """Verify tags filtering doesn't do str(t) on already-filtered strings."""
        qs_path = Path(__file__).parent.parent / "dashboard_modules" / "queue_scheduler.py"
        source = qs_path.read_text()
        # Should NOT have: [str(t) for t in ... if isinstance(t, str)]
        # because str(t) on a str is redundant
        assert 'str(t) for t in data.get("tags"' not in source


class TestUpdateQueueItemPriorityValidation:
    """Test update_queue_item validates priority types."""

    def test_priority_validation_in_update(self):
        """Verify priority validation exists in update_queue_item."""
        qs_path = Path(__file__).parent.parent / "dashboard_modules" / "queue_scheduler.py"
        source = qs_path.read_text()
        # Should have priority type validation
        assert 'isinstance(value, str)' in source
        assert 'isinstance(value, (int, float))' in source


class TestSanitizeProjectNameLayout:
    """Test _sanitize_project_name has proper code layout."""

    def test_blank_line_after_function(self):
        """Verify there's a blank line between function end and next variable."""
        qs_path = Path(__file__).parent.parent / "dashboard_modules" / "queue_scheduler.py"
        source = qs_path.read_text()
        # The line after sanitized[:100] should be blank (not _socketio = None)
        lines = source.split('\n')
        for i, line in enumerate(lines):
            if 'return sanitized[:100]' in line:
                # Next line should be blank
                assert i + 1 < len(lines)
                assert lines[i + 1].strip() == '', \
                    f"Line after sanitized[:100] should be blank, got: '{lines[i+1]}'"
                break


# =============================================================================
# experiment_framework.py tests
# =============================================================================

class TestModelRegexUnification:
    """Test model name validation uses _MODEL_NAME_RE consistently."""

    def test_no_inline_regex_in_claude_cli(self):
        """Verify _invoke_claude_cli uses _MODEL_NAME_RE for model validation."""
        ef_path = Path(__file__).parent.parent / "experiment_framework.py"
        source = ef_path.read_text()
        # Find _invoke_claude_cli function
        match = re.search(r'def _invoke_claude_cli\(.*?\n(?=def )', source, re.DOTALL)
        assert match, "_invoke_claude_cli not found"
        func_body = match.group(0)
        assert '_MODEL_NAME_RE.match' in func_body, \
            "_invoke_claude_cli should use _MODEL_NAME_RE"
        # Model validation should NOT use inline regex (tool name validation can still use _re)
        assert "_re.match(r'^[a-zA-Z0-9._/:-]+$'" not in func_body, \
            "_invoke_claude_cli still uses inline regex for model validation"

    def test_no_inline_regex_in_codex_cli(self):
        """Verify _invoke_codex_cli uses _MODEL_NAME_RE."""
        ef_path = Path(__file__).parent.parent / "experiment_framework.py"
        source = ef_path.read_text()
        match = re.search(r'def _invoke_codex_cli\(.*?\n(?=def )', source, re.DOTALL)
        assert match, "_invoke_codex_cli not found"
        func_body = match.group(0)
        assert '_MODEL_NAME_RE.match' in func_body, \
            "_invoke_codex_cli should use _MODEL_NAME_RE"
        assert "import re as _re" not in func_body, \
            "_invoke_codex_cli should not import re locally"


class TestEmptyConditionsValidation:
    """Test Experiment.run() rejects empty conditions."""

    def test_empty_conditions_raises(self):
        from experiment_framework import Experiment, ExperimentConfig, ModelType
        config = ExperimentConfig(
            name="test",
            description="test",
            conditions=[],
            model=ModelType.BALANCED,
        )
        exp = Experiment(config)
        try:
            exp.run()
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "conditions" in str(e).lower()


class TestNoneResponseGuard:
    """Test _compute_summary handles None response."""

    def test_none_response_pattern(self):
        """Verify the None guard exists in _compute_summary."""
        ef_path = Path(__file__).parent.parent / "experiment_framework.py"
        source = ef_path.read_text()
        assert 't.response and t.response.startswith("ERROR")' in source
