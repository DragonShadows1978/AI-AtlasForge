#!/usr/bin/env python3
"""
Investigation Engine - Parallel Research Mode for RDE Missions

This module provides a simplified, single-cycle investigation workflow that:
1. Takes an investigation query
2. Spawns a lead agent (Sonnet) to analyze and decompose the query
3. Lead agent spawns 3-5 parallel subagents (Haiku) to explore different aspects
4. Synthesizes findings into a comprehensive report

This is COMPLETELY SEPARATE from the standard R&D engine - no stages,
no mission.json modifications, no iterative cycles.
"""

import json
import subprocess
import time
import logging
import uuid
import concurrent.futures
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass, field, asdict
from enum import Enum

# URL Handler integration - deferred import to avoid circular deps
_url_handlers_available = None

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("investigation_engine")

# Base paths - use centralized configuration
from atlasforge_config import BASE_DIR, STATE_DIR
INVESTIGATION_STATE_PATH = STATE_DIR / "investigation_state.json"
INV_GROUND_RULES_PATH = BASE_DIR / "investigations" / "INV_GROUND_RULES.md"


# =============================================================================
# DATA STRUCTURES
# =============================================================================

class InvestigationStatus(Enum):
    """Status of an investigation."""
    PENDING = "pending"
    ANALYZING = "analyzing"
    SPAWNING_SUBAGENTS = "spawning_subagents"
    EXPLORING = "exploring"
    VALIDATING = "validating"  # Adversarial fact-checking of citations
    SYNTHESIZING = "synthesizing"
    COMPLETED = "completed"
    FAILED = "failed"


class ModelType(Enum):
    """Available model types."""
    CLAUDE_SONNET = "sonnet"
    CLAUDE_OPUS = "opus"
    CLAUDE_HAIKU = "haiku"


@dataclass
class InvestigationConfig:
    """Configuration for an investigation."""
    query: str
    investigation_id: str = field(default_factory=lambda: f"inv_{uuid.uuid4().hex[:8]}")
    max_subagents: int = 5
    timeout_minutes: int = 10
    lead_model: ModelType = ModelType.CLAUDE_SONNET
    subagent_model: ModelType = ModelType.CLAUDE_HAIKU
    workspace_dir: Optional[Path] = None
    deliverable_format: Optional[str] = None  # e.g., "HTML", "JSON", "markdown", "PDF"
    source: str = "dashboard"  # "dashboard" | "email" | "api" - tracks origin of investigation
    skip_global_state: bool = False  # When True, don't write to investigation_state.json (for email concurrency)

    # Adversarial validation settings
    enable_validation: bool = True  # Enable fact-checking before synthesis
    validation_filter_mode: str = "balanced"  # "strict", "annotated", or "balanced"

    def __post_init__(self):
        if self.workspace_dir is None:
            self.workspace_dir = BASE_DIR / "investigations" / self.investigation_id
        elif isinstance(self.workspace_dir, str):
            self.workspace_dir = Path(self.workspace_dir)

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "investigation_id": self.investigation_id,
            "max_subagents": self.max_subagents,
            "timeout_minutes": self.timeout_minutes,
            "lead_model": self.lead_model.value,
            "subagent_model": self.subagent_model.value,
            "workspace_dir": str(self.workspace_dir),
            "deliverable_format": self.deliverable_format,
            "source": self.source,
            "skip_global_state": self.skip_global_state,
            "enable_validation": self.enable_validation,
            "validation_filter_mode": self.validation_filter_mode,
        }


@dataclass
class SubagentResult:
    """Result from a single subagent exploration."""
    subagent_id: str
    focus_area: str
    findings: str
    elapsed_seconds: float
    status: str = "completed"
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class InvestigationResult:
    """Complete result from an investigation."""
    investigation_id: str
    query: str
    status: InvestigationStatus
    subagent_results: List[SubagentResult]
    synthesis: Optional[str]
    report_path: Optional[Path]
    started_at: str
    completed_at: Optional[str]
    elapsed_seconds: float
    error: Optional[str] = None

    # Validation metadata
    validation_stats: Optional[Dict[str, Any]] = None

    def to_dict(self) -> dict:
        result = {
            "investigation_id": self.investigation_id,
            "query": self.query,
            "status": self.status.value,
            "subagent_results": [r.to_dict() for r in self.subagent_results],
            "synthesis": self.synthesis,
            "report_path": str(self.report_path) if self.report_path else None,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "elapsed_seconds": self.elapsed_seconds,
            "error": self.error
        }
        if self.validation_stats:
            result["validation_stats"] = self.validation_stats
        return result


# =============================================================================
# GROUND RULES LOADING
# =============================================================================

# =============================================================================
# URL HANDLER INTEGRATION
# =============================================================================

def _check_url_handlers():
    """Check if URL handlers are available."""
    global _url_handlers_available
    if _url_handlers_available is None:
        try:
            from url_handlers import classify_url, extract_metadata, extract_all_metadata
            _url_handlers_available = True
        except ImportError as e:
            logger.warning(f"URL handlers not available: {e}")
            _url_handlers_available = False
    return _url_handlers_available


def detect_and_extract_urls(query: str) -> List[Dict[str, Any]]:
    """
    Detect URLs in query and extract metadata using specialized handlers.

    Returns list of metadata dicts for each detected URL that has a handler.
    This enables pre-fetching GitHub stars, GitLab metrics, doc structure, etc.
    BEFORE the lead agent runs, so it can incorporate that data.
    """
    if not _check_url_handlers():
        return []

    try:
        from url_handlers import extract_all_metadata
        results = extract_all_metadata(query)
        if results:
            logger.info(f"Extracted metadata for {len(results)} URLs from query")
        return results
    except Exception as e:
        logger.warning(f"URL metadata extraction failed: {e}")
        return []


def get_url_handler_prompt(url: str, metadata: Dict[str, Any]) -> Optional[str]:
    """Get specialized analysis prompt for a URL based on its handler."""
    if not _check_url_handlers():
        return None

    try:
        from url_handlers import classify_url, get_handler
        handler_type = classify_url(url)
        if handler_type:
            handler = get_handler(handler_type)
            if handler:
                return handler.get_analysis_prompt(metadata)
    except Exception as e:
        logger.debug(f"Could not get handler prompt: {e}")
    return None


def format_url_metadata_for_prompt(url_metadata: List[Dict[str, Any]]) -> str:
    """
    Format extracted URL metadata as a section for the lead agent prompt.

    This gives the lead agent pre-fetched data about GitHub repos, GitLab projects,
    or documentation sites so it can make informed decisions about research directions.
    """
    if not url_metadata or not _check_url_handlers():
        return ""

    try:
        from url_handlers import classify_url, get_handler

        sections = []
        for meta in url_metadata:
            url = meta.get('url', '')
            handler_type = classify_url(url)
            if handler_type:
                handler = get_handler(handler_type)
                if handler:
                    section = handler.format_metadata_section(meta)
                    if section:
                        sections.append(section)

        if sections:
            return "\n\n## Pre-Fetched URL Metadata\n\n" + "\n\n---\n\n".join(sections)

    except Exception as e:
        logger.warning(f"Could not format URL metadata: {e}")

    return ""


