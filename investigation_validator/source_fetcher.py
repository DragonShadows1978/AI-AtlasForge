"""
Source Fetcher - Fetch and cache source content for validation.

Handles URL fetching with caching, PDF extraction, HTML parsing.
"""

import os
import re
import json
import hashlib
import logging
import threading
import time
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, quote, unquote
import shlex

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

# Try pypdf (v3+) first, then PyPDF2 as fallback
try:
    from pypdf import PdfReader
    HAS_PDF = True
    PDF_LIBRARY = "pypdf"
except ImportError:
    try:
        from PyPDF2 import PdfReader
        HAS_PDF = True
        PDF_LIBRARY = "PyPDF2"
    except ImportError:
        HAS_PDF = False
        PDF_LIBRARY = None

try:
    from .models import FetchedSource, ValidationConfig
except ImportError:
    from models import FetchedSource, ValidationConfig

logger = logging.getLogger("source_fetcher")


class UnsafeUrlError(ValueError):
    """Raised when a URL fails safety validation before fetch.

    Inherits from ValueError so pre-existing ``except ValueError`` sites
    keep behaving the same, while new code can catch the narrower type
    to distinguish "URL-level safety rejection" from generic parse errors.
    """


# Network-layer exception tuple used by _fetch_url / fetch_sources to
# catch real I/O failures without swallowing programming bugs.
_NETWORK_EXCEPTIONS: tuple = (OSError,)
if HAS_HTTPX:
    _NETWORK_EXCEPTIONS = _NETWORK_EXCEPTIONS + (httpx.HTTPError,)
if HAS_REQUESTS:
    _NETWORK_EXCEPTIONS = _NETWORK_EXCEPTIONS + (requests.exceptions.RequestException,)

# Default cache directory lives with this validator package. The location can
# still be overridden by ValidationConfig.cache_dir or VALIDATION_CACHE_DIR.
CACHE_DIR = Path(__file__).resolve().parent / ".source_cache"

# AtlasForge web proxy — tried before httpx/requests/curl when reachable.
# The proxy handles Reddit JSON routing, 24h fetch cache, raw HTML, and
# survives Anthropic-filtered domains. Configurable via WEB_PROXY_URL.
_DEFAULT_WEB_PROXY_URL = "http://127.0.0.1:8765"
_raw_web_proxy_url = os.environ.get("WEB_PROXY_URL", _DEFAULT_WEB_PROXY_URL).rstrip("/")
try:
    _proxy_parsed = urlparse(_raw_web_proxy_url)
    if _proxy_parsed.scheme not in ("http", "https") or not _proxy_parsed.netloc:
        logger.warning(
            f"WEB_PROXY_URL has invalid scheme/netloc: {_raw_web_proxy_url!r}; "
            f"falling back to {_DEFAULT_WEB_PROXY_URL}"
        )
        WEB_PROXY_URL = _DEFAULT_WEB_PROXY_URL
    else:
        WEB_PROXY_URL = _raw_web_proxy_url
except (ValueError, TypeError) as _e:
    logger.warning(
        f"WEB_PROXY_URL parse failed ({_e}); falling back to {_DEFAULT_WEB_PROXY_URL}"
    )
    WEB_PROXY_URL = _DEFAULT_WEB_PROXY_URL

_DEFAULT_WEB_PROXY_TIMEOUT = 30.0
try:
    WEB_PROXY_TIMEOUT = float(os.environ.get("WEB_PROXY_TIMEOUT", str(_DEFAULT_WEB_PROXY_TIMEOUT)))
    if WEB_PROXY_TIMEOUT <= 0:
        logger.warning(
            f"WEB_PROXY_TIMEOUT must be positive, got {WEB_PROXY_TIMEOUT}; "
            f"falling back to {_DEFAULT_WEB_PROXY_TIMEOUT}"
        )
        WEB_PROXY_TIMEOUT = _DEFAULT_WEB_PROXY_TIMEOUT
except (ValueError, TypeError) as _e:
    logger.warning(
        f"WEB_PROXY_TIMEOUT parse failed ({_e}); falling back to {_DEFAULT_WEB_PROXY_TIMEOUT}"
    )
    WEB_PROXY_TIMEOUT = _DEFAULT_WEB_PROXY_TIMEOUT


