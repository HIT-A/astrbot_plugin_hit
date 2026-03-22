"""
HIT智能助手 - AstrBot插件 v2.0.1

模块化架构，包含:
- 智能意图识别（MiniMax）
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
import traceback
import json
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, Any, List

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
from .services.agent_integration import create_hit_tools, HITAgentSession

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
        self.error_log_enabled = self.config_manager.config.error_log_enabled
        debug_log(
            f"配置加载完成: AI额度={self.config_manager.config.glm_quota_limit}/{self.config_manager.config.glm_quota_window_hours}h"
        )
        debug_log(f"报错诊断日志: {'开启' if self.error_log_enabled else '关闭'}")

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

    def _set_error_log_enabled(self, enabled: bool):
        self.error_log_enabled = enabled
        self.config_manager.config.error_log_enabled = enabled
        os.environ["HITSZ_ERROR_LOG_ENABLED"] = "true" if enabled else "false"

    def _stop_event_safe(self, event: AstrMessageEvent, reason: str = ""):
        """尽量终止后续事件处理，避免被默认聊天插件二次回复。"""
        try:
            event.stop_event()
            if reason:
                debug_log(f"事件已 stop: {reason}", "DEBUG")
        except Exception as e:
            debug_log(f"事件 stop 失败: {e}", "DEBUG")

    async def _send_text(self, event: AstrMessageEvent, text: str):
        """统一文本发送，避免把 MessageEventResult 误传给 send() 导致空消息。"""
        await event.send(MessageChain([Plain(str(text))]))

    async def _send_folded_texts(
        self, event: AstrMessageEvent, title: str, texts: List[str]
    ) -> bool:
        """优先发送 Napcat 合并转发（折叠）消息；失败时返回 False 让上层走普通文本。"""
        if not texts:
            return False

        group_id = str(event.get_group_id() or "").strip()
        user_id = str(event.get_sender_id() or "").strip()
        message_type = "group" if group_id else "private"

        get_self_id = getattr(event, "get_self_id", None)
        self_uin = ""
        if callable(get_self_id):
            try:
                self_uin = str(get_self_id() or "").strip()
            except Exception:
                self_uin = ""
        if not self_uin:
            self_uin = user_id or "10000"

        nodes = []
        for text in texts[:20]:
            nodes.append(
                {
                    "type": "node",
                    "data": {
                        "uin": self_uin,
                        "name": "HIT智能助手",
                        "content": [{"type": "text", "data": {"text": str(text)}}],
                    },
                }
            )

        payload = {
            "message_type": message_type,
            "message": nodes,
            "summary": title,
            "prompt": "点击查看详情",
            "source": "HIT智能助手",
            "news": [{"text": title}],
        }
        if message_type == "group":
            payload["group_id"] = group_id
        else:
            payload["user_id"] = user_id

        try:
            resp = await self._napcat_call_action(event, "send_forward_msg", **payload)
            ok = (
                isinstance(resp, dict)
                and str(resp.get("status") or "ok").lower() == "ok"
            )
            if ok:
                debug_log(f"折叠消息发送成功: title={title}, nodes={len(nodes)}")
                return True
            debug_log(f"折叠消息发送失败: resp={resp}", "WARNING")
            return False
        except Exception as e:
            debug_log(f"折叠消息发送异常: {e}", "WARNING")
            return False

    def _split_markdown_by_titles(
        self, markdown_text: str, max_chunk_chars: int = 1200
    ) -> List[str]:
        """按 Markdown 标题拆段，并控制单段长度，避免超出转发节点限制。"""
        text = str(markdown_text or "").strip()
        if not text:
            return []

        lines = text.splitlines()
        sections: List[tuple] = []
        cur_title = "导言"
        cur_lines: List[str] = []

        for line in lines:
            if re.match(r"^#{1,6}\s+", line.strip()):
                if cur_lines:
                    sections.append((cur_title, "\n".join(cur_lines).strip()))
                cur_title = re.sub(r"^#{1,6}\s+", "", line.strip()) or "小节"
                cur_lines = [line]
            else:
                cur_lines.append(line)

        if cur_lines:
            sections.append((cur_title, "\n".join(cur_lines).strip()))

        chunks: List[str] = []
        for title, body in sections:
            if not body:
                continue
            if len(body) <= max_chunk_chars:
                chunks.append(f"【{title}】\n{body}")
                continue

            start = 0
            part = 1
            while start < len(body):
                end = min(start + max_chunk_chars, len(body))
                piece = body[start:end].strip()
                if piece:
                    chunks.append(f"【{title} - {part}】\n{piece}")
                start = end
                part += 1

        return chunks

    def _strip_readme_comments(self, markdown_text: str) -> str:
        """移除 README 中注释内容，减少噪音输出。"""
        text = str(markdown_text or "")
        if not text.strip():
            return ""

        # 1) HTML 注释: <!-- ... -->
        text = re.sub(r"<!--([\s\S]*?)-->", "", text)

        # 2) Markdown 隐藏注释: [//]: # ( ... )
        text = re.sub(r"^\s*\[//\]:\s*#\s*\(.*?\)\s*$", "", text, flags=re.MULTILINE)

        # 3) 清理多余空白行
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text

    def _extract_readme_md_from_course_read(self, output: Dict[str, Any]) -> str:
        """兼容不同返回结构，提取 course.read 的 readme_md。"""
        if not isinstance(output, dict):
            return ""

        queue: List[Any] = [output]
        visited = set()
        while queue:
            cur = queue.pop(0)
            if id(cur) in visited:
                continue
            visited.add(id(cur))

            if isinstance(cur, dict):
                for k in ("readme_md", "readme", "readmeMarkdown", "markdown"):
                    v = cur.get(k)
                    if isinstance(v, str) and v.strip():
                        return v.strip()
                for v in cur.values():
                    if isinstance(v, (dict, list)):
                        queue.append(v)
            elif isinstance(cur, list):
                for v in cur:
                    if isinstance(v, (dict, list)):
                        queue.append(v)

        return ""

    def _resolve_precise_course(
        self, courses: List[Dict[str, Any]], query: str
    ) -> Optional[Dict[str, Any]]:
        """判断是否已精确定位到单课（唯一结果/课程代码精准命中）。"""
        if not courses:
            return None
        if len(courses) == 1:
            return courses[0]

        q = str(query or "").strip().lower()
        if not q:
            return None

        for c in courses:
            code = str(c.get("code") or "").strip().lower()
            if code and code == q:
                return c

        return None

    def _get_event_campus(self, event: AstrMessageEvent) -> str:
        """从群配置获取校区，默认 shenzhen。"""
        try:
            gid = str(event.get_group_id() or "").strip()
            if gid:
                cfg = self.config_manager.get_group_config(gid)
                campus = str((cfg or {}).get("campus") or "").strip().lower()
                if campus:
                    return campus
        except Exception:
            pass
        return "shenzhen"

    async def _send_course_readme_folded(
        self,
        event: AstrMessageEvent,
        course_code: str,
        course_name: str,
        readme_md: str,
    ) -> bool:
        """将 README 按标题拆段后折叠发送。"""
        clean_md = self._strip_readme_comments(readme_md)
        chunks = self._split_markdown_by_titles(clean_md, max_chunk_chars=1200)
        if not chunks:
            return False

        title = f"{course_code} {course_name} README"
        header = f"课程: {course_code} - {course_name}\n以下为 README 按标题分段内容（共 {len(chunks)} 段）"
        texts = [header] + chunks
        return await self._send_folded_texts(event, title, texts)

    def _extract_text_keywords(self, text: str, max_terms: int = 6) -> List[str]:
        """从自由文本提取简易关键词，给搜索意图兜底。"""
        cleaned = re.sub(r"\[At:[^\]]+\]", " ", text or "")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        parts = [p for p in re.split(r"[^0-9A-Za-z\u4e00-\u9fff]+", cleaned) if p]
        # 过滤无意义短词
        parts = [p for p in parts if len(p) >= 2]
        return parts[:max_terms]

    def _sanitize_search_keywords(self, keywords: List[str]) -> List[str]:
        """移除泛指词，保留更有检索价值的关键词。"""
        generic = {
            "资源",
            "资料",
            "学习资料",
            "课件",
            "文档",
            "文件",
            "内容",
            "信息",
            "东西",
            "帮我",
            "给我",
            "查",
            "查询",
            "搜索",
            "检索",
            "一下",
            "看看",
            "相关",
            "一些",
            "这个",
            "那个",
            "有没有",
        }
        out: List[str] = []
        seen = set()
        for k in keywords or []:
            token = str(k or "").strip()
            if not token:
                continue
            tl = token.lower()
            if tl in generic:
                continue
            if tl in seen:
                continue
            seen.add(tl)
            out.append(token)
        return out

    def _adjust_intent_with_keywords(self, intent_result, raw_text: str):
        """基于关键词做确定性纠偏，减少模型把搜索问句误判成课程查询。"""
        from .services.intent_service import IntentType

        text = (raw_text or "").lower()
        paper_like = ["论文", "paper", "arxiv", "综述", "survey", "github", "开源"]
        file_like = ["课件", "资料", "讲义", "ppt", "pdf", "试卷", "答案"]

        if intent_result.intent == IntentType.COURSE_QUERY and any(
            k in text for k in paper_like
        ):
            intent_result.intent = IntentType.SEARCH
            info = (
                intent_result.extracted_info
                if isinstance(intent_result.extracted_info, dict)
                else {}
            )
            if not info.get("keywords"):
                info["keywords"] = self._extract_text_keywords(raw_text)
            intent_result.extracted_info = info
            intent_result.reasoning = (
                f"{intent_result.reasoning}; override=paper_like_to_search"
            )

        if intent_result.intent == IntentType.COURSE_QUERY and any(
            k in text for k in file_like
        ):
            intent_result.intent = IntentType.SEARCH
            info = (
                intent_result.extracted_info
                if isinstance(intent_result.extracted_info, dict)
                else {}
            )
            if not info.get("keywords"):
                info["keywords"] = self._extract_text_keywords(raw_text)
            intent_result.extracted_info = info
            intent_result.reasoning = (
                f"{intent_result.reasoning}; override=file_like_to_search"
            )

    def _extract_hit_command_query(
        self, event: AstrMessageEvent, command: str, fallback_query: str = ""
    ) -> str:
        """从原始消息中提取 hit 子命令后的完整查询文本。"""
        raw = str(event.get_message_outline() or "")
        cleaned = re.sub(r"\[At:[^\]]+\]", " ", raw)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()

        pattern = re.compile(rf"^/?hit\s+{re.escape(command)}\s+(.+)$", re.IGNORECASE)
        m = pattern.match(cleaned)
        if m:
            return m.group(1).strip()

        # 兼容 hitcourse <query>
        if command == "course":
            m2 = re.match(r"^/?hitcourse\s+(.+)$", cleaned, re.IGNORECASE)
            if m2:
                return m2.group(1).strip()

        return (fallback_query or "").strip()

    def _build_heuristic_intent(self, text: str):
        """当模型失效时，通过关键词给出兜底意图。"""
        from .services.intent_service import IntentType, IntentResult

        raw = text or ""
        lower = raw.lower()

        # 命令消息由 command handler 处理，避免启发式误判成课程查询。
        probe = re.sub(r"\[At:[^\]]+\]", " ", lower)
        probe = re.sub(r"\s+", " ", probe).strip()
        if re.match(r"^/?hit\s+\w+", probe) or re.match(r"^/?hitcourse\s+", probe):
            return None

        keywords = self._extract_text_keywords(raw)

        search_like = [
            "论文",
            "paper",
            "arxiv",
            "综述",
            "survey",
            "github",
            "开源",
            "检索",
            "搜索",
            "课件",
            "资料",
            "讲义",
            "ppt",
            "pdf",
            "试卷",
            "答案",
            "文件",
        ]
        course_like = [
            "课程",
            "老师",
            "给分",
            "作业",
            "考试",
            "课程代码",
            "comp",
            "auto",
        ]

        if any(k in lower for k in search_like):
            return IntentResult(
                has_valid_intent=True,
                intent=IntentType.SEARCH,
                confidence=0.66,
                extracted_info={"keywords": keywords or [raw.strip()]},
                reasoning="heuristic_fallback_search",
                raw_response=None,
            )

        if any(k in lower for k in course_like):
            return IntentResult(
                has_valid_intent=True,
                intent=IntentType.COURSE_QUERY,
                confidence=0.62,
                extracted_info={"keywords": keywords or [raw.strip()]},
                reasoning="heuristic_fallback_course_query",
                raw_response=None,
            )

        return None

    def _looks_like_nl_query(self, text: str) -> bool:
        """判断是否像自然语言查询请求（无需命令前缀）。"""
        raw = str(text or "")
        probe = re.sub(r"\[At:[^\]]+\]", " ", raw)
        probe = re.sub(r"\s+", " ", probe).strip().lower()
        if not probe:
            return False

        # 显式命令交给命令处理器
        if re.match(r"^/?hit\s+\w+", probe) or re.match(r"^/?hitcourse\s+", probe):
            return False

        # 课程代码形态
        if re.search(r"\b[a-z]{2,}\d{3,5}\b", probe):
            return True

        query_words = [
            "查",
            "查询",
            "找",
            "搜索",
            "检索",
            "看看",
            "帮我",
            "有没有",
            "哪里有",
            "给我",
            "课程",
            "老师",
            "给分",
            "课件",
            "资料",
            "论文",
            "arxiv",
            "github",
            "综述",
            "保研",
        ]
        if any(w in probe for w in query_words):
            return True

        if probe.endswith("?") or probe.endswith("？"):
            return True

        return False

    async def _try_handle_wakeup_query(
        self, event: AstrMessageEvent, content: str, queue_key: str, queue
    ) -> bool:
        """自然语言优先入口：即时处理查询型消息，避免依赖命令或定时轮询。"""
        if self.intent_service is None:
            return False

        try:
            is_wakeup = bool(event.is_wake_up())
        except Exception:
            is_wakeup = False

        if not is_wakeup:
            return False

        normalized = (content or "").strip().lower()
        cmd_probe = re.sub(r"\[At:[^\]]+\]", " ", normalized)
        cmd_probe = re.sub(r"\s+", " ", cmd_probe).strip()

        # 兼容常见误写命令：hitcourse <query>
        if cmd_probe.startswith("hitcourse ") or cmd_probe.startswith("/hitcourse "):
            query = self._extract_hit_command_query(event, "course", "")
            if query:
                debug_log(f"命中兼容命令 hitcourse，转 course 查询: {query}")
                await self._search_course(event, query)
                await queue.mark_all_processed()
                self._stop_event_safe(event, reason="alias:hitcourse")
                return True

        # hit 指令仍交给 command handler，避免重复处理
        if re.match(r"^/?hit\s+\w+", cmd_probe):
            return False

        # 自然语言主入口：使用 Agent + Tools 进行多轮对话
        if not is_wakeup and not self._looks_like_nl_query(content):
            return False

        debug_log(
            f"检测到自然语言查询，使用 Agent 处理: queue={queue_key}, wakeup={is_wakeup}",
            "INFO",
        )

        # 尝试使用 Agent + Tools 处理
        agent_handled = await self._handle_agent_chat(event, content)
        if agent_handled:
            await queue.mark_all_processed()
            self._stop_event_safe(event, reason="agent_chat_handled")
            return True

        debug_log("Agent 处理未命中，继续默认链路", "DEBUG")
        return False

    async def _handle_agent_chat(self, event: AstrMessageEvent, query: str) -> bool:
        """使用 Agent + Tools 处理自然语言查询，支持多轮对话"""
        if self.context is None or self.agent_tools is None:
            debug_log("Agent Tools 未注册，使用旧版意图分析", "DEBUG")
            return False

        try:
            # 获取会话ID
            session_id = (
                event.get_session_id()
                if hasattr(event, "get_session_id")
                else str(event.get_sender_id())
            )
            group_id = str(event.get_group_id()) if event.get_group_id() else None
            queue_key = (
                f"agent:{session_id}" if not group_id else f"agent:group:{group_id}"
            )

            # 获取或创建会话
            if queue_key not in self.agent_sessions:
                self.agent_sessions[queue_key] = HITAgentSession(
                    event, self.agent_tools
                )
            session = self.agent_sessions[queue_key]

            # 构建系统提示词
            system_prompt = """你是一个哈工大课程助手，负责回答关于课程、资料、教师等信息的问题。

