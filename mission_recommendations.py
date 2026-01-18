#!/usr/bin/env python3
"""
Mission Recommendations Module

Converts investigation findings into actionable mission recommendations.
This module closes the loop between investigation-mode research and
actionable development work.

Features:
1. Extract actionable items from investigation learnings
2. Generate mission-ready problem statements
3. Prioritize recommendations based on relevance and complexity
4. Track recommendation-to-mission conversion metrics

Usage:
    from mission_recommendations import MissionRecommendationEngine

    engine = MissionRecommendationEngine()

    # Generate recommendations from investigation
    recommendations = engine.generate_from_investigation("inv_abc123")

    # Get all pending recommendations
    pending = engine.get_pending_recommendations()

    # Convert recommendation to mission
    mission = engine.convert_to_mission(recommendation_id)
"""

import json
import sqlite3
import hashlib
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

# Paths - use centralized configuration
from atlasforge_config import BASE_DIR, KNOWLEDGE_BASE_DIR
RECOMMENDATIONS_DB = KNOWLEDGE_BASE_DIR / "recommendations.db"


@dataclass
class MissionRecommendation:
    """A recommended mission generated from investigation findings."""
    recommendation_id: str
    source_investigation_id: str
    investigation_query: str
    title: str
    problem_statement: str
    rationale: str  # Why this should become a mission
    estimated_complexity: str  # low, medium, high
    priority: int  # 1-5, higher is more important
    tags: List[str] = field(default_factory=list)
    source_learning_ids: List[str] = field(default_factory=list)
    status: str = "pending"  # pending, accepted, rejected, completed
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    accepted_at: Optional[str] = None
    converted_mission_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MissionRecommendation':
        return cls(**data)


