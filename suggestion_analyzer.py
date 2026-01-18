#!/usr/bin/env python3
"""
Intelligent Mission Suggestion Analyzer

Provides:
1. Auto-tagging of suggestions based on content analysis
2. Priority scoring algorithm with weighted factors
3. Health indicators (stale, orphaned, needs review)
4. Similarity-based merge suggestions

Usage:
    from suggestion_analyzer import SuggestionAnalyzer

    analyzer = SuggestionAnalyzer()
    result = analyzer.analyze_all()  # Returns prioritized, tagged, health-checked list
"""

import json
import re
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple, Set
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

# Import paths from centralized config
from atlasforge_config import BASE_DIR, STATE_DIR, MISSIONS_DIR

# Paths
RECOMMENDATIONS_PATH = STATE_DIR / "recommendations.json"
MISSION_PATH = STATE_DIR / "mission.json"
MISSION_LOGS_DIR = MISSIONS_DIR / "mission_logs"

# SQLite storage backend (imported lazily to avoid circular imports)
_storage_backend = None

def _get_storage():
    """Get the SQLite storage backend (lazy import)."""
    global _storage_backend
    if _storage_backend is None:
        try:
            from suggestion_storage import get_storage
            _storage_backend = get_storage()
        except ImportError:
            _storage_backend = None
    return _storage_backend


# =============================================================================
# TAG PATTERNS
# =============================================================================

TAG_PATTERNS = {
    'refactor': [
        'refactor', 'reorganize', 'restructure', 'cleanup', 'clean up',
        'simplify', 'consolidate', 'modularize', 'decouple', 'extract',
        'rename', 'split', 'merge files', 'rewrite'
    ],
    'feature': [
        'add', 'implement', 'create', 'build', 'new feature', 'extend',
        'enhance', 'introduce', 'develop', 'enable', 'support for',
        'capability', 'functionality'
    ],
    'bugfix': [
        'fix', 'bug', 'repair', 'resolve', 'patch', 'correct', 'issue',
        'error', 'broken', 'failing', 'crash', 'regression', 'hotfix',
        'wrong', 'incorrect'
    ],
    'infrastructure': [
        'infrastructure', 'deployment', 'ci/cd', 'ci cd', 'docker', 'testing',
        'monitoring', 'database', 'api', 'backend', 'pipeline', 'devops',
        'logging', 'metrics', 'alerting', 'automation', 'script'
    ],
    'documentation': [
        'document', 'docs', 'readme', 'comment', 'explain', 'guide',
        'tutorial', 'reference', 'api docs', 'changelog'
    ],
    'performance': [
        'performance', 'speed', 'optimize', 'cache', 'latency', 'memory',
        'fast', 'efficient', 'bottleneck', 'profil', 'benchmark', 'gpu'
    ],
    'security': [
        'security', 'authentication', 'authorization', 'vulnerability',
        'encryption', 'auth', 'permission', 'access control', 'secure',
        'token', 'credential', 'sanitize', 'validate input'
    ],
    'ui': [
        'ui', 'frontend', 'dashboard', 'modal', 'widget', 'display',
        'visual', 'ux', 'user interface', 'button', 'form', 'layout',
        'css', 'styling', 'responsive', 'mobile'
    ]
}


# =============================================================================
# AUTO TAGGER
# =============================================================================