def format_url_executive_summaries(url_metadata: List[Dict[str, Any]], findings: str = "") -> str:
    """
    Generate executive summaries for all URLs using their handlers.

    Returns formatted markdown suitable for inclusion at the top of reports.
    """
    if not url_metadata or not _check_url_handlers():
        return ""

    try:
        from url_handlers import classify_url, get_handler

        summaries = []
        for meta in url_metadata:
            url = meta.get('url', '')
            handler_type = classify_url(url)
            if handler_type:
                handler = get_handler(handler_type)
                if handler:
                    summary = handler.format_executive_summary(meta, findings)
                    if summary:
                        summaries.append(summary)

        if summaries:
            return "\n".join(summaries)

    except Exception as e:
        logger.warning(f"Could not generate executive summaries: {e}")

    return ""


# =============================================================================
# GROUND RULES LOADING
# =============================================================================

def load_investigation_ground_rules() -> str:
    """
    Load investigation ground rules from file.

    Returns the contents of INV_GROUND_RULES.md, or empty string if unavailable.
    """
    try:
        if INV_GROUND_RULES_PATH.exists():
            content = INV_GROUND_RULES_PATH.read_text()
            logger.info("Loaded investigation ground rules from INV_GROUND_RULES.md")
            return content
        else:
            logger.warning(f"Investigation ground rules file not found: {INV_GROUND_RULES_PATH}")
    except Exception as e:
        logger.warning(f"Failed to load investigation ground rules: {e}")
    return ""


# =============================================================================
# STATE MANAGEMENT
# =============================================================================

def load_investigation_state() -> dict:
    """Load current investigation state from file."""
    try:
        if INVESTIGATION_STATE_PATH.exists():
            with open(INVESTIGATION_STATE_PATH, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load investigation state: {e}")
    return {
        "current": None,
        "history": []
    }


def save_investigation_state(state: dict):
    """Save investigation state to file."""
    try:
        STATE_DIR.mkdir(exist_ok=True)
        with open(INVESTIGATION_STATE_PATH, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save investigation state: {e}")


def update_investigation_status(investigation_id: str, status: InvestigationStatus, extra: dict = None):
    """Update the status of an investigation."""
    state = load_investigation_state()
    if state.get("current") and state["current"].get("investigation_id") == investigation_id:
        state["current"]["status"] = status.value
        state["current"]["last_updated"] = datetime.now().isoformat()
        if extra:
            state["current"].update(extra)
        save_investigation_state(state)


# =============================================================================
# CLAUDE INVOCATION
# =============================================================================

def invoke_claude(
    prompt: str,
    model: ModelType = ModelType.CLAUDE_SONNET,
    system_prompt: Optional[str] = None,
    timeout: int = 120,
    cwd: Optional[Path] = None
) -> tuple[str, float]:
    """
    Invoke Claude CLI with the given prompt.

    Returns:
        Tuple of (response_text, elapsed_seconds)
    """
    if cwd is None:
        cwd = BASE_DIR

    start_time = time.time()

    cmd = ["claude", "-p", "--dangerously-skip-permissions"]

    if model:
        cmd.extend(["--model", model.value])

    if system_prompt:
        cmd.extend(["--system-prompt", system_prompt])

    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd)
        )

        elapsed = time.time() - start_time

        if result.returncode == 0:
            return result.stdout.strip(), elapsed
        else:
            return f"ERROR: {result.stderr}", elapsed
    except subprocess.TimeoutExpired:
        return "ERROR: Timeout", time.time() - start_time
    except Exception as e:
        return f"ERROR: {str(e)}", time.time() - start_time


# =============================================================================
# INVESTIGATION PROMPTS
# =============================================================================

def build_lead_agent_prompt(query: str, max_subagents: int, deliverable_format: str = None, ground_rules: str = "") -> str:
    """Build the prompt for the lead investigation agent."""

    # Include ground rules at the top of the prompt if provided
    ground_rules_section = ""
    if ground_rules:
        ground_rules_section = f"""
=== INVESTIGATION GROUND RULES ===
{ground_rules}
=== END GROUND RULES ===

"""

    deliverable_instruction = ""
    if deliverable_format:
        deliverable_instruction = f"""
## Deliverable Format Requested
The user has requested the final output in: **{deliverable_format}**
Ensure your research directions account for gathering information needed to produce this deliverable.
"""

    return f"""{ground_rules_section}You are a lead investigation agent conducting a deep-dive research analysis.

## SCOPE: UNRESTRICTED RESEARCH

You are a GENERAL RESEARCH agent, NOT limited to software development.
You can and SHOULD investigate ANY topic including but not limited to:
- Gaming (builds, strategies, mechanics, lore)
- Science (physics, biology, chemistry, mathematics)
- Technology (hardware, software, engineering)
- History, geography, culture
- Business, economics, market research
- Creative topics (art, music, writing)
- Sports, fitness, health
- Any other domain the user asks about

If the query is about software/code, investigate software.
If the query is about gaming, investigate gaming.
If the query is about physics, investigate physics.
**NEVER refuse to investigate a topic because it's "outside your scope."**

## Investigation Query
{query}
{deliverable_instruction}
## Your Task

1. **Analyze the Query**: Understand what exactly needs to be investigated and why. Accept the query as-is - do NOT suggest the user has the wrong tool or should go elsewhere.

2. **Decompose into Research Directions**: Identify {max_subagents} independent areas that should be explored to fully understand this topic. Each should be a distinct, parallelizable research direction.

3. **For each research direction, provide**:
   - A clear focus area name (2-5 words)
   - A specific research prompt for a subagent (detailed enough that the subagent can work independently)
   - Whether the subagent should prioritize web research vs local file exploration

## Output Format

Respond with a JSON object in this EXACT format:

```json
{{
    "understanding": "Brief summary of what this investigation is about",
    "domain": "The domain of this query (e.g., 'gaming', 'physics', 'software', 'general')",
    "key_questions": ["Question 1", "Question 2", ...],
    "research_directions": [
        {{
            "focus_area": "Area name",
            "prompt": "Detailed research prompt for the subagent...",
            "research_type": "web" | "local" | "both"
        }},
        ...
    ]
}}
```

Important:
- Provide exactly {max_subagents} research directions
- Each prompt should be self-contained and specific
- Focus on exploration and understanding
- Subagents have access to: web search, file reading, and documentation lookup
- For non-software topics, prioritize web research
- For software/codebase topics, prioritize local file exploration
"""


