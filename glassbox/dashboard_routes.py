"""
Dashboard API Routes for GlassBox.

Provides Flask blueprint with endpoints for:
- Listing missions
- Getting mission timeline/agents
- Viewing transcripts
- Searching across missions
"""

from flask import Blueprint, jsonify, request, abort
from pathlib import Path
from typing import Optional
from datetime import datetime
import json
from werkzeug.exceptions import HTTPException

from .mission_archiver import (
    list_archived_missions,
    load_mission_archive,
    search_missions,
    MissionArchiver,
    TRANSCRIPTS_ARCHIVE_DIR,
)
from .transcript_parser import TranscriptParser
from .mission_reconstructor import MissionReconstructor


# Create Blueprint
glassbox_bp = Blueprint('glassbox', __name__, url_prefix='/api/glassbox')


@glassbox_bp.route('/integrity', methods=['GET'])
def api_get_integrity():
    """
    Report transcript archive token integrity for the Mission Control widget.

    Flags missions with missing or suspiciously low token counts so the widget
    can surface archival/parser regressions without failing when no anomalies
    exist.
    """
    try:
        missions = list_archived_missions()
        anomalies = []
        normal_count = 0

        for mission in missions:
            if mission.transcript_count <= 0:
                continue

            if mission.total_tokens <= 0:
                category = 'zero_token'
            elif mission.total_tokens < 100:
                category = 'low_token'
            else:
                normal_count += 1
                continue

            anomalies.append({
                'mission_id': mission.mission_id,
                'created_at': mission.created_at.isoformat() if mission.created_at else None,
                'completed_at': mission.completed_at.isoformat() if mission.completed_at else None,
                'total_tokens': mission.total_tokens,
                'transcript_count': mission.transcript_count,
                'has_manifest': mission.has_manifest,
                'category': category,
            })

        zero_token_count = sum(1 for a in anomalies if a['category'] == 'zero_token')
        low_token_count = sum(1 for a in anomalies if a['category'] == 'low_token')

        return jsonify({
            'ok': True,
            'archive_dir': str(TRANSCRIPTS_ARCHIVE_DIR),
            'archive_dir_exists': TRANSCRIPTS_ARCHIVE_DIR.exists(),
            'summary': {
                'total_scanned': len(missions),
                'normal_count': normal_count,
                'zero_token_count': zero_token_count,
                'low_token_count': low_token_count,
                'anomaly_count': len(anomalies),
            },
            'anomalies': anomalies[:100],
        })
    except Exception as e:
        return jsonify({'error': str(e), 'ok': False}), 500