【重要-必须严格遵守】：
当用户询问任何与"课程"相关的问题时（课程名字/课程内容/课程评价/推荐课程/课程安排等），你【必须】首先调用 search_courses 工具进行搜索！
不允许直接使用 unified_search 或其他工具搜索课程！

可用工具：
- search_courses: 【最优先】搜索哈工大课程数据库（用于查找课程信息）
- get_course_detail: 获取课程详细信息（在 search_courses 之后使用）
- search_teacher: 搜索教师信息
- unified_search: 仅用于搜索参考书、教材、试卷等【非课程内容】（当用户明确说"网上搜索"或"参考资料"时使用）
- rag_query: 查询已入库的课程资料、评价等
- list_cos_files: 查看COS文件列表
- scan_group_files: 扫描群文件并入库

工作流程：
1. 用户问课程 → 必须先调用 search_courses
2. search_courses 返回结果 → 列出课程列表
3. 用户选择课程 → 调用 get_course_detail 获取详情

【输出格式要求】：
- 禁止使用 Markdown 标记语言（如 **加粗**、*斜体*、`代码`、```代码块``` 等）
- 禁止使用 Emoji 表情符号
- 使用纯文字输出，用换行和缩进组织结构
- 列表用数字+点号，如 "1. xxx"
- 重点内容用"【】"或"()"强调

