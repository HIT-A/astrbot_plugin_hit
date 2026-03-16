"""
HIT智能助手 - AstrBot插件 v1.0.0

功能：
- 智能意图识别（Gemini 2.5 Flash）
- 课程查询
- 资料搜索
- 群聊每日总结
- 智能贡献引导

作者: HIT-A
"""

import os
import json
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any
from dataclasses import dataclass

import httpx
from astrbot.api.all import *
from astrbot.api.event import filter
from astrbot.api.star import Context, Star, register
from astrbot.api import logger


@dataclass
class MessageContext:
    """消息上下文"""

    timestamp: datetime
    sender_id: str
    sender_name: str
    content: str
    message_type: str
    group_id: Optional[str] = None
    event: Any = None
    processed: bool = False


@dataclass
class IntentResult:
    """意图识别结果"""

    has_valid_intent: bool
    intent: str
    confidence: float
    extracted_info: Dict[str, Any]
    reasoning: str


class MessageQueue:
    """3分钟滑动窗口消息队列"""

    def __init__(self, window_minutes: int = 3):
        self.window = timedelta(minutes=window_minutes)
        self.messages: List[MessageContext] = []
        self._lock = asyncio.Lock()

    async def add_message(self, msg: MessageContext):
        async with self._lock:
            self.messages.append(msg)
            await self._cleanup()

    async def has_new_messages(self) -> bool:
        async with self._lock:
            return any(not m.processed for m in self.messages)

    async def get_context(self) -> List[MessageContext]:
        async with self._lock:
            await self._cleanup()
            return self.messages.copy()

    async def mark_all_processed(self):
        async with self._lock:
            for m in self.messages:
                m.processed = True

    async def _cleanup(self):
        cutoff = datetime.now() - self.window
        self.messages = [m for m in self.messages if m.timestamp > cutoff]


