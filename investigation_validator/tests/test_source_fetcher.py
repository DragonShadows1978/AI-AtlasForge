"""Regression tests for pre-existing defects in source_fetcher.py.

Covers five defects:
  1. _apply_rate_limit used a request counter instead of elapsed time,
     so every call after the first slept unconditionally.
  2. fetch_sources' thread-pool collector used a bare ``except Exception``,
     silently masking programming bugs as inaccessible FetchedSource.
  3. _fetch_url wrapped its whole body in a bare ``except Exception``,
     which (a) masked bugs and (b) swallowed UnsafeUrlError that
     callers must be able to distinguish.
  4. _try_wayback re-entered _fetch_via_proxy on retry, so when the
     proxy was down each fetch burned WEB_PROXY_TIMEOUT twice.
  5. _is_valid_url did not reject whitespace, angle brackets, or
     option-like prefixes (e.g. ``-oOUTPUT``).

These are **functional** tests: they exercise real _apply_rate_limit
timing, real _is_valid_url parsing, and real exception propagation.
Only the external network boundary is patched.
"""

from __future__ import annotations

import os
import sys
import time
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

# Make the validator package importable whether invoked as a package test
# (python -m unittest investigation_validator.tests.test_source_fetcher) or
# directly (python tests/test_source_fetcher.py).
_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG = os.path.dirname(_HERE)
_PARENT = os.path.dirname(_PKG)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

try:
    from investigation_validator.source_fetcher import (
        SourceFetcher,
        UnsafeUrlError,
        _NETWORK_EXCEPTIONS,
    )
    from investigation_validator.models import ValidationConfig, FetchedSource
except ImportError:
    # Direct-invocation fallback.
    if _PKG not in sys.path:
        sys.path.insert(0, _PKG)
    from source_fetcher import (  # type: ignore
        SourceFetcher,
        UnsafeUrlError,
        _NETWORK_EXCEPTIONS,
    )
    from models import ValidationConfig, FetchedSource  # type: ignore


def _make_fetcher(tmp_cache_dir: str) -> SourceFetcher:
    cfg = ValidationConfig(cache_dir=tmp_cache_dir)
    return SourceFetcher(cfg)


class TestRateLimit(unittest.TestCase):
    """Defect 1: _apply_rate_limit should sleep based on elapsed time."""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp(prefix="sf_ratelimit_")
        self.fetcher = _make_fetcher(self.tmpdir)
        # Shrink the delay so the test finishes quickly but remains observable.
        self.fetcher._rate_limit_delay = 0.1

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_first_call_does_not_sleep(self):
        """First call ever should return immediately — no prior slot to wait for."""
        start = time.monotonic()
        self.fetcher._apply_rate_limit()
        elapsed = time.monotonic() - start
        self.assertLess(
            elapsed, 0.05,
            f"First rate-limit call slept {elapsed:.3f}s; expected ~0",
        )

    def test_idle_call_does_not_sleep(self):
        """A call that arrives after the delay has already passed must not sleep."""
        self.fetcher._apply_rate_limit()
        # Wait longer than the delay so the slot is definitely stale.
        time.sleep(self.fetcher._rate_limit_delay * 2)
        start = time.monotonic()
        self.fetcher._apply_rate_limit()
        elapsed = time.monotonic() - start
        self.assertLess(
            elapsed, 0.05,
            f"Idle-arrival call slept {elapsed:.3f}s; expected ~0 "
            f"(defect: old impl slept 0.5s on every call after the first)",
        )

    def test_burst_calls_total_delay_is_bounded(self):
        """Three rapid calls should take approximately 2 * delay seconds total.

        Calls: 0, +delay, +2*delay. The first is free; the other two each pay
        one delay. Total wall-clock ≈ 2 * delay.
        """
        delay = self.fetcher._rate_limit_delay
        start = time.monotonic()
        self.fetcher._apply_rate_limit()
        self.fetcher._apply_rate_limit()
        self.fetcher._apply_rate_limit()
        elapsed = time.monotonic() - start
        # Lower bound: can't be less than 2*delay - scheduling slack.
        self.assertGreaterEqual(
            elapsed, 2 * delay - 0.03,
            f"Burst of 3 took {elapsed:.3f}s; expected ~{2 * delay:.3f}s",
        )
        # Upper bound: ensures we're not sleeping unconditionally (old bug
        # would give ~3*delay here).
        self.assertLess(
            elapsed, 2 * delay + 0.15,
            f"Burst of 3 took {elapsed:.3f}s; expected ~{2 * delay:.3f}s "
            f"(defect: old impl would have slept ~{3 * delay:.3f}s)",
        )


