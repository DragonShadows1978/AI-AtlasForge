#!/usr/bin/env python3
"""
AtlasForge Conductor Error Classification Module

Provides categorization of restart/failure reasons for clear Activity Log messages
and better error handling in the Conductor.

Error Categories:
    - GRACEFUL: Context exhaustion, time-based handoffs - don't count towards limit
    - RETRIABLE: CLI timeouts, API 500 errors, tool call bugs - count towards limit
    - BLOCKING: Rate limits, auth failures - halt immediately with specific message

Usage:
    from atlasforge_conductor_errors import (
        RestartReason, classify_error, is_graceful, is_blocking
    )

    reason, explanation = classify_error(error_info, response_text)
    if is_graceful(reason):
        # Don't count towards restart limit
    elif is_blocking(reason):
        # Halt immediately with specific message
    else:
        # Count towards limit and retry
"""

import re
from enum import Enum
from typing import Tuple, Optional


class RestartReason(Enum):
    """
    Categorizes restart/failure reasons for clear Activity Log messages.

    Categories:
        GRACEFUL (don't count towards limit):
            - Context approaching limit, planned handoff
            - Time-based handoff at 55 minutes
            - Context overflow (for non-Anthropic providers)

        RETRIABLE (count towards limit, retry 3 times):
            - CLI timeout (no response within timeout period)
            - API error 500 (transient server error)
            - Tool call bug (Claude Code versions with parallel tool call issues)
            - Output too long (response exceeded token limit)
            - CLI crash (generic CLI error)

        BLOCKING (halt immediately, no retry):
            - Rate limited (API usage cap hit)
            - Auth failed (invalid API key or session expired)

        UNKNOWN:
            - Error couldn't be classified
    """

    # === Graceful (don't count towards limit) ===
    CONTEXT_EXHAUSTION = "context_exhaustion"
    TIME_BASED_HANDOFF = "time_based_handoff"
    CONTEXT_OVERFLOW = "context_overflow"

    # === Retriable errors (count towards limit) ===
    CLI_TIMEOUT = "cli_timeout"
    API_ERROR_500 = "api_error_500"
    TOOL_CALL_BUG = "tool_call_bug"
    OUTPUT_TOO_LONG = "output_too_long"
    CLI_CRASH = "cli_crash"
    NETWORK_ERROR = "network_error"
    OVERLOADED = "overloaded"

    # === Blocking (halt immediately with specific message) ===
    RATE_LIMITED = "rate_limited"
    AUTH_FAILED = "auth_failed"
    INVALID_REQUEST = "invalid_request"

    # === Unknown ===
    UNKNOWN = "unknown"


def is_graceful(reason: RestartReason) -> bool:
    """
    Returns True if this is a graceful restart (shouldn't count as error).

    Graceful restarts happen when:
    - Context is approaching the limit (planned handoff)
    - Time limit (55 min) reached (proactive handoff before 1-hour timeout)
    - Model context window overflowed (safe exit)

    These are EXPECTED behaviors during long-running missions like model training.
    """
    return reason in {
        RestartReason.CONTEXT_EXHAUSTION,
        RestartReason.TIME_BASED_HANDOFF,
        RestartReason.CONTEXT_OVERFLOW,
    }


def is_blocking(reason: RestartReason) -> bool:
    """
    Returns True if this error should halt immediately (no retry).

    Blocking errors are unrecoverable without intervention:
    - Rate limited: User needs to wait or has no remaining quota
    - Auth failed: API key invalid or session expired
    - Invalid request: Request malformed (indicates code bug)
    """
    return reason in {
        RestartReason.RATE_LIMITED,
        RestartReason.AUTH_FAILED,
        RestartReason.INVALID_REQUEST,
    }