def build_subagent_prompt(focus_area: str, base_prompt: str, investigation_query: str, research_type: str = "both", ground_rules: str = "") -> str:
    """Build the prompt for a subagent exploration."""

    # Include ground rules at the top of the prompt if provided
    ground_rules_section = ""
    if ground_rules:
        ground_rules_section = f"""
=== INVESTIGATION GROUND RULES ===
{ground_rules}
=== END GROUND RULES ===

"""

    # Build research guidelines based on type
    if research_type == "web":
        research_guidelines = """
## Research Guidelines

1. **Use WebSearch and WebFetch tools** to find authoritative sources on this topic
2. Search for recent information, guides, documentation, and expert opinions
3. Cross-reference multiple sources for accuracy
4. Focus on finding practical, actionable information
5. Document your sources with URLs where possible
6. Provide clear, well-researched insights
"""
    elif research_type == "local":
        research_guidelines = """
## Research Guidelines

1. **Use Read, Glob, and Grep tools** to explore the local codebase/files
2. Focus on understanding the structure and implementation
3. Document relevant files and their purposes
4. Do NOT make any code changes or create files
5. Provide clear, actionable insights
"""
    else:  # both
        research_guidelines = """
## Research Guidelines

1. **Use ALL available tools** as appropriate for this topic:
   - WebSearch/WebFetch for external information, guides, and documentation
   - Read/Glob/Grep for local codebase or file exploration
2. Combine web research with local exploration when relevant
3. Cross-reference sources for accuracy
4. Focus on finding practical, actionable information
5. Document your sources (URLs or file paths as applicable)
6. Provide clear, well-researched insights
"""

    return f"""{ground_rules_section}You are a research subagent exploring a specific aspect of an investigation.

## SCOPE: UNRESTRICTED RESEARCH

You are a GENERAL RESEARCH agent. You can investigate ANY topic - gaming, science, software,
history, sports, business, or any other domain. Your job is to thoroughly research your assigned
focus area, not to question whether it's appropriate.

## Original Investigation Query
{investigation_query}

## Your Focus Area
{focus_area}

## Your Task
{base_prompt}
{research_guidelines}
## Output Format

Respond with a JSON object:

```json
{{
    "focus_area": "{focus_area}",
    "key_findings": [
        "Finding 1",
        "Finding 2",
        ...
    ],
    "sources": [
        {{"type": "web|file", "reference": "URL or file path", "relevance": "why it matters"}}
    ],
    "insights": "Your analysis and understanding of this area",
    "recommendations": ["Actionable recommendation 1", "Recommendation 2"],
    "follow_up_questions": ["Question 1", "Question 2"]
}}
```
"""


def build_synthesis_prompt(query: str, subagent_results: List[SubagentResult], deliverable_format: str = None, source: str = "dashboard", ground_rules: str = "") -> str:
    """Build the prompt for synthesizing subagent findings.

    Args:
        query: The original investigation query
        subagent_results: Results from parallel subagent explorations
        deliverable_format: Optional format (HTML, JSON, markdown)
        source: Origin of investigation - "dashboard", "email", or "api"
               When "email", removes mission-style language (phases, timelines, next steps)
        ground_rules: Investigation ground rules to include in prompt
    """
    # Include ground rules at the top of the prompt if provided
    ground_rules_section = ""
    if ground_rules:
        ground_rules_section = f"""
=== INVESTIGATION GROUND RULES ===
{ground_rules}
=== END GROUND RULES ===

"""

    findings_text = "\n\n".join([
        f"### {r.focus_area}\n{r.findings}"
        for r in subagent_results if r.status == "completed"
    ])

    # Build format-specific instructions
    if deliverable_format:
        format_lower = deliverable_format.lower()
        if "html" in format_lower:
            # Truncate query for title (escape HTML special chars)
            title_query = query[:50].replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
            format_instruction = f"""
=== CRITICAL: OUTPUT FORMAT REQUIREMENT ===

Your response MUST be a complete HTML document. This is MANDATORY.

REQUIRED FORMAT:
1. Your response MUST start with exactly: <!DOCTYPE html>
2. Your response MUST include: <html>, <head>, <body> tags
3. Your response MUST include <style> block with CSS

FORBIDDEN:
- DO NOT use markdown syntax (no #, ##, **, -, etc.)
- DO NOT wrap your HTML in ```html code fences
- DO NOT include any text before <!DOCTYPE html>
- DO NOT output anything that is not valid HTML

TEMPLATE TO FOLLOW:
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Investigation Report: {title_query}...</title>
    <style>
        body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 0 auto; padding: 2rem; line-height: 1.6; background: #f8f9fa; }}
        h1 {{ color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 0.5rem; }}
        h2 {{ color: #34495e; margin-top: 2rem; }}
        .summary {{ background: #e8f4f8; padding: 1rem; border-radius: 8px; margin: 1rem 0; }}
        .finding {{ background: white; padding: 1rem; margin: 0.5rem 0; border-left: 4px solid #3498db; }}
        ul {{ padding-left: 1.5rem; }}
        li {{ margin: 0.5rem 0; }}
    </style>
</head>
<body>
    <h1>Investigation Report</h1>
    <!-- Your content here using ONLY HTML tags -->
</body>
</html>

=== END FORMAT REQUIREMENT ===
"""
        elif "json" in format_lower:
            format_instruction = f"""
## Deliverable Format: JSON

The user requested a **JSON** deliverable. Create a well-structured JSON object containing:
- All key findings as structured data
- Recommendations as an array
- Sources/references
- Any other relevant data in machine-readable format

Start your response with a valid JSON object.
"""
        elif "pdf" in format_lower or "document" in format_lower:
            format_instruction = f"""
## Deliverable Format: Formatted Document

The user requested a formatted document. Create a well-structured markdown report that:
- Uses clear headings and subheadings
- Includes tables where appropriate
- Is suitable for conversion to PDF
- Is professional in tone and presentation
"""
        else:
            format_instruction = f"""
## Deliverable Format: {deliverable_format}

The user requested the output in **{deliverable_format}** format. Adapt your output to best match this format while including all relevant research findings.
"""
    else:
        # Email investigations get a streamlined format - NO mission language
        if source == "email":
            format_instruction = """
## Output Format

Create a focused research report with these sections:

# Investigation Report

## Summary
(2-3 paragraphs summarizing the key findings)

## Key Findings
(Bulleted list of the most important discoveries)

## Detailed Analysis
(Synthesized analysis organized by theme, not by subagent)

## Sources & References
(List of sources used in the research)

DO NOT include:
- "Next Steps" or action items
- "Phase 1, 2, 3" or project phases
- "Weeks 1-2" or any timeline estimates
- "If this becomes a mission..." or similar language
- Recommendations for further work
- Any references to missions or projects

Just report the FINDINGS. This is a standalone research report, not a mission proposal.
"""
        else:
            format_instruction = """
## Output Format

Create a comprehensive markdown report with these sections:

# Investigation Report

## Executive Summary
(2-3 paragraphs summarizing the key findings)

## Key Findings
(Bulleted list of the most important discoveries)

## Detailed Analysis
(Synthesized analysis organized by theme, not by subagent)

## Recommendations
(Specific actionable recommendations based on findings)

## Follow-up Questions
(Important questions that remain unanswered)

## Next Steps
(If this becomes a full mission, what should be done?)
"""

    return f"""{ground_rules_section}You are synthesizing research findings from multiple parallel investigations.

## SCOPE: UNRESTRICTED RESEARCH

You are a GENERAL RESEARCH synthesizer. Your job is to combine findings from any domain -
gaming, science, software, business, or any other topic. Produce a complete, useful deliverable
that answers the original query. Do NOT question whether this is the right tool for the job.
DELIVER what the user asked for.

## Original Investigation Query
{query}

## Subagent Findings

{findings_text}

## Your Task

Synthesize these findings into a comprehensive deliverable that fully addresses the original query.
Include specific, actionable information - not just suggestions to "consult other resources."
{format_instruction}
## Important

- Include CONCRETE information from the research, not just pointers to other resources
- If the query asks for a specific build, config, or setup - PROVIDE IT based on research
- If information is incomplete, note what's missing but still provide the best answer possible
- Do NOT tell the user "this is outside my scope" - that is NEVER true for this system
- Do NOT suggest the user use a different tool - YOU are the research tool
"""


