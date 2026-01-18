#!/usr/bin/env python3
"""
Project Name Resolver Module

Intelligently extracts/generates a project name from a mission's problem statement.
Uses a multi-strategy approach:
1. User-specified name (if provided)
2. Quoted string extraction ("Project X")
3. PascalCase word extraction (WindowsAtlasForge)
4. "Project X" pattern matching
5. snake_case extraction (emotion_model)
6. AtlasForge-related keyword detection
7. Fallback to mission ID prefix

This enables workspace deduplication across missions working on the same project.
"""

import re
from typing import List, Optional

# Common words to filter out from extracted names
COMMON_WORDS = {
    'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
    'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'been',
    'be', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
    'could', 'should', 'may', 'might', 'must', 'shall', 'can', 'need',
    'this', 'that', 'these', 'those', 'it', 'its', 'my', 'your', 'our',
    'their', 'his', 'her', 'we', 'they', 'you', 'i', 'me', 'us', 'them',
    'what', 'which', 'who', 'whom', 'whose', 'when', 'where', 'why', 'how',
    'all', 'each', 'every', 'both', 'few', 'more', 'most', 'other', 'some',
    'such', 'no', 'not', 'only', 'same', 'so', 'than', 'too', 'very',
    'just', 'also', 'now', 'here', 'there', 'then', 'once', 'if', 'unless',
    'create', 'build', 'make', 'implement', 'add', 'fix', 'update', 'modify',
    'change', 'remove', 'delete', 'write', 'read', 'get', 'set', 'find',
    'new', 'old', 'bug', 'feature', 'issue', 'task', 'mission', 'work',
    'help', 'please', 'want', 'need', 'using', 'use', 'about', 'into'
}

# PascalCase words that are too generic to use as project names
GENERIC_PASCAL = {
    'Error', 'Exception', 'Handler', 'Manager', 'Service', 'Controller',
    'Module', 'Component', 'Helper', 'Utility', 'Utils', 'Config', 'Settings',
    'Data', 'Model', 'View', 'Test', 'Tests', 'File', 'Files', 'Class',
    'Function', 'Method', 'Object', 'Type', 'Interface', 'Abstract'
}

# Keywords that indicate AtlasForge-related work
ATLASFORGE_KEYWORDS = {
    'atlasforge', 'dashboard', 'mission', 'af_engine', 'claude',
    'r&d', 'rd_engine', 'workspace', 'analytics', 'exploration',
    'fingerprint', 'drift', 'stage', 'planning', 'building', 'testing'
}


def sanitize_project_name(name: str) -> str:
    """
    Sanitize a string to be a valid project/directory name.

    - Removes special characters except underscores and hyphens
    - Strips leading/trailing whitespace
    - Replaces spaces with nothing (camelCase preservation)
    - Limits length to 50 characters
    """
    if not name:
        return ""

    # Remove quotes
    name = name.strip('"\'')

    # Replace multiple spaces with single space
    name = re.sub(r'\s+', ' ', name.strip())

    # Remove special characters except alphanumeric, underscore, hyphen, space
    name = re.sub(r'[^\w\s-]', '', name)

    # If has spaces, convert to PascalCase
    if ' ' in name:
        name = ''.join(word.capitalize() for word in name.split())

    # Ensure it doesn't start with a number
    if name and name[0].isdigit():
        name = '_' + name

    # Limit length
    return name[:50]


def extract_quoted_names(text: str) -> List[str]:
    """
    Extract quoted strings that might be project names.
    Matches "Project Name" or 'Project Name' patterns.
    """
    # Match double or single quoted strings
    patterns = [
        r'"([^"]{2,40})"',  # Double quotes
        r"'([^']{2,40})'"   # Single quotes
    ]

    names = []
    for pattern in patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            # Filter out common phrases
            words = match.lower().split()
            if len(words) <= 5 and not all(w in COMMON_WORDS for w in words):
                names.append(match)

    return names


def extract_pascal_case(text: str) -> List[str]:
    """
    Extract PascalCase words that look like project names.
    Examples: WindowsAtlasForge, ProjectPrism, EmotionModel
    """
    # Match PascalCase words (at least 2 capital letters or camelCase)
    pattern = r'\b([A-Z][a-z]+(?:[A-Z][a-z]*)+)\b'
    matches = re.findall(pattern, text)

    # Filter out generic terms
    return [m for m in matches if m not in GENERIC_PASCAL and len(m) >= 6]


def extract_snake_case(text: str) -> List[str]:
    """
    Extract snake_case identifiers that might be project names.
    Examples: emotion_model, user_auth_service
    """
    pattern = r'\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b'
    matches = re.findall(pattern, text)

    # Filter out common patterns
    filtered = []
    for match in matches:
        parts = match.split('_')
        # Must have at least 2 parts and not be all common words
        if len(parts) >= 2 and not all(p in COMMON_WORDS for p in parts):
            filtered.append(match)

    return filtered


def extract_project_patterns(text: str) -> List[str]:
    """
    Extract names following "Project X" or "project X" patterns.
    """
    patterns = [
        r'[Pp]roject\s+([A-Z][a-zA-Z0-9]*)',  # Project Athena
        r'[Pp]roject\s+"([^"]+)"',             # Project "Athena"
        r'[Pp]roject\s+\'([^\']+)\'',          # Project 'Athena'
        r'[Pp]roject:\s*([A-Z][a-zA-Z0-9]*)',  # Project: Athena
    ]

    names = []
    for pattern in patterns:
        matches = re.findall(pattern, text)
        names.extend(matches)

    return names


def check_atlasforge_related(text: str) -> bool:
    """
    Check if the text is related to AtlasForge development.
    """
    text_lower = text.lower()
    return any(kw in text_lower for kw in ATLASFORGE_KEYWORDS)