def classify_error(error_info: str, response_text: Optional[str] = None) -> Tuple[RestartReason, str]:
    """
    Classify error type from CLI error info and response text.

    Analyzes error strings from invoke_llm() and response text to determine
    the specific type of failure and provide a human-readable explanation.

    Args:
        error_info: Error string from invoke_llm() (e.g., "timeout:3600s", "cli_error:...")
        response_text: Optional response text that may contain error patterns

    Returns:
        Tuple of (RestartReason, human_readable_explanation)

    Error Detection Patterns:
        - "timeout:<seconds>s" -> CLI_TIMEOUT
        - "rate_limit", "hit your limit" -> RATE_LIMITED
        - "authentication", "auth" + "fail" -> AUTH_FAILED
        - "tool_use" + "ids must be unique" -> TOOL_CALL_BUG (v2.1.19-2.1.20)
        - "api error: 500", "internal server error" -> API_ERROR_500
        - "output token" + "exceeded" -> OUTPUT_TOO_LONG
        - "maximum context length" + "tokens" -> CONTEXT_OVERFLOW
        - "overloaded" -> OVERLOADED
        - "connection" + "error" -> NETWORK_ERROR
        - "cli_error:" prefix -> CLI_CRASH
        - "exception:" prefix -> UNKNOWN

    Examples:
        >>> classify_error("timeout:3600s")
        (RestartReason.CLI_TIMEOUT, "Claude CLI did not respond within timeout period (timeout:3600s)")

        >>> classify_error("cli_error:rate_limit_exceeded")
        (RestartReason.RATE_LIMITED, "API rate limit reached. Resets at later")

        >>> classify_error("", "You've hit your limit. Resets at 11am.")
        (RestartReason.RATE_LIMITED, "API rate limit reached. Resets at 11am")
    """
    if not error_info:
        error_info = ""

    error_lower = error_info.lower()
    response_lower = (response_text or "").lower()
    combined = f"{error_lower} {response_lower}"

    # === Check for timeout (most common) ===
    if error_info.startswith("timeout:"):
        timeout_value = error_info[8:]  # Extract "3600s" part
        return (
            RestartReason.CLI_TIMEOUT,
            f"Claude CLI did not respond within timeout period ({error_info})"
        )

    # === Rate limit detection ===
    rate_limit_patterns = [
        "rate_limit",
        "rate limit",
        "ratelimit",
        "hit your limit",
        "too many requests",
        "429",
        "quota exceeded"
    ]
    if any(pattern in combined for pattern in rate_limit_patterns):
        # Try to extract reset time
        reset_match = re.search(r"resets?\s+(?:at\s+)?(\d+[ap]m|\d+:\d+)", combined)
        reset_time = reset_match.group(1) if reset_match else "later"
        return (
            RestartReason.RATE_LIMITED,
            f"API rate limit reached. Resets at {reset_time}"
        )

    # === Auth failure detection ===
    auth_failure_patterns = [
        ("authentication", "fail"),
        ("authentication", "error"),
        ("auth", "fail"),
        ("unauthorized", ""),
        ("401", ""),
        ("api key", "invalid"),
        ("api_key", "invalid"),
        ("session", "expired"),
    ]
    for pattern1, pattern2 in auth_failure_patterns:
        if pattern1 in combined:
            if not pattern2 or pattern2 in combined:
                return (
                    RestartReason.AUTH_FAILED,
                    "Authentication failed. Check API key or run /login"
                )

    # === Invalid request detection ===
    invalid_request_patterns = [
        "invalid_request",
        "invalid request",
        "malformed",
        "bad request",
        "400",
    ]
    if any(pattern in combined for pattern in invalid_request_patterns):
        # Extract specific issue if possible
        return (
            RestartReason.INVALID_REQUEST,
            f"Invalid request. Check prompt format. Error: {error_info[:100]}"
        )

    # === Tool call bug (v2.1.19-2.1.20) ===
    if "tool_use" in combined and "ids must be unique" in combined:
        return (
            RestartReason.TOOL_CALL_BUG,
            "Claude Code bug: duplicate tool_use IDs. Consider updating claude CLI"
        )

    # Also check for other tool call related errors
    tool_call_error_patterns = [
        "tool_use_block",
        "invalid tool_use",
        "tool call",
        "function call",
    ]
    if any(pattern in combined for pattern in tool_call_error_patterns) and "error" in combined:
        return (
            RestartReason.TOOL_CALL_BUG,
            f"Tool call error detected. Error: {error_info[:100]}"
        )

    # === API server error (500) ===
    api_500_patterns = [
        "api error: 500",
        "api_error: 500",
        "internal server error",
        "500",
        "server error",
    ]
    # Be careful with 500 - only match if it's clearly an HTTP error
    for pattern in api_500_patterns:
        if pattern == "500":
            # More specific match for 500
            if re.search(r"(error|status|code)[:\s]*500", combined) or "http 500" in combined:
                return (
                    RestartReason.API_ERROR_500,
                    "Anthropic API server error (500). Transient issue."
                )
        elif pattern in combined:
            return (
                RestartReason.API_ERROR_500,
                "Anthropic API server error (500). Transient issue."
            )

    # === Overloaded ===
    if "overloaded" in combined or "503" in combined:
        return (
            RestartReason.OVERLOADED,
            "Anthropic API is overloaded. Will retry after brief pause."
        )

    # === Output token limit ===
    output_limit_patterns = [
        ("output token", "exceeded"),
        ("output token", "maximum"),
        ("max_tokens", "exceeded"),
        ("response", "too long"),
    ]
    for pattern1, pattern2 in output_limit_patterns:
        if pattern1 in combined and pattern2 in combined:
            return (
                RestartReason.OUTPUT_TOO_LONG,
                "Claude response exceeded output token limit"
            )

    # === Context overflow (for non-Anthropic providers or edge cases) ===
    context_overflow_patterns = [
        ("maximum context length", "tokens"),
        ("context length", "exceeded"),
        ("context window", "exceeded"),
        ("too many tokens", ""),
        ("prompt is too long", ""),
    ]
    for pattern1, pattern2 in context_overflow_patterns:
        if pattern1 in combined:
            if not pattern2 or pattern2 in combined:
                return (
                    RestartReason.CONTEXT_OVERFLOW,
                    "Context window exceeded for this model"
                )

    # === Network errors ===
    network_patterns = [
        "connection refused",
        "connection error",
        "network error",
        "socket error",
        "timeout error",
        "connect timeout",
        "read timeout",
        "econnrefused",
        "dns",
    ]
    if any(pattern in combined for pattern in network_patterns):
        return (
            RestartReason.NETWORK_ERROR,
            f"Network error communicating with API. Error: {error_info[:100]}"
        )

    # === CLI error/crash (generic) ===
    if error_info.startswith("cli_error:"):
        stderr_snippet = error_info[10:110]  # 100 char snippet
        return (
            RestartReason.CLI_CRASH,
            f"Claude CLI error: {stderr_snippet}"
        )

    # === Exception (generic) ===
    if error_info.startswith("exception:"):
        exc_msg = error_info[10:110]  # 100 char snippet
        return (
            RestartReason.UNKNOWN,
            f"Exception: {exc_msg}"
        )

    # === Unknown error ===
    snippet = error_info[:100] if error_info else "No error information available"
    return (
        RestartReason.UNKNOWN,
        f"Unknown error: {snippet}"
    )