回答要简洁，列出要点即可。"""

            # 获取聊天提供商
            try:
                chat_provider_id = await self.context.get_current_chat_provider_id(
                    event.unified_msg_origin
                    if hasattr(event, "unified_msg_origin")
                    else ""
                )
            except Exception as e:
                debug_log(f"获取聊天提供商失败: {e}", "WARNING")
                return False

            # 使用 tool_loop_agent 进行多轮对话
            debug_log(f"调用 Agent 处理查询: {query[:50]}...", "DEBUG")

            from astrbot.core.agent.tool import ToolSet
            from astrbot.core.agent.message import Message

            # 添加用户消息到历史
            session.add_user_message(query)

            # 构建消息历史 (contexts)
            contexts = []
            for msg in session.get_messages()[:-1]:  # 除了最后一条（当前消息）
                contexts.append(Message(role=msg["role"], content=msg["content"]))

            tool_set = ToolSet()
            for tool in self.agent_tools:
                tool_set.add_tool(tool)

            llm_resp = await self.context.tool_loop_agent(
                event=event,
                chat_provider_id=chat_provider_id,
                prompt=query,
                tools=tool_set,
                system_prompt=system_prompt,
                contexts=contexts if contexts else None,
                max_steps=10,
            )

            # 发送回复
            debug_log(
                f"Agent 响应: llm_resp={llm_resp}, completion_text={getattr(llm_resp, 'completion_text', 'N/A') if llm_resp else 'None'}",
                "DEBUG",
            )

            if llm_resp and llm_resp.completion_text:
                response_text = llm_resp.completion_text
                if isinstance(response_text, list):
                    response_text = "\n".join(str(item) for item in response_text)
                response_text = str(response_text)

                # 添加助手回复到历史
                session.add_assistant_message(response_text)

                await self._send_text(event, response_text)
                debug_log("Agent 回复已发送", "DEBUG")
                return True

            debug_log("Agent 无有效回复，返回 False", "DEBUG")
            return False

        except Exception as e:
            debug_log(f"Agent 处理失败: {e}", "ERROR")
            if self.error_log_enabled:
                import traceback

                traceback.print_exc()
            return False

    def _log_exception(self, where: str, err: Exception):
        debug_log(f"❌ {where}: {err}", "ERROR")
        if self.error_log_enabled:
            debug_log(f"错误堆栈: {traceback.format_exc()}", "ERROR")

    def _init_services(self):
        """初始化服务"""
        config = self.config_manager.config
        has_glm = bool(config.glm_api_key)

        # 意图分析服务
        debug_log(
            f"初始化 IntentService: intent_model={config.glm_intent_model}, complex_model={config.glm_complex_model}"
        )
        if has_glm:
            debug_log(f"AI API Key 已配置: {config.glm_api_key[:10]}...")
            self.intent_service = IntentService(
                api_key=config.glm_api_key,
                intent_model=config.glm_intent_model,
                complex_model=config.glm_complex_model,
                base_url=config.glm_base_url,
                quota_limit=config.glm_quota_limit,
                quota_window_hours=config.glm_quota_window_hours,
                context_window_minutes=config.context_window_minutes,
            )
            debug_log("IntentService 初始化完成")
        else:
            self.intent_service = None
            debug_log(
                "未配置 AI API Key：已关闭自动意图分析",
                "WARNING",
            )

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
        if has_glm:
            self.chat_summarizer = ChatSummarizerService(
                api_key=config.glm_api_key,
                model=config.glm_complex_model,
                base_url=config.glm_base_url,
                summary_time=config.daily_summary_time,
            )
            debug_log("ChatSummarizerService 初始化完成")
        else:
            self.chat_summarizer = None
            debug_log("未配置 AI API Key：已关闭每日总结功能", "WARNING")

        # 贡献引导服务
        debug_log("初始化 ContributionService...")
        self.contribution_service = ContributionService(
            plugin=self,
            agent_client=self.agent_client,
        )
        debug_log("ContributionService 初始化完成")

        # Agent会话管理
        self.agent_sessions: Dict[str, HITAgentSession] = {}
        self.agent_tools = None
        debug_log("Agent会话管理器初始化完成")

        debug_log("所有服务初始化完成")

    def _register_agent_tools(self):
        """注册 Agent Tools 到 astrbot"""
        if self.context is None:
            debug_log("Context 未初始化，跳过 Tool 注册", "WARNING")
            return

        try:
            tools = create_hit_tools(self.agent_client, self.file_scanner, self)
            self.agent_tools = tools
            self.context.add_llm_tools(*tools)
            debug_log(f"✅ 已注册 {len(tools)} 个 HIT Agent Tools")

            tool_names = [t.name for t in tools]
            debug_log(f"Tools: {', '.join(tool_names)}", "DEBUG")
        except Exception as e:
            debug_log(f"❌ 注册 Agent Tools 失败: {e}", "ERROR")
            import traceback

            traceback.print_exc()

    async def initialize(self):
        """插件初始化"""
        debug_log("=" * 60)
        debug_log("🚀 HIT智能助手 v2.0.1 初始化中...")
        debug_log(f"当前时间: {datetime.now().isoformat()}")
        debug_log(f"配置信息:")
        debug_log(f"  - AI意图模型: {self.config_manager.config.glm_intent_model}")
        debug_log(f"  - AI复杂模型: {self.config_manager.config.glm_complex_model}")
        debug_log(
            f"  - AI额度: {self.config_manager.config.glm_quota_limit}/{self.config_manager.config.glm_quota_window_hours}h"
        )
        debug_log(f"  - AI接口: {self.config_manager.config.glm_base_url}")
        debug_log(
            f"  - 上下文窗口: {self.config_manager.config.context_window_minutes}分钟"
        )
        debug_log(f"  - Agent后端: {self.config_manager.config.agent_backend_url}")
        debug_log(
            f"  - 意图检查间隔: {self.config_manager.config.intent_check_interval}秒"
        )
        debug_log(f"  - 每日总结时间: {self.config_manager.config.daily_summary_time}")
        debug_log(
            f"  - 文件扫描间隔: {self.config_manager.config.file_scan_interval_hours}小时"
        )

        # 注册 Agent Tools
        debug_log("注册 HIT Agent Tools...")
        self._register_agent_tools()

        # 启动定时任务
        debug_log("启动定时任务...")
        if self.intent_service is not None:
            debug_log("  - 启动意图检查循环")
            self._tasks.append(asyncio.create_task(self._intent_check_loop()))
        else:
            debug_log("  - 跳过意图检查循环（AI未配置）", "WARNING")

        if self.chat_summarizer is not None:
            debug_log("  - 启动每日总结循环")
            self._tasks.append(asyncio.create_task(self._daily_summary_loop()))
        else:
            debug_log("  - 跳过每日总结循环（AI未配置）", "WARNING")

        # 启动定时文件扫描入库循环
        scan_interval = self.config_manager.config.file_scan_interval_hours
        if scan_interval > 0:
            debug_log(f"  - 启动定时文件扫描入库循环（每{scan_interval}小时）")
            self._tasks.append(asyncio.create_task(self._file_scan_loop()))
        else:
            debug_log("  - 禁用定时文件扫描入库", "WARNING")

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

        # 忽略非文本组件事件（例如 [ComponentType.Poke]），避免被误当查询语句。
        if re.match(r"^\[ComponentType\.[^\]]+\]\s*$", str(content).strip()):
            debug_log(f"忽略非文本组件消息: {content}", "DEBUG")
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

        # 对 @机器人 的自然语言查询进行即时处理，避免与主聊天插件分离。
        # 现在自然语言查询为主入口：无需命令前缀也可触发业务技能。
        handled = await self._try_handle_wakeup_query(event, content, queue_key, queue)
        if handled:
            return

    # ==================== 意图判断循环 ====================

    async def _intent_check_loop(self):
        """意图判断循环（每3分钟）"""
        if self.intent_service is None:
            debug_log("IntentService 未初始化，意图循环退出", "WARNING")
            return

        debug_log("意图检查循环已启动")
        loop_count = 0

        while True:
            try:
                loop_count += 1
                await self.intent_service._check_and_reset_quota()
                now = datetime.now()
                quota_status = self.intent_service.get_quota_status()

                debug_log(
                    f"[循环 #{loop_count}] 当前时间: {now.strftime('%H:%M:%S')}, AI额度: {quota_status['calls_in_window']}/{quota_status['quota_limit']} ({quota_status['window_hours']}h)",
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
                                debug_log("⚠️ AI窗口额度已用完，跳过意图分析", "WARNING")
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
                self._log_exception("意图判断循环错误", e)
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
            debug_log("调用 MiniMax 进行意图分析...")
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
            self._adjust_intent_with_keywords(intent_result, last_msg.content)
            debug_log(f"执行意图处理器: {intent_result.intent}")
            await self._execute_intent(last_msg.event, intent_result)

            # 标记为已处理
            await queue.mark_all_processed()
            debug_log(f"队列 {queue_key} 已标记为已处理", "DEBUG")

        except Exception as e:
            self._log_exception("分析意图失败", e)

    async def _execute_intent(self, event: AstrMessageEvent, intent_result):
        """根据意图执行操作"""
        from .services.intent_service import IntentType

        debug_log(f"执行意图: {intent_result.intent}")

        intent_handlers = {
            IntentType.COURSE_QUERY: self._handle_course_query,
            IntentType.SEARCH: self._handle_search,
            IntentType.CONTRIBUTION: self._handle_contribution,
        }

        handler = intent_handlers.get(intent_result.intent)
        if handler:
            debug_log(f"找到意图处理器: {handler.__name__}")
            try:
                await handler(event, intent_result)
                self._stop_event_safe(
                    event, reason=f"intent:{intent_result.intent.value}"
                )
                debug_log(f"意图处理器执行完成: {handler.__name__}")
            except Exception as e:
                self._log_exception("意图处理器执行失败", e)
        else:
            debug_log(f"未找到意图处理器: {intent_result.intent}", "WARNING")

    async def _handle_course_query(self, event: AstrMessageEvent, intent_result):
        """处理课程查询"""
        info = (
            intent_result.extracted_info
            if isinstance(intent_result.extracted_info, dict)
            else {}
        )
        query = str(info.get("course_name") or info.get("course_code") or "").strip()

        if not query:
            keywords = (
                info.get("keywords") if isinstance(info.get("keywords"), list) else []
            )
            clean_keywords = []
            noise = {
                "hit",
                "course",
                "查",
                "一下",
                "帮我",
                "componenttype",
                "poke",
                "at",
            }
            for x in keywords:
                token = str(x or "").strip()
                if not token:
                    continue
                if token.lower() in noise:
                    continue
                clean_keywords.append(token)

            # 优先课程代码，如 COMP3006 / AUTO1001
            for token in clean_keywords:
                if re.match(r"^[A-Za-z]{2,}\d{3,5}$", token):
                    query = token
                    break
            if not query and clean_keywords:
                query = clean_keywords[0]

        if not query:
            query = self._extract_hit_command_query(event, "course", "")

        debug_log(f"处理课程查询: query={query}")

        if not query:
            debug_log("未能提取查询关键词", "WARNING")
            await self._send_text(
                event,
                "💡 我检测到您想查询课程信息，但未能识别具体课程。\n"
                "请提供课程代码或名称，例如：AUTO1001 或 自动控制原理",
            )
            return

        debug_log(f"调用课程搜索: {query}")
        await self._search_course(event, query)

    async def _handle_search(self, event: AstrMessageEvent, intent_result):
        """统一搜索：只识别搜索意图，信源由任务规划器选择。"""
        raw_keywords = intent_result.extracted_info.get("keywords", [])
        keywords = self._sanitize_search_keywords(raw_keywords)
        query = (
            " ".join(keywords)
            if keywords
            else " ".join(raw_keywords[:3])
            if raw_keywords
            else ""
        )

        debug_log(
            f"处理统一搜索: query={query}, keywords={keywords}, raw_keywords={raw_keywords}"
        )

        if not query:
            debug_log("未能提取搜索关键词", "WARNING")
            await self._send_text(
                event, "💡 我检测到您想进行搜索，请提供更具体的关键词。"
            )
            return

        await self._send_text(event, f"🔍 正在搜索「{query}」...")
        try:
            if self.intent_service is not None:
                tasks = await self.intent_service.plan_search_tasks(query, max_tasks=3)
            else:
                tasks = []
            if not tasks:
                tasks = [
                    {
                        "query": query,
                        "sources": ["rag", "brave", "arxiv", "github"],
                        "reason": "fallback_empty_tasks",
                    }
                ]

            all_rows = []
            failed_tasks = 0
            for idx, task in enumerate(tasks, 1):
                sub_query = task.get("query") or query
                sources = task.get("sources") or ["rag", "brave"]
                result = await self.agent_client.invoke_skill(
                    "search",
                    {
                        "query": sub_query,
                        "sources": sources,
                        "top_k": 5,
                        "summarize": False,
                    },
                )
                if not result.success:
                    failed_tasks += 1
                    debug_log(
                        f"统一搜索子任务失败: idx={idx}, query={sub_query}, sources={sources}, err={result.error}",
                        "WARNING",
                    )
                    continue
                rows = (result.output or {}).get("results") or []
                for row in rows:
                    row["_task_index"] = idx
                    row["_task_query"] = sub_query
                all_rows.extend(rows)

            # 去重（优先按URL，其次按标题+来源）
            deduped = []
            seen = set()
            for row in all_rows:
                key = (
                    row.get("url") or f"{row.get('title', '')}::{row.get('source', '')}"
                )
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(row)

            rows = deduped
            debug_log(
                f"统一搜索聚合完成: tasks={len(tasks)}, failed_tasks={failed_tasks}, merged={len(rows)}",
                "INFO",
            )
            if not rows:
                await self._send_text(event, f"🔍 未找到「{query}」相关结果")
                return

            msg = f"🔍 搜索结果（{len(rows)}条，拆分任务 {len(tasks)} 个）:\n\n"
            for i, row in enumerate(rows[:5], 1):
                title = row.get("title") or "未命名"
                source = row.get("source") or ""
                url = row.get("url") or ""
                task_query = row.get("_task_query") or query
                msg += f"{i}. {title}"
                if source:
                    msg += f" [{source}]"
                msg += f"\n   ↳ 子查询: {task_query}\n"
                if url:
                    msg += f"   {url}\n"

            await self._send_text(event, msg)
        except Exception as e:
            self._log_exception("统一搜索失败", e)
            await self._send_text(event, f"❌ 搜索失败: {e}")

    async def _handle_file_search(self, event: AstrMessageEvent, intent_result):
        """兼容旧入口：文件搜索转统一搜索。"""
        await self._handle_search(event, intent_result)

    async def _handle_deep_search(self, event: AstrMessageEvent, intent_result):
        """兼容旧入口：深度搜索转统一搜索。"""
        await self._handle_search(event, intent_result)

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
                await self._send_text(
                    event,
                    f"❌ 搜索失败: {error.get('message', '未知错误')}",
                )
                return

            output = result.output or {}
            courses = []

            # 兼容多种返回结构：
            # 1) {"results": [...]}
            # 2) {"data": {"results": [...]}}
            # 3) {"output": {"data": {"results": [...]}}}
            if isinstance(output.get("results"), list):
                courses = output.get("results") or []
            elif isinstance(output.get("data"), dict):
                courses = output.get("data", {}).get("results") or []
            elif isinstance(output.get("output"), dict):
                nested = output.get("output") or {}
                if isinstance(nested.get("results"), list):
                    courses = nested.get("results") or []
                elif isinstance(nested.get("data"), dict):
                    courses = nested.get("data", {}).get("results") or []

            if not isinstance(courses, list):
                courses = []

            debug_log(f"找到 {len(courses)} 个课程")

            if not courses:
                debug_log("未找到课程，显示引导信息")
                msg = f"🔍 未找到包含「{query}」的课程\n\n"
                msg += "💡 您可以通过以下方式贡献:\n"
                msg += "1. 使用 /hit contribute 提交课程信息\n"
                msg += "2. 分享课程资料"
                await self._send_text(event, msg)
                return

            # 若已精准命中单课，直接读取并折叠转发 README.md
            precise = self._resolve_precise_course(courses, query)
            if precise:
                course_code = str(precise.get("code") or "").strip()
                course_name = str(precise.get("name") or "").strip()
                if course_code:
                    try:
                        campus = self._get_event_campus(event)
                        debug_log(
                            f"精准命中课程，读取README: code={course_code}, campus={campus}"
                        )
                        detail = await self.agent_client.get_course_detail(
                            course_code=course_code,
                            campus=campus,
                            include_toml=False,
                        )
                        if detail.success:
                            readme_md = self._extract_readme_md_from_course_read(
                                detail.output or {}
                            )
                            if readme_md:
                                folded_ok = await self._send_course_readme_folded(
                                    event,
                                    course_code=course_code,
                                    course_name=course_name or "课程详情",
                                    readme_md=readme_md,
                                )
                                if folded_ok:
                                    debug_log(f"README 折叠发送成功: {course_code}")
                                    return
                                # 折叠失败则回退纯文本片段
                                chunks = self._split_markdown_by_titles(
                                    readme_md, max_chunk_chars=1200
                                )
                                if chunks:
                                    await self._send_text(
                                        event,
                                        f"📘 {course_code} - {course_name or '课程详情'} README（文本回退）\n\n{chunks[0][:1800]}",
                                    )
                                    return
                            else:
                                debug_log(
                                    f"course.read 成功但缺少 readme_md: code={course_code}",
                                    "WARNING",
                                )
                        else:
                            debug_log(
                                f"course.read 失败: code={course_code}, error={detail.error}",
                                "WARNING",
                            )
                    except Exception as e:
                        self._log_exception("读取课程 README 失败", e)

            # 优先发送折叠结果（合并转发）
            lines = [f"查询关键词: {query}"]
            for i, course in enumerate(courses[:10], 1):
                code = str(course.get("code", "未知") or "未知")
                name = str(course.get("name", "未知") or "未知")
                teacher = str(
                    course.get("teacher") or course.get("teacher_name") or ""
                ).strip()
                line = f"{i}. {code} - {name}"
                if teacher:
                    line += f" | 教师: {teacher}"
                lines.append(line)

            folded_ok = await self._send_folded_texts(
                event, f"课程检索结果（{len(courses)}）", lines
            )
            if folded_ok:
                return

            # 折叠失败则回退普通文本
            msg = f"🔍 找到 {len(courses)} 个相关课程:\n\n"
            for i, course in enumerate(courses[:5], 1):
                code = str(course.get("code", "未知") or "未知")
                name = str(course.get("name", "未知") or "未知")
                msg += f"{i}. {code} - {name}\n"

            if len(courses) > 5:
                msg += f"\n... 还有 {len(courses) - 5} 个结果"
            msg += "\n\n💡 使用 /hit course <课程代码> 查看详情"
            debug_log(f"发送搜索结果（文本回退），长度: {len(msg)}")
            await self._send_text(event, msg)

        except Exception as e:
            self._log_exception("搜索课程失败", e)
            await self._send_text(event, f"❌ 搜索失败: {str(e)}")

    async def _napcat_call_action(self, event: AstrMessageEvent, action: str, **kwargs):
        """调用 Napcat/OneBot API。"""
        bot = getattr(event, "bot", None)
        if bot is None:
            raise RuntimeError("当前 event 无 bot，无法调用 Napcat API")
        api = getattr(bot, "api", None)
        if api is None:
            raise RuntimeError("当前平台 bot 无 api 对象")
        call_action = getattr(api, "call_action", None)
        if call_action is None:
            raise RuntimeError("当前平台 api 不支持 call_action")
        return await call_action(action, **kwargs)

    async def _napcat_get_group_file_list(self, event: AstrMessageEvent, group_id: str):
        """获取群文件列表（兼容多返回格式，并递归扫描子目录）。"""

        def _safe_int(v: Any) -> int:
            try:
                return int(v or 0)
            except Exception:
                return 0

        def _unwrap_data(resp: Any) -> Optional[Dict[str, Any]]:
            if isinstance(resp, dict):
                data = resp.get("data")
                if isinstance(data, dict):
                    return data
                if isinstance(resp, dict):
                    return resp
            if isinstance(resp, str):
                try:
                    parsed = json.loads(resp)
                    if isinstance(parsed, dict):
                        data = parsed.get("data")
                        if isinstance(data, dict):
                            return data
                        return parsed
                except Exception:
                    return None
            return None

        def _extract_files_and_folders(data: Dict[str, Any]) -> tuple:
            files_raw: List[Dict[str, Any]] = []
            folders_raw: List[Dict[str, Any]] = []

            file_keys = ["files", "file_list", "fileList", "items", "records", "list"]
            folder_keys = [
                "folders",
                "folder_list",
                "folderList",
                "dirs",
                "dir_list",
                "directories",
            ]

            for k in file_keys:
                v = data.get(k)
                if isinstance(v, list):
                    for row in v:
                        if isinstance(row, dict):
                            # 一些实现会把 folder 混在 items 里
                            row_type = str(
                                row.get("type") or row.get("item_type") or ""
                            ).lower()
                            is_folder = bool(row.get("is_folder")) or row_type in {
                                "folder",
                                "dir",
                                "directory",
                            }
                            if is_folder:
                                folders_raw.append(row)
                            else:
                                files_raw.append(row)

            for k in folder_keys:
                v = data.get(k)
                if isinstance(v, list):
                    for row in v:
                        if isinstance(row, dict):
                            folders_raw.append(row)

            return files_raw, folders_raw

        def _normalize_file_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            out: List[Dict[str, Any]] = []
            for row in rows:
                file_id = str(
                    row.get("file_id")
                    or row.get("fileId")
                    or row.get("fid")
                    or row.get("id")
                    or row.get("uuid")
                    or ""
                ).strip()
                file_name = str(
                    row.get("file_name")
                    or row.get("fileName")
                    or row.get("name")
                    or row.get("title")
                    or ""
                ).strip()
                if not file_name:
                    continue

                item = {
                    "file_id": file_id,
                    "file_name": file_name,
                    "file_size": _safe_int(
                        row.get("file_size")
                        or row.get("size")
                        or row.get("filesize")
                        or row.get("fileSize")
                    ),
                    "file_url": str(
                        row.get("file_url")
                        or row.get("url")
                        or row.get("download_url")
                        or ""
                    ).strip(),
                    "uploader_id": str(
                        row.get("uploader_id")
                        or row.get("uploader")
                        or row.get("uploader_uin")
                        or row.get("uploaderId")
                        or ""
                    ).strip(),
                    "uploader_name": str(
                        row.get("uploader_name")
                        or row.get("uploader_nick")
                        or row.get("uploaderName")
                        or ""
                    ).strip(),
                    "busid": str(
                        row.get("busid") or row.get("bus_id") or row.get("busId") or ""
                    ).strip(),
                }
                # 某些实现只返回 name/size，不返回 file_id，这里先保留，后续下载阶段会再尝试 get_group_file_url
                out.append(item)
            return out

        def _extract_folder_ids(rows: List[Dict[str, Any]]) -> List[str]:
            out: List[str] = []
            for row in rows:
                folder_id = str(
                    row.get("folder_id")
                    or row.get("folderId")
                    or row.get("id")
                    or row.get("fid")
                    or row.get("folder")
                    or ""
                ).strip()
                if folder_id:
                    out.append(folder_id)
            return out

        async def _call_and_parse(
            action: str, payload: Dict[str, Any]
        ) -> Optional[Dict[str, Any]]:
            try:
                resp = await self._napcat_call_action(event, action, **payload)
                data = _unwrap_data(resp)
                if data is None:
                    debug_log(
                        f"群文件API {action} 返回不可解析: payload={payload}",
                        "DEBUG",
                    )
                    return None
                files_raw, folders_raw = _extract_files_and_folders(data)
                debug_log(
                    f"群文件API {action} 成功: payload={payload}, files={len(files_raw)}, folders={len(folders_raw)}",
                    "DEBUG",
                )
                return data
            except Exception as e:
                debug_log(
                    f"群文件API {action} 调用失败: payload={payload}, err={e}", "DEBUG"
                )
                return None

        gid = str(group_id)
        visited_folders = set()
        folder_queue = ["/", "root", "0", ""]
        all_files: List[Dict[str, Any]] = []
        seen_keys = set()

        # 先尝试根目录接口（部分实现只支持这个）
        root_actions = ["get_group_root_files", "get_group_file_list"]
        for action in root_actions:
            data = await _call_and_parse(action, {"group_id": gid})
            if not isinstance(data, dict):
                continue
            files_raw, folders_raw = _extract_files_and_folders(data)
            for item in _normalize_file_rows(files_raw):
                key = f"{item.get('file_id', '')}::{item.get('file_name', '')}"
                if key not in seen_keys:
                    seen_keys.add(key)
                    all_files.append(item)
            for fid in _extract_folder_ids(folders_raw):
                if fid not in visited_folders and fid not in folder_queue:
                    folder_queue.append(fid)

        # 递归扫描子目录
        while folder_queue:
            folder_id = folder_queue.pop(0)
            if folder_id in visited_folders:
                continue
            visited_folders.add(folder_id)

            payloads = [
                {"group_id": gid, "folder_id": folder_id, "file_count": 200},
                {"group_id": gid, "folder": folder_id, "file_count": 200},
                {"group_id": gid, "folder_id": folder_id, "limit": 200},
                {"group_id": gid, "folder": folder_id, "limit": 200},
                {"group_id": gid, "folder_id": folder_id, "count": 200},
                {"group_id": gid, "folder": folder_id, "count": 200},
            ]

            data = None
            for body in payloads:
                parsed = await _call_and_parse("get_group_files_by_folder", body)
                if isinstance(parsed, dict):
                    data = parsed
                    # 该接口成功返回时，通常不需要继续试其他参数形态
                    break

            if not isinstance(data, dict):
                continue

            files_raw, folders_raw = _extract_files_and_folders(data)
            for item in _normalize_file_rows(files_raw):
                key = f"{item.get('file_id', '')}::{item.get('file_name', '')}"
                if key not in seen_keys:
                    seen_keys.add(key)
                    all_files.append(item)

            for fid in _extract_folder_ids(folders_raw):
                if fid not in visited_folders and fid not in folder_queue:
                    folder_queue.append(fid)

        debug_log(
            f"群文件枚举完成: group_id={gid}, files={len(all_files)}, visited_folders={len(visited_folders)}",
            "INFO",
        )
        return all_files

    async def _napcat_download_group_file(
        self, event: AstrMessageEvent, file_ref: Dict[str, str]
    ):
        """下载群文件并返回 bytes。"""
        file_id = str(file_ref.get("file_id") or "").strip()
        file_name = str(file_ref.get("file_name") or "").strip() or f"{file_id}.bin"
        file_url = str(file_ref.get("file_url") or "").strip()
        group_id = str(event.get_group_id() or "").strip()
        busid = str(file_ref.get("busid") or "").strip()

        url_to_download = file_url
        if not url_to_download and file_id:
            try:
                args = {"group_id": group_id, "file_id": file_id}
                if busid:
                    args["busid"] = busid
                resp = await self._napcat_call_action(
                    event, "get_group_file_url", **args
                )
                if isinstance(resp, dict):
                    data = (
                        resp.get("data") if isinstance(resp.get("data"), dict) else resp
                    )
                    if isinstance(data, dict):
                        url_to_download = str(
                            data.get("url") or data.get("download_url") or ""
                        ).strip()
            except Exception:
                url_to_download = ""

        if not url_to_download:
            raise RuntimeError(f"无法获取文件下载地址: {file_name} ({file_id})")

        resp = await self._napcat_call_action(
            event,
            "download_file",
            url=url_to_download,
            name=file_name,
        )
        data = (
            resp.get("data")
            if isinstance(resp, dict) and isinstance(resp.get("data"), dict)
            else resp
        )
        if not isinstance(data, dict):
            raise RuntimeError("download_file 返回格式异常")
        file_path = str(data.get("file") or "").strip()
        if not file_path:
            raise RuntimeError("download_file 未返回文件路径")

        p = Path(file_path)
        if not p.exists():
            raise RuntimeError(f"download_file 返回路径不存在: {file_path}")
        return p.read_bytes()

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
                self._log_exception("每日总结循环错误", e)
                await asyncio.sleep(3600)

    async def _file_scan_loop(self):
        """定时扫描入库循环 - 自动检查群文件并入库"""
        debug_log("定时文件扫描入库循环已启动")
        loop_count = 0
        scan_interval_hours = self.config_manager.config.file_scan_interval_hours
        scan_interval_seconds = scan_interval_hours * 3600

        # 启动时先等待一段时间，避免和启动时间太近
        await asyncio.sleep(60)

        while True:
            try:
                loop_count += 1
                now = datetime.now()
                debug_log(f"[扫描入库循环 #{loop_count}] 开始扫描...")

                # 获取所有已配置的群组
                group_ids = list(self.config_manager.group_configs.keys())
                if not group_ids:
                    debug_log("没有配置群组，跳过扫描")
                else:
                    for group_id in group_ids:
                        try:
                            await self._scan_and_ingest_group(group_id)
                        except Exception as e:
                            debug_log(f"群 {group_id} 扫描入库失败: {e}", "ERROR")

                debug_log(
                    f"[扫描入库循环 #{loop_count}] 完成，下次扫描等待 {scan_interval_hours} 小时"
                )

                # 等待下次扫描
                await asyncio.sleep(scan_interval_seconds)

            except Exception as e:
                self._log_exception("定时扫描入库循环错误", e)
                await asyncio.sleep(3600)

    async def _scan_and_ingest_group(self, group_id: str):
        """扫描并入库单个群的文件

        注意: 定时任务无法直接调用需要 event 上下文的文件接口。
        当前实现依赖于是否配置了文件监听回调。
        实际使用时，建议通过消息事件触发扫描（用户发送文件时自动处理）。
        """
        debug_log(f"检查群文件扫描: group={group_id}")

        # 检查是否有可用的文件监听回调
        # file_scanner 应该通过消息事件自动处理文件
        # 这里只做统计和日志
        stats = self.file_scanner.get_statistics()
        debug_log(
            f"文件扫描统计: 已入库={stats['already_ingested']}, "
            f"待处理={stats['uploaded_pending_ingest']}, "
            f"已扫描={stats['total_scanned']}"
        )

        # 如果有待入库文件，执行入库
        if stats["uploaded_pending_ingest"] > 0:
            debug_log(
                f"发现 {stats['uploaded_pending_ingest']} 个待入库文件，执行入库..."
            )
            try:
                result = await self.file_scanner.ingest_uploaded_files(limit=20)
                if result.ingested_files > 0:
                    debug_log(f"✅ 入库成功: {result.ingested_files} 个文件")
                    # 可以发送通知到管理员
                    await self._notify_admin_scan_result(group_id, result)
                else:
                    debug_log(f"入库完成: {result.error_message or '无新文件'}")
            except Exception as e:
                debug_log(f"入库执行失败: {e}", "ERROR")
        else:
            debug_log(f"群 {group_id} 没有待入库文件")

    async def _notify_admin_scan_result(self, group_id: str, result):
        """通知管理员扫描入库结果"""
        try:
            group_config = self.config_manager.get_group_config(group_id)
            admin_users = group_config.get("admin_users", [])
            if not admin_users:
                return

            msg = (
                f"📁 群文件定时扫描入库完成\n"
                f"群组: {group_id}\n"
                f"入库: {result.ingested_files} 个\n"
                f"失败: {result.failed_files} 个\n"
                f"跳过: {result.skipped_files} 个"
            )

            from astrbot.core.message.message_event_result import MessageChain
            from astrbot.api.platform import MessageMember

            for admin_id in admin_users:
                try:
                    session = f"private:{admin_id}"
                    sender = MessageMember(user_id=admin_id, nickname="管理员")
                    msg_chain = MessageChain([{"type": "plain", "text": msg}])
                    if self.context:
                        await self.context.send_message(session, msg_chain)
                except Exception as e:
                    debug_log(f"通知管理员 {admin_id} 失败: {e}", "WARNING")
        except Exception as e:
            debug_log(f"发送管理员通知失败: {e}", "WARNING")

    # ==================== 指令 ====================

    @filter.command_group("hit")
    def hit_group(self):
        """HIT智能助手指令组"""
        pass

    @hit_group.command("help")
    async def help_cmd(self, event: AstrMessageEvent):
        """显示帮助信息"""
        self._stop_event_safe(event, reason="command:help")
        debug_log(f"用户 {event.get_sender_name()} 请求帮助")

        msg = """🎓 HIT智能助手 v2.0.1