@register("astrbot_plugin_hit", "HIT-A", "HIT智能助手", "1.0.0")
class HITPlugin(Star):
    """HIT智能助手插件"""

    def __init__(self, context: Context):
        super().__init__(context)

        # 配置
        self.gemini_api_key = os.getenv("HITSZ_GEMINI_API_KEY", "")
        self.gemini_model = "gemini-2.5-flash-preview-05-20"
        self.agent_backend_url = os.getenv(
            "HITSZ_AGENT_BACKEND_URL", "http://localhost:8080"
        )
        self.agent_backend_api_key = os.getenv("HITSZ_AGENT_BACKEND_API_KEY", "")

        # 消息队列
        self.message_queues: Dict[str, MessageQueue] = {}

        # 统计
        self.gemini_calls_today = 0
        self.last_reset_date = datetime.now().date()

        # 定时任务
        self._tasks = []

    async def initialize(self):
        """插件初始化"""
        logger.info("🚀 HIT智能助手插件初始化中...")
        self._tasks.append(asyncio.create_task(self._intent_check_loop()))
        self._tasks.append(asyncio.create_task(self._daily_summary_loop()))
        logger.info("✅ HIT智能助手插件初始化完成")

    def _check_and_reset_quota(self):
        """检查并重置额度"""
        today = datetime.now().date()
        if today != self.last_reset_date:
            self.gemini_calls_today = 0
            self.last_reset_date = today

    # ==================== 消息监听 ====================

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        await self._handle_message(event, "group")

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    async def on_private_message(self, event: AstrMessageEvent):
        await self._handle_message(event, "private")

    async def _handle_message(self, event: AstrMessageEvent, msg_type: str):
        content = event.get_message_outline()
        if not content or not content.strip():
            return

        if msg_type == "group":
            queue_key = f"group:{event.get_group_id()}"
        else:
            queue_key = f"private:{event.get_sender_id()}"

        if queue_key not in self.message_queues:
            self.message_queues[queue_key] = MessageQueue()

        msg_ctx = MessageContext(
            timestamp=datetime.now(),
            sender_id=event.get_sender_id(),
            sender_name=event.get_sender_name(),
            content=content,
            message_type=msg_type,
            group_id=event.get_group_id() if msg_type == "group" else None,
            event=event,
        )

        await self.message_queues[queue_key].add_message(msg_ctx)

    # ==================== 意图判断循环 ====================

    async def _intent_check_loop(self):
        """意图判断循环（每3分钟）"""
        while True:
            try:
                self._check_and_reset_quota()
                now = datetime.now()

                if 8 <= now.hour < 22:
                    for queue_key, queue in list(self.message_queues.items()):
                        if await queue.has_new_messages():
                            if self.gemini_calls_today >= 250:
                                logger.warning("⚠️ Gemini日额度已用完")
                                break
                            await self._analyze_and_respond(queue_key, queue)

                await asyncio.sleep(180)
            except Exception as e:
                logger.error(f"❌ 意图判断循环错误: {e}")
                await asyncio.sleep(60)

    async def _analyze_and_respond(self, queue_key: str, queue: MessageQueue):
        """分析意图并响应"""
        try:
            messages = await queue.get_context()
            if not messages:
                return

            context_text = "\n".join(
                [f"{m.sender_name}: {m.content}" for m in messages[-10:]]
            )

            intent_result = await self._call_gemini_for_intent(context_text)
            self.gemini_calls_today += 1

            if not intent_result or not intent_result.has_valid_intent:
                logger.debug(f"队列 {queue_key} 无有效意图")
                await queue.mark_all_processed()
                return

            logger.info(f"🎯 检测到意图: {intent_result.intent}")

            last_msg = messages[-1]
            if last_msg.event:
                await self._execute_intent(last_msg.event, intent_result)

            await queue.mark_all_processed()
        except Exception as e:
            logger.error(f"❌ 分析意图失败: {e}")

    async def _call_gemini_for_intent(
        self, context_text: str
    ) -> Optional[IntentResult]:
        """调用Gemini判断意图"""
        if not self.gemini_api_key:
            return None

        prompt = f"""分析以下对话上下文，判断用户的真实意图。

对话上下文:
{context_text}

输出JSON格式:
{{
  "has_valid_intent": true/false,
  "intent": "course_query|file_search|deep_search|contribution|chat",
  "confidence": 0.95,
  "extracted_info": {{
    "course_code": "",
    "course_name": "",
    "teacher_name": "",
    "keywords": []
  }},
  "reasoning": ""
}}"""

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent",
                    headers={"Content-Type": "application/json"},
                    params={"key": self.gemini_api_key},
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"responseMimeType": "application/json"},
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                result_json = json.loads(text)

                return IntentResult(
                    has_valid_intent=result_json.get("has_valid_intent", False),
                    intent=result_json.get("intent", "chat"),
                    confidence=result_json.get("confidence", 0.0),
                    extracted_info=result_json.get("extracted_info", {}),
                    reasoning=result_json.get("reasoning", ""),
                )
        except Exception as e:
            logger.error(f"调用Gemini失败: {e}")
            return None

    async def _execute_intent(self, event: AstrMessageEvent, intent: IntentResult):
        """根据意图执行操作"""
        if intent.intent == "course_query":
            query = intent.extracted_info.get(
                "course_code"
            ) or intent.extracted_info.get("course_name", "")
            if query:
                await self.hit_course_search(event, query)
            else:
                await event.send(event.plain_result("💡 请提供课程代码或名称"))

        elif intent.intent == "file_search":
            keywords = intent.extracted_info.get("keywords", [])
            query = " ".join(keywords) if keywords else "资料"
            await event.send(event.plain_result(f"🔍 正在搜索「{query}」相关资料..."))

        elif intent.intent == "deep_search":
            keywords = intent.extracted_info.get("keywords", [])
            query = " ".join(keywords) if keywords else ""
            if query:
                await event.send(event.plain_result(f"🔍 正在深度搜索「{query}」..."))
            else:
                await event.send(event.plain_result("💡 请提供搜索内容"))

        elif intent.intent == "contribution":
            await event.send(
                event.plain_result(
                    "💡 我检测到您想分享有价值的内容！\n\n"
                    "您可以通过以下方式贡献：\n"
                    "1. 分享课程评价：直接发送评价内容\n"
                    "2. 分享学习资料：发送文件或链接\n"
                    "3. 使用 /hit contribute 进入贡献流程"
                )
            )

    # ==================== Tools ====================

    @filter.llm_tool(name="hit_course_search")
    async def hit_course_search(self, event: AstrMessageEvent, query: str):
        """搜索哈工大课程信息"""
        try:
            result = await self._call_agent_backend(
                "courses.search", {"keyword": query, "limit": 10}
            )

            if not result.get("ok"):
                error = result.get("error", {})
                yield event.plain_result(
                    f"❌ 搜索失败: {error.get('message', '未知错误')}"
                )
                return

            courses = result.get("output", {}).get("results", [])

            if not courses:
                msg = f"🔍 未找到包含「{query}」的课程\n\n"
                msg += "💡 您可以通过以下方式贡献:\n"
                msg += "1. 使用 /hit contribute 提交课程信息\n"
                msg += "2. 分享课程资料"
                yield event.plain_result(msg)
                return

            msg = f"🔍 找到 {len(courses)} 个相关课程:\n\n"
            for i, course in enumerate(courses[:5], 1):
                code = course.get("code", "未知")
                name = course.get("name", "未知")
                msg += f"{i}. {code} - {name}\n"

            if len(courses) > 5:
                msg += f"\n... 还有 {len(courses) - 5} 个结果"

            msg += "\n\n💡 使用 /hit course <课程代码> 查看详情"
            yield event.plain_result(msg)

        except Exception as e:
            logger.error(f"搜索课程失败: {e}")
            yield event.plain_result(f"❌ 搜索失败: {str(e)}")

    async def _call_agent_backend(self, skill_name: str, input_data: dict) -> dict:
        """调用agent-backend"""
        url = f"{self.agent_backend_url}/v1/skills/{skill_name}:invoke"
        headers = {"Content-Type": "application/json"}
        if self.agent_backend_api_key:
            headers["Authorization"] = f"Bearer {self.agent_backend_api_key}"

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=headers, json={"input": input_data})
            resp.raise_for_status()
            return resp.json()

    # ==================== 每日总结循环 ====================

    async def _daily_summary_loop(self):
        """每日总结循环"""
        while True:
            try:
                now = datetime.now()
                target = now.replace(hour=22, minute=30, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)

                wait_seconds = (target - now).total_seconds()
                logger.info(f"⏰ 距离下次每日总结还有 {wait_seconds / 3600:.1f} 小时")
                await asyncio.sleep(wait_seconds)

                # TODO: 生成每日总结
                logger.info("📝 生成每日总结...")

            except Exception as e:
                logger.error(f"❌ 每日总结循环错误: {e}")
                await asyncio.sleep(3600)

    # ==================== 指令 ====================

    @filter.command_group("hit")
    def hit_group(self):
        """HIT智能助手指令组"""
        pass

    @hit_group.command("help")
    async def help_cmd(self, event: AstrMessageEvent):
        """显示帮助信息"""
        msg = """🎓 HIT智能助手 v1.0.0

📚 课程查询:
  /hit course <关键词> - 搜索课程

💡 智能功能:
  - 自动识别课程查询意图
  - 自动识别资料分享意图
  - 每日22:30总结群聊精华

📝 贡献:
  /hit contribute - 提交课程评价或资料

📊 状态:
  /hit status - 查看插件状态
"""
        yield event.plain_result(msg)

    @hit_group.command("course")
    async def course_cmd(self, event: AstrMessageEvent, query: str = ""):
        """搜索课程"""
        if not query:
            yield event.plain_result("❌ 请输入搜索关键词\n用法: /hit course 自动控制")
            return

        async for result in self.hit_course_search(event, query):
            yield result

    @hit_group.command("status")
    async def status_cmd(self, event: AstrMessageEvent):
        """查看插件状态"""
        self._check_and_reset_quota()
        remaining = 250 - self.gemini_calls_today

        msg = f"""📊 HIT智能助手状态

🤖 Gemini调用:
  今日已调用: {self.gemini_calls_today}/250
  剩余额度: {remaining}

💬 消息队列:
  活跃队列数: {len(self.message_queues)}

⏰ 定时任务:
  意图判断: {"✅ 运行中" if self._tasks and not self._tasks[0].done() else "❌ 已停止"}
  每日总结: {"✅ 运行中" if len(self._tasks) > 1 and not self._tasks[1].done() else "❌ 已停止"}
"""
        yield event.plain_result(msg)

    async def terminate(self):
        """插件卸载"""
        logger.info("🛑 HIT智能助手插件卸载中...")
        for task in self._tasks:
            if task and not task.done():
                task.cancel()
        logger.info("✅ HIT智能助手插件已卸载")