class TestFetchSourcesErrorPropagation(unittest.TestCase):
    """Defect 2: fetch_sources must NOT swallow programming bugs."""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp(prefix="sf_fs_err_")
        self.fetcher = _make_fetcher(self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_programming_bug_in_fetch_single_propagates(self):
        """An AttributeError inside fetch_single must surface, not be buried
        in a FetchedSource(accessible=False)."""
        url = "http://example.com/buggy"

        def buggy_fetch_single(u: str):
            raise AttributeError("deliberate test bug - this is a real programming error")

        with mock.patch.object(self.fetcher, "fetch_single", side_effect=buggy_fetch_single):
            with self.assertRaises(AttributeError):
                self.fetcher.fetch_sources([url])

    def test_known_network_error_still_wrapped(self):
        """OSError (network-layer) should still be caught and wrapped into
        an inaccessible FetchedSource — fetch_sources is a batch API, so
        one bad URL must not break the whole batch."""
        url = "http://example.com/netfail"

        def failing_fetch(u: str):
            raise OSError("simulated DNS failure")

        with mock.patch.object(self.fetcher, "fetch_single", side_effect=failing_fetch):
            results = self.fetcher.fetch_sources([url])
        self.assertIn(url, results)
        self.assertFalse(results[url].accessible)
        self.assertIn("simulated DNS failure", results[url].error or "")


class TestFetchUrlErrorPropagation(unittest.TestCase):
    """Defect 3: _fetch_url must let UnsafeUrlError propagate."""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp(prefix="sf_fu_err_")
        self.fetcher = _make_fetcher(self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_unsafe_url_error_propagates(self):
        """If the underlying fetch raises UnsafeUrlError, _fetch_url must
        re-raise it — callers need to distinguish safety rejection from
        a transient network failure."""
        # Force the proxy path off so the fetch_with_* ladder runs.
        with mock.patch.object(self.fetcher, "_fetch_via_proxy", return_value=None):
            with mock.patch.object(
                self.fetcher, "_fetch_with_httpx",
                side_effect=UnsafeUrlError("dangerous url"),
            ):
                with mock.patch.object(
                    self.fetcher, "_fetch_with_requests",
                    side_effect=UnsafeUrlError("dangerous url"),
                ):
                    with mock.patch.object(
                        self.fetcher, "_fetch_with_curl",
                        side_effect=UnsafeUrlError("dangerous url"),
                    ):
                        with self.assertRaises(UnsafeUrlError):
                            self.fetcher._fetch_url("http://example.com/whatever")

    def test_programming_error_propagates(self):
        """Programming bugs (e.g. TypeError) must not be buried as inaccessible."""
        with mock.patch.object(self.fetcher, "_fetch_via_proxy", return_value=None):
            with mock.patch.object(
                self.fetcher, "_fetch_with_httpx",
                side_effect=TypeError("bad arg"),
            ):
                with mock.patch.object(
                    self.fetcher, "_fetch_with_requests",
                    side_effect=TypeError("bad arg"),
                ):
                    with mock.patch.object(
                        self.fetcher, "_fetch_with_curl",
                        side_effect=TypeError("bad arg"),
                    ):
                        with self.assertRaises(TypeError):
                            self.fetcher._fetch_url("http://example.com/whatever")

    def test_network_error_still_wrapped(self):
        """Real network errors should still produce accessible=False results."""
        with mock.patch.object(self.fetcher, "_fetch_via_proxy", return_value=None):
            fake_err = OSError("connection refused")
            with mock.patch.object(
                self.fetcher, "_fetch_with_httpx", side_effect=fake_err,
            ):
                with mock.patch.object(
                    self.fetcher, "_fetch_with_requests", side_effect=fake_err,
                ):
                    with mock.patch.object(
                        self.fetcher, "_fetch_with_curl", side_effect=fake_err,
                    ):
                        result = self.fetcher._fetch_url("http://example.com/ok")
        self.assertFalse(result.accessible)
        self.assertIn("connection refused", result.error or "")


class TestProxyFetchContract(unittest.TestCase):
    """Regression: proxy fetch must request content, not max_chars=0 empty text."""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp(prefix="sf_proxy_contract_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_fetch_via_proxy_uses_validator_source_limit(self):
        from investigation_validator import source_fetcher as source_fetcher_mod

        fetcher = SourceFetcher(
            ValidationConfig(cache_dir=self.tmpdir, max_source_chars=12345)
        )

        class FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "text": "proxy text",
                    "content_type": "text/html",
                }

        with mock.patch.object(source_fetcher_mod, "HAS_REQUESTS", True):
            with mock.patch.object(
                source_fetcher_mod.requests,
                "post",
                return_value=FakeResponse(),
            ) as post:
                result = fetcher._fetch_via_proxy("https://example.com/source")

        self.assertIsNotNone(result)
        self.assertEqual(result["content"], "proxy text")
        self.assertEqual(
            post.call_args.kwargs["json"],
            {"url": "https://example.com/source", "max_chars": 12345},
        )


class TestWaybackRetryBypassesProxy(unittest.TestCase):
    """Defect 4: _try_wayback must not re-enter _fetch_via_proxy on retry."""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp(prefix="sf_wb_")
        self.fetcher = _make_fetcher(self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_wayback_retry_skips_proxy(self):
        """When _try_wayback falls back to Wayback, the retry fetch must
        NOT hit _fetch_via_proxy again — otherwise a dead proxy costs
        two WEB_PROXY_TIMEOUT waits per URL."""

        proxy_call_count = {"n": 0}

        def count_proxy_calls(u: str):
            proxy_call_count["n"] += 1
            return None  # simulate proxy down

        # Make the direct-fetch ladder return a plausible payload so
        # the wayback retry succeeds.
        def direct_fetch_ok(u: str):
            return {"content": "snapshot body", "content_type": "text/html"}

        # Fake wayback API response via _try_wayback's httpx/requests path.
        class _FakeResp:
            def json(self):
                return {
                    "archived_snapshots": {
                        "closest": {"available": True, "url": "http://web.archive.org/snap"}
                    }
                }

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, *a, **kw):
                return _FakeResp()

        # Patch httpx.Client (used by _try_wayback when HAS_HTTPX) to return
        # our fake wayback payload. If httpx isn't available, the code uses
        # requests.get — patch both to cover either branch.
        patches = []
        try:
            import httpx  # type: ignore

            patches.append(mock.patch.object(httpx, "Client", _FakeClient))
        except ImportError:
            pass
        try:
            import requests  # type: ignore

            patches.append(mock.patch.object(requests, "get", return_value=_FakeResp()))
        except ImportError:
            pass

        with mock.patch.object(
            self.fetcher, "_fetch_via_proxy", side_effect=count_proxy_calls,
        ):
            # Direct fetch succeeds on the wayback URL (retry path).
            patched_httpx = mock.patch.object(
                self.fetcher, "_fetch_with_httpx", side_effect=direct_fetch_ok,
            )
            patched_requests = mock.patch.object(
                self.fetcher, "_fetch_with_requests", side_effect=direct_fetch_ok,
            )
            patched_curl = mock.patch.object(
                self.fetcher, "_fetch_with_curl", side_effect=direct_fetch_ok,
            )
            with patched_httpx, patched_requests, patched_curl:
                for p in patches:
                    p.start()
                try:
                    # Call _try_wayback directly on a URL whose primary fetch
                    # already conceptually failed.
                    result = self.fetcher._try_wayback("http://example.com/gone")
                finally:
                    for p in patches:
                        try:
                            p.stop()
                        except RuntimeError:
                            pass

        # The retry fetch must not have called _fetch_via_proxy.
        self.assertEqual(
            proxy_call_count["n"], 0,
            f"_try_wayback retry re-entered _fetch_via_proxy "
            f"{proxy_call_count['n']} time(s); expected 0 (skip_proxy=True)",
        )
        # And it should have produced an accessible result from the direct path.
        self.assertIsNotNone(result)
        self.assertTrue(result.accessible)


class TestIsValidUrlHardening(unittest.TestCase):
    """Defect 5: _is_valid_url must reject whitespace, angle brackets,
    and option-like prefixes."""

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp(prefix="sf_url_")
        self.fetcher = _make_fetcher(self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_rejects_whitespace(self):
        cases = [
            "http://example.com/ path",            # literal space
            "http://example.com/\tpath",           # tab
            "http://example.com/pa\x0bth",         # vertical tab
            "http://exa mple.com/",                # space inside netloc
            "http://example.com/\u00a0x",          # non-breaking space
        ]
        for bad in cases:
            with self.subTest(url=bad):
                self.assertFalse(
                    self.fetcher._is_valid_url(bad),
                    f"Expected _is_valid_url({bad!r}) to be False",
                )

    def test_rejects_angle_brackets(self):
        for bad in ["http://example.com/<script>", "http://example.com/>redir"]:
            with self.subTest(url=bad):
                self.assertFalse(self.fetcher._is_valid_url(bad))

    def test_rejects_option_like_prefix(self):
        for bad in ["-oOUTPUT", "--data=@/etc/passwd", "-fsSL"]:
            with self.subTest(url=bad):
                self.assertFalse(self.fetcher._is_valid_url(bad))

    def test_rejects_encoded_angle_brackets(self):
        # URL-encoded forms should be rejected too (bypass check).
        self.assertFalse(self.fetcher._is_valid_url("http://example.com/%3Cscript%3E"))

    def test_rejects_encoded_whitespace(self):
        """The raw-whitespace guard must also reject percent-encoded whitespace —
        otherwise %0A / %20 / %09 let a newline/space/tab slip past and then
        reappear after unquote() in downstream consumers."""
        for bad in [
            "http://example.com/x%0Ay",  # encoded LF
            "http://example.com/x%0Dy",  # encoded CR
            "http://example.com/x%09y",  # encoded tab
            "http://example.com/x%20y",  # encoded space
        ]:
            with self.subTest(url=bad):
                self.assertFalse(
                    self.fetcher._is_valid_url(bad),
                    f"Expected _is_valid_url({bad!r}) to be False "
                    f"(encoded whitespace bypass)",
                )

    def test_accepts_benign_url(self):
        """Regression guard: real URLs still pass after the hardening changes.

        Note: the pre-existing ``dangerous_chars`` list rejects ``&``, so we
        avoid query strings with multiple params in the positive cases. That
        rejection predates this mission and is out of scope.
        """
        self.assertTrue(self.fetcher._is_valid_url("https://example.com/some/path"))
        self.assertTrue(self.fetcher._is_valid_url("https://example.com/"))
        self.assertTrue(self.fetcher._is_valid_url("http://sub.example.com:8080/a/b/c"))
        self.assertTrue(self.fetcher._is_valid_url("https://example.com/?q=1"))


class TestCurlFailureIsNetworkException(unittest.TestCase):
    """Adversarial finding: _fetch_with_curl previously raised a bare
    Exception on non-zero rc, which escaped _fetch_url's narrowed
    _NETWORK_EXCEPTIONS catch and surfaced to callers as an untyped bug.

    After the fix, curl failures raise OSError — a member of
    _NETWORK_EXCEPTIONS — so _fetch_url wraps them into an inaccessible
    FetchedSource just like any other transport-level error.
    """

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp(prefix="sf_curl_")
        self.fetcher = _make_fetcher(self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_curl_timeout_raises_oserror(self):
        """subprocess.TimeoutExpired does not inherit from OSError, so if
        _fetch_with_curl did not catch and re-wrap it, a curl timeout
        would escape the narrowed _NETWORK_EXCEPTIONS catch in _fetch_url
        — regressing the defect-3 fix for timeout failures specifically."""
        import subprocess as _subp
        with mock.patch.object(
            _subp, "run",
            side_effect=_subp.TimeoutExpired(cmd=["curl"], timeout=5),
        ):
            with self.assertRaises(OSError) as cm:
                self.fetcher._fetch_with_curl("http://example.com/x")
        self.assertIn("timed out", str(cm.exception))
        from investigation_validator.source_fetcher import _NETWORK_EXCEPTIONS
        self.assertIsInstance(cm.exception, _NETWORK_EXCEPTIONS)

    def test_curl_nonzero_raises_oserror(self):
        """Simulate curl exiting non-zero; expect OSError, not bare Exception.

        OSError is a member of _NETWORK_EXCEPTIONS, so the overall
        _fetch_url flow wraps it into an inaccessible FetchedSource.
        """
        class _FakeCompletedProcess:
            returncode = 7
            stdout = ""
            stderr = "\x1b[31mboom\x1b[0m\ntraceback"  # control chars

        import subprocess as _subp
        with mock.patch.object(
            _subp, "run", return_value=_FakeCompletedProcess(),
        ):
            with self.assertRaises(OSError) as cm:
                self.fetcher._fetch_with_curl("http://example.com/x")

        # Control characters must be stripped from the message to prevent
        # log injection through a compromised curl stderr.
        self.assertNotIn("\x1b", str(cm.exception))
        self.assertNotIn("\n", str(cm.exception))
        # And because OSError ∈ _NETWORK_EXCEPTIONS, _fetch_url wraps it
        # instead of letting it escape as a bug.
        from investigation_validator.source_fetcher import _NETWORK_EXCEPTIONS
        self.assertIsInstance(cm.exception, _NETWORK_EXCEPTIONS)


class TestWaybackPropagatesUnsafeUrlError(unittest.TestCase):
    """Adversarial finding: _try_wayback previously used bare
    ``except Exception``, silently swallowing UnsafeUrlError raised
    by the retry _fetch_url call when the wayback snapshot URL was
    itself unsafe. After the fix, UnsafeUrlError propagates so the
    caller can distinguish 'snapshot is unsafe' from 'wayback down'.
    """

    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp(prefix="sf_wb2_")
        self.fetcher = _make_fetcher(self.tmpdir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_unsafe_wayback_url_propagates(self):
        """If the wayback-returned URL raises UnsafeUrlError inside the
        retry _fetch_url, _try_wayback must not bury it — it must
        propagate so fetch_single can surface a safety rejection."""

        class _FakeResp:
            def json(self):
                return {
                    "archived_snapshots": {
                        "closest": {"available": True, "url": "http://bad/x"}
                    }
                }

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get(self, *a, **kw):
                return _FakeResp()

        # Patch the external boundaries used by _try_wayback.
        patches = []
        try:
            import httpx  # type: ignore

            patches.append(mock.patch.object(httpx, "Client", _FakeClient))
        except ImportError:
            pass
        try:
            import requests  # type: ignore

            patches.append(mock.patch.object(requests, "get", return_value=_FakeResp()))
        except ImportError:
            pass

        # Make the retry _fetch_url raise UnsafeUrlError.
        with mock.patch.object(
            self.fetcher, "_fetch_url", side_effect=UnsafeUrlError("snapshot is unsafe"),
        ):
            for p in patches:
                p.start()
            try:
                with self.assertRaises(UnsafeUrlError):
                    self.fetcher._try_wayback("http://example.com/gone")
            finally:
                for p in patches:
                    try:
                        p.stop()
                    except RuntimeError:
                        pass


if __name__ == "__main__":
    unittest.main()