class MissionRecommendationEngine:
    """
    Engine for generating and managing mission recommendations
    from investigation findings.
    """

    def __init__(self, db_path: Optional[Path] = None):
        """Initialize the recommendation engine."""
        self.db_path = db_path or RECOMMENDATIONS_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize the recommendations database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS recommendations (
                    recommendation_id TEXT PRIMARY KEY,
                    source_investigation_id TEXT,
                    investigation_query TEXT,
                    title TEXT NOT NULL,
                    problem_statement TEXT NOT NULL,
                    rationale TEXT,
                    estimated_complexity TEXT DEFAULT 'medium',
                    priority INTEGER DEFAULT 3,
                    tags TEXT,
                    source_learning_ids TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT,
                    accepted_at TEXT,
                    converted_mission_id TEXT
                )
            """)

            # Index for faster queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_rec_status
                ON recommendations(status)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_rec_investigation
                ON recommendations(source_investigation_id)
            """)

            conn.commit()

    def generate_from_investigation(
        self,
        investigation_id: str
    ) -> List[MissionRecommendation]:
        """
        Generate mission recommendations from an investigation's learnings.

        Args:
            investigation_id: The investigation ID to generate recommendations from

        Returns:
            List of generated MissionRecommendation objects
        """
        from mission_knowledge_base import get_knowledge_base
        kb = get_knowledge_base()

        # Get learnings from this investigation
        learnings = kb.get_investigation_learnings(investigation_id=investigation_id)

        if not learnings:
            logger.warning(f"No learnings found for investigation {investigation_id}")
            return []

        # Get the investigation query
        investigation_query = ""
        for learning in learnings:
            if learning.get("investigation_query"):
                investigation_query = learning["investigation_query"]
                break

        recommendations = []

        # Group learnings by type for different recommendation strategies
        templates = [l for l in learnings if l.get("learning_type") == "template"]
        insights = [l for l in learnings if l.get("learning_type") == "insight"]
        techniques = [l for l in learnings if l.get("learning_type") == "technique"]

        # Generate recommendations from templates (highest actionability)
        for template in templates:
            rec = self._create_recommendation_from_learning(
                template,
                investigation_id,
                investigation_query,
                priority=5,  # Templates are highly actionable
                complexity="medium"
            )
            if rec:
                recommendations.append(rec)

        # Generate recommendations from insights with action words
        action_keywords = ["implement", "create", "build", "add", "integrate", "fix", "optimize"]
        for insight in insights:
            desc = (insight.get("description") or "").lower()
            if any(kw in desc for kw in action_keywords):
                rec = self._create_recommendation_from_learning(
                    insight,
                    investigation_id,
                    investigation_query,
                    priority=4,
                    complexity="medium"
                )
                if rec:
                    recommendations.append(rec)

        # Generate a summary recommendation if multiple techniques found
        if len(techniques) >= 3:
            combined_rec = self._create_combined_recommendation(
                techniques,
                investigation_id,
                investigation_query
            )
            if combined_rec:
                recommendations.append(combined_rec)

        # Store recommendations
        for rec in recommendations:
            self._store_recommendation(rec)

        logger.info(f"Generated {len(recommendations)} recommendations from investigation {investigation_id}")
        return recommendations

    def _create_recommendation_from_learning(
        self,
        learning: Dict[str, Any],
        investigation_id: str,
        investigation_query: str,
        priority: int,
        complexity: str
    ) -> Optional[MissionRecommendation]:
        """Create a recommendation from a single learning."""
        title = learning.get("title", "")
        description = learning.get("description", "")

        if not title or len(description) < 30:
            return None

        # Clean up title
        clean_title = title
        for prefix in ["Recommendation:", "Next Step:", "Key Finding:", "Research:", "Insight:"]:
            if clean_title.startswith(prefix):
                clean_title = clean_title[len(prefix):].strip()

        # Generate a problem statement
        problem_statement = self._generate_problem_statement(
            clean_title, description, investigation_query
        )

        # Generate rationale
        rationale = f"This recommendation emerged from investigation '{investigation_query}'. " \
                    f"The underlying insight suggests actionable work that could improve the system."

        recommendation_id = hashlib.sha256(
            f"{investigation_id}_{learning.get('learning_id', '')}".encode()
        ).hexdigest()[:16]

        return MissionRecommendation(
            recommendation_id=recommendation_id,
            source_investigation_id=investigation_id,
            investigation_query=investigation_query,
            title=clean_title[:100],
            problem_statement=problem_statement,
            rationale=rationale,
            estimated_complexity=complexity,
            priority=priority,
            tags=learning.get("relevance_keywords", [])[:5],
            source_learning_ids=[learning.get("learning_id", "")]
        )

    def _create_combined_recommendation(
        self,
        learnings: List[Dict[str, Any]],
        investigation_id: str,
        investigation_query: str
    ) -> Optional[MissionRecommendation]:
        """Create a combined recommendation from multiple learnings."""
        if not learnings:
            return None

        # Extract key themes
        all_keywords = []
        titles = []
        for l in learnings:
            all_keywords.extend(l.get("relevance_keywords", []))
            titles.append(l.get("title", ""))

        # Find most common theme
        from collections import Counter
        theme_counts = Counter(all_keywords)
        top_theme = theme_counts.most_common(1)[0][0] if theme_counts else "implementation"

        title = f"Implement {top_theme.replace('_', ' ').title()} Improvements"

        problem_statement = f"Based on investigation '{investigation_query}', " \
                           f"implement comprehensive improvements related to {top_theme}. " \
                           f"This includes: " + "; ".join(t[:50] for t in titles[:3])

        rationale = f"Multiple findings ({len(learnings)}) from the investigation point to " \
                    f"opportunities in the {top_theme} area. Combining them into a single " \
                    f"mission ensures coherent implementation."

        recommendation_id = hashlib.sha256(
            f"{investigation_id}_combined_{top_theme}".encode()
        ).hexdigest()[:16]

        return MissionRecommendation(
            recommendation_id=recommendation_id,
            source_investigation_id=investigation_id,
            investigation_query=investigation_query,
            title=title,
            problem_statement=problem_statement,
            rationale=rationale,
            estimated_complexity="high",  # Combined missions are usually more complex
            priority=4,
            tags=list(dict.fromkeys(all_keywords[:5])),  # Deduplicated
            source_learning_ids=[l.get("learning_id", "") for l in learnings]
        )

    def _generate_problem_statement(
        self,
        title: str,
        description: str,
        investigation_query: str
    ) -> str:
        """Generate a mission-ready problem statement."""
        # Start with the title as the core problem
        base = title

        # Add context from description
        if len(description) > 50:
            # Extract first sentence or first 200 chars
            first_sentence = description.split('.')[0] if '.' in description else description[:200]
            base += f". {first_sentence}"

        # Make it action-oriented
        if not any(base.lower().startswith(w) for w in ["implement", "create", "build", "add", "fix"]):
            base = f"Implement: {base}"

        return base[:500]  # Cap at 500 chars

    def _store_recommendation(self, rec: MissionRecommendation):
        """Store a recommendation in the database."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO recommendations
                (recommendation_id, source_investigation_id, investigation_query,
                 title, problem_statement, rationale, estimated_complexity,
                 priority, tags, source_learning_ids, status, created_at,
                 accepted_at, converted_mission_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                rec.recommendation_id,
                rec.source_investigation_id,
                rec.investigation_query,
                rec.title,
                rec.problem_statement,
                rec.rationale,
                rec.estimated_complexity,
                rec.priority,
                json.dumps(rec.tags),
                json.dumps(rec.source_learning_ids),
                rec.status,
                rec.created_at,
                rec.accepted_at,
                rec.converted_mission_id
            ))
            conn.commit()

    def get_pending_recommendations(
        self,
        limit: int = 100,
        investigation_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get pending recommendations that haven't been acted on.

        Args:
            limit: Maximum number of recommendations to return
            investigation_id: Optional filter by investigation

        Returns:
            List of recommendation dicts sorted by priority
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            if investigation_id:
                cursor.execute("""
                    SELECT * FROM recommendations
                    WHERE status = 'pending' AND source_investigation_id = ?
                    ORDER BY priority DESC, created_at DESC
                    LIMIT ?
                """, (investigation_id, limit))
            else:
                cursor.execute("""
                    SELECT * FROM recommendations
                    WHERE status = 'pending'
                    ORDER BY priority DESC, created_at DESC
                    LIMIT ?
                """, (limit,))

            rows = cursor.fetchall()

        columns = [
            "recommendation_id", "source_investigation_id", "investigation_query",
            "title", "problem_statement", "rationale", "estimated_complexity",
            "priority", "tags", "source_learning_ids", "status", "created_at",
            "accepted_at", "converted_mission_id"
        ]

        recommendations = []
        for row in rows:
            data = dict(zip(columns, row))
            data["tags"] = json.loads(data["tags"] or "[]")
            data["source_learning_ids"] = json.loads(data["source_learning_ids"] or "[]")
            recommendations.append(data)

        return recommendations

    def get_recommendations_paginated(
        self,
        page: int = 1,
        per_page: int = 20,
        complexity: Optional[str] = None,
        priority: Optional[int] = None,
        investigation_id: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        search: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get paginated recommendations with filtering.

        Args:
            page: Page number (1-indexed)
            per_page: Items per page
            complexity: Filter by complexity (low/medium/high)
            priority: Filter by priority level (1-5)
            investigation_id: Filter by source investigation
            start_date: Filter by created_at >= start_date (ISO format)
            end_date: Filter by created_at <= end_date (ISO format)
            search: Keyword search in title and problem_statement

        Returns:
            Dict with recommendations, total, page, per_page, pages, has_next, has_prev
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Build WHERE clause dynamically
            where_clauses = ["status = 'pending'"]
            params = []

            if complexity:
                where_clauses.append("estimated_complexity = ?")
                params.append(complexity)

            if priority is not None:
                where_clauses.append("priority = ?")
                params.append(priority)

            if investigation_id:
                where_clauses.append("source_investigation_id = ?")
                params.append(investigation_id)

            if start_date:
                where_clauses.append("created_at >= ?")
                params.append(start_date)

            if end_date:
                where_clauses.append("created_at <= ?")
                params.append(end_date)

            if search:
                where_clauses.append("(title LIKE ? OR problem_statement LIKE ?)")
                search_pattern = f"%{search}%"
                params.append(search_pattern)
                params.append(search_pattern)

            where_sql = " AND ".join(where_clauses)

            # Count total matching records
            count_query = f"SELECT COUNT(*) FROM recommendations WHERE {where_sql}"
            cursor.execute(count_query, params)
            total = cursor.fetchone()[0]

            # Calculate pagination
            pages = (total + per_page - 1) // per_page if total > 0 else 1
            page = max(1, min(page, pages))
            offset = (page - 1) * per_page

            # Fetch paginated results
            data_query = f"""
                SELECT * FROM recommendations
                WHERE {where_sql}
                ORDER BY priority DESC, created_at DESC
                LIMIT ? OFFSET ?
            """
            cursor.execute(data_query, params + [per_page, offset])
            rows = cursor.fetchall()

        columns = [
            "recommendation_id", "source_investigation_id", "investigation_query",
            "title", "problem_statement", "rationale", "estimated_complexity",
            "priority", "tags", "source_learning_ids", "status", "created_at",
            "accepted_at", "converted_mission_id"
        ]

        recommendations = []
        for row in rows:
            data = dict(zip(columns, row))
            data["tags"] = json.loads(data["tags"] or "[]")
            data["source_learning_ids"] = json.loads(data["source_learning_ids"] or "[]")
            recommendations.append(data)

        return {
            "recommendations": recommendations,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": pages,
            "has_next": page < pages,
            "has_prev": page > 1
        }

    def get_distinct_investigations(self) -> List[Dict[str, str]]:
        """
        Get list of distinct investigation IDs with their queries.

        Returns:
            List of dicts with investigation_id and query
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT source_investigation_id, investigation_query
                FROM recommendations
                WHERE status = 'pending' AND source_investigation_id IS NOT NULL
                ORDER BY source_investigation_id
            """)
            rows = cursor.fetchall()

        return [
            {"investigation_id": row[0], "query": row[1] or ""}
            for row in rows
        ]

    def get_recommendation(self, recommendation_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific recommendation by ID."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM recommendations WHERE recommendation_id = ?", (recommendation_id,))
            row = cursor.fetchone()

        if not row:
            return None

        columns = [
            "recommendation_id", "source_investigation_id", "investigation_query",
            "title", "problem_statement", "rationale", "estimated_complexity",
            "priority", "tags", "source_learning_ids", "status", "created_at",
            "accepted_at", "converted_mission_id"
        ]

        data = dict(zip(columns, row))
        data["tags"] = json.loads(data["tags"] or "[]")
        data["source_learning_ids"] = json.loads(data["source_learning_ids"] or "[]")
        return data

    def accept_recommendation(self, recommendation_id: str) -> bool:
        """
        Mark a recommendation as accepted (ready to convert to mission).

        Args:
            recommendation_id: The recommendation to accept

        Returns:
            True if successfully accepted, False otherwise
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE recommendations
                SET status = 'accepted', accepted_at = ?
                WHERE recommendation_id = ? AND status = 'pending'
            """, (datetime.now().isoformat(), recommendation_id))

            updated = cursor.rowcount > 0
            conn.commit()

        return updated

    def reject_recommendation(self, recommendation_id: str) -> bool:
        """
        Mark a recommendation as rejected.

        Args:
            recommendation_id: The recommendation to reject

        Returns:
            True if successfully rejected, False otherwise
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE recommendations
                SET status = 'rejected'
                WHERE recommendation_id = ? AND status = 'pending'
            """, (recommendation_id,))

            updated = cursor.rowcount > 0
            conn.commit()

        return updated

    def delete_all_pending_recommendations(self) -> Dict[str, Any]:
        """
        Delete all pending recommendations.

        Returns:
            Dict with deleted count and success status
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM recommendations WHERE status = 'pending'")
            count = cursor.rowcount
            conn.commit()

        logger.info(f"Deleted {count} pending recommendations")
        return {
            "deleted_count": count,
            "success": True
        }

    def delete_recommendations_by_ids(self, recommendation_ids: List[str]) -> Dict[str, Any]:
        """
        Delete recommendations by their IDs.

        Args:
            recommendation_ids: List of recommendation IDs to delete

        Returns:
            Dict with deleted count and success status
        """
        if not recommendation_ids:
            return {"deleted_count": 0, "success": True}

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            placeholders = ','.join('?' * len(recommendation_ids))
            cursor.execute(
                f"DELETE FROM recommendations WHERE recommendation_id IN ({placeholders})",
                recommendation_ids
            )
            count = cursor.rowcount
            conn.commit()

        logger.info(f"Deleted {count} recommendations by ID")
        return {
            "deleted_count": count,
            "success": True
        }

    def convert_to_mission(
        self,
        recommendation_id: str,
        cycle_budget: int = 3
    ) -> Optional[Dict[str, Any]]:
        """
        Convert an accepted recommendation into a mission-ready format.

        This doesn't actually create the mission but returns the data needed
        to create one via the dashboard or API.

        Args:
            recommendation_id: The recommendation to convert
            cycle_budget: Default cycle budget for the mission

        Returns:
            Mission-ready dict or None if recommendation not found/not accepted
        """
        rec = self.get_recommendation(recommendation_id)

        if not rec:
            logger.error(f"Recommendation {recommendation_id} not found")
            return None

        if rec["status"] not in ["pending", "accepted"]:
            logger.warning(f"Recommendation {recommendation_id} has status {rec['status']}")
            # Still allow conversion but log warning

        # Generate mission format
        mission_data = {
            "title": rec["title"],
            "problem_statement": rec["problem_statement"],
            "cycle_budget": cycle_budget,
            "metadata": {
                "source": "investigation_recommendation",
                "recommendation_id": recommendation_id,
                "source_investigation_id": rec["source_investigation_id"],
                "investigation_query": rec["investigation_query"],
                "rationale": rec["rationale"],
                "estimated_complexity": rec["estimated_complexity"],
                "source_learning_ids": rec["source_learning_ids"]
            }
        }

        return mission_data

    def mark_converted(self, recommendation_id: str, mission_id: str) -> bool:
        """
        Mark a recommendation as converted to a mission.

        Args:
            recommendation_id: The recommendation that was converted
            mission_id: The ID of the created mission

        Returns:
            True if successfully marked, False otherwise
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE recommendations
                SET status = 'completed', converted_mission_id = ?
                WHERE recommendation_id = ?
            """, (mission_id, recommendation_id))

            updated = cursor.rowcount > 0
            conn.commit()

        return updated

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about recommendations."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Count by status
            cursor.execute("""
                SELECT status, COUNT(*) FROM recommendations GROUP BY status
            """)
            by_status = dict(cursor.fetchall())

            # Count by complexity
            cursor.execute("""
                SELECT estimated_complexity, COUNT(*) FROM recommendations GROUP BY estimated_complexity
            """)
            by_complexity = dict(cursor.fetchall())

            # Count by investigation
            cursor.execute("""
                SELECT source_investigation_id, COUNT(*) FROM recommendations
                GROUP BY source_investigation_id ORDER BY COUNT(*) DESC LIMIT 10
            """)
            by_investigation = [
                {"investigation_id": r[0], "count": r[1]}
                for r in cursor.fetchall()
            ]

            # Total counts
            cursor.execute("SELECT COUNT(*) FROM recommendations")
            total = cursor.fetchone()[0]

            # Conversion rate
            converted = by_status.get("completed", 0)
            conversion_rate = (converted / total * 100) if total > 0 else 0

        return {
            "total_recommendations": total,
            "by_status": by_status,
            "by_complexity": by_complexity,
            "by_investigation": by_investigation,
            "conversion_rate": round(conversion_rate, 1)
        }

    def generate_from_all_investigations(self, clear_existing: bool = True) -> Dict[str, Any]:
        """
        Generate recommendations from all investigations.

        Args:
            clear_existing: If True, clear all pending recommendations first (default True)

        Returns:
            Statistics about generated recommendations
        """
        from mission_knowledge_base import get_knowledge_base
        kb = get_knowledge_base()

        # Clear existing pending recommendations if requested
        deleted_count = 0
        if clear_existing:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM recommendations WHERE status = 'pending'")
                deleted_count = cursor.rowcount
                conn.commit()
            logger.info(f"Cleared {deleted_count} pending recommendations")

        # Get all distinct investigation IDs
        stats = kb.get_investigation_stats()
        investigation_ids = set()

        for query_info in stats.get("recent_queries", []):
            inv_id = query_info.get("investigation_id")
            if inv_id:
                investigation_ids.add(inv_id)

        # Also check the investigations directory
        investigations_dir = BASE_DIR / "investigations"
        if investigations_dir.exists():
            for inv_dir in investigations_dir.iterdir():
                if inv_dir.is_dir() and inv_dir.name.startswith("inv_"):
                    investigation_ids.add(inv_dir.name)

        # Generate for all investigations (since we cleared existing ones)
        total_generated = 0

        for inv_id in investigation_ids:
            recs = self.generate_from_investigation(inv_id)
            total_generated += len(recs)

        return {
            "investigations_processed": len(investigation_ids),
            "recommendations_generated": total_generated,
            "recommendations_cleared": deleted_count
        }


