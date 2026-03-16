"""
消息队列模块 - 3分钟滑动窗口
"""

import asyncio
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class MessageContext:
    """消息上下文"""

    timestamp: datetime
    sender_id: str
    sender_name: str
    content: str
    message_type: str  # 'group' or 'private'
    group_id: Optional[str] = None
    event: Any = field(default=None)
    processed: bool = False


class MessageQueue:
    """3分钟滑动窗口消息队列"""

    def __init__(self, window_minutes: int = 3):
        self.window = timedelta(minutes=window_minutes)
        self.messages: List[MessageContext] = []
        self._lock = asyncio.Lock()

    async def add_message(self, msg: MessageContext):
        """添加消息"""
        async with self._lock:
            self.messages.append(msg)
            await self._cleanup()

    async def has_new_messages(self) -> bool:
        """检查是否有新消息（未处理）"""
        async with self._lock:
            return any(not m.processed for m in self.messages)

    async def get_context(self) -> List[MessageContext]:
        """获取当前上下文"""
        async with self._lock:
            await self._cleanup()
            return self.messages.copy()

    async def get_unprocessed_messages(self) -> List[MessageContext]:
        """获取未处理的消息"""
        async with self._lock:
            await self._cleanup()
            return [m for m in self.messages if not m.processed]

    async def mark_all_processed(self):
        """标记所有消息为已处理"""
        async with self._lock:
            for m in self.messages:
                m.processed = True

    async def mark_processed(self, indices: List[int]):
        """标记指定消息为已处理"""
        async with self._lock:
            for idx in indices:
                if 0 <= idx < len(self.messages):
                    self.messages[idx].processed = True

    async def _cleanup(self):
        """清理过期消息"""
        cutoff = datetime.now() - self.window
        self.messages = [m for m in self.messages if m.timestamp > cutoff]

    async def get_message_count(self) -> int:
        """获取消息数量"""
        async with self._lock:
            return len(self.messages)

    async def clear(self):
        """清空队列"""
        async with self._lock:
            self.messages.clear()


class MessageQueueManager:
    """消息队列管理器"""

    def __init__(self):
        self.queues: Dict[str, MessageQueue] = {}

    def get_or_create_queue(
        self, queue_key: str, window_minutes: int = 3
    ) -> MessageQueue:
        """获取或创建队列"""
        if queue_key not in self.queues:
            self.queues[queue_key] = MessageQueue(window_minutes)
        return self.queues[queue_key]

    def get_queue(self, queue_key: str) -> Optional[MessageQueue]:
        """获取队列"""
        return self.queues.get(queue_key)

    def remove_queue(self, queue_key: str):
        """移除队列"""
        if queue_key in self.queues:
            del self.queues[queue_key]

    def get_all_queues(self) -> Dict[str, MessageQueue]:
        """获取所有队列"""
        return self.queues.copy()

    def get_queue_keys(self) -> List[str]:
        """获取所有队列key"""
        return list(self.queues.keys())