def format_error_message(reason: RestartReason, explanation: str, attempt: int = 0, max_attempts: int = 3) -> str:
    """
    Format an error message for the Activity Log with proper prefix.

    Args:
        reason: The classified restart reason
        explanation: Human-readable explanation
        attempt: Current attempt number (0-indexed)
        max_attempts: Maximum attempts before halt

    Returns:
        Formatted message string with appropriate prefix

    Examples:
        >>> format_error_message(RestartReason.RATE_LIMITED, "Resets at 11am", 0, 3)
        "[ERROR:RATE_LIMITED] Resets at 11am"

        >>> format_error_message(RestartReason.CLI_TIMEOUT, "60s timeout", 1, 3)
        "[ERROR:CLI_TIMEOUT] 60s timeout (attempt 2/3)"
    """
    prefix = f"[ERROR:{reason.value.upper()}]"

    if is_blocking(reason):
        # Blocking errors - no attempt count
        return f"{prefix} {explanation}"
    else:
        # Retriable errors - include attempt count
        return f"{prefix} {explanation} (attempt {attempt + 1}/{max_attempts})"


def format_fatal_message(reason: RestartReason, explanation: str, max_attempts: int = 3) -> str:
    """
    Format a fatal message when mission is halted.

    Args:
        reason: The classified restart reason
        explanation: Human-readable explanation
        max_attempts: Maximum attempts that were tried

    Returns:
        Formatted fatal message string

    Example:
        >>> format_fatal_message(RestartReason.CLI_TIMEOUT, "60s timeout", 3)
        "[FATAL] Mission halted after 3 errors. Last error: CLI_TIMEOUT - 60s timeout"
    """
    if is_blocking(reason):
        return f"[FATAL] Mission halted due to blocking error: {reason.value.upper()} - {explanation}"
    else:
        return f"[FATAL] Mission halted after {max_attempts} errors. Last error: {reason.value.upper()} - {explanation}"


def format_restart_message(reason: RestartReason, extra_info: str = "") -> str:
    """
    Format a restart message for graceful handoffs.

    Args:
        reason: The graceful restart reason
        extra_info: Additional info (e.g., token count, elapsed time)

    Returns:
        Formatted restart message string

    Examples:
        >>> format_restart_message(RestartReason.CONTEXT_EXHAUSTION, "125K tokens")
        "[RESTART:CONTEXT_EXHAUSTION] Context limit reached (125K tokens). Fresh instance starting..."

        >>> format_restart_message(RestartReason.TIME_BASED_HANDOFF, "55.2 min")
        "[RESTART:TIME_BASED_HANDOFF] Time limit reached (55.2 min). Fresh instance starting..."
    """
    prefix = f"[RESTART:{reason.value.upper()}]"

    if reason == RestartReason.CONTEXT_EXHAUSTION:
        info_str = f" ({extra_info})" if extra_info else ""
        return f"{prefix} Context limit reached{info_str}. Fresh instance starting..."
    elif reason == RestartReason.TIME_BASED_HANDOFF:
        info_str = f" ({extra_info})" if extra_info else ""
        return f"{prefix} Time limit reached{info_str}. Fresh instance starting..."
    elif reason == RestartReason.CONTEXT_OVERFLOW:
        info_str = f" ({extra_info})" if extra_info else ""
        return f"{prefix} Context overflow detected{info_str}. Fresh instance starting..."
    else:
        # Generic graceful restart
        info_str = f" ({extra_info})" if extra_info else ""
        return f"{prefix} Graceful handoff{info_str}. Fresh instance starting..."


# Known Claude Code version bugs for reference
KNOWN_VERSION_BUGS = {
    "2.1.19": ["parallel tool call duplicate IDs"],
    "2.1.20": ["parallel tool call duplicate IDs"],
}


def get_version_bug_info(version: str) -> Optional[str]:
    """
    Get known bug info for a specific Claude Code version.

    Args:
        version: Claude Code version string (e.g., "2.1.19")

    Returns:
        Bug description string if known bugs exist, None otherwise
    """
    bugs = KNOWN_VERSION_BUGS.get(version)
    if bugs:
        return f"Known issues in v{version}: {', '.join(bugs)}"
    return None
