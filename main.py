"""
HIT智能助手 - AstrBot插件 v2.0.0

模块化架构，包含:
- 智能意图识别（Gemini 2.5 Flash）
- 课程查询
- 资料搜索（群文件/COS/深度搜索）
- 群聊每日总结
- 智能贡献引导
- 群文件主动扫描

作者: HIT-A
版本: v2.0.0
"""

import os
import asyncio
from datetime import datetime
from typing import Dict, Optional

from astrbot.api.all import *
from astrbot.api.event import filter
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

from .core import ConfigManager, MessageContext, MessageQueueManager
from .services import (
    IntentService,
    FileScannerService,
    ChatSummarizerService,
    ContributionService,
    AgentClient,
)


@register("astrbot_plugin_hit", "HIT-A", "HIT智能助手", "2.0.0")
class HITPlugin(Star):
    """HIT智能助手插件主类"""

    def __init__(self, context: Context):
        super().__init__(context)

        # 配置管理
        self.config_manager = ConfigManager()

        # 消息队列管理
        self.queue_manager = MessageQueueManager()

        # 初始化服务
        self._init_services()

        # 定时任务
        self._tasks: list = []

    def _init_services(self):
        """初始化服务"""
        config = self.config_manager.config

        # 意图分析服务
        self.intent_service = IntentService(
            api_key=config.gemini_api_key,
            model=config.gemini_model,
            daily_quota=config.gemini_daily_quota,
            context_window_minutes=config.context_window_minutes,
        )

        # Agent后端客户端
        self.agent_client = AgentClient(
            base_url=config.agent_backend_url,
            api_key=config.agent_backend_api_key,
        )

        # 文件扫描服务
        self.file_scanner = FileScannerService(
            plugin=self,
            agent_client=self.agent_client,
        )

        # 聊天总结服务
        self.chat_summarizer = ChatSummarizerService(
            plugin=self,
            agent_client=self.agent_client,
        )

        # 贡献引导服务
        self.contribution_service = ContributionService(
            plugin=self,
            agent_client=self.agent_client,
        )

    async def initialize(self):
        """插件初始化"""
        logger.info("🚀 HIT智能助手 v2.0.0 初始化中...")

        # 启动定时任务
        self._tasks.append(asyncio.create_task(self._intent_check_loop()))
        self._tasks.append(asyncio.create_task(self._daily_summary_loop()))

        logger.info("✅ HIT智能助手初始化完成")
        logger.info(
            f"📊 配置: Gemini额度={self.config_manager.config.gemini_daily_quota}/天"
        )

    # ==================== 消息监听 ====================

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """处理群消息"""
        await self._handle_message(event, "group")

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    async def on_private_message(self, event: AstrMessageEvent):
        """处理私聊消息"""
        await self._handle_message(event, "private")

    async def _handle_message(self, event: AstrMessageEvent, msg_type: str):
        """处理消息"""
        content = event.get_message_outline()
        if not content or not content.strip():
            return

        # 获取队列key
        if msg_type == "group":
            queue_key = f"group:{event.get_group_id()}"
        else:
            queue_key = f"private:{event.get_sender_id()}"

        # 获取或创建队列
        queue = self.queue_manager.get_or_create_queue(
            queue_key, self.config_manager.config.context_window_minutes
        )

        # 创建消息上下文
        msg_ctx = MessageContext(
            timestamp=datetime.now(),
            sender_id=event.get_sender_id(),
            sender_name=event.get_sender_name(),
            content=content,
            message_type=msg_type,
            group_id=event.get_group_id() if msg_type == "group" else None,
            event=event,
        )

        # 添加到队列
        await queue.add_message(msg_ctx)
        logger.debug(f"消息已添加到队列 {queue_key}: {content[:50]}...")

    # ==================== 意图判断循环 ====================

    async def _intent_check_loop(self):
        """意图判断循环（每3分钟）"""
        while True:
            try:
                now = datetime.now()

                # 检查是否在激活时间（08:00-22:00）
                if 8 <= now.hour < 22:
                    for queue_key, queue in list(
                        self.queue_manager.get_all_queues().items()
                    ):
                        if await queue.has_new_messages():
                            await self._analyze_and_respond(queue_key, queue)

                await asyncio.sleep(self.config_manager.config.intent_check_interval)

            except Exception as e:
                logger.error(f"❌ 意图判断循环错误: {e}")
                await asyncio.sleep(60)

    async def _analyze_and_respond(self, queue_key: str, queue):
        """分析意图并响应"""
        try:
            messages = await queue.get_context()
            if not messages:
                return

            # 构建上下文文本
            context_text = "\n".join(
                [f"{m.sender_name}: {m.content}" for m in messages[-10:]]
            )

            # 调用意图分析服务
            intent_result = await self.intent_service.analyze_intent(context_text)

            if not intent_result or not intent_result.has_valid_intent:
                logger.debug(f"队列 {queue_key} 无有效意图")
                await queue.mark_all_processed()
                return

            # 获取最后一条消息
            last_msg = messages[-1]
            if not last_msg.event:
                return

            # 根据意图执行操作
            await self._execute_intent(last_msg.event, intent_result)

            # 标记为已处理
            await queue.mark_all_processed()

        except Exception as e:
            logger.error(f"❌ 分析意图失败: {e}")

    async def _execute_intent(self, event: AstrMessageEvent, intent_result):
        """根据意图执行操作"""
        from .services.intent_service import IntentType

        intent_handlers = {
            IntentType.COURSE_QUERY: self._handle_course_query,
            IntentType.FILE_SEARCH: self._handle_file_search,
            IntentType.DEEP_SEARCH: self._handle_deep_search,
            IntentType.CONTRIBUTION: self._handle_contribution,
        }

        handler = intent_handlers.get(intent_result.intent)
        if handler:
            await handler(event, intent_result)

    async def _handle_course_query(self, event: AstrMessageEvent, intent_result):
        """处理课程查询"""
        query = (
            intent_result.extracted_info.get("course_code")
            or intent_result.extracted_info.get("course_name")
            or intent_result.extracted_info.get("keywords", [""])[0]
        )

        if not query:
            await event.send(
                event.plain_result(
                    "💡 我检测到您想查询课程信息，但未能识别具体课程。\n"
                    "请提供课程代码或名称，例如：AUTO1001 或 自动控制原理"
                )
            )
            return

        await self._search_course(event, query)

    async def _handle_file_search(self, event: AstrMessageEvent, intent_result):
        """处理文件搜索"""
        keywords = intent_result.extracted_info.get("keywords", [])
        query = " ".join(keywords) if keywords else "资料"

        await event.send(event.plain_result(f"🔍 正在为您搜索「{query}」相关资料..."))
        # TODO: 调用文件搜索服务

    async def _handle_deep_search(self, event: AstrMessageEvent, intent_result):
        """处理深度搜索"""
        keywords = intent_result.extracted_info.get("keywords", [])
        query = " ".join(keywords) if keywords else ""

        if not query:
            await event.send(
                event.plain_result("💡 我检测到您想进行深度搜索，请提供具体搜索内容。")
            )
            return

        await event.send(event.plain_result(f"🔍 正在深度搜索「{query}」..."))
        # TODO: 调用深度搜索服务

    async def _handle_contribution(self, event: AstrMessageEvent, intent_result):
        """处理贡献引导"""
        await self.contribution_service.guide_contribution(event, intent_result)

    async def _search_course(self, event: AstrMessageEvent, query: str):
        """搜索课程"""
        try:
            result = await self.agent_client.call_skill(
                "courses.search", {"keyword": query, "limit": 10}
            )

            if not result.get("ok"):
                error = result.get("error", {})
                await event.send(
                    event.plain_result(
                        f"❌ 搜索失败: {error.get('message', '未知错误')}"
                    )
                )
                return

            courses = result.get("output", {}).get("results", [])

            if not courses:
                msg = f"🔍 未找到包含「{query}」的课程\n\n"
                msg += "💡 您可以通过以下方式贡献:\n"
                msg += "1. 使用 /hit contribute 提交课程信息\n"
                msg += "2. 分享课程资料"
                await event.send(event.plain_result(msg))
                return

            msg = f"🔍 找到 {len(courses)} 个相关课程:\n\n"
            for i, course in enumerate(courses[:5], 1):
                code = course.get("code", "未知")
                name = course.get("name", "未知")
                msg += f"{i}. {code} - {name}\n"

            if len(courses) > 5:
                msg += f"\n... 还有 {len(courses) - 5} 个结果"

            msg += "\n\n💡 使用 /hit course <课程代码> 查看详情"
            await event.send(event.plain_result(msg))

        except Exception as e:
            logger.error(f"搜索课程失败: {e}")
            await event.send(event.plain_result(f"❌ 搜索失败: {str(e)}"))

    # ==================== 每日总结循环 ====================

    async def _daily_summary_loop(self):
        """每日总结循环"""
        while True:
            try:
                now = datetime.now()
                target_hour, target_minute = map(
                    int, self.config_manager.config.daily_summary_time.split(":")
                )
                target = now.replace(
                    hour=target_hour, minute=target_minute, second=0, microsecond=0
                )

                if target <= now:
                    target += __import__("datetime").timedelta(days=1)

                wait_seconds = (target - now).total_seconds()
                logger.info(f"⏰ 距离下次每日总结还有 {wait_seconds / 3600:.1f} 小时")

                await asyncio.sleep(wait_seconds)

                # 执行每日总结
                await self.chat_summarizer.generate_summary()

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
        msg = """🎓 HIT智能助手 v2.0.0

📚 课程查询:
  /hit course <关键词> - 搜索课程

🔍 资料搜索:
  /hit file <关键词> - 搜索群文件
  /hit search <关键词> - 深度搜索

📁 群文件:
  /hit scan - 主动扫描群文件
  /hit ingest - 入库已扫描文件

💡 智能功能:
  - 自动识别课程查询意图
  - 自动识别资料分享意图
  - 每日22:30总结群聊精华

📝 贡献:
  /hit contribute - 提交课程评价或资料

📊 状态:
  /hit status - 查看插件状态
"""
        await event.send(event.plain_result(msg))

    @hit_group.command("course")
    async def course_cmd(self, event: AstrMessageEvent, query: str = ""):
        """搜索课程"""
        if not query:
            await event.send(
                event.plain_result("❌ 请输入搜索关键词\n用法: /hit course 自动控制")
            )
            return

        await self._search_course(event, query)

    @hit_group.command("scan")
    async def scan_cmd(self, event: AstrMessageEvent):
        """扫描群文件"""
        if not event.is_group_chat():
            await event.send(event.plain_result("❌ 该指令仅在群聊中可用"))
            return

        await event.send(event.plain_result("📁 开始扫描群文件，请稍候..."))

        try:
            result = await self.file_scanner.scan_group_files(event.get_group_id())
            await event.send(event.plain_result(result))
        except Exception as e:
            logger.error(f"扫描群文件失败: {e}")
            await event.send(event.plain_result(f"❌ 扫描失败: {str(e)}"))

    @hit_group.command("status")
    async def status_cmd(self, event: AstrMessageEvent):
        """查看插件状态"""
        quota_status = self.intent_service.get_quota_status()

        msg = f"""📊 HIT智能助手状态

🤖 Gemini调用:
  今日已调用: {quota_status["calls_today"]}/{quota_status["daily_quota"]}
  剩余额度: {quota_status["remaining"]}

💬 消息队列:
  活跃队列数: {len(self.queue_manager.get_all_queues())}

⏰ 定时任务:
  意图判断: {"✅ 运行中" if self._tasks and any(not t.done() for t in self._tasks[:1]) else "❌ 已停止"}
  每日总结: {"✅ 运行中" if self._tasks and len(self._tasks) > 1 and not self._tasks[1].done() else "❌ 已停止"}

📦 服务状态:
  意图服务: {"✅" if self.intent_service else "❌"}
  Agent客户端: {"✅" if self.agent_client else "❌"}
  文件扫描: {"✅" if self.file_scanner else "❌"}
  聊天总结: {"✅" if self.chat_summarizer else "❌"}
"""
        await event.send(event.plain_result(msg))

    async def terminate(self):
        """插件卸载"""
        logger.info("🛑 HIT智能助手插件卸载中...")

        # 取消所有定时任务
        for task in self._tasks:
            if task and not task.done():
                task.cancel()

        # 等待任务取消
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        logger.info("✅ HIT智能助手插件已卸载")
