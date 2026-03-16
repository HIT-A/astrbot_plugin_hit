"""
History Manager - Chat history management with metadata

Learned from SpectreCore's HistoryStorage and Daily Analysis plugin's HistoryManager.
Provides persistent storage of chat history with metadata for summarization.
"""

import os
import json
import hashlib
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, asdict
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent


@dataclass
class MessageEntry:
    """A single message entry with metadata.

    Attributes:
        timestamp: Message timestamp
        sender_id: Sender's user ID
        sender_name: Sender's display name
        message: Message content
        group_id: Group ID (None for private chats)
        message_type: Type of message (text, image, file, etc.)
        metadata: Additional metadata
    """

    timestamp: float
    sender_id: str
    sender_name: str
    message: str
    group_id: Optional[str] = None
    message_type: str = "text"
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class IntentEntry:
    """Stored intent with metadata.

    Attributes:
        timestamp: When the intent was detected
        group_id: Group ID where intent was detected
        intent_type: Type of intent
        confidence: Confidence score
        extracted_info: Extracted information
        reasoning: Reasoning for classification
    """

    timestamp: float
    group_id: str
    intent_type: str
    confidence: float
    extracted_info: Dict[str, Any]
    reasoning: str


class HistoryManager:
    """History manager for storing and retrieving chat history.

    Combines approaches from SpectreCore's HistoryStorage (file-based persistence)
    and Daily Analysis plugin's HistoryManager (metadata and summaries).
    """

    def __init__(self, plugin_instance: Any):
        """Initialize the history manager.

        Args:
            plugin_instance: The Star plugin instance for accessing KV storage
        """
        self.plugin = plugin_instance
        self.base_path = Path("data/hit_plugin/history")
        self.base_path.mkdir(parents=True, exist_ok=True)

        # In-memory cache for recent messages
        self._message_cache: Dict[str, List[MessageEntry]] = {}
        self._cache_lock = asyncio.Lock()

        logger.info(f"HistoryManager initialized with path: {self.base_path}")

    async def save_message(self, event: AstrMessageEvent) -> bool:
        """Save a message to history.

        Args:
            event: The message event

        Returns:
            True if saved successfully
        """
        try:
            group_id = event.get_group_id()
            if not group_id:
                return False

            entry = MessageEntry(
                timestamp=event.message_obj.timestamp,
                sender_id=str(event.get_sender_id()),
                sender_name=event.get_sender_name(),
                message=event.message_str,
                group_id=str(group_id),
                message_type="text",  # Could be extended for other types
            )

            # Save to file
            await self._append_to_file(group_id, entry)

            # Update cache
            async with self._cache_lock:
                if group_id not in self._message_cache:
                    self._message_cache[group_id] = []
                self._message_cache[group_id].append(entry)

                # Limit cache size
                if len(self._message_cache[group_id]) > 1000:
                    self._message_cache[group_id] = self._message_cache[group_id][
                        -1000:
                    ]

            return True

        except Exception as e:
            logger.error(f"Failed to save message: {e}")
            return False

    async def _append_to_file(self, group_id: str, entry: MessageEntry) -> None:
        """Append a message entry to the history file.

        Args:
            group_id: Group ID
            entry: Message entry to save
        """
        date_str = datetime.now().strftime("%Y-%m-%d")
        file_path = self.base_path / f"{group_id}_{date_str}.jsonl"

        try:
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Failed to write to history file: {e}")

    async def get_messages(
        self,
        group_id: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: Optional[int] = None,
    ) -> List[MessageEntry]:
        """Get messages from history.

        Args:
            group_id: Group ID
            start_time: Start time filter
            end_time: End time filter
            limit: Maximum number of messages to return

        Returns:
            List of message entries
        """
        messages = []

        # Determine which files to read
        if start_time and end_time:
            dates = self._get_date_range(start_time, end_time)
        else:
            dates = [datetime.now().strftime("%Y-%m-%d")]

        for date_str in dates:
            file_path = self.base_path / f"{group_id}_{date_str}.jsonl"
            if file_path.exists():
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        for line in f:
                            if line.strip():
                                data = json.loads(line)
                                entry = MessageEntry(**data)

                                # Apply time filters
                                entry_time = datetime.fromtimestamp(entry.timestamp)
                                if start_time and entry_time < start_time:
                                    continue
                                if end_time and entry_time > end_time:
                                    continue

                                messages.append(entry)
                except Exception as e:
                    logger.error(f"Failed to read history file {file_path}: {e}")

        # Sort by timestamp
        messages.sort(key=lambda x: x.timestamp)

        # Apply limit
        if limit:
            messages = messages[-limit:]

        return messages

    def _get_date_range(self, start: datetime, end: datetime) -> List[str]:
        """Get list of date strings between start and end.

        Args:
            start: Start datetime
            end: End datetime

        Returns:
            List of date strings in YYYY-MM-DD format
        """
        dates = []
        current = start
        while current <= end:
            dates.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)
        return dates

    async def store_intent(self, group_id: str, intent_result: Any) -> bool:
        """Store a detected intent.

        Args:
            group_id: Group ID
            intent_result: Intent classification result

        Returns:
            True if stored successfully
        """
        try:
            entry = IntentEntry(
                timestamp=datetime.now().timestamp(),
                group_id=str(group_id),
                intent_type=intent_result.intent.value,
                confidence=intent_result.confidence,
                extracted_info=intent_result.extracted_info,
                reasoning=intent_result.reasoning,
            )

            # Use plugin's KV storage
            key = f"hit_intent_{group_id}_{int(datetime.now().timestamp())}"
            await self.plugin.put_kv_data(key, asdict(entry))

            return True

        except Exception as e:
            logger.error(f"Failed to store intent: {e}")
            return False

    async def get_recent_intents(
        self,
        group_id: str,
        hours: int = 24,
    ) -> List[IntentEntry]:
        """Get recent intents for a group.

        Args:
            group_id: Group ID
            hours: Number of hours to look back

        Returns:
            List of intent entries
        """
        intents = []
        cutoff = datetime.now() - timedelta(hours=hours)

        try:
            # This is a simplified implementation
            # In production, you might want to use a more efficient query method
            pattern = f"hit_intent_{group_id}_"
            # Note: AstrBot's KV storage might not support pattern matching
            # This is a placeholder for the actual implementation

            return intents

        except Exception as e:
            logger.error(f"Failed to get recent intents: {e}")
            return []

    async def get_daily_summary_data(
        self,
        group_id: str,
        date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Get data for daily summary generation.

        Args:
            group_id: Group ID
            date: Date to summarize (default: today)

        Returns:
            Dictionary with summary data
        """
        if date is None:
            date = datetime.now()

        start_time = date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_time = date.replace(hour=23, minute=59, second=59, microsecond=999999)

        messages = await self.get_messages(group_id, start_time, end_time)

        # Calculate statistics
        unique_senders = set(m.sender_id for m in messages)

        return {
            "date": date.strftime("%Y-%m-%d"),
            "group_id": group_id,
            "total_messages": len(messages),
            "unique_senders": len(unique_senders),
            "messages": [
                {
                    "timestamp": m.timestamp,
                    "sender": m.sender_name,
                    "content": m.message,
                }
                for m in messages
            ],
        }

    async def clear_old_history(self, days: int = 30) -> int:
        """Clear history older than specified days.

        Args:
            days: Number of days to keep

        Returns:
            Number of files deleted
        """
        cutoff = datetime.now() - timedelta(days=days)
        deleted = 0

        try:
            for file_path in self.base_path.glob("*.jsonl"):
                try:
                    # Extract date from filename
                    parts = file_path.stem.split("_")
                    if len(parts) >= 2:
                        date_str = parts[-1]
                        file_date = datetime.strptime(date_str, "%Y-%m-%d")

                        if file_date < cutoff:
                            file_path.unlink()
                            deleted += 1
                except Exception as e:
                    logger.error(f"Failed to process file {file_path}: {e}")

            logger.info(f"Cleared {deleted} old history files")
            return deleted

        except Exception as e:
            logger.error(f"Failed to clear old history: {e}")
            return 0


# Import asyncio for the lock
import asyncio
