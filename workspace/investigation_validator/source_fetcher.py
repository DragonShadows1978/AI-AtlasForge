"""
Source Fetcher - Fetch and cache source content for validation.

Handles URL fetching with caching, PDF extraction, HTML parsing.
"""

import os
import re
import json
import hashlib
import logging
import time
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, quote
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

# Default cache directory - use relative path from module location
CACHE_DIR = Path(__file__).resolve().parent / ".source_cache"


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

        # Use config cache_dir if provided, else environment variable, else default
        if self.config.cache_dir:
            self.cache_dir = Path(self.config.cache_dir)
        elif os.environ.get("VALIDATION_CACHE_DIR"):
            self.cache_dir = Path(os.environ["VALIDATION_CACHE_DIR"])
        else:
            self.cache_dir = CACHE_DIR

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._fetch_count = 0
        self._rate_limit_delay = 0.5  # seconds between requests

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
                except Exception as e:
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

    def _fetch_url(self, url: str) -> FetchedSource:
        """Perform the actual URL fetch."""
        try:
            start_time = time.time()

            # Use httpx if available, fallback to requests
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

            # Process content based on type
            if "pdf" in content_type.lower():
                text_content = self._extract_pdf_text(raw_content)
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

        except Exception as e:
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
            raise ValueError(f"Invalid or unsafe URL: {url[:100]}")

        # Use list-based argument passing - this is safe because:
        # 1. shell=False (default) means no shell interpretation
        # 2. URL is passed as a single argument, not interpolated into a string
        result = subprocess.run(
            ["curl", "-sL", "-A", "Mozilla/5.0 (compatible; FactChecker/1.0)",
             "--max-time", str(self.config.fetch_timeout_seconds), url],
            capture_output=True,
            text=True,
            timeout=self.config.fetch_timeout_seconds + 5,
        )

        if result.returncode != 0:
            raise Exception(f"curl failed: {result.stderr}")

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

            except Exception as e:
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
                except Exception as e:
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

        except Exception as e:
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
                logger.info(f"Using Wayback snapshot: {wayback_url}")
                return self._fetch_url(wayback_url)

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
        except Exception as e:
            logger.warning(f"Failed to read cache for {url}: {e}")
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

            try:
                with os.fdopen(fd, 'w') as f:
                    json.dump(data, f)

                # Atomic rename - on POSIX this is atomic
                os.rename(temp_path, cache_file)
                temp_file = None  # Rename succeeded, don't clean up

            except Exception:
                os.close(fd)  # Close if fdopen failed
                raise

        except Exception as e:
            logger.warning(f"Failed to cache {url}: {e}")
            # Clean up temp file if it exists
            if temp_file and temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass

    def _get_cache_path(self, url: str) -> Path:
        """Get cache file path for URL."""
        url_hash = hashlib.md5(url.encode()).hexdigest()
        return self.cache_dir / f"{url_hash}.json"

    def _apply_rate_limit(self):
        """Apply rate limiting between requests."""
        self._fetch_count += 1
        if self._fetch_count > 1:
            time.sleep(self._rate_limit_delay)

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
        if not url:
            return False

        # Skip local files
        if url.startswith("/") or url.startswith("file://"):
            return False

        # Must be http(s)
        if not url.startswith("http://") and not url.startswith("https://"):
            return False

        # Security: Check for shell metacharacters that could enable command injection
        # These should NEVER appear in a legitimate URL
        dangerous_chars = [';', '|', '&', '$', '`', '\n', '\r', '\x00']
        for char in dangerous_chars:
            if char in url:
                logger.warning(f"Rejecting URL with dangerous character '{repr(char)}': {url[:100]}")
                return False

        try:
            parsed = urlparse(url)

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

            return True

        except Exception as e:
            logger.warning(f"URL parsing failed for {url[:100]}: {e}")
            return False

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