📚 课程查询:
  /hit course <关键词> - 搜索课程

🔍 资料搜索:
  /hit file <关键词> - 搜索群文件
  /hit search <关键词> - 深度搜索

📁 群文件:
  /hit scan - 扫描并入库群文件（一步完成）
  自动: 定时扫描（每6小时）+ 文件监听自动处理

💡 智能功能:
  - 自动识别课程查询意图
  - 自动识别资料分享意图
  - Agent多轮对话（自然语言交互）
  - 每日22:30总结群聊精华

📝 贡献:
  /hit contribute - 提交课程评价或资料

📊 状态:
  /hit status - 查看插件状态

🔧 Debug:
    当前模式: {"DEBUG" if DEBUG_MODE else "PRODUCTION"}
    报错诊断日志: {"开启" if self.error_log_enabled else "关闭"}
"""
        await event.send(event.plain_result(msg))

    @hit_group.command("course")
    async def course_cmd(self, event: AstrMessageEvent, query: str = ""):
        """搜索课程"""
        full_query = self._extract_hit_command_query(event, "course", query)
        debug_log(
            f"用户 {event.get_sender_name()} 执行 course 命令: query={full_query}"
        )

        if not full_query:
            debug_log("查询关键词为空", "WARNING")
            await event.send(
                event.plain_result("❌ 请输入搜索关键词\n用法: /hit course 自动控制")
            )
            self._stop_event_safe(event, reason="command:course_empty")
            return

        await self._search_course(event, full_query)
        self._stop_event_safe(event, reason="command:course_done")

    @hit_group.command("scan")
    async def scan_cmd(self, event: AstrMessageEvent):
        """扫描并入库群文件（一步完成）"""
        self._stop_event_safe(event, reason="command:scan")
        debug_log(f"用户 {event.get_sender_name()} 执行 scan 命令")

        group_id = str(event.get_group_id() or "").strip()
        if not group_id:
            debug_log("非群聊环境，拒绝执行", "WARNING")
            await event.send(event.plain_result("❌ 该指令仅在群聊中可用"))
            return

        debug_log(f"开始扫描并入库群 {group_id} 的文件")
        await event.send(event.plain_result("📁 开始扫描并入库群文件，请稍候..."))

        try:

            async def _list_cb(gid: str):
                return await self._napcat_get_group_file_list(event, gid)

            async def _download_cb(file_ref):
                if isinstance(file_ref, dict):
                    return await self._napcat_download_group_file(event, file_ref)
                # 兼容旧签名（仅 file_id）
                return await self._napcat_download_group_file(
                    event,
                    {"file_id": str(file_ref), "file_name": str(file_ref)},
                )

            result = await self.file_scanner.scan_and_ingest_group_files(
                group_id,
                get_file_list_func=_list_cb,
                download_file_func=_download_cb,
            )
            debug_log(f"扫描入库完成: {result}")
            if result.error_message:
                await event.send(
                    event.plain_result(f"❌ 扫描入库失败: {result.error_message}")
                )
                return

            msg = (
                "📁 群文件扫描入库完成\n"
                f"总文件: {result.total_candidates}\n"
                f"入库成功: {result.ingested_files}\n"
                f"跳过: {result.skipped_files}\n"
                f"失败: {result.failed_files}"
            )
            await event.send(event.plain_result(msg))
        except Exception as e:
            self._log_exception("扫描入库群文件失败", e)
            await event.send(event.plain_result(f"❌ 扫描入库失败: {str(e)}"))

    @hit_group.command("status")
    async def status_cmd(self, event: AstrMessageEvent):
        """查看插件状态"""
        self._stop_event_safe(event, reason="command:status")
        debug_log(f"用户 {event.get_sender_name()} 请求状态信息")

        if self.intent_service is not None:
            await self.intent_service._check_and_reset_quota()
            quota_status = self.intent_service.get_quota_status()
        else:
            quota_status = {
                "calls_in_window": 0,
                "quota_limit": 0,
                "window_hours": 5,
                "remaining": 0,
            }
        scanner_stats = self.file_scanner.get_statistics()

        msg = f"""📊 HIT智能助手状态

