"""
每日聊天总结服务 - 生成群聊每日精华总结
"""

import json
import asyncio
import re
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass, field

import httpx
from astrbot.api import logger


@dataclass
class ChatMessage:
    """聊天消息"""

    timestamp: datetime
    sender_id: str
    sender_name: str
    content: str
    message_type: str = "text"  # text, image, file, etc.
    is_educational: bool = False
    importance_score: float = 0.0


@dataclass
class DailySummary:
    """每日总结"""

    date: datetime
    group_id: str
    total_messages: int
    active_users: int
    educational_content: List[ChatMessage] = field(default_factory=list)
    hot_topics: List[str] = field(default_factory=list)
    key_questions: List[Dict[str, Any]] = field(default_factory=list)
    summary_text: str = ""
    is_confirmed: bool = False
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[datetime] = None


class ChatSummarizerService:
    """每日聊天总结服务"""

    # 教育相关关键词
    EDUCATIONAL_KEYWORDS = [
        # 课程相关
        "课程",
        "考试",
        "作业",
        "学分",
        "绩点",
        "选课",
        "退课",
        "挂科",
        "补考",
        "重修",
        # 学术相关
        "论文",
        "实验",
        "报告",
        "答辩",
        "毕设",
        "科研",
        "项目",
        "竞赛",
        # 学习资源
        "资料",
        "课件",
        "笔记",
        "真题",
        "答案",
        "教材",
        "参考书",
        "复习",
        # 教师相关
        "老师",
        "教授",
        "讲师",
        "助教",
        "给分",
        "点名",
        # 其他
        "保研",
        "考研",
        "留学",
        "实习",
        "就业",
        "面试",
    ]

    def __init__(
        self,
        api_key: str,
        model: str = "MiniMax-M2.7",
        base_url: str = "https://api.minimaxi.com",
        summary_time: str = "22:30",
        min_educational_score: float = 0.6,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.summary_time = summary_time
        self.min_educational_score = min_educational_score
        self._pending_summaries: Dict[str, DailySummary] = {}  # 待确认总结
        self._lock = asyncio.Lock()
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def _parse_summary_time(self) -> tuple:
        """解析总结时间"""
        try:
            hour, minute = map(int, self.summary_time.split(":"))
            return hour, minute
        except ValueError:
            logger.warning(f"⚠️ 无效的时间格式 {self.summary_time}，使用默认22:30")
            return 22, 30

    def _calculate_next_run(self) -> float:
        """计算下次运行的时间（秒）"""
        now = datetime.now()
        hour, minute = self._parse_summary_time()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        if target <= now:
            target += timedelta(days=1)

        return (target - now).total_seconds()

    async def start(self, generate_callback: Callable[[str], Any]):
        """
        启动每日总结定时任务

        Args:
            generate_callback: 生成总结的回调函数，接收group_id
        """
        if self._running:
            logger.warning("⚠️ 每日总结服务已在运行")
            return

        self._running = True
        self._task = asyncio.create_task(self._summary_loop(generate_callback))
        logger.info(f"✅ 每日总结服务已启动，将在 {self.summary_time} 生成总结")

    async def stop(self):
        """停止每日总结服务"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("🛑 每日总结服务已停止")

    async def _summary_loop(self, generate_callback: Callable[[str], Any]):
        """总结循环"""
        while self._running:
            try:
                wait_seconds = self._calculate_next_run()
                logger.info(f"⏰ 距离下次每日总结还有 {wait_seconds / 3600:.1f} 小时")
                await asyncio.sleep(wait_seconds)

                # 触发总结生成
                if self._running:
                    logger.info("📝 开始生成每日总结...")
                    # 这里应该遍历所有活跃的群
                    # 实际实现需要从外部传入群列表
                    await generate_callback("all")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ 每日总结循环错误: {e}")
                await asyncio.sleep(3600)  # 出错后1小时重试

    def _is_educational_content(self, content: str) -> tuple[bool, float]:
        """
        判断内容是否为教育相关内容

        Returns:
            (是否为教育内容, 重要性分数)
        """
        content_lower = content.lower()
        score = 0.0

        # 关键词匹配
        for keyword in self.EDUCATIONAL_KEYWORDS:
            if keyword in content_lower:
                score += 0.2

        # 问题模式匹配
        question_patterns = ["?", "？", "怎么", "如何", "请问", "有没有", "求"]
        for pattern in question_patterns:
            if pattern in content:
                score += 0.1

        # 限制分数在0-1之间
        score = min(score, 1.0)

        return score >= self.min_educational_score, score

    async def analyze_messages(self, messages: List[ChatMessage]) -> List[ChatMessage]:
        """
        分析消息，标记教育相关内容

        Args:
            messages: 原始消息列表

        Returns:
            标记后的消息列表
        """
        for msg in messages:
            is_edu, score = self._is_educational_content(msg.content)
            msg.is_educational = is_edu
            msg.importance_score = score

        return messages

    async def generate_summary(
        self,
        group_id: str,
        messages: List[ChatMessage],
        use_ai: bool = True,
    ) -> DailySummary:
        """
        生成每日总结

        Args:
            group_id: 群ID
            messages: 当天的消息列表
            use_ai: 是否使用AI生成总结

        Returns:
            DailySummary 每日总结
        """
        logger.info(f"📝 正在为群 {group_id} 生成每日总结...")

        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        analyzed_messages = await self.analyze_messages(messages)

        total_messages = len(analyzed_messages)
        active_users = len(set(m.sender_id for m in analyzed_messages))
        educational_messages = [m for m in analyzed_messages if m.is_educational]

        summary_text = ""
        hot_topics = []
        key_questions = []

        if use_ai and self.api_key and educational_messages:
            try:
                ai_summary = await self._generate_summary_json(
                    self._build_summary_prompt(educational_messages)
                )
                summary_text = ai_summary.get("summary", "")
                hot_topics = ai_summary.get("hot_topics", [])
                key_questions = ai_summary.get("key_questions", [])
            except Exception as e:
                logger.error(f"❌ AI总结生成失败: {e}")
                summary_text = self._generate_simple_summary(educational_messages)
        else:
            summary_text = self._generate_simple_summary(educational_messages)

        summary = DailySummary(
            date=today,
            group_id=group_id,
            total_messages=total_messages,
            active_users=active_users,
            educational_content=educational_messages[:20],
            hot_topics=hot_topics,
            key_questions=key_questions,
            summary_text=summary_text,
        )

        async with self._lock:
            self._pending_summaries[group_id] = summary

        logger.info(f"✅ 群 {group_id} 每日总结生成完成")
        return summary

    def _strip_reasoning_tags(self, text: str) -> str:
        """移除 MiniMax 思考过程标签及其内容"""
        if not text:
            return text
        reasoning_pattern = re.compile(
            r"<\|MiniMax[_\s]?Reasoning\|>.*?<\|Assistant\|>", re.DOTALL
        )
        text = reasoning_pattern.sub("", text)
        thinking_boss_pattern = re.compile(r"<\|Thinking\|>.*?<\|Output\|>", re.DOTALL)
        text = thinking_boss_pattern.sub("", text)
        return text.strip()

    def _build_summary_prompt(self, messages: List[ChatMessage]) -> str:
        """构建总结提示"""
        context = "\n".join([f"{m.sender_name}: {m.content}" for m in messages[:50]])
        return f"""分析以下群聊消息，生成每日精华总结。

消息内容:
{context}

请输出JSON格式:
{{
  "summary": "总结文本（200字以内）",
  "hot_topics": ["热门话题1", "热门话题2", ...],
  "key_questions": [
    {{
      "question": "问题内容",
      "asker": "提问者",
      "has_answer": true/false
    }}
  ]
}}

要求:
1. 重点关注学习、课程、考试相关的内容
2. 提取有价值的问题和讨论
3. 总结要简洁明了"""

    async def _generate_summary_json(self, prompt: str) -> Dict[str, Any]:
        """生成结构化总结（调用AI）"""

        url = f"{self.base_url}/v1/text/chatcompletion_v2"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "name": "MiniMax AI"},
                {"role": "user", "content": prompt, "name": "用户"},
            ],
            "temperature": 0.3,
            "max_tokens": 4096,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        text = None
        choices = data.get("choices") if isinstance(data, dict) else None
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(msg, dict):
                text = msg.get("content")

        if not text and isinstance(data, dict):
            text = data.get("reply") or data.get("output")

        if not isinstance(text, str) or not text.strip():
            raise ValueError(
                f"MiniMax响应缺少可解析文本: keys={list(data.keys()) if isinstance(data, dict) else type(data)}"
            )

        text = self._strip_reasoning_tags(text).strip()
        return json.loads(text)

    def _generate_simple_summary(self, messages: List[ChatMessage]) -> str:
        """生成简单总结（无AI时）"""
        if not messages:
            return "今日暂无教育相关内容"

        topics = set()
        for msg in messages:
            for keyword in self.EDUCATIONAL_KEYWORDS:
                if keyword in msg.content.lower():
                    topics.add(keyword)

        summary = f"今日共有 {len(messages)} 条教育相关讨论"
        if topics:
            summary += f"，涉及主题: {', '.join(list(topics)[:5])}"

        return summary

    async def confirm_summary(
        self, group_id: str, admin_id: str, confirmed: bool = True
    ) -> bool:
        """
        管理员确认总结

        Args:
            group_id: 群ID
            admin_id: 管理员ID
            confirmed: 是否确认

        Returns:
            bool 是否成功
        """
        async with self._lock:
            if group_id not in self._pending_summaries:
                logger.warning(f"⚠️ 群 {group_id} 没有待确认的总结")
                return False

            summary = self._pending_summaries[group_id]

            if confirmed:
                summary.is_confirmed = True
                summary.confirmed_by = admin_id
                summary.confirmed_at = datetime.now()
                logger.info(f"✅ 群 {group_id} 的每日总结已确认")
                return True
            else:
                # 拒绝总结，从待确认列表移除
                del self._pending_summaries[group_id]
                logger.info(f"❌ 群 {group_id} 的每日总结已拒绝")
                return True

    def get_pending_summary(self, group_id: str) -> Optional[DailySummary]:
        """获取待确认的总结"""
        return self._pending_summaries.get(group_id)

    def format_summary_for_display(self, summary: DailySummary) -> str:
        """格式化总结用于显示"""
        msg = f"""📊 {summary.date.strftime("%Y年%m月%d日")} 群聊精华总结

💬 今日概况:
• 总消息数: {summary.total_messages}
• 活跃用户: {summary.active_users}
• 教育相关内容: {len(summary.educational_content)}

📝 内容摘要:
{summary.summary_text}
"""

        if summary.hot_topics:
            msg += f"\n🔥 热门话题:\n"
            for i, topic in enumerate(summary.hot_topics[:5], 1):
                msg += f"{i}. {topic}\n"

        if summary.key_questions:
            msg += f"\n❓ 待解决问题:\n"
            for q in summary.key_questions[:3]:
                status = "✅" if q.get("has_answer") else "❓"
                msg += f"{status} {q.get('question', '')}\n"

        if not summary.is_confirmed:
            msg += "\n⚠️ 此总结待管理员确认后才会发布"

        return msg

    def get_status(self) -> Dict[str, Any]:
        """获取服务状态"""
        return {
            "running": self._running,
            "summary_time": self.summary_time,
            "pending_summaries": len(self._pending_summaries),
            "api_configured": bool(self.api_key),
        }