def build_synthesis_prompt_validated(
    query: str,
    validated_findings_text: str,
    validation_stats: dict,
    deliverable_format: str = None,
    source: str = "dashboard",
    ground_rules: str = ""
) -> str:
    """Build the prompt for synthesizing VALIDATED subagent findings.

    This version uses pre-validated findings where claims have been fact-checked
    and filtered/annotated based on source verification.

    Args:
        query: The original investigation query
        validated_findings_text: Pre-processed findings text with validation markers
        validation_stats: Dictionary with validation statistics
        deliverable_format: Optional format (HTML, JSON, markdown)
        source: Origin of investigation - "dashboard", "email", or "api"
        ground_rules: Investigation ground rules to include in prompt
    """
    # Include ground rules at the top of the prompt if provided
    ground_rules_section = ""
    if ground_rules:
        ground_rules_section = f"""
=== INVESTIGATION GROUND RULES ===
{ground_rules}
=== END GROUND RULES ===

"""
    # Build validation summary
    total = validation_stats.get("total_claims", 0)
    supported = validation_stats.get("supported_claims", 0)
    unsupported = validation_stats.get("unsupported_claims", 0)
    unverifiable = validation_stats.get("unverifiable_claims", 0)

    validation_note = f"""
## IMPORTANT: Citation Validation Applied

These findings have been fact-checked by independent validator agents:
- **{supported}/{total}** claims verified by cited sources
- **{unsupported}** claims contradicted or unsupported by sources (marked/removed)
- **{unverifiable}** claims could not be verified (source unavailable)

Findings marked with:
- ✅ = Verified by source
- 🔶 = Partially verified
- ⚠️ = Unverified (source inaccessible)
- ❌ = Disputed (source contradicts claim)

ONLY synthesize information that has been verified or partially verified.
Treat unverified claims with appropriate skepticism.
Do NOT include disputed claims in your synthesis.
"""

    # Build format-specific instructions (reuse same logic)
    if deliverable_format:
        format_lower = deliverable_format.lower()
        if "html" in format_lower:
            title_query = query[:50].replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
            format_instruction = f"""
=== CRITICAL: OUTPUT FORMAT REQUIREMENT ===

Your response MUST be a complete HTML document. This is MANDATORY.

REQUIRED FORMAT:
1. Your response MUST start with exactly: <!DOCTYPE html>
2. Your response MUST include: <html>, <head>, <body> tags
3. Your response MUST include <style> block with CSS

=== END FORMAT REQUIREMENT ===
"""
        elif "json" in format_lower:
            format_instruction = """
## Deliverable Format: JSON
Output a well-structured JSON object with all verified findings.
"""
        else:
            format_instruction = f"""
## Deliverable Format: {deliverable_format}
"""
    else:
        if source == "email":
            format_instruction = """
## Output Format

Create a focused research report with these sections:
# Investigation Report
## Summary
## Verified Findings (from fact-checked sources)
## Detailed Analysis
## Sources & References

DO NOT include: "Next Steps", timelines, or mission-style language.
"""
        else:
            format_instruction = """
## Output Format

Create a comprehensive markdown report with:
# Investigation Report
## Executive Summary
## Verified Key Findings
## Detailed Analysis
## Recommendations
## Data Quality Note (mention validation stats)
"""

    return f"""{ground_rules_section}You are synthesizing VALIDATED research findings.

## SCOPE: UNRESTRICTED RESEARCH

You are a GENERAL RESEARCH synthesizer. Produce a complete, useful deliverable
that answers the original query based on VERIFIED information.

## Original Investigation Query
{query}
{validation_note}
## Validated Subagent Findings

{validated_findings_text}

## Your Task

Synthesize these VALIDATED findings into a comprehensive deliverable.
Prioritize verified information. Note confidence levels where relevant.
{format_instruction}
## Important

- Prioritize VERIFIED claims over unverified ones
- Do NOT include disputed/unsupported claims in your synthesis
- If critical information is unverified, note this caveat
- The validation ensures you're working with fact-checked information
"""


# =============================================================================
# HTML FORMAT VALIDATION AND CONVERSION
# =============================================================================

def validate_html_format(response: str) -> tuple:
    """
    Validate that a response is proper HTML format.

    Returns:
        Tuple of (is_valid, list_of_issues)
    """
    import re as regex_module

    issues = []
    response_stripped = response.strip()

    # Check for DOCTYPE
    if not response_stripped.lower().startswith('<!doctype html'):
        issues.append("Missing <!DOCTYPE html> declaration at start")

    # Check for HTML structure
    if '<html' not in response_stripped.lower():
        issues.append("Missing <html> tag")
    if '<head' not in response_stripped.lower():
        issues.append("Missing <head> tag")
    if '<body' not in response_stripped.lower():
        issues.append("Missing <body> tag")

    # Check for markdown contamination (only if outside of code blocks)
    # First, remove any code/pre blocks to avoid false positives
    clean_response = regex_module.sub(r'<code>.*?</code>', '', response_stripped, flags=regex_module.DOTALL)
    clean_response = regex_module.sub(r'<pre>.*?</pre>', '', clean_response, flags=regex_module.DOTALL)

    markdown_patterns = [
        (r'^# ', "Contains markdown header (# )"),
        (r'^## ', "Contains markdown header (## )"),
        (r'\*\*[^*]+\*\*', "Contains markdown bold (**)"),
        (r'^```', "Contains markdown code fence (```)"),
    ]
    for pattern, message in markdown_patterns:
        if regex_module.search(pattern, clean_response, regex_module.MULTILINE):
            issues.append(message)

    return len(issues) == 0, issues


