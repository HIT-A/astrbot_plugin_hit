"""
HIT智能助手 - AstrBot插件 v2.0.1

模块化架构，包含:
- 智能意图识别（Gemini 2.5 Flash）
- 课程查询
- 资料搜索（群文件/COS/深度搜索）
- 群聊每日总结
- 智能贡献引导
- 群文件主动扫描

作者: HIT-A
版本: v2.0.1 (Debug版本)
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

# Debug模式开关
DEBUG_MODE = os.getenv("HITSZ_DEBUG_MODE", "true").lower() == "true"


def debug_log(msg: str, level: str = "INFO"):
    """统一的debug日志输出"""
    prefix = "[HIT_DEBUG]"
    full_msg = f"{prefix} {msg}"

    if level == "INFO":
        logger.info(full_msg)
    elif level == "DEBUG":
        if DEBUG_MODE:
            logger.debug(full_msg)
    elif level == "WARNING":
        logger.warning(full_msg)
    elif level == "ERROR":
        logger.error(full_msg)


@register("astrbot_plugin_hit", "HIT-A", "HIT智能助手", "2.0.1")
class HITPlugin(Star):
    """HIT智能助手插件主类"""

    def __init__(self, context: Context):
        super().__init__(context)
        debug_log("=" * 60)
        debug_log("HITPlugin 初始化开始")
        debug_log(f"Debug模式: {DEBUG_MODE}")

        # 配置管理
        debug_log("正在加载配置管理器...")
        self.config_manager = ConfigManager()
        debug_log(
            f"配置加载完成: Gemini额度={self.config_manager.config.gemini_daily_quota}"
        )

        # 消息队列管理
        debug_log("正在初始化消息队列管理器...")
        self.queue_manager = MessageQueueManager()
        debug_log("消息队列管理器初始化完成")

        # 初始化服务
        debug_log("正在初始化服务...")
        self._init_services()

        # 定时任务
        self._tasks: list = []
        debug_log("HITPlugin 初始化完成")
        debug_log("=" * 60)

    def _init_services(self):
        """初始化服务"""
        config = self.config_manager.config

        # 意图分析服务
        debug_log(f"初始化 IntentService: model={config.gemini_model}")
        if not config.gemini_api_key:
            debug_log("警告: Gemini API Key 未配置！意图判断功能将不可用", "WARNING")
        else:
            debug_log(f"Gemini API Key 已配置: {config.gemini_api_key[:10]}...")

        self.intent_service = IntentService(
            api_key=config.gemini_api_key,
            model=config.gemini_model,
            daily_quota=config.gemini_daily_quota,
            context_window_minutes=config.context_window_minutes,
        )
        debug_log("IntentService 初始化完成")

        # Agent后端客户端
        debug_log(f"初始化 AgentClient: url={config.agent_backend_url}")
        if not config.agent_backend_api_key:
            debug_log("警告: Agent Backend API Key 未配置", "WARNING")

        self.agent_client = AgentClient(
            base_url=config.agent_backend_url,
            api_key=config.agent_backend_api_key,
        )
        debug_log("AgentClient 初始化完成")

        # 文件扫描服务
        debug_log("初始化 FileScannerService...")
        self.file_scanner = FileScannerService(
            plugin=self,
            agent_client=self.agent_client,
        )
        debug_log("FileScannerService 初始化完成")

        # 聊天总结服务
        debug_log("初始化 ChatSummarizerService...")
        self.chat_summarizer = ChatSummarizerService(
            plugin=self,
            agent_client=self.agent_client,
        )
        debug_log("ChatSummarizerService 初始化完成")

        # 贡献引导服务
        debug_log("初始化 ContributionService...")
        self.contribution_service = ContributionService(
            plugin=self,
            agent_client=self.agent_client,
        )
        debug_log("ContributionService 初始化完成")
        debug_log("所有服务初始化完成")

    async def initialize(self):
        """插件初始化"""
        debug_log("=" * 60)
        debug_log("🚀 HIT智能助手 v2.0.1 初始化中...")
        debug_log(f"当前时间: {datetime.now().isoformat()}")
        debug_log(f"配置信息:")
        debug_log(f"  - Gemini模型: {self.config_manager.config.gemini_model}")
        debug_log(f"  - Gemini额度: {self.config_manager.config.gemini_daily_quota}/天")
        debug_log(
            f"  - 上下文窗口: {self.config_manager.config.context_window_minutes}分钟"
        )
        debug_log(f"  - Agent后端: {self.config_manager.config.agent_backend_url}")
        debug_log(
            f"  - 意图检查间隔: {self.config_manager.config.intent_check_interval}秒"
        )
        debug_log(f"  - 每日总结时间: {self.config_manager.config.daily_summary_time}")

        # 启动定时任务
        debug_log("启动定时任务...")
        debug_log("  - 启动意图检查循环")
        self._tasks.append(asyncio.create_task(self._intent_check_loop()))
        debug_log("  - 启动每日总结循环")
        self._tasks.append(asyncio.create_task(self._daily_summary_loop()))
        debug_log(f"  - 已启动 {len(self._tasks)} 个定时任务")

        debug_log("✅ HIT智能助手初始化完成")
        debug_log("=" * 60)

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
            debug_log(f"收到空消息，跳过处理 [{msg_type}]", "DEBUG")
            return

        # 获取队列key
        if msg_type == "group":
            queue_key = f"group:{event.get_group_id()}"
            debug_log(
                f"[群消息] group_id={event.get_group_id()}, sender={event.get_sender_name()}",
                "DEBUG",
            )
        else:
            queue_key = f"private:{event.get_sender_id()}"
            debug_log(f"[私聊] sender={event.get_sender_name()}", "DEBUG")

        debug_log(f"消息内容: {content[:100]}...", "DEBUG")
        debug_log(f"队列key: {queue_key}", "DEBUG")

        # 获取或创建队列
        if queue_key not in self.queue_manager.queues:
            debug_log(f"创建新队列: {queue_key}", "DEBUG")

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
        queue_size = await queue.get_message_count()
        debug_log(f"消息已添加到队列 {queue_key}, 当前队列大小: {queue_size}", "DEBUG")

    # ==================== 意图判断循环 ====================

    async def _intent_check_loop(self):
        """意图判断循环（每3分钟）"""
        debug_log("意图检查循环已启动")
        loop_count = 0

        while True:
            try:
                loop_count += 1
                await self.intent_service._check_and_reset_quota()
                now = datetime.now()
                quota_status = self.intent_service.get_quota_status()

                debug_log(
                    f"[循环 #{loop_count}] 当前时间: {now.strftime('%H:%M:%S')}, Gemini额度: {quota_status['calls_today']}/{quota_status['daily_quota']}",
                    "DEBUG",
                )

                # 检查是否在激活时间（08:00-22:00）
                if 8 <= now.hour < 22:
                    queues = self.queue_manager.get_all_queues()
                    debug_log(f"检查 {len(queues)} 个消息队列...", "DEBUG")

                    checked_count = 0
                    processed_count = 0

                    for queue_key, queue in list(queues.items()):
                        has_new = await queue.has_new_messages()
                        checked_count += 1

                        if has_new:
                            debug_log(
                                f"队列 {queue_key} 有新消息，准备分析意图...", "DEBUG"
                            )

                            if quota_status["remaining"] <= 0:
                                debug_log(
                                    "⚠️ Gemini日额度已用完，跳过意图分析", "WARNING"
                                )
                                break

                            await self._analyze_and_respond(queue_key, queue)
                            processed_count += 1

                    debug_log(
                        f"检查了 {checked_count} 个队列，处理了 {processed_count} 个",
                        "DEBUG",
                    )
                else:
                    debug_log(
                        f"当前时间 {now.hour}:00 不在激活时段 (08:00-22:00)，跳过检查",
                        "DEBUG",
                    )

                # 等待3分钟
                wait_time = self.config_manager.config.intent_check_interval
                debug_log(f"等待 {wait_time} 秒后进行下一次检查...", "DEBUG")
                await asyncio.sleep(wait_time)

            except Exception as e:
                debug_log(f"❌ 意图判断循环错误: {e}", "ERROR")
                import traceback

                debug_log(f"错误堆栈: {traceback.format_exc()}", "ERROR")
                await asyncio.sleep(60)

    async def _analyze_and_respond(self, queue_key: str, queue):
        """分析意图并响应"""
        debug_log(f"开始分析队列 {queue_key} 的意图...")

        try:
            messages = await queue.get_context()
            if not messages:
                debug_log(f"队列 {queue_key} 为空，跳过", "DEBUG")
                return

            debug_log(f"队列中有 {len(messages)} 条消息", "DEBUG")

            # 构建上下文文本
            context_text = "\n".join(
                [f"{m.sender_name}: {m.content}" for m in messages[-10:]]
            )
            debug_log(f"上下文文本长度: {len(context_text)} 字符", "DEBUG")
            debug_log(f"上下文内容:\n{context_text[:200]}...", "DEBUG")

            # 调用意图分析服务
            debug_log("调用 Gemini 进行意图分析...")
            intent_result = await self.intent_service.analyze_intent(context_text)

            if intent_result:
                debug_log(
                    f"意图分析结果: has_valid_intent={intent_result.has_valid_intent}, intent={intent_result.intent}, confidence={intent_result.confidence:.2f}"
                )
                debug_log(f"推理: {intent_result.reasoning}", "DEBUG")
                debug_log(f"提取信息: {intent_result.extracted_info}", "DEBUG")
            else:
                debug_log("意图分析返回 None", "WARNING")

            if not intent_result or not intent_result.has_valid_intent:
                debug_log(f"队列 {queue_key} 无有效意图，标记为已处理", "DEBUG")
                await queue.mark_all_processed()
                return

            debug_log(
                f"🎯 检测到有效意图: {intent_result.intent}, 置信度: {intent_result.confidence:.2f}"
            )

            # 获取最后一条消息
            last_msg = messages[-1]
            if not last_msg.event:
                debug_log("最后一条消息没有 event 对象，无法响应", "WARNING")
                return

            # 根据意图执行操作
            debug_log(f"执行意图处理器: {intent_result.intent}")
            await self._execute_intent(last_msg.event, intent_result)

            # 标记为已处理
            await queue.mark_all_processed()
            debug_log(f"队列 {queue_key} 已标记为已处理", "DEBUG")

        except Exception as e:
            debug_log(f"❌ 分析意图失败: {e}", "ERROR")
            import traceback

            debug_log(f"错误堆栈: {traceback.format_exc()}", "ERROR")

    async def _execute_intent(self, event: AstrMessageEvent, intent_result):
        """根据意图执行操作"""
        from .services.intent_service import IntentType

        debug_log(f"执行意图: {intent_result.intent}")

        intent_handlers = {
            IntentType.COURSE_QUERY: self._handle_course_query,
            IntentType.FILE_SEARCH: self._handle_file_search,
            IntentType.DEEP_SEARCH: self._handle_deep_search,
            IntentType.CONTRIBUTION: self._handle_contribution,
        }

        handler = intent_handlers.get(intent_result.intent)
        if handler:
            debug_log(f"找到意图处理器: {handler.__name__}")
            try:
                await handler(event, intent_result)
                debug_log(f"意图处理器执行完成: {handler.__name__}")
            except Exception as e:
                debug_log(f"❌ 意图处理器执行失败: {e}", "ERROR")
                import traceback

                debug_log(f"错误堆栈: {traceback.format_exc()}", "ERROR")
        else:
            debug_log(f"未找到意图处理器: {intent_result.intent}", "WARNING")

    async def _handle_course_query(self, event: AstrMessageEvent, intent_result):
        """处理课程查询"""
        query = (
            intent_result.extracted_info.get("course_code")
            or intent_result.extracted_info.get("course_name")
            or intent_result.extracted_info.get("keywords", [""])[0]
        )

        debug_log(f"处理课程查询: query={query}")

        if not query:
            debug_log("未能提取查询关键词", "WARNING")
            await event.send(
                event.plain_result(
                    "💡 我检测到您想查询课程信息，但未能识别具体课程。\n"
                    "请提供课程代码或名称，例如：AUTO1001 或 自动控制原理"
                )
            )
            return

        debug_log(f"调用课程搜索: {query}")
        await self._search_course(event, query)

    async def _handle_file_search(self, event: AstrMessageEvent, intent_result):
        """处理文件搜索"""
        keywords = intent_result.extracted_info.get("keywords", [])
        query = " ".join(keywords) if keywords else "资料"

        debug_log(f"处理文件搜索: query={query}, keywords={keywords}")
        await event.send(event.plain_result(f"🔍 正在为您搜索「{query}」相关资料..."))
        try:
            result = await self.agent_client.search_files(query, limit=10)
            if not result.success:
                err = (result.error or {}).get("message", "未知错误")
                await event.send(event.plain_result(f"❌ 文件搜索失败: {err}"))
                return

            output = result.output or {}
            files = output.get("results") or output.get("files") or []
            if not files:
                await event.send(event.plain_result(f"🔍 未找到「{query}」相关资料"))
                return

            msg = f"🔍 找到 {len(files)} 个相关文件:\n\n"
            for i, file in enumerate(files[:8], 1):
                name = file.get("name") or file.get("file_name") or "未命名"
                source = file.get("source") or "未知来源"
                url = file.get("url") or file.get("download_url") or ""
                msg += f"{i}. 📄 {name} ({source})\n"
                if url:
                    msg += f"   {url}\n"

            if len(files) > 8:
                msg += f"\n... 还有 {len(files) - 8} 个结果"

            await event.send(event.plain_result(msg))
        except Exception as e:
            debug_log(f"❌ 文件搜索失败: {e}", "ERROR")
            await event.send(event.plain_result(f"❌ 文件搜索失败: {e}"))

    async def _handle_deep_search(self, event: AstrMessageEvent, intent_result):
        """处理深度搜索"""
        keywords = intent_result.extracted_info.get("keywords", [])
        query = " ".join(keywords) if keywords else ""

        debug_log(f"处理深度搜索: query={query}, keywords={keywords}")

        if not query:
            debug_log("未能提取搜索关键词", "WARNING")
            await event.send(
                event.plain_result("💡 我检测到您想进行深度搜索，请提供具体搜索内容。")
            )
            return

        await event.send(event.plain_result(f"🔍 正在深度搜索「{query}」..."))
        try:
            result = await self.agent_client.invoke_skill(
                "search",
                {
                    "query": query,
                    "sources": ["rag", "brave", "annas", "arxiv", "github"],
                    "top_k": 5,
                    "summarize": True,
                },
            )
            if not result.success:
                err = (result.error or {}).get("message", "未知错误")
                await event.send(event.plain_result(f"❌ 深度搜索失败: {err}"))
                return

            output = result.output or {}
            rows = output.get("results") or []
            if not rows:
                await event.send(event.plain_result(f"🔍 未找到「{query}」相关深度资料"))
                return

            msg = f"🔍 深度搜索结果（{len(rows)}条）:\n\n"
            for i, row in enumerate(rows[:5], 1):
                title = row.get("title") or "未命名"
                source = row.get("source") or ""
                url = row.get("url") or ""
                msg += f"{i}. {title}"
                if source:
                    msg += f" [{source}]"
                msg += "\n"
                if url:
                    msg += f"   {url}\n"

            await event.send(event.plain_result(msg))
        except Exception as e:
            debug_log(f"❌ 深度搜索失败: {e}", "ERROR")
            await event.send(event.plain_result(f"❌ 深度搜索失败: {e}"))

    async def _handle_contribution(self, event: AstrMessageEvent, intent_result):
        """处理贡献引导"""
        debug_log("处理贡献引导")
        await self.contribution_service.guide_contribution(event, intent_result)

    async def _search_course(self, event: AstrMessageEvent, query: str):
        """搜索课程"""
        debug_log(f"开始搜索课程: {query}")

        try:
            debug_log(f"调用 agent_client.search_course: {query}")
            result = await self.agent_client.invoke_skill(
                "courses.search", {"keyword": query, "limit": 10}
            )

            debug_log(f"搜索结果: ok={result.success}", "DEBUG")

            if not result.success:
                error = result.error or {}
                debug_log(f"搜索失败: {error}", "ERROR")
                await event.send(
                    event.plain_result(
                        f"❌ 搜索失败: {error.get('message', '未知错误')}"
                    )
                )
                return

            output = result.output or {}
            courses = output.get("results", [])
            debug_log(f"找到 {len(courses)} 个课程")

            if not courses:
                debug_log("未找到课程，显示引导信息")
                msg = f"🔍 未找到包含「{query}」的课程\n\n"
                msg += "💡 您可以通过以下方式贡献:\n"
                msg += "1. 使用 /hit contribute 提交课程信息\n"
                msg += "2. 分享课程资料"
                await event.send(event.plain_result(msg))
                return

            # 构建结果消息
            msg = f"🔍 找到 {len(courses)} 个相关课程:\n\n"
            for i, course in enumerate(courses[:5], 1):
                code = course.get("code", "未知")
                name = course.get("name", "未知")
                msg += f"{i}. {code} - {name}\n"

            if len(courses) > 5:
                msg += f"\n... 还有 {len(courses) - 5} 个结果"

            msg += "\n\n💡 使用 /hit course <课程代码> 查看详情"
            debug_log(f"发送搜索结果，长度: {len(msg)}")
            await event.send(event.plain_result(msg))

        except Exception as e:
            debug_log(f"❌ 搜索课程失败: {e}", "ERROR")
            import traceback

            debug_log(f"错误堆栈: {traceback.format_exc()}", "ERROR")
            await event.send(event.plain_result(f"❌ 搜索失败: {str(e)}"))

    # ==================== 每日总结循环 ====================

    async def _daily_summary_loop(self):
        """每日总结循环"""
        debug_log("每日总结循环已启动")
        loop_count = 0

        while True:
            try:
                loop_count += 1
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
                debug_log(
                    f"[循环 #{loop_count}] 距离下次每日总结还有 {wait_seconds / 3600:.1f} 小时 ({target.strftime('%Y-%m-%d %H:%M')})"
                )

                await asyncio.sleep(wait_seconds)

                # 执行每日总结
                debug_log("📝 开始生成每日总结...")
                summary = await self.chat_summarizer.generate_summary(
                    group_id="all", messages=[]
                )
                debug_log(f"✅ 每日总结生成完成: group={summary.group_id}")

            except Exception as e:
                debug_log(f"❌ 每日总结循环错误: {e}", "ERROR")
                import traceback

                debug_log(f"错误堆栈: {traceback.format_exc()}", "ERROR")
                await asyncio.sleep(3600)

    # ==================== 指令 ====================

    @filter.command_group("hit")
    def hit_group(self):
        """HIT智能助手指令组"""
        pass

    @hit_group.command("help")
    async def help_cmd(self, event: AstrMessageEvent):
        """显示帮助信息"""
        debug_log(f"用户 {event.get_sender_name()} 请求帮助")

        msg = """🎓 HIT智能助手 v2.0.1

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

