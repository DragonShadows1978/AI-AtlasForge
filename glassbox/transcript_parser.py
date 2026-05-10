"""
TranscriptParser: Reads .jsonl transcript files and extracts structured data.

JSONL Format:
Each line is a JSON object with fields:
- uuid: Unique message ID
- parentUuid: Parent message ID (for threading)
- sessionId: Session identifier
- agentId: Agent identifier (for subagents)
- type: "assistant" | "user" | "queue-operation"
- timestamp: ISO 8601 timestamp
- message: Contains model, role, content, usage, etc.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
import json
import re


@dataclass
class ToolCall:
    """Represents a tool invocation."""
    tool_use_id: str
    name: str
    input_params: Dict[str, Any]
    timestamp: datetime
    duration_ms: Optional[float] = None  # Time until result received
    result_snippet: Optional[str] = None  # First 200 chars of result


@dataclass
class TranscriptMessage:
    """Single message in a transcript."""
    uuid: str
    parent_uuid: Optional[str]
    type: str  # "user", "assistant", "queue-operation"
    timestamp: datetime
    session_id: str
    agent_id: Optional[str] = None
    request_id: Optional[str] = None

    # For assistant messages
    model: Optional[str] = None
    content: List[Dict] = field(default_factory=list)
    usage: Optional[Dict] = None  # Token counts

    # Extracted tool calls
    tool_calls: List[ToolCall] = field(default_factory=list)

    # For user messages (tool results)
    tool_results: List[Dict] = field(default_factory=list)

    @property
    def input_tokens(self) -> int:
        if self.usage:
            return self.usage.get('input_tokens', 0)
        return 0

    @property
    def output_tokens(self) -> int:
        if self.usage:
            return self.usage.get('output_tokens', 0)
        return 0

    @property
    def cache_creation_tokens(self) -> int:
        if self.usage:
            return self.usage.get('cache_creation_input_tokens', 0)
        return 0

    @property
    def cache_read_tokens(self) -> int:
        if self.usage:
            return self.usage.get('cache_read_input_tokens', 0)
        return 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def text_content(self) -> str:
        """Extract text from content blocks."""
        texts = []
        for block in self.content:
            if isinstance(block, dict) and block.get('type') == 'text':
                texts.append(block.get('text', ''))
        return '\n'.join(texts)


@dataclass
class TranscriptSession:
    """Complete parsed session."""
    session_id: str
    source_file: str
    agent_id: Optional[str] = None
    is_subagent: bool = False  # True if filename starts with "agent-"
    messages: List[TranscriptMessage] = field(default_factory=list)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    # Aggregated metrics
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_creation: int = 0
    total_cache_read: int = 0

    # Tool usage aggregated
    tool_calls: List[ToolCall] = field(default_factory=list)
    tool_call_counts: Dict[str, int] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    @property
    def duration_seconds(self) -> float:
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0.0

    def to_dict(self) -> Dict:
        """Convert to JSON-serializable dict."""
        return {
            'session_id': self.session_id,
            'source_file': self.source_file,
            'agent_id': self.agent_id,
            'is_subagent': self.is_subagent,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'duration_seconds': self.duration_seconds,
            'total_input_tokens': self.total_input_tokens,
            'total_output_tokens': self.total_output_tokens,
            'total_tokens': self.total_tokens,
            'total_cache_creation': self.total_cache_creation,
            'total_cache_read': self.total_cache_read,
            'message_count': len(self.messages),
            'tool_call_counts': self.tool_call_counts,
        }


class TranscriptParser:
    """Parser for Claude session transcript files."""

    # Stage detection patterns
    STAGE_PATTERNS = [
        re.compile(r'===\s*(INITIALIZE|PLANNING|BUILDING|TESTING|ANALYZING|COMPLETE)\s+STAGE\s*===', re.IGNORECASE),
        re.compile(r'"status":\s*"(ready|plan_complete|build_complete|test_complete|analysis_complete)"'),
        re.compile(r'CURRENT STAGE:\s*(INITIALIZE|PLANNING|BUILDING|TESTING|ANALYZING|COMPLETE)', re.IGNORECASE),
    ]

    def __init__(self):
        self._session_cache: Dict[str, TranscriptSession] = {}

    def parse_file(self, path: Path) -> TranscriptSession:
        """
        Parse a single .jsonl transcript file.

        Args:
            path: Path to the .jsonl file

        Returns:
            TranscriptSession with parsed messages and metrics
        """
        path = Path(path)
        filename = path.name

        # Determine if this is a subagent file
        is_subagent = filename.startswith('agent-')

        # Extract agent_id from filename
        if is_subagent:
            # agent-7366cfa4.jsonl -> 7366cfa4
            agent_id = filename[6:].replace('.jsonl', '')
        else:
            # UUID session file
            agent_id = None

        session = TranscriptSession(
            session_id='',
            source_file=filename,
            agent_id=agent_id,
            is_subagent=is_subagent,
        )

        messages = []
        tool_calls = []
        pending_tool_calls: Dict[str, ToolCall] = {}  # tool_use_id -> ToolCall

        with open(path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue

                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg = self._parse_message(record)
                if msg:
                    messages.append(msg)

                    # Update session_id from first message if not set
                    if not session.session_id and msg.session_id:
                        session.session_id = msg.session_id

                    # Update agent_id if present in message
                    if msg.agent_id and not session.agent_id:
                        session.agent_id = msg.agent_id

                    # Track tool calls
                    for tc in msg.tool_calls:
                        pending_tool_calls[tc.tool_use_id] = tc
                        tool_calls.append(tc)

                    # Match tool results to pending calls
                    for tr in msg.tool_results:
                        tool_use_id = tr.get('tool_use_id')
                        if tool_use_id and tool_use_id in pending_tool_calls:
                            tc = pending_tool_calls[tool_use_id]
                            tc.duration_ms = (msg.timestamp - tc.timestamp).total_seconds() * 1000
                            content = tr.get('content', '')
                            if isinstance(content, str):
                                tc.result_snippet = content[:200]

        # Sort messages by timestamp
        messages.sort(key=lambda m: m.timestamp)
        session.messages = messages

        # Set time bounds
        if messages:
            session.start_time = messages[0].timestamp
            session.end_time = messages[-1].timestamp

        # Aggregate metrics
        session.tool_calls = tool_calls
        for msg in messages:
            if msg.type == 'assistant':
                session.total_input_tokens += msg.input_tokens
                session.total_output_tokens += msg.output_tokens
                session.total_cache_creation += msg.cache_creation_tokens
                session.total_cache_read += msg.cache_read_tokens

            for tc in msg.tool_calls:
                session.tool_call_counts[tc.name] = session.tool_call_counts.get(tc.name, 0) + 1

        return session

    def parse_directory(self, dir_path: Path) -> List[TranscriptSession]:
        """
        Parse all .jsonl files in a directory.

        Args:
            dir_path: Directory containing transcript files

        Returns:
            List of TranscriptSession objects
        """
        dir_path = Path(dir_path)
        sessions = []

        for jsonl_file in sorted(dir_path.glob('*.jsonl')):
            try:
                session = self.parse_file(jsonl_file)
                sessions.append(session)
            except Exception as e:
                print(f"Warning: Failed to parse {jsonl_file}: {e}")

        return sessions

    def _parse_message(self, record: Dict) -> Optional[TranscriptMessage]:
        """Parse a single JSON record into a TranscriptMessage."""
        try:
            # Parse timestamp
            ts_str = record.get('timestamp', '')
            if ts_str:
                # Handle ISO format with Z suffix
                ts_str = ts_str.replace('Z', '+00:00')
                try:
                    timestamp = datetime.fromisoformat(ts_str)
                    # Make timezone-naive for consistent comparison
                    if timestamp.tzinfo is not None:
                        timestamp = timestamp.replace(tzinfo=None)
                except ValueError:
                    timestamp = datetime.now()
            else:
                timestamp = datetime.now()

            msg_type = record.get('type', 'unknown')

            # Skip queue operations for now
            if msg_type == 'queue-operation':
                return None

            msg = TranscriptMessage(
                uuid=record.get('uuid', ''),
                parent_uuid=record.get('parentUuid'),
                type=msg_type,
                timestamp=timestamp,
                session_id=record.get('sessionId', ''),
                agent_id=record.get('agentId'),
                request_id=record.get('requestId'),
            )

            # Parse message content
            message = record.get('message', {})
            if isinstance(message, dict):
                msg.model = message.get('model')
                msg.content = message.get('content', [])
                msg.usage = message.get('usage')

                # Extract tool calls from content
                if isinstance(msg.content, list):
                    for block in msg.content:
                        if isinstance(block, dict):
                            if block.get('type') == 'tool_use':
                                tc = ToolCall(
                                    tool_use_id=block.get('id', ''),
                                    name=block.get('name', ''),
                                    input_params=block.get('input', {}),
                                    timestamp=timestamp,
                                )
                                msg.tool_calls.append(tc)
                            elif block.get('type') == 'tool_result':
                                msg.tool_results.append(block)

                # Handle user messages with tool results
                if msg_type == 'user' and isinstance(msg.content, list):
                    for block in msg.content:
                        if isinstance(block, dict) and block.get('type') == 'tool_result':
                            msg.tool_results.append(block)

            return msg

        except Exception as e:
            return None

    def extract_tool_calls(self, session: TranscriptSession) -> List[Dict]:
        """
        Extract all tool calls with parameters and timing.

        Returns list of dicts with name, count, avg_duration, etc.
        """
        tool_stats: Dict[str, Dict] = {}

        for tc in session.tool_calls:
            if tc.name not in tool_stats:
                tool_stats[tc.name] = {
                    'name': tc.name,
                    'count': 0,
                    'total_duration_ms': 0,
                    'durations': [],
                }

            stats = tool_stats[tc.name]
            stats['count'] += 1
            if tc.duration_ms:
                stats['durations'].append(tc.duration_ms)
                stats['total_duration_ms'] += tc.duration_ms

        # Calculate averages
        result = []
        for name, stats in sorted(tool_stats.items(), key=lambda x: -x[1]['count']):
            avg_duration = 0.0
            if stats['durations']:
                avg_duration = stats['total_duration_ms'] / len(stats['durations'])

            result.append({
                'name': name,
                'count': stats['count'],
                'avg_duration_ms': round(avg_duration, 2),
            })

        return result

    def extract_stage_transitions(self, session: TranscriptSession) -> List[Dict]:
        """
        Find stage transition markers in messages.

        Returns list of {stage, timestamp, message_uuid}
        """
        transitions = []

        for msg in session.messages:
            text = msg.text_content
            if not text:
                continue

            for pattern in self.STAGE_PATTERNS:
                match = pattern.search(text)
                if match:
                    stage = match.group(1).upper()
                    # Map status to stage
                    status_to_stage = {
                        'READY': 'INITIALIZE',
                        'PLAN_COMPLETE': 'PLANNING',
                        'BUILD_COMPLETE': 'BUILDING',
                        'TEST_COMPLETE': 'TESTING',
                        'ANALYSIS_COMPLETE': 'ANALYZING',
                    }
                    stage = status_to_stage.get(stage, stage)

                    transitions.append({
                        'stage': stage,
                        'timestamp': msg.timestamp.isoformat(),
                        'message_uuid': msg.uuid,
                    })
                    break

        return transitions

    def get_message_by_uuid(self, session: TranscriptSession, uuid: str) -> Optional[TranscriptMessage]:
        """Find a message by UUID."""
        for msg in session.messages:
            if msg.uuid == uuid:
                return msg
        return None