# =============================================================================
# SINGLETON ACCESSOR
# =============================================================================

_engine_instance = None


def get_recommendation_engine() -> MissionRecommendationEngine:
    """Get or create the global recommendation engine instance."""
    global _engine_instance
    if _engine_instance is None:
        _engine_instance = MissionRecommendationEngine()
    return _engine_instance


# =============================================================================
# MAIN (Self-test)
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Mission Recommendations Engine - Self Test")
    print("=" * 60)

    engine = MissionRecommendationEngine()

    print("\n[1] Generating recommendations from all investigations...")
    result = engine.generate_from_all_investigations()
    print(f"    Processed: {result['investigations_processed']} investigations")
    print(f"    Generated: {result['recommendations_generated']} recommendations")
    print(f"    Skipped: {result['investigations_skipped']} (already have recommendations)")

    print("\n[2] Getting pending recommendations...")
    pending = engine.get_pending_recommendations(limit=5)
    print(f"    Found {len(pending)} pending recommendations")
    for rec in pending[:3]:
        print(f"    - [{rec['priority']}] {rec['title'][:50]}...")

    print("\n[3] Getting statistics...")
    stats = engine.get_stats()
    print(f"    Total: {stats['total_recommendations']}")
    print(f"    By status: {stats['by_status']}")
    print(f"    Conversion rate: {stats['conversion_rate']}%")

    print("\n" + "=" * 60)
    print("Self-test complete!")
    print("=" * 60)