🔧 Debug:
  当前模式: {"DEBUG" if DEBUG_MODE else "PRODUCTION"}
"""
        await event.send(event.plain_result(msg))

    @hit_group.command("course")
    async def course_cmd(self, event: AstrMessageEvent, query: str = ""):
        """搜索课程"""
        debug_log(f"用户 {event.get_sender_name()} 执行 course 命令: query={query}")

        if not query:
            debug_log("查询关键词为空", "WARNING")
            await event.send(
                event.plain_result("❌ 请输入搜索关键词\n用法: /hit course 自动控制")
            )
            return

        await self._search_course(event, query)

    @hit_group.command("scan")
    async def scan_cmd(self, event: AstrMessageEvent):
        """扫描群文件"""
        debug_log(f"用户 {event.get_sender_name()} 执行 scan 命令")

        if not event.is_group_chat():
            debug_log("非群聊环境，拒绝执行", "WARNING")
            await event.send(event.plain_result("❌ 该指令仅在群聊中可用"))
            return

        group_id = event.get_group_id()
        debug_log(f"开始扫描群 {group_id} 的文件")
        await event.send(event.plain_result("📁 开始扫描群文件，请稍候..."))

        try:
            result = await self.file_scanner.scan_group_files(group_id)
            debug_log(f"扫描完成: {result}")
            if result.error_message:
                await event.send(event.plain_result(f"❌ 扫描失败: {result.error_message}"))
                return

            msg = (
                "📁 群文件扫描完成\n"
                f"总文件: {result.total_files}\n"
                f"新增: {result.new_files}\n"
                f"上传成功: {result.uploaded_files}\n"
                f"失败: {result.failed_files}"
            )
            await event.send(event.plain_result(msg))
        except Exception as e:
            debug_log(f"❌ 扫描群文件失败: {e}", "ERROR")
            import traceback

            debug_log(f"错误堆栈: {traceback.format_exc()}", "ERROR")
            await event.send(event.plain_result(f"❌ 扫描失败: {str(e)}"))

    @hit_group.command("status")
    async def status_cmd(self, event: AstrMessageEvent):
        """查看插件状态"""
        debug_log(f"用户 {event.get_sender_name()} 请求状态信息")

        await self.intent_service._check_and_reset_quota()
        quota_status = self.intent_service.get_quota_status()
        scanner_stats = self.file_scanner.get_statistics()

        msg = f"""📊 HIT智能助手状态

