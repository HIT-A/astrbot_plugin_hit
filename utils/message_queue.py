"""
消息队列模块 - 3分钟滑动窗口
"""

import asyncio
from datetime import datetime, timedelta
from dataclasses import dataclass
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
    event: Any = None  # AstrMessageEvent
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
            await self._cleanup_old_messages()

    async def has_new_messages(self) -> bool:
        """检查是否有新消息（未处理）"""
        async with self._lock:
            return any(not m.processed for m in self.messages)

    async def get_context(self) -> List[MessageContext]:
        """获取当前上下文"""
        async with self._lock:
            await self._cleanup_old_messages()
            return self.messages.copy()

    async def mark_all_processed(self):
        """标记所有消息为已处理"""
        async with self._lock:
            for m in self.messages:
                m.processed = True

    async def _cleanup_old_messages(self):
        """清理过期消息"""
        cutoff = datetime.now() - self.window
        self.messages = [m for m in self.messages if m.timestamp > cutoff]