class SourceFetcher:
    """
    Fetches and caches source content for validation.

    Features:
    - URL content fetching with timeout
    - HTML to text conversion
    - Response caching
    - Rate limiting
    - Wayback Machine fallback
    """

    def __init__(self, config: Optional[ValidationConfig] = None):
        self.config = config or ValidationConfig()

        if self.config.max_source_chars <= 0:
            raise ValueError(
                f"ValidationConfig.max_source_chars must be positive, "
                f"got {self.config.max_source_chars}"
            )

        # Use config cache_dir if provided, else environment variable, else default
        if self.config.cache_dir:
            self.cache_dir = Path(self.config.cache_dir)
        elif os.environ.get("VALIDATION_CACHE_DIR"):
            self.cache_dir = Path(os.environ["VALIDATION_CACHE_DIR"])
        else:
            self.cache_dir = CACHE_DIR

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # monotonic-clock timestamp of the most recently reserved request slot;
        # 0.0 means "never issued a request yet" (no rate-limit on first call).
        self._last_request_time = 0.0
        self._rate_limit_delay = 0.5  # seconds between requests
        self._rate_limit_lock = threading.Lock()

    def fetch_sources(self, urls: List[str]) -> Dict[str, FetchedSource]:
        """
        Fetch multiple URLs in parallel.

        Args:
            urls: List of URLs to fetch

        Returns:
            Dict mapping URL to FetchedSource
        """
        unique_urls = list(set(u for u in urls if u and self._is_valid_url(u)))
        results = {}

        if not unique_urls:
            return results

        logger.info(f"Fetching {len(unique_urls)} unique URLs")

        # Use thread pool for parallel fetching
        max_workers = min(10, len(unique_urls))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.fetch_single, url): url
                for url in unique_urls
            }

            for future in as_completed(futures):
                url = futures[future]
                try:
                    result = future.result()
                    results[url] = result
                except (UnsafeUrlError, ValueError) + _NETWORK_EXCEPTIONS as e:
                    # Known fetch-layer failures — surface them as inaccessible
                    # results so the batch API contract (one result per URL)
                    # holds. Programming bugs (AttributeError, TypeError, etc.)
                    # are NOT caught here — they propagate so they get noticed.
                    logger.error(f"Failed to fetch {url}: {e}")
                    results[url] = FetchedSource(
                        url=url,
                        content="",
                        accessible=False,
                        error=str(e),
                    )

        accessible_count = sum(1 for r in results.values() if r.accessible)
        logger.info(f"Fetched {accessible_count}/{len(results)} sources successfully")

        return results

    def fetch_single(self, url: str) -> FetchedSource:
        """
        Fetch a single URL with caching.

        Args:
            url: URL to fetch

        Returns:
            FetchedSource with content or error
        """
        if not self._is_valid_url(url):
            return FetchedSource(
                url=url if isinstance(url, str) else str(url),
                content="",
                accessible=False,
                error="invalid or unsafe URL",
            )

        # Check cache first
        cached = self._get_cached(url)
        if cached:
            logger.debug(f"Cache hit for {url}")
            return cached

        # Rate limiting
        self._apply_rate_limit()

        # Fetch the URL
        result = self._fetch_url(url)

        # Try Wayback Machine if failed
        if not result.accessible:
            wayback_result = self._try_wayback(url)
            if wayback_result and wayback_result.accessible:
                result = wayback_result
                result.url = url  # Keep original URL

        # Cache the result
        if result.accessible:
            self._cache_result(url, result)

        return result

    def _fetch_via_proxy(self, url: str) -> Optional[Dict]:
        """
        Try the AtlasForge web proxy first.

        Returns a dict shaped like _fetch_with_httpx's output on success, or
        None if the proxy is unreachable / returned a non-success result. The
        caller falls back to direct httpx/requests/curl on None.

        Why this matters:
        - Proxy returns raw HTML (22x more bytes than Anthropic's filtered view)
        - Proxy handles Reddit auto-routing via JSON API
        - Proxy has 24h cache so repeated validator runs hit warm cache
        """
        if not HAS_REQUESTS:
            return None
        try:
            resp = requests.post(
                f"{WEB_PROXY_URL}/fetch",
                json={"url": url, "max_chars": self.config.max_source_chars},
                timeout=WEB_PROXY_TIMEOUT,
            )
            if resp.status_code != 200:
                logger.debug(f"proxy /fetch returned {resp.status_code} for {url}")
                return None
            data = resp.json()
            text = data.get("text") or data.get("content") or ""
            if not text:
                logger.debug(f"proxy returned empty text for {url}")
                return None
            # The proxy's extract_page_content already ran BeautifulSoup on the
            # source HTML — the `text` field is clean plaintext. Flag it so the
            # caller skips a second BS4 pass that would strip literal `<` chars
            # in code snippets (e.g. `if x < y`) as if they were HTML tags.
            return {
                "content": text,
                "content_type": data.get("content_type", "text/html"),
                "pre_extracted": True,
            }
        except requests.exceptions.RequestException as e:
            logger.debug(f"proxy fetch unavailable for {url}: {e}")
            return None
        except (ValueError, KeyError) as e:
            logger.warning(f"proxy returned malformed response for {url}: {e}")
            return None

    def _fetch_url(self, url: str, skip_proxy: bool = False) -> FetchedSource:
        """Perform the actual URL fetch.

        Args:
            url: URL to fetch.
            skip_proxy: If True, bypass the AtlasForge web proxy and go
                directly to httpx/requests/curl. Used by _try_wayback on
                retry so we don't burn WEB_PROXY_TIMEOUT a second time
                when the proxy is known to be unreachable.
        """
        try:
            start_time = time.time()

            # Try AtlasForge proxy first — gives unfiltered raw HTML + Reddit support.
            # Callers that already paid the proxy-timeout cost pass skip_proxy=True.
            response = None if skip_proxy else self._fetch_via_proxy(url)

            # Fall back to direct HTTP if proxy is down, skipped, or returned nothing.
            if response is None:
                if HAS_HTTPX:
                    response = self._fetch_with_httpx(url)
                elif HAS_REQUESTS:
                    response = self._fetch_with_requests(url)
                else:
                    # Fallback to subprocess curl
                    response = self._fetch_with_curl(url)

            elapsed = time.time() - start_time
            content_type = response.get("content_type", "text/html")
            raw_content = response.get("content", "")
            pre_extracted = response.get("pre_extracted", False)

            # Proxy / fetcher contract: non-PDF paths require str content; the
            # PDF path tolerates str or bytes. A wrong-typed payload is a
            # programming bug, not a network failure — raise TypeError so it
            # escapes _fetch_url's narrowed _NETWORK_EXCEPTIONS/ValueError
            # catches and surfaces to the caller.
            if "pdf" in content_type.lower():
                if not isinstance(raw_content, (str, bytes)):
                    raise TypeError(
                        f"proxy returned non-str/non-bytes content for PDF url "
                        f"{url[:100]}: {type(raw_content).__name__}"
                    )
            else:
                if not isinstance(raw_content, str):
                    raise TypeError(
                        f"proxy returned non-str content for url {url[:100]}: "
                        f"{type(raw_content).__name__}"
                    )

            # Process content based on type
            if "pdf" in content_type.lower():
                text_content = self._extract_pdf_text(raw_content)
            elif pre_extracted:
                text_content = raw_content
            else:
                text_content = self._extract_text_from_html(raw_content)

            # Truncate if too long
            truncated = False
            original_length = len(text_content)
            if original_length > self.config.max_source_chars:
                text_content = text_content[:self.config.max_source_chars]
                truncated = True

            return FetchedSource(
                url=url,
                content=text_content,
                accessible=True,
                content_type=content_type,
                truncated=truncated,
                original_length=original_length,
            )

        except UnsafeUrlError:
            # Callers must be able to distinguish URL-safety rejection from
            # a transient network failure — propagate it untouched.
            raise
        except _NETWORK_EXCEPTIONS as e:
            logger.warning(f"Failed to fetch {url}: {e}")
            return FetchedSource(
                url=url,
                content="",
                accessible=False,
                error=str(e),
            )
        except ValueError as e:
            # Malformed JSON/response payloads from _fetch_via_proxy or similar.
            # UnsafeUrlError is also a ValueError but is handled above.
            logger.warning(f"Failed to fetch {url}: {e}")
            return FetchedSource(
                url=url,
                content="",
                accessible=False,
                error=str(e),
            )

    def _fetch_with_httpx(self, url: str) -> Dict:
        """Fetch using httpx library."""
        with httpx.Client(timeout=self.config.fetch_timeout_seconds, follow_redirects=True) as client:
            response = client.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; FactChecker/1.0)"})
            response.raise_for_status()
            return {
                "content": response.text,
                "content_type": response.headers.get("content-type", "text/html"),
            }

    def _fetch_with_requests(self, url: str) -> Dict:
        """Fetch using requests library."""
        response = requests.get(
            url,
            timeout=self.config.fetch_timeout_seconds,
            headers={"User-Agent": "Mozilla/5.0 (compatible; FactChecker/1.0)"},
            allow_redirects=True,
        )
        response.raise_for_status()
        return {
            "content": response.text,
            "content_type": response.headers.get("content-type", "text/html"),
        }

    def _fetch_with_curl(self, url: str) -> Dict:
        """
        Fetch using subprocess curl as fallback.

        Security: URL is validated before this method is called, and we use
        list-based subprocess call (not shell=True) to prevent injection.
        """
        import subprocess

        # Double-check URL safety before passing to subprocess
        # The URL should already be validated by _is_valid_url, but defense in depth
        if not self._is_valid_url(url):
            raise UnsafeUrlError(f"Invalid or unsafe URL: {url[:100]}")

        # Use list-based argument passing - this is safe because:
        # 1. shell=False (default) means no shell interpretation
        # 2. URL is passed as a single argument, not interpolated into a string
        try:
            result = subprocess.run(
                ["curl", "-sL", "-A", "Mozilla/5.0 (compatible; FactChecker/1.0)",
                 "--max-time", str(self.config.fetch_timeout_seconds), url],
                capture_output=True,
                text=True,
                timeout=self.config.fetch_timeout_seconds + 5,
            )
        except subprocess.TimeoutExpired as e:
            # TimeoutExpired inherits from Exception, not OSError, so without
            # this re-wrap a curl timeout would escape _fetch_url's narrowed
            # _NETWORK_EXCEPTIONS catch and be treated as a programming bug.
            raise OSError(f"curl timed out after {e.timeout}s: {url[:100]}") from e

        if result.returncode != 0:
            # OSError, not bare Exception — so _fetch_url's narrowed
            # _NETWORK_EXCEPTIONS catch wraps this as an inaccessible
            # FetchedSource instead of letting it escape as an untyped bug.
            # stderr is sanitized (control chars stripped) so a curl error
            # containing ANSI escapes or CRLF cannot inject into logs.
            sanitized = re.sub(r"[\x00-\x1f\x7f]", "?", result.stderr or "")
            raise OSError(f"curl failed (rc={result.returncode}): {sanitized[:500]}")

        return {
            "content": result.stdout,
            "content_type": "text/html",  # Can't easily get content type from curl
        }

    def _extract_text_from_html(self, html: str) -> str:
        """Extract readable text from HTML."""
        if not html:
            return ""

        if HAS_BS4:
            try:
                soup = BeautifulSoup(html, "html.parser")

                # Remove script and style elements
                for element in soup(["script", "style", "nav", "header", "footer", "aside"]):
                    element.decompose()

                # Get text
                text = soup.get_text(separator="\n", strip=True)

                # Clean up whitespace
                lines = [line.strip() for line in text.splitlines() if line.strip()]
                return "\n".join(lines)

            except (AttributeError, ValueError, TypeError) as e:
                # BS4 raises ValueError (FeatureNotFound for bad parsers),
                # TypeError (non-string input), AttributeError (malformed
                # parse trees). Programming bugs (NameError, ImportError)
                # must propagate.
                logger.warning(f"BeautifulSoup parsing failed: {e}")

        # Fallback: basic regex cleaning
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def _extract_pdf_text(self, content: bytes) -> str:
        """
        Extract text from PDF content.

        Uses pypdf (v3+) or PyPDF2 as fallback.
        Extracts text from first 20 pages to avoid excessive processing.

        Args:
            content: Raw PDF bytes

        Returns:
            Extracted text or error message
        """
        if not HAS_PDF:
            logger.warning("PDF extraction unavailable: neither pypdf nor PyPDF2 installed")
            return "[PDF content - text extraction unavailable. Install pypdf: pip install pypdf]"

        try:
            import io

            # Handle both string and bytes content
            if isinstance(content, str):
                # If string, try to encode as latin-1 (preserves byte values)
                try:
                    content = content.encode('latin-1')
                except UnicodeEncodeError:
                    content = content.encode('utf-8', errors='replace')

            pdf_file = io.BytesIO(content)
            reader = PdfReader(pdf_file)

            # Extract text from first 20 pages
            max_pages = min(20, len(reader.pages))
            text_parts = []

            for page_num in range(max_pages):
                try:
                    page = reader.pages[page_num]
                    text = page.extract_text()
                    if text:
                        text_parts.append(f"--- Page {page_num + 1} ---\n{text}")
                except (ValueError, KeyError, IndexError, TypeError, AttributeError) as e:
                    logger.debug(f"Failed to extract page {page_num + 1}: {e}")
                    continue

            if not text_parts:
                return "[PDF content - no extractable text found (may be scanned/image-based)]"

            full_text = "\n\n".join(text_parts)

            # Add note if truncated
            if len(reader.pages) > max_pages:
                full_text += f"\n\n[... {len(reader.pages) - max_pages} more pages not extracted ...]"

            logger.debug(f"Extracted {len(text_parts)} pages from PDF using {PDF_LIBRARY}")
            return full_text

        except (ValueError, OSError, KeyError, IndexError, TypeError, AttributeError) as e:
            # pypdf/PyPDF2 surface these for corrupt/unparseable PDFs.
            # Programming bugs (NameError, AssertionError, ImportError) must
            # propagate so they get fixed instead of silently returning a
            # misleading "extraction failed" placeholder.
            logger.warning(f"PDF extraction failed: {e}")
            return f"[PDF content - extraction failed: {str(e)[:100]}]"

    def _try_wayback(self, url: str) -> Optional[FetchedSource]:
        """Try to fetch from Wayback Machine as fallback."""
        try:
            wayback_api = f"https://archive.org/wayback/available?url={url}"

            if HAS_HTTPX:
                with httpx.Client(timeout=10) as client:
                    response = client.get(wayback_api)
                    data = response.json()
            elif HAS_REQUESTS:
                response = requests.get(wayback_api, timeout=10)
                data = response.json()
            else:
                return None

            snapshots = data.get("archived_snapshots", {})
            closest = snapshots.get("closest", {})

            if closest.get("available"):
                wayback_url = closest.get("url")
                # Defect 11: wayback may report available=True with a missing
                # or null "url" field. Bail out rather than pass None downstream.
                if not wayback_url:
                    logger.warning(
                        f"Wayback reported available but returned no url for {url}"
                    )
                    return None
                # Defect 10: the wayback API's URL is not implicitly trusted —
                # re-run it through _is_valid_url so a malicious or malformed
                # snapshot URL cannot bypass the safety gate that the original
                # URL already passed.
                if not self._is_valid_url(wayback_url):
                    logger.warning(
                        f"Wayback returned unsafe URL for {url}: {str(wayback_url)[:100]}"
                    )
                    return None
                logger.info(f"Using Wayback snapshot: {wayback_url}")
                # skip_proxy=True: the primary fetch already failed, and when
                # the proxy is down the original _fetch_via_proxy call has
                # already burned WEB_PROXY_TIMEOUT seconds. Hitting it again
                # here would double that cost for no gain — go direct.
                return self._fetch_url(wayback_url, skip_proxy=True)

        except UnsafeUrlError:
            # If the wayback-provided URL is itself unsafe, that is a
            # real safety-level signal — callers must be able to
            # distinguish it from "wayback API was unreachable".
            raise
        except Exception as e:
            logger.warning(f"Wayback fallback failed for {url}: {e}")

        return None

    def _get_cached(self, url: str) -> Optional[FetchedSource]:
        """
        Get cached content for URL if valid.

        Uses try-first approach to avoid race conditions:
        - Attempts to read directly without checking existence first
        - Handles FileNotFoundError gracefully
        - TTL check happens after successful read
        """
        cache_file = self._get_cache_path(url)

        try:
            # Try to read directly - avoids TOCTOU race condition
            with open(cache_file, "r") as f:
                data = json.load(f)

            # Check TTL after successful read
            cached_time = datetime.fromisoformat(data["fetch_time"])
            if datetime.now() - cached_time > timedelta(hours=self.config.cache_ttl_hours):
                # Cache expired - try to remove stale file
                try:
                    cache_file.unlink()
                except OSError:
                    pass  # Another process may have already removed it
                return None

            return FetchedSource(
                url=data["url"],
                content=data["content"],
                accessible=data["accessible"],
                error=data.get("error"),
                fetch_time=cached_time,
                content_type=data.get("content_type", "unknown"),
                truncated=data.get("truncated", False),
                original_length=data.get("original_length", len(data["content"])),
            )

        except FileNotFoundError:
            # Cache miss - this is the expected common case
            return None
        except json.JSONDecodeError as e:
            logger.warning(f"Corrupted cache file for {url}: {e}")
            # Try to remove corrupted file
            try:
                cache_file.unlink()
            except OSError:
                pass
            return None
        except (OSError, KeyError, ValueError, TypeError) as e:
            # OSError: file-system errors that aren't FileNotFoundError.
            # KeyError/TypeError: missing/wrong-typed cache fields.
            # ValueError: datetime.fromisoformat on a non-iso fetch_time.
            # Delete the corrupt cache file so we don't keep tripping on it;
            # matches the JSONDecodeError branch above.
            logger.warning(f"Failed to read cache for {url}: {e}")
            try:
                cache_file.unlink()
            except OSError:
                pass
            return None

    def _cache_result(self, url: str, result: FetchedSource):
        """
        Cache a fetch result using atomic write.

        Uses write-to-temp-then-rename pattern for atomicity:
        - Writes to a temporary file first
        - Uses os.rename() for atomic move
        - Cleans up temp file on failure
        """
        cache_file = self._get_cache_path(url)
        temp_file = None

        try:
            data = {
                "url": url,
                "content": result.content,
                "accessible": result.accessible,
                "error": result.error,
                "fetch_time": result.fetch_time.isoformat(),
                "content_type": result.content_type,
                "truncated": result.truncated,
                "original_length": result.original_length,
            }

            # Write to temp file in same directory (needed for atomic rename)
            fd, temp_path = tempfile.mkstemp(
                dir=self.cache_dir,
                prefix=".cache_tmp_",
                suffix=".json"
            )
            temp_file = Path(temp_path)

            # os.fdopen takes ownership of fd; its context manager closes it
            # once. Any further os.close(fd) would be a double-close. Let
            # the outer except block clean up temp_file on failure.
            with os.fdopen(fd, 'w') as f:
                json.dump(data, f)

            # os.replace is atomic and cross-platform (unlike os.rename on
            # Windows when the destination exists).
            os.replace(temp_path, cache_file)
            temp_file = None  # Rename succeeded, don't clean up

        except (OSError, TypeError, ValueError) as e:
            # OSError for file I/O (disk full, permission, mkstemp failure),
            # TypeError for non-serializable dict values,
            # ValueError for json.dump edge cases (e.g., NaN with allow_nan=False).
            # Programming bugs (NameError, AttributeError on self) propagate.
            logger.warning(f"Failed to cache {url}: {e}")
            # Clean up temp file if it exists
            if temp_file and temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass

    def _get_cache_path(self, url: str) -> Path:
        """Get cache file path for URL."""
        # SHA256 avoids the collision-attack surface of MD5.
        url_hash = hashlib.sha256(url.encode()).hexdigest()
        return self.cache_dir / f"{url_hash}.json"

    def _apply_rate_limit(self):
        """Sleep just long enough to keep requests >= _rate_limit_delay apart.

        Uses monotonic elapsed time (not a simple counter) so a request that
        arrives after a long idle period is not penalized, and concurrent
        callers queue fairly: the slot is reserved inside the lock before
        sleeping, so two threads cannot both observe "elapsed > delay"
        against the same stale timestamp.
        """
        with self._rate_limit_lock:
            now = time.monotonic()
            # First call ever: no prior slot, so proceed immediately.
            if self._last_request_time == 0.0:
                self._last_request_time = now
                sleep_for = 0.0
            else:
                # Desired time for the next slot is delay after the last one.
                next_slot = self._last_request_time + self._rate_limit_delay
                if now >= next_slot:
                    # Idle long enough; take the slot now.
                    self._last_request_time = now
                    sleep_for = 0.0
                else:
                    # Reserve the next slot so concurrent callers queue behind it.
                    self._last_request_time = next_slot
                    sleep_for = next_slot - now
        if sleep_for > 0:
            time.sleep(sleep_for)

    def _is_valid_url(self, url: str) -> bool:
        """
        Check if URL is valid and safe for fetching.

        Security checks:
        - Only http/https schemes allowed
        - Must have valid netloc (domain)
        - No shell metacharacters that could enable injection
        - No local file paths

        Args:
            url: URL to validate

        Returns:
            True if URL is safe to fetch
        """
        if not isinstance(url, str):
            return False

        if not url:
            return False

        # Reject option-like prefixes BEFORE the scheme check: a string like
        # "-oOUTPUT" or "--data=@/etc/passwd" would fail the scheme check
        # anyway, but being explicit protects callers that might bypass the
        # scheme gate.
        if url.startswith("-"):
            logger.warning(f"Rejecting option-like URL: {url[:100]}")
            return False

        # Skip local files
        if url.startswith("/") or url.startswith("file://"):
            return False

        # Must be http(s)
        if not url.startswith("http://") and not url.startswith("https://"):
            return False

        # Normalize once, then apply whitespace and dangerous-char rejection
        # to BOTH the raw URL and the percent-decoded form. This closes the
        # %0A / %20 / %09 encoded-whitespace bypass that would otherwise be
        # possible now that dangerous_chars already normalizes via unquote().
        try:
            normalized = unquote(url)
        except Exception:
            normalized = url

        # Reject any whitespace anywhere in the URL (raw or encoded). Real
        # URLs never contain whitespace; any whitespace is either a parser-
        # confusion attempt or a caller bug. Covers space, tab, \n, \r, \x0b,
        # \x0c, unicode whitespace classes, and their percent-encoded forms.
        if any(c.isspace() for c in url) or any(c.isspace() for c in normalized):
            logger.warning(f"Rejecting URL with whitespace: {url[:100]}")
            return False

        # Security: Check for shell metacharacters that could enable command injection
        # or HTML/XML tag injection. These should NEVER appear in a legitimate URL.
        dangerous_chars = [';', '|', '&', '$', '`', '\x00', '<', '>']
        for char in dangerous_chars:
            if char in url or char in normalized:
                logger.warning(f"Rejecting URL with dangerous character '{repr(char)}': {url[:100]}")
                return False

        # urlparse raises ValueError on a small set of structurally malformed
        # inputs that pass the earlier string checks — notably "http://[" and
        # other unterminated IPv6 brackets. This is documented stdlib
        # behavior, not a programming bug, so catch it narrowly and reject
        # the URL. Any other exception (NameError, AttributeError, etc.) is
        # a real bug and should propagate — that is what removing the
        # broader catch-all bought us.
        try:
            parsed = urlparse(url)
        except ValueError as e:
            logger.warning(f"Rejecting URL that urlparse cannot parse: {url[:100]} ({e})")
            return False

        # Must have a valid domain
        if not parsed.netloc:
            return False

        # Scheme must be http or https
        if parsed.scheme not in ('http', 'https'):
            return False

        # Basic netloc validation - must not be empty or just whitespace
        netloc = parsed.netloc.strip()
        if not netloc or netloc == ':':
            return False

        # Belt-and-suspenders for the curl fallback: reject any parsed
        # component that itself starts with '-'. urlparse happily returns
        # netloc="-oOUTPUT" for malformed schemes, which would end up as
        # an argv element passed to subprocess. The startswith('-') guard
        # at the top catches the most obvious form; this catches nested
        # variants like "http://-evil.example".
        for part in (parsed.netloc, parsed.path, parsed.params,
                     parsed.query, parsed.fragment):
            if part and part.startswith('-'):
                logger.warning(
                    f"Rejecting URL with option-like component: {url[:100]}"
                )
                return False

        return True

    def clear_cache(self):
        """Clear the source cache."""
        import shutil
        if self.cache_dir.exists():
            shutil.rmtree(self.cache_dir)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Source cache cleared")


# Convenience function
def fetch_sources(urls: List[str], config: Optional[ValidationConfig] = None) -> Dict[str, FetchedSource]:
    """Fetch multiple sources with default settings."""
    fetcher = SourceFetcher(config)
    return fetcher.fetch_sources(urls)