def markdown_to_html(markdown_text: str, query: str) -> str:
    """
    Convert markdown text to HTML as a fallback.

    Args:
        markdown_text: The markdown content to convert
        query: The original investigation query for the title

    Returns:
        Complete HTML document
    """
    import re as regex_module

    content = markdown_text

    # Remove code fences first (may wrap entire response)
    content = regex_module.sub(r'^```html\s*\n?', '', content, flags=regex_module.MULTILINE)
    content = regex_module.sub(r'^```\w*\s*\n?', '', content, flags=regex_module.MULTILINE)
    content = regex_module.sub(r'\n?```$', '', content, flags=regex_module.MULTILINE)

    # Convert headers (order matters - do h3 before h2 before h1)
    content = regex_module.sub(r'^### (.+)$', r'<h3>\1</h3>', content, flags=regex_module.MULTILINE)
    content = regex_module.sub(r'^## (.+)$', r'<h2>\1</h2>', content, flags=regex_module.MULTILINE)
    content = regex_module.sub(r'^# (.+)$', r'<h1>\1</h1>', content, flags=regex_module.MULTILINE)

    # Convert bold and italic
    content = regex_module.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', content)
    content = regex_module.sub(r'\*(.+?)\*', r'<em>\1</em>', content)

    # Convert markdown links [text](url) to HTML anchors
    content = regex_module.sub(
        r'\[([^\]]+)\]\(([^)]+)\)',
        r'<a href="\2">\1</a>',
        content
    )

    # Convert bare URLs to clickable links (http:// or https://)
    # Only match URLs that are not already inside an href
    content = regex_module.sub(
        r'(?<!href=")(?<!">)(https?://[^\s<>\[\]()]+)',
        r'<a href="\1">\1</a>',
        content
    )

    # Convert bullet points to list items
    lines = content.split('\n')
    in_list = False
    converted_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('- ') or stripped.startswith('* '):
            if not in_list:
                converted_lines.append('<ul>')
                in_list = True
            item = stripped[2:]
            converted_lines.append(f'<li>{item}</li>')
        else:
            if in_list:
                converted_lines.append('</ul>')
                in_list = False
            # Wrap non-empty, non-tag lines in paragraphs
            if stripped and not stripped.startswith('<'):
                converted_lines.append(f'<p>{line}</p>')
            else:
                converted_lines.append(line)
    if in_list:
        converted_lines.append('</ul>')
    content = '\n'.join(converted_lines)

    # Escape query for title
    title_query = query[:50].replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')

    # Wrap in HTML template
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Investigation Report: {title_query}...</title>
    <style>
        body {{ font-family: system-ui, -apple-system, sans-serif; max-width: 900px; margin: 0 auto; padding: 2rem; line-height: 1.6; background: #f8f9fa; color: #333; }}
        h1 {{ color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 0.5rem; }}
        h2 {{ color: #34495e; margin-top: 2rem; }}
        h3 {{ color: #7f8c8d; }}
        p {{ margin: 1rem 0; }}
        ul {{ padding-left: 1.5rem; }}
        li {{ margin: 0.5rem 0; }}
        strong {{ color: #2c3e50; }}
    </style>
</head>
<body>
{content}
</body>
</html>'''


# =============================================================================
# PDF GENERATION
# =============================================================================

def generate_pdf_from_markdown(markdown_text: str, query: str) -> bytes:
    """
    Convert markdown report to PDF with clickable hyperlinks.

    Uses weasyprint for high-quality PDF generation with proper CSS styling
    and clickable hyperlinks.

    Args:
        markdown_text: The markdown content of the report
        query: The original investigation query (for title)

    Returns:
        PDF content as bytes
    """
    try:
        from weasyprint import HTML
    except ImportError:
        logger.warning("weasyprint not available, falling back to basic HTML")
        # Return None to signal fallback needed
        return None

    import io

    # Convert markdown to HTML first
    html_content = markdown_to_html(markdown_text, query)

    # Enhance HTML with better PDF-specific styling
    pdf_style = """
    <style>
        @page {
            margin: 2cm;
            size: A4;
            @bottom-center {
                content: counter(page);
                font-size: 10pt;
                color: #666;
            }
        }
        body {
            font-family: 'Helvetica', 'Arial', sans-serif;
            font-size: 11pt;
            line-height: 1.6;
            color: #333;
        }
        h1 {
            color: #2c3e50;
            border-bottom: 2px solid #3498db;
            padding-bottom: 0.3em;
            font-size: 20pt;
            margin-top: 1em;
        }
        h2 {
            color: #34495e;
            margin-top: 1.5em;
            font-size: 16pt;
        }
        h3 {
            color: #5d6d7e;
            margin-top: 1.2em;
            font-size: 13pt;
        }
        a {
            color: #2980b9;
            text-decoration: underline;
        }
        a:hover {
            color: #1a5276;
        }
        .summary-box {
            background: #ecf0f1;
            padding: 1em;
            border-radius: 4px;
            margin: 1em 0;
            border-left: 4px solid #3498db;
        }
        ul, ol {
            padding-left: 1.5em;
        }
        li {
            margin: 0.3em 0;
        }
        code {
            background: #f4f4f4;
            padding: 0.2em 0.4em;
            border-radius: 3px;
            font-family: 'Courier New', monospace;
            font-size: 10pt;
        }
        pre {
            background: #f4f4f4;
            padding: 1em;
            border-radius: 4px;
            overflow-x: auto;
            font-size: 9pt;
        }
        table {
            border-collapse: collapse;
            width: 100%;
            margin: 1em 0;
        }
        th, td {
            border: 1px solid #ddd;
            padding: 8px;
            text-align: left;
        }
        th {
            background: #f5f5f5;
        }
        blockquote {
            border-left: 4px solid #bdc3c7;
            padding-left: 1em;
            margin-left: 0;
            color: #666;
            font-style: italic;
        }
    </style>
    """

    # Inject PDF-specific style into head
    if '<head>' in html_content:
        html_content = html_content.replace('<head>', f'<head>{pdf_style}')
    else:
        # If no head tag, wrap content
        html_content = f'<!DOCTYPE html><html><head>{pdf_style}</head><body>{html_content}</body></html>'

    # Generate PDF
    try:
        pdf_buffer = io.BytesIO()
        HTML(string=html_content).write_pdf(pdf_buffer)
        pdf_bytes = pdf_buffer.getvalue()
        logger.info(f"Generated PDF: {len(pdf_bytes)} bytes")
        return pdf_bytes
    except Exception as e:
        logger.error(f"PDF generation failed: {e}")
        return None


def extract_short_summary(synthesis: str, max_sentences: int = 3) -> str:
    """
    Extract a short, natural summary from the full synthesis.

    Returns 2-3 sentences suitable for an email body.

    Args:
        synthesis: Full synthesis text (markdown or plain text)
        max_sentences: Maximum number of sentences to extract

    Returns:
        Short summary string
    """
    import re

    if not synthesis:
        return "Investigation completed."

    # Remove markdown formatting that doesn't read well in plain text
    clean_text = synthesis

    # Remove headers (# ## ###)
    clean_text = re.sub(r'^#+\s+.*$', '', clean_text, flags=re.MULTILINE)

    # Remove bold/italic markers
    clean_text = re.sub(r'\*\*([^*]+)\*\*', r'\1', clean_text)
    clean_text = re.sub(r'\*([^*]+)\*', r'\1', clean_text)

    # Remove bullet points
    clean_text = re.sub(r'^\s*[-*]\s+', '', clean_text, flags=re.MULTILINE)

    # Remove links but keep text
    clean_text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', clean_text)

    # Try to find an executive summary section
    summary_match = re.search(
        r'(?:Executive Summary|Summary|Overview)[:\s]*\n(.*?)(?:\n##|\n\n\n|\Z)',
        synthesis,
        re.DOTALL | re.IGNORECASE
    )

    if summary_match:
        summary_text = summary_match.group(1).strip()
        # Clean the extracted summary
        summary_text = re.sub(r'^#+\s+.*$', '', summary_text, flags=re.MULTILINE)
        summary_text = re.sub(r'\*\*([^*]+)\*\*', r'\1', summary_text)
        summary_text = re.sub(r'\*([^*]+)\*', r'\1', summary_text)
        summary_text = re.sub(r'^\s*[-*]\s+', '', summary_text, flags=re.MULTILINE)
        summary_text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', summary_text)
    else:
        # Fall back to first few paragraphs
        paragraphs = [p.strip() for p in clean_text.split('\n\n') if p.strip()]
        # Skip empty or header-only paragraphs
        paragraphs = [p for p in paragraphs if len(p) > 20 and not p.startswith('#')]
        summary_text = paragraphs[0] if paragraphs else clean_text[:500]

    # Clean up whitespace
    summary_text = ' '.join(summary_text.split())

    # Extract first N sentences
    sentences = re.split(r'(?<=[.!?])\s+', summary_text)
    sentences = [s.strip() for s in sentences if s.strip()]

    short_summary = ' '.join(sentences[:max_sentences])

    # Truncate if still too long
    if len(short_summary) > 500:
        short_summary = short_summary[:497] + '...'

    return short_summary if short_summary else "Investigation completed."


# =============================================================================
# INVESTIGATION RUNNER
# =============================================================================

class InvestigationRunner:
    """Runs a complete investigation workflow."""

    def __init__(self, config: InvestigationConfig):
        self.config = config
        self.subagent_results: List[SubagentResult] = []
        self.started_at: Optional[str] = None
        self.progress_callback: Optional[Callable[[str], None]] = None
        # Load ground rules at startup
        self.ground_rules = load_investigation_ground_rules()
        # URL metadata extracted from query (GitHub repos, GitLab projects, docs, etc.)
        self.url_metadata: List[Dict[str, Any]] = []

    def run(
        self,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> InvestigationResult:
        """
        Execute the complete investigation.

        Args:
            progress_callback: Optional function called with status updates

        Returns:
            InvestigationResult with all findings
        """
        self.progress_callback = progress_callback
        self.started_at = datetime.now().isoformat()
        start_time = time.time()

        # Create workspace
        self.config.workspace_dir.mkdir(parents=True, exist_ok=True)
        artifacts_dir = self.config.workspace_dir / "artifacts"
        artifacts_dir.mkdir(exist_ok=True)

        # Initialize state - SKIP for email investigations to enable true concurrency
        # Email investigations track state via email_inbox_state.json, not investigation_state.json
        if not self.config.skip_global_state:
            state = load_investigation_state()
            state["current"] = {
                "investigation_id": self.config.investigation_id,
                "query": self.config.query,
                "status": InvestigationStatus.ANALYZING.value,
                "started_at": self.started_at,
                "workspace_dir": str(self.config.workspace_dir),
                "source": self.config.source  # Track origin: "dashboard", "email", or "api"
            }
            save_investigation_state(state)

        try:
            # Step 0: Extract URL metadata (GitHub repos, GitLab projects, docs, etc.)
            # This happens BEFORE the lead agent, so we can inject pre-fetched data
            self._log("Detecting URLs and extracting metadata...")
            self.url_metadata = detect_and_extract_urls(self.config.query)
            if self.url_metadata:
                self._log(f"Extracted metadata for {len(self.url_metadata)} URLs: "
                         f"{', '.join(m.get('type', 'unknown') for m in self.url_metadata)}")

            # Step 1: Lead agent analyzes query and decomposes
            self._log("Starting investigation analysis...")
            if not self.config.skip_global_state:
                update_investigation_status(self.config.investigation_id, InvestigationStatus.ANALYZING)

            research_directions = self._run_lead_agent()
            if not research_directions:
                raise Exception("Lead agent failed to provide research directions")

            # Step 2: Spawn parallel subagents
            self._log(f"Spawning {len(research_directions)} subagents...")
            if not self.config.skip_global_state:
                update_investigation_status(
                    self.config.investigation_id,
                    InvestigationStatus.SPAWNING_SUBAGENTS,
                    {"subagent_count": len(research_directions)}
                )

            # Step 3: Run subagents in parallel
            if not self.config.skip_global_state:
                update_investigation_status(self.config.investigation_id, InvestigationStatus.EXPLORING)
            self.subagent_results = self._run_subagents(research_directions)

            # Step 3.5: Adversarial validation (fact-check citations before synthesis)
            validated_findings = None
            if self.config.enable_validation:
                self._log("Running adversarial validation on findings...")
                if not self.config.skip_global_state:
                    update_investigation_status(self.config.investigation_id, InvestigationStatus.VALIDATING)
                validated_findings = self._validate_findings()

            # Step 4: Synthesize findings
            self._log("Synthesizing findings...")
            if not self.config.skip_global_state:
                update_investigation_status(self.config.investigation_id, InvestigationStatus.SYNTHESIZING)
            synthesis = self._synthesize_findings(validated_findings)

            # Step 5: Write report (with appropriate file extension)
            if self.config.deliverable_format:
                fmt = self.config.deliverable_format.lower()
                if "html" in fmt:
                    report_path = artifacts_dir / "investigation_report.html"
                elif "json" in fmt:
                    report_path = artifacts_dir / "investigation_report.json"
                else:
                    report_path = artifacts_dir / "investigation_report.md"
            else:
                report_path = artifacts_dir / "investigation_report.md"

            report_path.write_text(synthesis)
            self._log(f"Report written to {report_path}")

            # Save findings JSON with validation metadata and URL handler results
            findings_path = artifacts_dir / "findings.json"
            findings_data = {
                "investigation_id": self.config.investigation_id,
                "query": self.config.query,
                "subagent_results": [r.to_dict() for r in self.subagent_results],
                "validation": {
                    "enabled": self.config.enable_validation,
                    "filter_mode": self.config.validation_filter_mode,
                },
                # Include URL handler metadata for rich reports
                "url_metadata": self.url_metadata if self.url_metadata else [],
            }

            # Add validation stats if validation was performed
            if validated_findings:
                findings_data["validation"]["stats"] = validated_findings.to_dict()
                findings_data["validation"]["total_claims"] = validated_findings.total_claims
                findings_data["validation"]["supported"] = validated_findings.supported_claims
                findings_data["validation"]["unsupported"] = validated_findings.unsupported_claims
                findings_data["validation"]["unverifiable"] = validated_findings.unverifiable_claims
                # Include flagged claims for audit trail
                findings_data["validation"]["flagged_claims"] = [
                    {"id": c.id, "text": c.text, "reason": c.flag_reason}
                    for c in validated_findings.claims if c.flagged
                ]

            with open(findings_path, 'w') as f:
                json.dump(findings_data, f, indent=2)

            elapsed = time.time() - start_time
            completed_at = datetime.now().isoformat()

            # Update final state (skip for email investigations)
            if not self.config.skip_global_state:
                update_investigation_status(
                    self.config.investigation_id,
                    InvestigationStatus.COMPLETED,
                    {
                        "completed_at": completed_at,
                        "elapsed_seconds": elapsed,
                        "report_path": str(report_path)
                    }
                )

            self._log(f"Investigation completed in {elapsed:.1f}s")

            # Ingest investigation findings into Knowledge Base
            try:
                self._ingest_to_knowledge_base()
            except Exception as kb_error:
                logger.warning(f"KB ingestion failed (non-fatal): {kb_error}")

            return InvestigationResult(
                investigation_id=self.config.investigation_id,
                query=self.config.query,
                status=InvestigationStatus.COMPLETED,
                subagent_results=self.subagent_results,
                synthesis=synthesis,
                report_path=report_path,
                started_at=self.started_at,
                completed_at=completed_at,
                elapsed_seconds=elapsed,
                validation_stats=validated_findings.to_dict() if validated_findings else None
            )

        except Exception as e:
            logger.error(f"Investigation failed: {e}")
            elapsed = time.time() - start_time

            if not self.config.skip_global_state:
                update_investigation_status(
                    self.config.investigation_id,
                    InvestigationStatus.FAILED,
                    {"error": str(e)}
                )

            return InvestigationResult(
                investigation_id=self.config.investigation_id,
                query=self.config.query,
                status=InvestigationStatus.FAILED,
                subagent_results=self.subagent_results,
                synthesis=None,
                report_path=None,
                started_at=self.started_at,
                completed_at=datetime.now().isoformat(),
                elapsed_seconds=elapsed,
                error=str(e)
            )

    def _log(self, message: str):
        """Log a message and call progress callback if set."""
        logger.info(message)
        if self.progress_callback:
            self.progress_callback(message)

    def _run_lead_agent(self) -> List[Dict[str, str]]:
        """Run the lead agent to decompose the query."""
        prompt = build_lead_agent_prompt(
            self.config.query,
            self.config.max_subagents,
            self.config.deliverable_format,
            self.ground_rules
        )

        # Inject pre-fetched URL metadata if available
        if self.url_metadata:
            metadata_section = format_url_metadata_for_prompt(self.url_metadata)
            if metadata_section:
                # Insert metadata before "## Your Task" section
                if "## Your Task" in prompt:
                    prompt = prompt.replace(
                        "## Your Task",
                        f"{metadata_section}\n\n## Your Task"
                    )
                else:
                    prompt = f"{prompt}\n\n{metadata_section}"

        timeout = int(self.config.timeout_minutes * 60 * 0.3)  # 30% of budget

        response, elapsed = invoke_claude(
            prompt=prompt,
            model=self.config.lead_model,
            timeout=timeout,
            cwd=self.config.workspace_dir
        )

        self._log(f"Lead agent completed in {elapsed:.1f}s")

        # Parse response
        try:
            # Try to extract JSON from response
            import re
            json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(1))
            else:
                # Try parsing entire response
                start = response.find('{')
                end = response.rfind('}') + 1
                if start >= 0 and end > start:
                    data = json.loads(response[start:end])
                else:
                    raise ValueError("No JSON found in response")

            return data.get("research_directions", [])

        except Exception as e:
            logger.error(f"Failed to parse lead agent response: {e}")
            logger.error(f"Response was: {response[:500]}")
            return []

    def _run_subagents(self, research_directions: List[Dict[str, str]]) -> List[SubagentResult]:
        """Run subagents in parallel."""
        results = []

        timeout_per_agent = int(
            self.config.timeout_minutes * 60 * 0.5 / max(1, len(research_directions))
        )
        timeout_per_agent = min(timeout_per_agent, 180)  # Cap at 3 minutes each

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(research_directions)) as executor:
            futures = {}

            for i, direction in enumerate(research_directions):
                focus_area = direction.get("focus_area", f"Area {i+1}")
                base_prompt = direction.get("prompt", "Explore this area")
                research_type = direction.get("research_type", "both")

                subagent_id = f"{self.config.investigation_id}_sub_{i}"
                full_prompt = build_subagent_prompt(
                    focus_area,
                    base_prompt,
                    self.config.query,
                    research_type,
                    self.ground_rules
                )

                future = executor.submit(
                    self._run_single_subagent,
                    subagent_id,
                    focus_area,
                    full_prompt,
                    timeout_per_agent
                )
                futures[future] = (subagent_id, focus_area)

            for future in concurrent.futures.as_completed(futures):
                subagent_id, focus_area = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                    self._log(f"Subagent '{focus_area}' completed")
                except Exception as e:
                    logger.error(f"Subagent {subagent_id} failed: {e}")
                    results.append(SubagentResult(
                        subagent_id=subagent_id,
                        focus_area=focus_area,
                        findings="",
                        elapsed_seconds=0,
                        status="failed",
                        error=str(e)
                    ))

        return results

    def _run_single_subagent(
        self,
        subagent_id: str,
        focus_area: str,
        prompt: str,
        timeout: int
    ) -> SubagentResult:
        """Run a single subagent."""
        start_time = time.time()

        response, _ = invoke_claude(
            prompt=prompt,
            model=self.config.subagent_model,
            timeout=timeout,
            cwd=self.config.workspace_dir
        )

        elapsed = time.time() - start_time

        if response.startswith("ERROR:"):
            return SubagentResult(
                subagent_id=subagent_id,
                focus_area=focus_area,
                findings="",
                elapsed_seconds=elapsed,
                status="failed",
                error=response
            )

        return SubagentResult(
            subagent_id=subagent_id,
            focus_area=focus_area,
            findings=response,
            elapsed_seconds=elapsed,
            status="completed"
        )

    def _validate_findings(self):
        """
        Run adversarial validation on subagent findings.

        Spawns blind validator agents to fact-check cited sources.
        Returns ValidatedFindings object with filtered/annotated claims.
        """
        try:
            # Import the validator (deferred to avoid circular imports)
            import sys
            from atlasforge_config import WORKSPACE_DIR
            validator_path = str(WORKSPACE_DIR / "investigation_validator")
            if validator_path not in sys.path:
                sys.path.insert(0, validator_path)

            from orchestrator import ValidationOrchestrator
            from models import ValidationConfig, FilterMode

            # Create validation config from investigation config
            filter_mode_map = {
                "strict": FilterMode.STRICT,
                "annotated": FilterMode.ANNOTATED,
                "balanced": FilterMode.BALANCED,
            }
            filter_mode = filter_mode_map.get(
                self.config.validation_filter_mode,
                FilterMode.BALANCED
            )

            val_config = ValidationConfig(
                enabled=True,
                model="haiku",  # Use fast model for validators
                filter_mode=filter_mode,
                parallel_validators=10,
            )

            # Run validation pipeline
            orchestrator = ValidationOrchestrator(val_config)
            validated = orchestrator.validate(
                self.subagent_results,
                progress_callback=self.progress_callback
            )

            # Log stats
            stats = orchestrator.get_stats()
            self._log(f"Validation stats: {stats.supported}/{stats.total_claims} claims supported")

            return validated

        except Exception as e:
            logger.warning(f"Validation failed (non-fatal): {e}")
            self._log(f"Validation error: {e} - proceeding without validation")
            return None

    def _synthesize_findings(self, validated_findings=None) -> str:
        """Synthesize all subagent findings into a report with format validation."""
        MAX_RETRIES = 2
        previous_issues = ""

        # Generate executive summaries from URL metadata if available
        executive_summaries = ""
        if self.url_metadata:
            # Get raw findings text for context
            raw_findings = "\n\n".join([
                r.findings for r in self.subagent_results
                if r.status == "completed" and r.findings
            ])
            executive_summaries = format_url_executive_summaries(self.url_metadata, raw_findings)

        for attempt in range(MAX_RETRIES + 1):
            # Use validated findings text if available, otherwise use raw findings
            if validated_findings and validated_findings.filtered_findings_text:
                prompt = build_synthesis_prompt_validated(
                    self.config.query,
                    validated_findings.filtered_findings_text,
                    validated_findings.to_dict(),
                    self.config.deliverable_format,
                    self.config.source,
                    self.ground_rules
                )
            else:
                prompt = build_synthesis_prompt(
                    self.config.query,
                    self.subagent_results,
                    self.config.deliverable_format,
                    self.config.source,  # Pass source to control mission-style language
                    self.ground_rules
                )

            # Inject executive summaries from URL handlers
            if executive_summaries:
                prompt = f"""{prompt}

## Pre-Generated Executive Summaries (from URL metadata)

Include the following handler-generated summaries at the TOP of your report,
BEFORE your own synthesis. These provide structured metadata about the URLs
analyzed in this investigation:

{executive_summaries}

Incorporate these summaries, then add your deeper analysis below them."""

            # Add retry context if not first attempt
            if attempt > 0:
                prompt = f"""CRITICAL: Your previous response was REJECTED because it was not valid HTML.

You MUST output raw HTML starting with <!DOCTYPE html>. No markdown. No code fences.

Previous attempt errors: {previous_issues}

{prompt}"""

            # Increase timeout for synthesis - needs more time for complex HTML output
            # Use 40% of budget instead of 20%, minimum 120 seconds
            timeout = max(120, int(self.config.timeout_minutes * 60 * 0.40))

            response, elapsed = invoke_claude(
                prompt=prompt,
                model=self.config.lead_model,
                timeout=timeout,
                cwd=self.config.workspace_dir
            )

            self._log(f"Synthesis attempt {attempt + 1} completed in {elapsed:.1f}s")

            if response.startswith("ERROR:"):
                # API error or timeout
                self._log(f"Synthesis error: {response}")
                if "html" in (self.config.deliverable_format or "").lower():
                    # For HTML format, try to convert raw findings to HTML
                    self._log("Creating HTML fallback from raw findings")
                    raw_report = self._create_fallback_report()
                    return markdown_to_html(raw_report, self.config.query)
                else:
                    # For other formats, use markdown fallback
                    return self._create_fallback_report()

            # Validate HTML format if HTML was requested
            if self.config.deliverable_format and "html" in self.config.deliverable_format.lower():
                is_valid, issues = validate_html_format(response)
                if is_valid:
                    self._log("HTML validation passed")
                    return response
                else:
                    self._log(f"HTML validation failed (attempt {attempt + 1}): {issues}")
                    previous_issues = "; ".join(issues)
                    if attempt == MAX_RETRIES:
                        # Final attempt failed - convert markdown to HTML
                        self._log("Converting markdown to HTML as fallback")
                        return markdown_to_html(response, self.config.query)
            else:
                # Non-HTML format - return as-is
                return response

        return response

    def _create_fallback_report(self) -> str:
        """Create basic report from raw findings when API errors occur."""
        report = f"# Investigation Report\n\n"
        report += f"## Query\n{self.config.query}\n\n"
        report += "## Raw Findings\n\n"
        for r in self.subagent_results:
            if r.status == "completed":
                report += f"### {r.focus_area}\n{r.findings}\n\n"
        return report

    def _ingest_to_knowledge_base(self):
        """
        Ingest investigation findings into the Knowledge Base.

        This extracts learnings from the investigation and stores them
        in the KB for cross-referencing with mission learnings.

        For email investigations: Ingest to KB but do NOT generate recommendations.
        Email investigations are standalone research, not mission proposals.
        """
        try:
            from mission_knowledge_base import get_knowledge_base

            kb = get_knowledge_base()
            result = kb.ingest_investigation(self.config.workspace_dir)

            if result.get("status") == "success":
                learnings_count = result.get("learnings_extracted", 0)
                self._log(f"Ingested {learnings_count} learnings into Knowledge Base")

                # Generate recommendations ONLY for non-email investigations
                # Email investigations are standalone research, not mission proposals
                if learnings_count > 0 and self.config.source != "email":
                    try:
                        from mission_recommendations import get_recommendation_engine
                        engine = get_recommendation_engine()
                        recommendations = engine.generate_from_investigation(
                            self.config.investigation_id
                        )
                        if recommendations:
                            self._log(f"Generated {len(recommendations)} mission recommendations")
                    except Exception as rec_error:
                        logger.warning(f"Recommendation generation failed (non-fatal): {rec_error}")
                elif self.config.source == "email":
                    self._log("Skipping recommendation generation for email investigation (findings retained in KB)")
            else:
                logger.warning(f"KB ingestion returned non-success: {result}")

        except ImportError:
            logger.warning("Knowledge base module not available for ingestion")
        except Exception as e:
            logger.error(f"Failed to ingest investigation to KB: {e}")
            raise


# =============================================================================
# PUBLIC API
# =============================================================================

def run_investigation(
    query: str,
    max_subagents: int = 5,
    timeout_minutes: int = 10,
    progress_callback: Optional[Callable[[str], None]] = None,
    deliverable_format: Optional[str] = None
) -> InvestigationResult:
    """
    Run a complete investigation.

    This is the main entry point for starting an investigation.
    The investigation engine can research ANY topic - not just software.

    Args:
        query: The investigation query/topic (can be ANY domain: gaming, science, etc.)
        max_subagents: Maximum number of parallel subagents (default 5)
        timeout_minutes: Total timeout in minutes (default 10)
        progress_callback: Optional callback for progress updates
        deliverable_format: Optional format for output ("HTML", "JSON", "markdown", etc.)

    Returns:
        InvestigationResult with all findings

    Examples:
        # Software investigation
        run_investigation("How does the authentication module work?")

        # Gaming investigation
        run_investigation(
            "Best Destiny 2 Solar Warlock grenade build",
            deliverable_format="HTML"
        )

        # Science investigation
        run_investigation("Explain quantum entanglement for beginners")

        # General research
        run_investigation(
            "Compare electric vs gas vehicles for 2024",
            deliverable_format="markdown"
        )
    """
    config = InvestigationConfig(
        query=query,
        max_subagents=max_subagents,
        timeout_minutes=timeout_minutes,
        deliverable_format=deliverable_format
    )

    runner = InvestigationRunner(config)
    return runner.run(progress_callback=progress_callback)


def get_investigation_status(investigation_id: Optional[str] = None) -> dict:
    """
    Get the status of an investigation.

    Args:
        investigation_id: Optional specific investigation ID. If None, returns current.

    Returns:
        Status dict or None if not found
    """
    state = load_investigation_state()

    if investigation_id is None:
        return state.get("current")

    # Check current
    if state.get("current") and state["current"].get("investigation_id") == investigation_id:
        return state["current"]

    # Check history
    for inv in state.get("history", []):
        if inv.get("investigation_id") == investigation_id:
            return inv

    return None


def stop_investigation(investigation_id: str) -> bool:
    """
    Request to stop an ongoing investigation.

    Note: This only updates state - the running processes may continue
    until they check the state or hit timeout.

    Returns:
        True if investigation was found and marked for stopping
    """
    state = load_investigation_state()

    if state.get("current") and state["current"].get("investigation_id") == investigation_id:
        state["current"]["status"] = InvestigationStatus.FAILED.value
        state["current"]["error"] = "Stopped by user"
        state["current"]["completed_at"] = datetime.now().isoformat()

        # Move to history
        state.setdefault("history", []).append(state["current"])
        state["current"] = None

        save_investigation_state(state)
        return True

    return False


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
    else:
        query = "What are the key components of this codebase and how do they interact?"

    print("=" * 60)
    print("Investigation Engine - Test Run")
    print("=" * 60)
    print(f"Query: {query}")
    print("-" * 60)

    def progress(msg):
        print(f"  >> {msg}")

    result = run_investigation(query, max_subagents=3, timeout_minutes=5, progress_callback=progress)

    print("-" * 60)
    print(f"Status: {result.status.value}")
    print(f"Elapsed: {result.elapsed_seconds:.1f}s")
    if result.report_path:
        print(f"Report: {result.report_path}")
    if result.error:
        print(f"Error: {result.error}")

    print("=" * 60)