@glassbox_bp.route('/missions', methods=['GET'])
def api_list_missions():
    """
    List all archived missions with pagination, search, and date filtering.

    Query parameters:
        - page: Page number (default: 1)
        - limit: Items per page (default: 20, max: 100)
        - search: Search text for mission_id (case-insensitive)
        - from: Filter missions created after this ISO date
        - to: Filter missions created before this ISO date

    Returns:
        {
            missions: [{mission_id, created_at, completed_at, total_tokens, archive_path}],
            pagination: {page, limit, total, pages, has_next, has_prev},
            filters: {search, from, to}
        }
    """
    try:
        # Get query parameters
        page = request.args.get('page', 1, type=int)
        limit = request.args.get('limit', 20, type=int)
        limit = min(max(1, limit), 100)  # Clamp between 1-100
        search_query = request.args.get('search', '').strip().lower()
        from_date = request.args.get('from')
        to_date = request.args.get('to')

        # Get all missions (cached)
        missions = list_archived_missions()

        # Apply search filter
        if search_query:
            missions = [m for m in missions if search_query in m.mission_id.lower()]

        # Apply date range filters
        if from_date:
            try:
                from_dt = datetime.fromisoformat(from_date.replace('Z', '+00:00'))
                missions = [m for m in missions if m.created_at and m.created_at >= from_dt]
            except ValueError:
                pass  # Ignore invalid date format

        if to_date:
            try:
                to_dt = datetime.fromisoformat(to_date.replace('Z', '+00:00'))
                missions = [m for m in missions if m.created_at and m.created_at <= to_dt]
            except ValueError:
                pass  # Ignore invalid date format

        # Calculate pagination
        total = len(missions)
        total_pages = (total + limit - 1) // limit if total > 0 else 1
        page = max(1, min(page, total_pages))  # Clamp to valid range

        start = (page - 1) * limit
        end = start + limit
        paginated = missions[start:end]

        return jsonify({
            'missions': [m.to_dict() for m in paginated],
            'pagination': {
                'page': page,
                'limit': limit,
                'total': total,
                'pages': total_pages,
                'has_next': end < total,
                'has_prev': page > 1
            },
            'filters': {
                'search': search_query or None,
                'from': from_date,
                'to': to_date
            }
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@glassbox_bp.route('/missions/<mission_id>/timeline', methods=['GET'])
def api_get_timeline(mission_id: str):
    """
    Get complete mission timeline.

    Returns:
        {mission_id, stages, agents, total_tokens, decision_log}
    """
    try:
        timeline = load_mission_archive(mission_id)
        if not timeline:
            abort(404, description=f"Mission not found: {mission_id}")

        return jsonify(timeline.to_dict())
    except HTTPException:
        raise  # Re-raise HTTP errors (404, 405, etc.) with correct status
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@glassbox_bp.route('/missions/<mission_id>/agents', methods=['GET'])
def api_get_agents(mission_id: str):
    """
    Get agent hierarchy tree for a mission.

    Returns:
        {root: {agent_id, children: [...], tokens, duration}}
    """
    try:
        timeline = load_mission_archive(mission_id)
        if not timeline:
            abort(404, description=f"Mission not found: {mission_id}")

        return jsonify({
            'mission_id': mission_id,
            'root_agents': [a.to_dict() for a in timeline.root_agents],
            'all_agents': [
                {
                    'agent_id': a.agent_id,
                    'session_id': a.session_id,
                    'parent_agent_id': a.parent_agent_id,
                    'total_tokens': a.total_tokens,
                    'duration_seconds': round(a.duration_seconds, 2),
                    'model': a.model,
                    'tool_calls_count': a.tool_calls_count,
                }
                for a in timeline.all_agents
            ],
            'agent_count': len(timeline.all_agents),
        })
    except HTTPException:
        raise  # Re-raise HTTP errors (404, 405, etc.) with correct status
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@glassbox_bp.route('/missions/<mission_id>/transcripts/<agent_id>', methods=['GET'])
def api_get_transcript(mission_id: str, agent_id: str):
    """
    Get transcript for a specific agent.

    Returns:
        [{type, timestamp, content, usage}]
    """
    try:
        archive_path = TRANSCRIPTS_ARCHIVE_DIR / mission_id
        if not archive_path.exists():
            abort(404, description=f"Mission not found: {mission_id}")

        # Find the transcript file
        parser = TranscriptParser()

        # Try agent file first
        agent_file = archive_path / f"agent-{agent_id}.jsonl"
        if not agent_file.exists():
            # Try as session file
            agent_file = archive_path / f"{agent_id}.jsonl"
        if not agent_file.exists():
            # Search all files
            for f in archive_path.glob("*.jsonl"):
                if agent_id in f.name:
                    agent_file = f
                    break

        if not agent_file.exists():
            abort(404, description=f"Transcript not found: {agent_id}")

        session = parser.parse_file(agent_file)

        # Format messages for display
        messages = []
        for msg in session.messages:
            msg_dict = {
                'uuid': msg.uuid,
                'type': msg.type,
                'timestamp': msg.timestamp.isoformat(),
                'model': msg.model,
                'usage': msg.usage,
            }

            # Format content
            if msg.type == 'assistant':
                content_parts = []
                for block in msg.content:
                    if isinstance(block, dict):
                        if block.get('type') == 'text':
                            content_parts.append({
                                'type': 'text',
                                'text': block.get('text', ''),
                            })
                        elif block.get('type') == 'tool_use':
                            content_parts.append({
                                'type': 'tool_use',
                                'name': block.get('name', ''),
                                'input': _truncate_dict(block.get('input', {})),
                            })
                msg_dict['content'] = content_parts
            elif msg.type == 'user':
                # Tool results
                if msg.tool_results:
                    content_parts = []
                    for tr in msg.tool_results:
                        result_content = tr.get('content', '')
                        if isinstance(result_content, str) and len(result_content) > 500:
                            result_content = result_content[:500] + '...'
                        content_parts.append({
                            'type': 'tool_result',
                            'tool_use_id': tr.get('tool_use_id', ''),
                            'content': result_content,
                        })
                    msg_dict['content'] = content_parts
                else:
                    # Regular user message
                    msg_dict['content'] = msg.content

            messages.append(msg_dict)

        return jsonify({
            'agent_id': agent_id,
            'session_id': session.session_id,
            'source_file': session.source_file,
            'message_count': len(messages),
            'total_tokens': session.total_tokens,
            'messages': messages,
        })
    except HTTPException:
        raise  # Re-raise HTTP errors (404, 405, etc.) with correct status
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@glassbox_bp.route('/search', methods=['GET'])
def api_search():
    """
    Search across all missions.

    Query params:
        - query: Text to search
        - stage: Filter by stage
        - min_tokens: Minimum tokens
        - max_tokens: Maximum tokens

    Returns:
        [{mission_id, matches: [...]}]
    """
    try:
        query = request.args.get('query')
        stage = request.args.get('stage')
        min_tokens = request.args.get('min_tokens', type=int)
        max_tokens = request.args.get('max_tokens', type=int)

        results = search_missions(
            query=query,
            stage=stage,
            min_tokens=min_tokens,
            max_tokens=max_tokens,
        )

        return jsonify({
            'query': query,
            'stage': stage,
            'min_tokens': min_tokens,
            'max_tokens': max_tokens,
            'results': results,
            'count': len(results),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@glassbox_bp.route('/missions/<mission_id>/enhance', methods=['POST'])
def api_enhance_manifest(mission_id: str):
    """
    Enhance manifest with GlassBox metadata.

    Adds stage timeline, agent hierarchy, and decision log to manifest.json.
    """
    try:
        archive_path = TRANSCRIPTS_ARCHIVE_DIR / mission_id
        if not archive_path.exists():
            abort(404, description=f"Mission not found: {mission_id}")

        archiver = MissionArchiver()
        manifest = archiver.enhance_manifest(archive_path)

        return jsonify({
            'success': True,
            'mission_id': mission_id,
            'stages': len(manifest.get('stages', [])),
            'agents': len(manifest.get('agent_hierarchy', {}).get('agents', [])),
        })
    except HTTPException:
        raise  # Re-raise HTTP errors (404, 405, etc.) with correct status
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@glassbox_bp.route('/missions/<mission_id>/stages', methods=['GET'])
def api_get_stages(mission_id: str):
    """
    Get stage timeline for a mission (Gantt-style data).

    Returns:
        [{stage, start_time, end_time, duration_seconds, tokens_used}]
    """
    try:
        timeline = load_mission_archive(mission_id)
        if not timeline:
            abort(404, description=f"Mission not found: {mission_id}")

        return jsonify({
            'mission_id': mission_id,
            'stages': [s.to_dict() for s in timeline.stages],
            'total_duration_seconds': round(timeline.total_duration_seconds, 2),
        })
    except HTTPException:
        raise  # Re-raise HTTP errors (404, 405, etc.) with correct status
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@glassbox_bp.route('/missions/<mission_id>/decision-log', methods=['GET'])
def api_get_decision_log(mission_id: str):
    """
    Get decision log for a mission.

    Query params:
        - event_type: Filter by event type (stage_transition, file_write, error)
        - limit: Maximum events to return

    Returns:
        [{event_type, timestamp, description, agent_id}]
    """
    try:
        timeline = load_mission_archive(mission_id)
        if not timeline:
            abort(404, description=f"Mission not found: {mission_id}")

        event_type = request.args.get('event_type')
        limit = request.args.get('limit', default=100, type=int)

        events = timeline.decision_log
        if event_type:
            events = [e for e in events if e.event_type == event_type]

        return jsonify({
            'mission_id': mission_id,
            'events': [e.to_dict() for e in events[:limit]],
            'total_count': len(timeline.decision_log),
            'filtered_count': len(events),
        })
    except HTTPException:
        raise  # Re-raise HTTP errors (404, 405, etc.) with correct status
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@glassbox_bp.route('/stats', methods=['GET'])
def api_get_stats():
    """
    Get aggregate statistics across all missions.

    Returns:
        {total_missions, total_tokens, total_cost_usd, top_stages}
    """
    try:
        missions = list_archived_missions()

        total_tokens = sum(m.total_tokens for m in missions)
        total_cost = sum(m.total_cost_usd for m in missions)
        total_transcripts = sum(m.transcript_count for m in missions)

        return jsonify({
            'total_missions': len(missions),
            'total_tokens': total_tokens,
            'total_cost_usd': round(total_cost, 4),
            'total_transcripts': total_transcripts,
            'avg_tokens_per_mission': round(total_tokens / len(missions), 0) if missions else 0,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def _truncate_dict(d: dict, max_str_len: int = 200) -> dict:
    """Truncate long string values in a dict for display."""
    result = {}
    for k, v in d.items():
        if isinstance(v, str) and len(v) > max_str_len:
            result[k] = v[:max_str_len] + '...'
        elif isinstance(v, dict):
            result[k] = _truncate_dict(v, max_str_len)
        else:
            result[k] = v
    return result