def resolve_project_name(
    problem_statement: str,
    mission_id: str,
    user_specified: Optional[str] = None
) -> str:
    """
    Resolve a project name from a problem statement using multiple strategies.

    Args:
        problem_statement: The mission's problem statement text
        mission_id: The mission UUID (used for fallback)
        user_specified: Optional user-provided project name

    Returns:
        A sanitized project name suitable for directory naming

    Strategy order:
    1. User-specified name (if provided)
    2. Quoted strings in the problem statement
    3. PascalCase words
    4. "Project X" pattern
    5. snake_case identifiers
    6. AtlasForge keyword detection -> "AtlasForge"
    7. Fallback to "project_<mission_id[:8]>"
    """
    # Strategy 1: User-specified name
    if user_specified:
        sanitized = sanitize_project_name(user_specified)
        if sanitized:
            return sanitized

    # Strategy 2: Quoted names
    quoted = extract_quoted_names(problem_statement)
    for name in quoted:
        sanitized = sanitize_project_name(name)
        if sanitized and len(sanitized) >= 3:
            return sanitized

    # Strategy 3: PascalCase extraction
    pascal = extract_pascal_case(problem_statement)
    if pascal:
        # Prefer longer names as they're more specific
        pascal.sort(key=len, reverse=True)
        return pascal[0]

    # Strategy 4: "Project X" pattern
    project_patterns = extract_project_patterns(problem_statement)
    for name in project_patterns:
        sanitized = sanitize_project_name(name)
        if sanitized and len(sanitized) >= 3:
            # Prepend "Project" if not already there
            if not sanitized.startswith('Project'):
                sanitized = 'Project' + sanitized
            return sanitized

    # Strategy 5: snake_case identifiers
    snake = extract_snake_case(problem_statement)
    if snake:
        # Prefer longer names
        snake.sort(key=len, reverse=True)
        return snake[0]

    # Strategy 6: AtlasForge-related work
    if check_atlasforge_related(problem_statement):
        return "AtlasForge"

    # Strategy 7: Fallback to mission ID prefix
    mid_short = mission_id.replace('mission_', '')[:8]
    return f"project_{mid_short}"


def get_existing_projects() -> List[str]:
    """
    Get list of existing project directories in the workspace.
    Useful for suggesting existing projects when creating new missions.
    """
    from atlasforge_config import WORKSPACE_DIR

    projects = []
    if WORKSPACE_DIR.exists():
        for item in WORKSPACE_DIR.iterdir():
            if item.is_dir() and not item.name.startswith('.'):
                # Exclude standard workspace subdirs
                if item.name not in {'artifacts', 'research', 'tests'}:
                    projects.append(item.name)

    return sorted(projects)


def suggest_project_name(problem_statement: str) -> dict:
    """
    Suggest a project name and return debugging info.
    Useful for the dashboard to show suggested names before confirmation.

    Returns:
        dict with:
        - suggested_name: The resolved project name
        - strategies_tried: List of strategies and what they found
        - existing_projects: List of existing projects that might match
    """
    strategies = []

    # Try each strategy and log results
    quoted = extract_quoted_names(problem_statement)
    strategies.append({"strategy": "quoted_names", "found": quoted})

    pascal = extract_pascal_case(problem_statement)
    strategies.append({"strategy": "pascal_case", "found": pascal})

    patterns = extract_project_patterns(problem_statement)
    strategies.append({"strategy": "project_patterns", "found": patterns})

    snake = extract_snake_case(problem_statement)
    strategies.append({"strategy": "snake_case", "found": snake})

    atlasforge_related = check_atlasforge_related(problem_statement)
    strategies.append({"strategy": "atlasforge_keywords", "found": atlasforge_related})

    # Get final suggestion (using a dummy mission_id)
    suggested = resolve_project_name(problem_statement, "mission_00000000")

    # Check existing projects
    try:
        existing = get_existing_projects()
    except Exception:
        existing = []

    return {
        "suggested_name": suggested,
        "strategies_tried": strategies,
        "existing_projects": existing
    }


# Self-test when run directly
if __name__ == "__main__":
    test_cases = [
        # PascalCase extraction - compound words in text
        ("Build WindowsAtlasForge with cross-platform support", "WindowsAtlasForge"),
        ("Refactor the AtlasForge codebase", "AtlasForge"),
        # Project pattern
        ("Create emotion recognition model for Project Prism", "ProjectPrism"),
        ("Build Project Athena's core features", "ProjectAthena"),
        # Quoted names
        ("Implement user authentication for 'MyApp'", "MyApp"),
        # snake_case extraction
        ("Work on the emotion_model training pipeline", "emotion_model"),
        # snake_case takes priority over keywords (specific file = project context)
        ("Fix bug in dashboard_v2.py", "dashboard_v2"),
        # Mission ID fallback
        ("Random task with no clear project", "project_"),
    ]

    print("Project Name Resolver Self-Test")
    print("=" * 60)

    passed = 0
    for stmt, expected_prefix in test_cases:
        result = resolve_project_name(stmt, "mission_test1234")
        match = result.startswith(expected_prefix) or result == expected_prefix
        status = "PASS" if match else "FAIL"
        if match:
            passed += 1
        print(f"\n{status}: '{stmt[:50]}{'...' if len(stmt) > 50 else ''}'")
        print(f"  -> {result} (expected: {expected_prefix})")

    print("\n" + "=" * 60)
    print(f"Results: {passed}/{len(test_cases)} tests passed")

    print("\nSuggestion API Test:")
    suggestion = suggest_project_name("Build WindowsAtlasForge with cross-platform support")
    print(f"  Suggested: {suggestion['suggested_name']}")
    print(f"  Strategies: {len(suggestion['strategies_tried'])}")