🤖 Gemini调用:
  今日已调用: {quota_status["calls_today"]}/{quota_status["daily_quota"]}
  剩余额度: {quota_status["remaining"]}

💬 消息队列:
  活跃队列数: {len(self.queue_manager.get_all_queues())}

📁 文件扫描:
    已扫描哈希: {scanner_stats["total_scanned"]}
    待入库文件: {scanner_stats.get("uploaded_pending_ingest", 0)}

⏰ 定时任务:
  意图判断: {"✅ 运行中" if self._tasks and any(not t.done() for t in self._tasks[:1]) else "❌ 已停止"}
  每日总结: {"✅ 运行中" if self._tasks and len(self._tasks) > 1 and not self._tasks[1].done() else "❌ 已停止"}

📦 服务状态:
  意图服务: {"✅" if self.intent_service else "❌"}
  Agent客户端: {"✅" if self.agent_client else "❌"}
  文件扫描: {"✅" if self.file_scanner else "❌"}
  聊天总结: {"✅" if self.chat_summarizer else "❌"}

🔧 Debug模式: {"开启" if DEBUG_MODE else "关闭"}
"""
        debug_log(f"状态信息:\n{msg}", "DEBUG")
        await event.send(event.plain_result(msg))

    @hit_group.command("file")
    async def file_cmd(self, event: AstrMessageEvent, query: str = ""):
        """按关键词搜索文件资料"""
        if not query.strip():
            await event.send(event.plain_result("❌ 请输入关键词\n用法: /hit file 自动控制 课件"))
            return

        class _SimpleIntentResult:
            extracted_info = {"keywords": query.split()}

        await self._handle_file_search(event, _SimpleIntentResult())

    @hit_group.command("search")
    async def search_cmd(self, event: AstrMessageEvent, query: str = ""):
        """按关键词执行深度搜索"""
        if not query.strip():
            await event.send(event.plain_result("❌ 请输入关键词\n用法: /hit search 保研政策"))
            return

        class _SimpleIntentResult:
            extracted_info = {"keywords": query.split()}

        await self._handle_deep_search(event, _SimpleIntentResult())

    @hit_group.command("ingest")
    async def ingest_cmd(self, event: AstrMessageEvent, limit: str = "20"):
        """触发批量入库（rag.ingest）"""
        try:
            parsed_limit = int(limit)
        except Exception:
            await event.send(event.plain_result("❌ limit 必须是整数，例如: /hit ingest 20"))
            return

        if parsed_limit <= 0:
            await event.send(event.plain_result("❌ limit 必须大于 0"))
            return

        safe_limit = min(parsed_limit, 100)
        await event.send(event.plain_result(f"📥 开始入库，最多处理 {safe_limit} 个文件..."))

        try:
            result = await self.file_scanner.ingest_uploaded_files(limit=safe_limit)
            if result.error_message and result.total_candidates == 0:
                await event.send(event.plain_result(f"⚠️ 入库未执行: {result.error_message}"))
                return

            msg = (
                "📥 入库完成\n"
                f"候选文件: {result.total_candidates}\n"
                f"入库成功: {result.ingested_files}\n"
                f"入库失败: {result.failed_files}\n"
                f"跳过: {result.skipped_files}"
            )

            failed_items = [d for d in result.details if d.get("status") == "failed"]
            if failed_items:
                msg += "\n\n失败详情:\n"
                for item in failed_items[:5]:
                    msg += f"- {item.get('file_name', 'unknown')}: {item.get('error', '未知错误')}\n"

            await event.send(event.plain_result(msg))
        except Exception as e:
            debug_log(f"❌ 入库失败: {e}", "ERROR")
            await event.send(event.plain_result(f"❌ 入库失败: {e}"))

    @hit_group.command("debug")
    async def debug_cmd(self, event: AstrMessageEvent):
        """查看详细调试信息"""
        debug_log(f"用户 {event.get_sender_name()} 请求调试信息")

        # 收集调试信息
        config = self.config_manager.config
        quota = self.intent_service.get_quota_status()

        msg = f"""🔧 详细调试信息