class AutoTagger:
    """Classifies suggestions into tags based on keyword patterns."""

    def __init__(self, patterns: Dict[str, List[str]] = None):
        self.patterns = patterns or TAG_PATTERNS
        # Compile regex patterns for efficiency
        self._compiled = {}
        for tag, keywords in self.patterns.items():
            # Create pattern that matches any keyword (word boundary aware)
            pattern = r'\b(' + '|'.join(re.escape(kw) for kw in keywords) + r')\b'
            self._compiled[tag] = re.compile(pattern, re.IGNORECASE)

    def classify(self, text: str) -> List[str]:
        """
        Classify text into matching tags.

        Args:
            text: The text to analyze

        Returns:
            List of matching tag names, sorted by match count
        """
        if not text:
            return []

        tag_scores = {}
        for tag, pattern in self._compiled.items():
            matches = pattern.findall(text)
            if matches:
                tag_scores[tag] = len(matches)

        # Sort by match count descending, return tag names
        sorted_tags = sorted(tag_scores.items(), key=lambda x: x[1], reverse=True)
        return [tag for tag, _ in sorted_tags]

    def tag_suggestion(self, suggestion: Dict[str, Any]) -> Dict[str, Any]:
        """
        Add auto_tags field to a suggestion.

        Args:
            suggestion: The suggestion dict

        Returns:
            Modified suggestion with auto_tags field
        """
        # Build text corpus from title + description + rationale
        text = ' '.join([
            suggestion.get('mission_title', ''),
            suggestion.get('mission_description', ''),
            suggestion.get('rationale', '')
        ])

        suggestion['auto_tags'] = self.classify(text)
        return suggestion

    def tag_all(self, suggestions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Tag all suggestions in a list."""
        return [self.tag_suggestion(s) for s in suggestions]


# =============================================================================
# PRIORITIZER
# =============================================================================

class Prioritizer:
    """Calculates priority scores for suggestions based on multiple factors."""

    def __init__(self):
        # Weight configuration
        self.weights = {
            'age': 20,           # 0-20 points
            'importance': 30,    # 0-30 points
            'alignment': 30,     # 0-30 points
            'freshness': 20      # 0-20 points
        }

    def calculate_age_score(self, created_at: str) -> float:
        """
        Calculate age score. Newer items get a slight boost.

        Score decreases as item gets older:
        - 0-7 days: 20 points (full)
        - 7-30 days: 15-20 points (slight decay)
        - 30-90 days: 5-15 points (moderate decay)
        - 90+ days: 0-5 points (low priority)
        """
        if not created_at:
            return 10  # Default middle score

        try:
            created = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            age_days = (datetime.now(created.tzinfo) - created).days if created.tzinfo else \
                       (datetime.now() - created).days
        except (ValueError, TypeError):
            return 10

        if age_days <= 7:
            return self.weights['age']  # Full points for fresh items
        elif age_days <= 30:
            # Linear decay from 20 to 15
            return self.weights['age'] - ((age_days - 7) / 23) * 5
        elif age_days <= 90:
            # Linear decay from 15 to 5
            return 15 - ((age_days - 30) / 60) * 10
        else:
            # Very old items get minimal points
            return max(0, 5 - (age_days - 90) / 30)

    def calculate_importance_score(self, text: str, cycles: int) -> float:
        """
        Calculate semantic importance based on text complexity and scope.

        Factors:
        - Cycle count suggests complexity (more cycles = more important)
        - Keyword indicators of scope (comprehensive, complete, system-wide)
        - Length of description suggests detail/importance
        """
        score = 0
        max_score = self.weights['importance']

        # Cycle count factor (0-10 points)
        # 1 cycle = 2pts, 2 = 4pts, 3 = 6pts, 5+ = 10pts
        cycle_score = min(10, cycles * 2)
        score += cycle_score

        # Scope indicators (0-10 points)
        scope_keywords = [
            'comprehensive', 'complete', 'system-wide', 'full', 'entire',
            'production', 'critical', 'essential', 'core', 'primary'
        ]
        text_lower = text.lower()
        scope_matches = sum(1 for kw in scope_keywords if kw in text_lower)
        score += min(10, scope_matches * 2)

        # Description length factor (0-10 points)
        # Longer descriptions usually indicate more thought/importance
        word_count = len(text.split())
        if word_count >= 200:
            score += 10
        elif word_count >= 100:
            score += 7
        elif word_count >= 50:
            score += 4
        else:
            score += 2

        return min(max_score, score)

    def calculate_alignment_score(
        self,
        text: str,
        recent_missions: List[Dict[str, Any]]
    ) -> float:
        """
        Calculate alignment with recent work.

        Higher score if the suggestion relates to recently completed work.
        Uses simple keyword overlap as proxy for alignment.
        """
        max_score = self.weights['alignment']

        if not recent_missions:
            return max_score / 2  # Default to middle score if no recent missions

        # Extract keywords from suggestion
        suggestion_words = set(text.lower().split())

        # Extract keywords from recent missions
        recent_words = set()
        for mission in recent_missions[:5]:  # Last 5 missions
            mission_text = ' '.join([
                mission.get('original_mission', ''),
                mission.get('problem_statement', '')
            ])
            recent_words.update(mission_text.lower().split())

        # Calculate overlap
        if not recent_words:
            return max_score / 2

        # Filter out common stop words
        stop_words = {'the', 'a', 'an', 'is', 'are', 'and', 'or', 'to', 'for', 'of', 'in', 'on', 'with'}
        suggestion_words -= stop_words
        recent_words -= stop_words

        overlap = len(suggestion_words & recent_words)
        overlap_ratio = overlap / max(len(suggestion_words), 1)

        # Scale to max score
        return min(max_score, overlap_ratio * max_score * 2)  # x2 because overlap is usually small

    def calculate_freshness_score(self, suggestion: Dict[str, Any]) -> float:
        """
        Calculate freshness based on last modification.

        Recently edited items get a boost (user showed interest).
        """
        max_score = self.weights['freshness']

        last_modified = suggestion.get('last_edited_at') or suggestion.get('created_at')
        if not last_modified:
            return max_score / 2

        try:
            modified = datetime.fromisoformat(last_modified.replace('Z', '+00:00'))
            days_since = (datetime.now(modified.tzinfo) - modified).days if modified.tzinfo else \
                         (datetime.now() - modified).days
        except (ValueError, TypeError):
            return max_score / 2

        # Recent modifications get full score, decays over 30 days
        if days_since <= 1:
            return max_score
        elif days_since <= 7:
            return max_score * 0.9
        elif days_since <= 14:
            return max_score * 0.7
        elif days_since <= 30:
            return max_score * 0.5
        else:
            return max_score * 0.2

    def get_priority_score(
        self,
        suggestion: Dict[str, Any],
        recent_missions: List[Dict[str, Any]] = None
    ) -> float:
        """
        Calculate total priority score (0-100).

        Args:
            suggestion: The suggestion dict
            recent_missions: List of recent completed missions for alignment

        Returns:
            Priority score from 0 to 100
        """
        text = ' '.join([
            suggestion.get('mission_title', ''),
            suggestion.get('mission_description', ''),
            suggestion.get('rationale', '')
        ])
        cycles = suggestion.get('suggested_cycles', 3)

        age_score = self.calculate_age_score(suggestion.get('created_at'))
        importance_score = self.calculate_importance_score(text, cycles)
        alignment_score = self.calculate_alignment_score(text, recent_missions or [])
        freshness_score = self.calculate_freshness_score(suggestion)

        total = age_score + importance_score + alignment_score + freshness_score
        return round(total, 1)


# =============================================================================
# HEALTH ANALYZER
# =============================================================================

class HealthAnalyzer:
    """Analyzes suggestion health status."""

    def __init__(self, stale_threshold_days: int = 30):
        self.stale_threshold_days = stale_threshold_days

    def is_stale(self, suggestion: Dict[str, Any]) -> bool:
        """
        Check if suggestion is stale (old and never modified).

        Returns True if:
        - Created more than threshold_days ago
        - Never edited since creation
        """
        created_at = suggestion.get('created_at')
        last_edited = suggestion.get('last_edited_at')

        if not created_at:
            return False

        try:
            created = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            age_days = (datetime.now(created.tzinfo) - created).days if created.tzinfo else \
                       (datetime.now() - created).days
        except (ValueError, TypeError):
            return False

        # Stale if old AND never edited
        is_old = age_days >= self.stale_threshold_days
        never_edited = last_edited is None

        return is_old and never_edited

    def is_orphaned(
        self,
        suggestion: Dict[str, Any],
        archived_missions: List[Dict[str, Any]],
        threshold: float = 0.7
    ) -> bool:
        """
        Check if suggestion is orphaned (too similar to completed work).

        Returns True if suggestion is very similar to an archived mission,
        suggesting the work may already be done.
        """
        if not archived_missions:
            return False

        suggestion_text = ' '.join([
            suggestion.get('mission_title', ''),
            suggestion.get('mission_description', '')
        ]).lower()

        suggestion_words = set(suggestion_text.split())

        for mission in archived_missions:
            mission_text = ' '.join([
                mission.get('original_mission', ''),
                str(mission.get('problem_statement', ''))
            ]).lower()
            mission_words = set(mission_text.split())

            # Calculate Jaccard similarity
            if not suggestion_words or not mission_words:
                continue

            intersection = len(suggestion_words & mission_words)
            union = len(suggestion_words | mission_words)
            similarity = intersection / union if union > 0 else 0

            if similarity >= threshold:
                return True

        return False

    def get_merge_candidates(
        self,
        suggestion: Dict[str, Any],
        all_suggestions: List[Dict[str, Any]],
        threshold: float = 0.5
    ) -> List[str]:
        """
        Find suggestions that are similar enough to consider merging.

        Returns list of suggestion IDs that are similar to this one.
        """
        candidates = []

        suggestion_id = suggestion.get('id')
        suggestion_text = ' '.join([
            suggestion.get('mission_title', ''),
            suggestion.get('mission_description', '')
        ]).lower()
        suggestion_words = set(suggestion_text.split())

        for other in all_suggestions:
            if other.get('id') == suggestion_id:
                continue

            other_text = ' '.join([
                other.get('mission_title', ''),
                other.get('mission_description', '')
            ]).lower()
            other_words = set(other_text.split())

            if not suggestion_words or not other_words:
                continue

            intersection = len(suggestion_words & other_words)
            union = len(suggestion_words | other_words)
            similarity = intersection / union if union > 0 else 0

            if similarity >= threshold:
                candidates.append(other.get('id'))

        return candidates

    def get_health_status(
        self,
        suggestion: Dict[str, Any],
        all_suggestions: List[Dict[str, Any]] = None,
        archived_missions: List[Dict[str, Any]] = None
    ) -> str:
        """
        Get overall health status of a suggestion.

        Returns one of:
        - 'healthy': No issues
        - 'stale': Old and untouched
        - 'orphaned': Too similar to completed work
        - 'needs_review': Has merge candidates
        - 'hot': Recently created or modified (good!)
        """
        # Check for hot status first (positive indicator)
        created_at = suggestion.get('created_at')
        last_edited = suggestion.get('last_edited_at')

        is_hot = False
        if created_at:
            try:
                created = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                days_old = (datetime.now(created.tzinfo) - created).days if created.tzinfo else \
                           (datetime.now() - created).days
                is_hot = days_old <= 3
            except (ValueError, TypeError):
                pass

        if last_edited:
            try:
                edited = datetime.fromisoformat(last_edited.replace('Z', '+00:00'))
                days_since_edit = (datetime.now(edited.tzinfo) - edited).days if edited.tzinfo else \
                                  (datetime.now() - edited).days
                is_hot = is_hot or days_since_edit <= 3
            except (ValueError, TypeError):
                pass

        if is_hot:
            return 'hot'

        # Check for stale
        if self.is_stale(suggestion):
            return 'stale'

        # Check for orphaned
        if archived_missions and self.is_orphaned(suggestion, archived_missions):
            return 'orphaned'

        # Check for merge candidates
        if all_suggestions:
            candidates = self.get_merge_candidates(suggestion, all_suggestions)
            if len(candidates) >= 1:
                return 'needs_review'

        return 'healthy'


# =============================================================================
# SUGGESTION ANALYZER (COORDINATOR)
# =============================================================================

class SuggestionAnalyzer:
    """
    Main coordinator for suggestion analysis.

    Combines AutoTagger, Prioritizer, and HealthAnalyzer to provide
    comprehensive analysis of all suggestions.
    """

    def __init__(
        self,
        recommendations_path: Path = None,
        mission_logs_dir: Path = None
    ):
        self.recommendations_path = recommendations_path or RECOMMENDATIONS_PATH
        self.mission_logs_dir = mission_logs_dir or MISSION_LOGS_DIR

        self.tagger = AutoTagger()
        self.prioritizer = Prioritizer()
        self.health_analyzer = HealthAnalyzer()

    def _load_recommendations(self) -> List[Dict[str, Any]]:
        """Load recommendations from storage backend (SQLite + JSON merged for safety)."""
        sqlite_items = []
        json_items = []

        # Try SQLite storage first
        storage = _get_storage()
        if storage:
            try:
                sqlite_items = storage.get_all()
            except Exception as e:
                logger.warning(f"SQLite load failed: {e}")

        # Also load from JSON file to catch any items not migrated
        if self.recommendations_path.exists():
            try:
                with open(self.recommendations_path, 'r') as f:
                    data = json.load(f)
                json_items = data.get('items', [])
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"JSON load failed: {e}")

        # Merge: SQLite is authoritative, JSON fills gaps
        if sqlite_items and json_items:
            sqlite_ids = {item.get('id') for item in sqlite_items if item.get('id')}
            return sqlite_items + [item for item in json_items if item.get('id') not in sqlite_ids]
        elif sqlite_items:
            return sqlite_items
        else:
            return json_items

    def _save_recommendations(self, items: List[Dict[str, Any]]) -> bool:
        """Save recommendations to storage backend (SQLite preferred, JSON fallback)."""
        storage = _get_storage()
        if storage:
            try:
                storage.update_all(items)
                return True
            except Exception as e:
                logger.warning(f"SQLite save failed, falling back to JSON: {e}")

        # Fallback to JSON file
        try:
            with open(self.recommendations_path, 'w') as f:
                json.dump({'items': items}, f, indent=2)
            return True
        except IOError as e:
            logger.error(f"Error saving recommendations: {e}")
            return False

    def _load_recent_missions(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Load recent completed missions from mission logs."""
        missions = []

        if not self.mission_logs_dir or not self.mission_logs_dir.exists():
            return missions

        try:
            log_files = sorted(
                self.mission_logs_dir.glob("*_report.json"),
                key=lambda f: f.stat().st_mtime,
                reverse=True
            )

            for log_file in log_files[:limit]:
                try:
                    with open(log_file, 'r') as f:
                        data = json.load(f)
                    missions.append(data)
                except (json.JSONDecodeError, IOError):
                    continue

        except Exception as e:
            logger.error(f"Error loading mission logs: {e}")

        return missions

    def analyze_all(self, persist: bool = True) -> Dict[str, Any]:
        """
        Analyze all suggestions: tag, prioritize, and health-check.

        Args:
            persist: If True, save updated suggestions back to file

        Returns:
            Dict with:
            - items: List of analyzed suggestions (sorted by priority)
            - health_report: Summary of health statuses
            - total: Total count
        """
        suggestions = self._load_recommendations()
        recent_missions = self._load_recent_missions()

        # Analyze each suggestion
        for suggestion in suggestions:
            # Add auto-tags
            self.tagger.tag_suggestion(suggestion)

            # Calculate priority
            suggestion['priority_score'] = self.prioritizer.get_priority_score(
                suggestion, recent_missions
            )

            # Determine health status
            suggestion['health_status'] = self.health_analyzer.get_health_status(
                suggestion,
                all_suggestions=suggestions,
                archived_missions=recent_missions
            )

            # Track last analysis time
            suggestion['last_analyzed_at'] = datetime.now().isoformat()

        # Sort by priority descending
        suggestions.sort(key=lambda x: x.get('priority_score', 0), reverse=True)

        # Calculate health report
        health_counts = {
            'healthy': 0,
            'stale': 0,
            'orphaned': 0,
            'needs_review': 0,
            'hot': 0
        }
        for s in suggestions:
            status = s.get('health_status', 'healthy')
            health_counts[status] = health_counts.get(status, 0) + 1

        if persist:
            self._save_recommendations(suggestions)

        return {
            'items': suggestions,
            'health_report': health_counts,
            'total': len(suggestions)
        }

    def on_new_suggestion(
        self,
        suggestion: Dict[str, Any],
        all_suggestions: List[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Process a new suggestion: auto-tag and check for similar existing ones.

        Args:
            suggestion: The new suggestion
            all_suggestions: Existing suggestions (loads from file if None)

        Returns:
            Modified suggestion with:
            - auto_tags
            - priority_score
            - similar_to (list of IDs of similar suggestions)
        """
        if all_suggestions is None:
            all_suggestions = self._load_recommendations()

        recent_missions = self._load_recent_missions(5)

        # Add auto-tags
        self.tagger.tag_suggestion(suggestion)

        # Calculate initial priority
        suggestion['priority_score'] = self.prioritizer.get_priority_score(
            suggestion, recent_missions
        )

        # Check for merge candidates
        similar_ids = self.health_analyzer.get_merge_candidates(
            suggestion, all_suggestions, threshold=0.4  # Lower threshold for new items
        )
        suggestion['similar_to'] = similar_ids

        # Set initial health status
        suggestion['health_status'] = 'hot'  # New items are always "hot"

        return suggestion

    def get_health_report(self) -> Dict[str, Any]:
        """
        Get summary health report without full analysis.

        Returns:
            Dict with health status counts and statistics
        """
        suggestions = self._load_recommendations()

        health_counts = {
            'healthy': 0,
            'stale': 0,
            'orphaned': 0,
            'needs_review': 0,
            'hot': 0,
            'unknown': 0
        }

        stale_items = []
        orphaned_items = []

        for s in suggestions:
            status = s.get('health_status', 'unknown')
            health_counts[status] = health_counts.get(status, 0) + 1

            if status == 'stale':
                stale_items.append({
                    'id': s.get('id'),
                    'title': s.get('mission_title'),
                    'created_at': s.get('created_at')
                })
            elif status == 'orphaned':
                orphaned_items.append({
                    'id': s.get('id'),
                    'title': s.get('mission_title')
                })

        return {
            'counts': health_counts,
            'total': len(suggestions),
            'stale_items': stale_items[:10],
            'orphaned_items': orphaned_items[:10],
            'needs_analysis': health_counts.get('unknown', 0) > 0
        }

    def auto_tag_all(self, persist: bool = True) -> Dict[str, Any]:
        """
        Run auto-tagging on all suggestions.

        Args:
            persist: If True, save results back to file

        Returns:
            Dict with tagging results
        """
        suggestions = self._load_recommendations()

        tag_counts = {}
        for suggestion in suggestions:
            self.tagger.tag_suggestion(suggestion)
            for tag in suggestion.get('auto_tags', []):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1

        if persist:
            self._save_recommendations(suggestions)

        return {
            'tagged_count': len(suggestions),
            'tag_distribution': tag_counts
        }


# =============================================================================
# SINGLETON ACCESSOR
# =============================================================================

_analyzer_instance = None


def get_analyzer() -> SuggestionAnalyzer:
    """Get or create the global analyzer instance."""
    global _analyzer_instance
    if _analyzer_instance is None:
        _analyzer_instance = SuggestionAnalyzer()
    return _analyzer_instance


# =============================================================================
# MAIN (Self-test)
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Suggestion Analyzer - Self Test")
    print("=" * 60)

    analyzer = SuggestionAnalyzer()

    print("\n[1] Running full analysis...")
    result = analyzer.analyze_all(persist=False)
    print(f"    Total suggestions: {result['total']}")
    print(f"    Health report: {result['health_report']}")

    print("\n[2] Top 5 by priority:")
    for i, item in enumerate(result['items'][:5], 1):
        tags = ', '.join(item.get('auto_tags', [])[:3]) or 'no tags'
        print(f"    {i}. [{item.get('priority_score', 0):.1f}] {item.get('mission_title', 'Untitled')[:40]}...")
        print(f"       Tags: {tags}, Health: {item.get('health_status', 'unknown')}")

    print("\n[3] Health report:")
    health = analyzer.get_health_report()
    print(f"    Counts: {health['counts']}")
    if health['stale_items']:
        print(f"    Stale items: {len(health['stale_items'])}")
    if health['orphaned_items']:
        print(f"    Orphaned items: {len(health['orphaned_items'])}")

    print("\n" + "=" * 60)
    print("Self-test complete!")
    print("=" * 60)