🤖 AI调用:
    当前窗口已调用: {quota_status["calls_in_window"]}/{quota_status["quota_limit"]}
    窗口长度: {quota_status["window_hours"]}小时
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
    意图服务: {"✅" if self.intent_service else "⏸️ (未启用)"}
  Agent客户端: {"✅" if self.agent_client else "❌"}
  文件扫描: {"✅" if self.file_scanner else "❌"}
    聊天总结: {"✅" if self.chat_summarizer else "⏸️ (未启用)"}

🔧 Debug模式: {"开启" if DEBUG_MODE else "关闭"}
🐞 报错诊断日志: {"开启" if self.error_log_enabled else "关闭"}
"""
        debug_log(f"状态信息:\n{msg}", "DEBUG")
        await event.send(event.plain_result(msg))

    @hit_group.command("file")
    async def file_cmd(self, event: AstrMessageEvent, query: str = ""):
        """按关键词搜索文件资料"""
        full_query = self._extract_hit_command_query(event, "file", query)
        if not full_query.strip():
            await event.send(
                event.plain_result("❌ 请输入关键词\n用法: /hit file 自动控制 课件")
            )
            self._stop_event_safe(event, reason="command:file_empty")
            return

        class _SimpleIntentResult:
            extracted_info = {"keywords": full_query.split()}

        await self._handle_file_search(event, _SimpleIntentResult())
        self._stop_event_safe(event, reason="command:file_done")

    @hit_group.command("search")
    async def search_cmd(self, event: AstrMessageEvent, query: str = ""):
        """按关键词执行深度搜索"""
        full_query = self._extract_hit_command_query(event, "search", query)
        if not full_query.strip():
            await event.send(
                event.plain_result("❌ 请输入关键词\n用法: /hit search 保研政策")
            )
            self._stop_event_safe(event, reason="command:search_empty")
            return

        class _SimpleIntentResult:
            extracted_info = {"keywords": full_query.split()}

        await self._handle_deep_search(event, _SimpleIntentResult())
        self._stop_event_safe(event, reason="command:search_done")

    @hit_group.command("contribute")
    async def contribute_cmd(self, event: AstrMessageEvent, content: str = ""):
        """贡献课程内容（支持 preview/submit）。"""
        self._stop_event_safe(event, reason="command:contribute")
        await self.contribution_service.handle_contribute_command(event, content)

    @hit_group.command("ingest")
    async def ingest_cmd(self, event: AstrMessageEvent, limit: str = "20"):
        """触发批量入库（data.ingest）"""
        self._stop_event_safe(event, reason="command:ingest")
        try:
            parsed_limit = int(limit)
        except Exception:
            await event.send(
                event.plain_result("❌ limit 必须是整数，例如: /hit ingest 20")
            )
            return

        if parsed_limit <= 0:
            await event.send(event.plain_result("❌ limit 必须大于 0"))
            return

        safe_limit = min(parsed_limit, 100)
        await event.send(
            event.plain_result(f"📥 开始入库，最多处理 {safe_limit} 个文件...")
        )

        try:
            result = await self.file_scanner.ingest_uploaded_files(limit=safe_limit)
            if result.error_message and result.total_candidates == 0:
                await event.send(
                    event.plain_result(f"⚠️ 入库未执行: {result.error_message}")
                )
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
            self._log_exception("入库失败", e)
            await event.send(event.plain_result(f"❌ 入库失败: {e}"))

    @hit_group.command("errlog")
    async def errlog_cmd(self, event: AstrMessageEvent, action: str = "status"):
        """报错诊断日志开关（on/off/status）。"""
        self._stop_event_safe(event, reason="command:errlog")
        op = (action or "status").strip().lower()
        if op in {"status", "s"}:
            await event.send(
                event.plain_result(
                    "🐞 报错诊断日志当前状态: "
                    + ("开启" if self.error_log_enabled else "关闭")
                )
            )
            return

        if op in {"on", "enable", "1", "true"}:
            self._set_error_log_enabled(True)
            await event.send(event.plain_result("✅ 已开启报错诊断日志"))
            return

        if op in {"off", "disable", "0", "false"}:
            self._set_error_log_enabled(False)
            await event.send(event.plain_result("✅ 已关闭报错诊断日志"))
            return

        await event.send(event.plain_result("❌ 用法: /hit errlog [on|off|status]"))

    @hit_group.command("debug")
    async def debug_cmd(self, event: AstrMessageEvent):
        """查看详细调试信息"""
        self._stop_event_safe(event, reason="command:debug")
        debug_log(f"用户 {event.get_sender_name()} 请求调试信息")

        # 收集调试信息
        config = self.config_manager.config
        if self.intent_service is not None:
            quota = self.intent_service.get_quota_status()
        else:
            quota = {
                "calls_in_window": 0,
                "quota_limit": 0,
                "window_hours": 5,
                "remaining": 0,
            }

        msg = f"""🔧 详细调试信息

📋 配置信息:
        AI意图模型: {config.glm_intent_model}
        AI复杂模型: {config.glm_complex_model}
        AI额度: {config.glm_quota_limit}/{config.glm_quota_window_hours}h
        AI接口: {config.glm_base_url}
  Agent后端: {config.agent_backend_url}
  上下文窗口: {config.context_window_minutes}分钟
  检查间隔: {config.intent_check_interval}秒

📊 运行状态:
  当前时间: {datetime.now().isoformat()}
    AI窗口调用: {quota["calls_in_window"]}/{quota["quota_limit"]}
    AI窗口长度: {quota["window_hours"]}h
        AI剩余额度: {quota["remaining"]}
  消息队列数: {len(self.queue_manager.get_all_queues())}
  定时任务数: {len(self._tasks)}

🔑 API密钥状态:
        AI: {"✅ 已配置" if config.glm_api_key else "❌ 未配置"}
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