📋 配置信息:
  Gemini模型: {config.gemini_model}
  Gemini额度: {config.gemini_daily_quota}/天
  Agent后端: {config.agent_backend_url}
  上下文窗口: {config.context_window_minutes}分钟
  检查间隔: {config.intent_check_interval}秒

📊 运行状态:
  当前时间: {datetime.now().isoformat()}
  Gemini今日调用: {quota["calls_today"]}
  Gemini剩余额度: {quota["remaining"]}
  消息队列数: {len(self.queue_manager.get_all_queues())}
  定时任务数: {len(self._tasks)}

🔑 API密钥状态:
  Gemini: {"✅ 已配置" if config.gemini_api_key else "❌ 未配置"}
  Agent: {"✅ 已配置" if config.agent_backend_api_key else "❌ 未配置"}

💾 队列详情:
"""
        # 添加队列详情
        for i, (key, queue) in enumerate(
            list(self.queue_manager.get_all_queues().items())[:5]
        ):
            size = await queue.get_message_count()
            msg += f"  {i + 1}. {key}: {size}条消息\n"

        if len(self.queue_manager.get_all_queues()) > 5:
            msg += f"  ... 还有 {len(self.queue_manager.get_all_queues()) - 5} 个队列\n"

        await event.send(event.plain_result(msg))

    async def terminate(self):
        """插件卸载"""
        debug_log("=" * 60)
        debug_log("🛑 HIT智能助手插件卸载中...")
        debug_log(f"需要取消 {len(self._tasks)} 个定时任务")

        # 取消所有定时任务
        for i, task in enumerate(self._tasks):
            if task and not task.done():
                debug_log(f"取消任务 #{i + 1}...")
                task.cancel()

        # 等待任务取消
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            debug_log("所有定时任务已取消")

        debug_log("✅ HIT智能助手插件已卸载")
        debug_log("=" * 60)
