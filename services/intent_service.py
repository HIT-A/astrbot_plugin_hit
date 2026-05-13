"""
意图分析服务 - 使用 MiniMax API 进行意图识别与任务拆解
"""

import json
import asyncio
import re
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from enum import Enum

import httpx
from astrbot.api import logger


class IntentType(Enum):
    """意图类型"""

    COURSE_QUERY = "course_query"  # 课程查询
    SEARCH = "search"  # 统一搜索（由AI选择信源）
    CONTRIBUTION = "contribution"  # 贡献意图
    CHAT = "chat"  # 普通聊天
    UNKNOWN = "unknown"


@dataclass
class IntentResult:
    """意图识别结果"""

    has_valid_intent: bool
    intent: IntentType
    confidence: float
    extracted_info: Dict[str, Any]
    reasoning: str
    raw_response: Optional[str] = None


class IntentService:
    """意图分析服务"""

    def __init__(
        self,
        api_key: str,
        intent_model: str = "MiniMax-M2.7",
        complex_model: str = "MiniMax-M2.7",
        base_url: str = "https://api.minimaxi.com",
        quota_limit: int = 1500,
        quota_window_hours: int = 5,
        context_window_minutes: int = 3,
    ):
        self.api_key = api_key
        self.intent_model = intent_model
        self.complex_model = complex_model
        self.base_url = base_url.rstrip("/")
        self.quota_limit = max(1, int(quota_limit or 1500))
        self.quota_window_hours = max(1, int(quota_window_hours or 5))
        self.context_window_minutes = context_window_minutes

        # 配额管理
        self._calls_in_window = 0
        self._window_started_at = datetime.now()
        self._quota_lock = asyncio.Lock()

        # 意图检测历史（用于去重）
        self._recent_intents: Dict[str, datetime] = {}
        self._intent_lock = asyncio.Lock()

    async def _check_and_reset_quota(self):
        """检查并重置滚动窗口配额。"""
        async with self._quota_lock:
            now = datetime.now()
            elapsed = now - self._window_started_at
            if elapsed >= timedelta(hours=self.quota_window_hours):
                self._calls_in_window = 0
                self._window_started_at = now
                logger.info(
                    f"🔄 AI配额窗口已重置: {self._calls_in_window}/{self.quota_limit} (窗口={self.quota_window_hours}h)"
                )

    async def _consume_quota(self) -> bool:
        """
        消耗一次调用额度

        Returns:
            是否成功消耗额度
        """
        await self._check_and_reset_quota()

        async with self._quota_lock:
            if self._calls_in_window >= self.quota_limit:
                logger.warning(
                    f"⚠️ AI额度已用完: {self._calls_in_window}/{self.quota_limit} (窗口={self.quota_window_hours}h)"
                )
                return False

            self._calls_in_window += 1
            remaining = self.quota_limit - self._calls_in_window
            logger.debug(
                f"🤖 AI调用: {self._calls_in_window}/{self.quota_limit}, 剩余: {remaining} (窗口={self.quota_window_hours}h)"
            )
            return True

    def get_quota_status(self) -> Dict[str, Any]:
        """获取配额状态"""
        return {
            "calls_in_window": self._calls_in_window,
            "quota_limit": self.quota_limit,
            "window_hours": self.quota_window_hours,
            "remaining": self.quota_limit - self._calls_in_window,
            "window_started_at": self._window_started_at.isoformat(),
        }

    async def analyze_intent(
        self,
        context_text: str,
        min_confidence: float = 0.7,
    ) -> Optional[IntentResult]:
        """
        分析对话意图

        Args:
            context_text: 对话上下文文本
            min_confidence: 最小置信度阈值

        Returns:
            IntentResult或None
        """
        # 检查配额
        if not await self._consume_quota():
            logger.warning("⛔ 意图分析被跳过: 配额不足")
            return None

        # 检查API密钥
        if not self.api_key:
            logger.error("❌ AI API密钥未配置")
            return None

        try:
            result = await self._call_intent_llm(context_text)

            if not result:
                return None

            # 检查置信度
            if result.confidence < min_confidence:
                logger.debug(
                    f"⏭️ 意图置信度太低 ({result.confidence:.2f} < {min_confidence})"
                )
                result.has_valid_intent = False

            # 检查是否在冷却期内（避免重复检测相同意图）
            if result.has_valid_intent:
                async with self._intent_lock:
                    intent_key = f"{result.intent.value}_{hash(context_text) % 10000}"
                    now = datetime.now()

                    if intent_key in self._recent_intents:
                        elapsed = (
                            now - self._recent_intents[intent_key]
                        ).total_seconds()
                        if elapsed < 60:  # 1分钟内不重复检测相同意图
                            logger.debug(f"⏭️ 意图在冷却期内 ({elapsed:.0f}s)")
                            result.has_valid_intent = False

                    self._recent_intents[intent_key] = now

            if result.has_valid_intent:
                logger.info(
                    f"🎯 检测到意图: {result.intent.value} (置信度: {result.confidence:.2f})"
                )

            return result

        except Exception as e:
            logger.error(f"❌ 意图分析失败: {e}")
            return None

    async def _call_intent_llm(self, context_text: str) -> Optional[IntentResult]:
        """
        调用 MiniMax API 进行意图识别

        Args:
            context_text: 对话上下文

        Returns:
            IntentResult或None
        """
        prompt = f"""你是意图分类器。请严格输出一个 JSON 对象，不要输出 Markdown、解释、代码块、前后缀文本。

    任务：分析以下对话上下文，判断用户的真实意图。

对话上下文:
{context_text}

    请仔细分析对话内容，判断用户是否有明确的查询或操作意图。

输出JSON格式:
{{
  "has_valid_intent": true/false,
    "intent": "course_query|search|contribution|chat",
  "confidence": 0.95,
  "extracted_info": {{
        "course_name": "课程名称（优先提取，学生通常只知道课程名）",
        "course_code": "课程代码（可空）",
    "teacher_name": "教师姓名",
    "keywords": ["关键词1", "关键词2"],
    "semester": "学期信息",
    "additional_context": "其他相关信息"
  }},
  "reasoning": "判断理由，简要说明为什么识别为这个意图"
}}

意图类型说明:
- course_query: 查询课程信息（如"自动控制原理怎么样"、"COMP1011给分好吗"）
- search: 各类检索请求（如"求课件"、"有没有往年试卷"、"哈工大的保研政策"、"深圳校区有哪些实验室"）
- contribution: 用户想分享或贡献内容（如"我有资料要分享"、"这门课我上过，给分很好"）
- chat: 普通闲聊，没有明确意图

注意:
1. has_valid_intent为false时，intent应该是"chat"
2. confidence应该在0-1之间，表示置信度
3. 对于模糊的问题（如"这门课怎么样"），如果没有明确课程名称，confidence应该较低
4. 只输出 JSON 对象本身，禁止输出任何额外文本。"""

        try:
            text = await self._chat_completion_json(
                prompt=prompt,
                model=self.intent_model,
                temperature=0.3,
                max_tokens=2048,
            )
            result_json = self._parse_json_payload(text)

            # 映射意图类型
            intent_str = result_json.get("intent", "chat")
            intent_map = {
                "course_query": IntentType.COURSE_QUERY,
                "search": IntentType.SEARCH,
                "contribution": IntentType.CONTRIBUTION,
                "chat": IntentType.CHAT,
            }
            intent_type = intent_map.get(intent_str, IntentType.UNKNOWN)

            return IntentResult(
                has_valid_intent=result_json.get("has_valid_intent", False),
                intent=intent_type,
                confidence=result_json.get("confidence", 0.0),
                extracted_info=result_json.get("extracted_info", {}),
                reasoning=result_json.get("reasoning", ""),
                raw_response=text,
            )

        except json.JSONDecodeError as e:
            logger.error(
                f"❌ AI响应JSON解析失败: {e}; 原始返回预览={self._preview_text_for_debug(text)}"
            )
            return IntentResult(
                has_valid_intent=False,
                intent=IntentType.CHAT,
                confidence=0.0,
                extracted_info={},
                reasoning="json_parse_failed_fallback_chat",
                raw_response=text,
            )
        except Exception as e:
            logger.error(f"❌ 调用AI失败: {e}")
            return IntentResult(
                has_valid_intent=False,
                intent=IntentType.CHAT,
                confidence=0.0,
                extracted_info={},
                reasoning="llm_call_failed_fallback_chat",
                raw_response=None,
            )

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

    async def _chat_completion_json(
        self,
        prompt: str,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        """调用 MiniMax chatcompletion_v2 并提取文本内容。"""
        url = f"{self.base_url}/v1/text/chatcompletion_v2"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "name": "MiniMax AI"},
                {"role": "user", "content": prompt, "name": "用户"},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        def _extract_text_from_content(content: Any) -> str:
            if isinstance(content, str):
                return self._strip_reasoning_tags(content).strip()
            if isinstance(content, list):
                chunks: List[str] = []
                for part in content:
                    if isinstance(part, str):
                        stripped = self._strip_reasoning_tags(part).strip()
                        if stripped:
                            chunks.append(stripped)
                        continue
                    if isinstance(part, dict):
                        txt = part.get("text") or part.get("content") or ""
                        if isinstance(txt, str) and txt.strip():
                            chunks.append(self._strip_reasoning_tags(txt).strip())
                return "\n".join([c for c in chunks if c])
            if isinstance(content, dict):
                txt = content.get("text") or content.get("content") or ""
                if isinstance(txt, str):
                    return self._strip_reasoning_tags(txt).strip()
            return ""

        # 兼容多种响应格式
        choices = data.get("choices") if isinstance(data, dict) else None
        if isinstance(choices, list) and choices:
            first_choice = choices[0] if isinstance(choices[0], dict) else {}
            message = (
                first_choice.get("message") if isinstance(first_choice, dict) else None
            )
            if isinstance(message, dict):
                text = _extract_text_from_content(message.get("content"))
                if text:
                    return text

                # 一些实现会把文本挂在 message.text
                alt_text = message.get("text")
                if isinstance(alt_text, str) and alt_text.strip():
                    return self._strip_reasoning_tags(alt_text).strip()

            # 某些实现会把文本直接放在 choice.text/output_text
            choice_text = first_choice.get("text") or first_choice.get("output_text")
            if isinstance(choice_text, str) and choice_text.strip():
                return self._strip_reasoning_tags(choice_text).strip()

        reply = data.get("reply") if isinstance(data, dict) else None
        if isinstance(reply, str) and reply.strip():
            return self._strip_reasoning_tags(reply).strip()

        output = data.get("output") if isinstance(data, dict) else None
        if isinstance(output, str) and output.strip():
            return self._strip_reasoning_tags(output).strip()

        keys = list(data.keys()) if isinstance(data, dict) else type(data)
        first_choice_preview = ""
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            first_choice_preview = self._preview_text_for_debug(
                json.dumps(choices[0], ensure_ascii=False)
            )
        raise ValueError(
            f"MiniMax响应缺少可解析文本: keys={keys}; first_choice={first_choice_preview}"
        )

    def _parse_json_payload(self, text: str) -> Dict[str, Any]:
        """尽量从模型输出中解析 JSON 对象。"""
        raw = (text or "").strip()
        if not raw:
            raise json.JSONDecodeError("empty response", raw, 0)

        def _normalize_json_like_text(s: str) -> str:
            # 常见全角标点替换，降低中文输出导致的 JSON 失败概率
            pairs = {
                "，": ",",
                "：": ":",
                "“": '"',
                "”": '"',
                "‘": "'",
                "’": "'",
                "（": "(",
                "）": ")",
            }
            for a, b in pairs.items():
                s = s.replace(a, b)
            return s

        def _try_load_dict(s: str) -> Optional[Dict[str, Any]]:
            try:
                obj = json.loads(s)
            except Exception:
                return None

            # 兼容返回 JSON 字符串包裹 JSON 对象："{...}"
            if isinstance(obj, str):
                try:
                    nested = json.loads(obj)
                    if isinstance(nested, dict):
                        return nested
                except Exception:
                    return None
            if isinstance(obj, dict):
                return obj
            return None

        def _extract_first_balanced_json_block(s: str) -> Optional[str]:
            start = s.find("{")
            if start == -1:
                return None
            depth = 0
            in_str = False
            esc = False
            for i in range(start, len(s)):
                ch = s[i]
                if in_str:
                    if esc:
                        esc = False
                        continue
                    if ch == "\\":
                        esc = True
                        continue
                    if ch == '"':
                        in_str = False
                    continue
                if ch == '"':
                    in_str = True
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return s[start : i + 1]
            return None

        candidates: List[str] = []

        normalized = _normalize_json_like_text(raw)
        candidates.append(normalized)

        # 代码块提取（```json ... ``` / ``` ... ```）
        fence_hits = re.findall(
            r"```(?:json)?\s*([\s\S]*?)\s*```", normalized, flags=re.IGNORECASE
        )
        candidates.extend([x.strip() for x in fence_hits if x and x.strip()])

        # 从文本中提取第一个平衡的大括号 JSON
        block = _extract_first_balanced_json_block(normalized)
        if block:
            candidates.append(block)

        # 去重并按顺序尝试
        seen = set()
        ordered_candidates: List[str] = []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                ordered_candidates.append(c)

        for c in ordered_candidates:
            obj = _try_load_dict(c)
            if isinstance(obj, dict):
                return obj

        raise json.JSONDecodeError("no valid JSON object found", raw, 0)

    def _preview_text_for_debug(self, text: str, limit: int = 240) -> str:
        """返回用于日志的截断预览，避免整段输出污染日志。"""
        raw = (text or "").replace("\n", "\\n")
        if len(raw) <= limit:
            return raw
        return raw[:limit] + "..."

    async def analyze_batch(
        self,
        contexts: List[str],
        min_confidence: float = 0.7,
    ) -> List[Optional[IntentResult]]:
        """
        批量分析多个对话上下文

        Args:
            contexts: 对话上下文列表
            min_confidence: 最小置信度阈值

        Returns:
            IntentResult列表
        """
        results = []
        for context in contexts:
            result = await self.analyze_intent(context, min_confidence)
            results.append(result)
        return results

    async def plan_search_tasks(
        self,
        query: str,
        max_tasks: int = 3,
    ) -> List[Dict[str, Any]]:
        """将复杂搜索问题拆解为多个子任务。"""
        raw_query = (query or "").strip()
        if not raw_query:
            return []

        if not self.api_key:
            return [
                {
                    "query": raw_query,
                    "sources": ["rag", "brave", "arxiv", "github"],
                    "reason": "fallback_no_api_key",
                }
            ]

        safe_max = max(1, min(max_tasks, 5))
        prompt = f"""你是搜索任务规划器。请将用户问题拆成最多 {safe_max} 个子查询，并为每个子查询分配最合适的数据源。

用户问题:
{raw_query}

可选 sources（请按问题类型自行选择，不要机械平均分配）:
- rag: 站内知识库语义检索，适合课程资料、群内沉淀内容、已有问答。
- brave: 通用网页搜索，适合最新资讯、政策通知、官网页面。
- annas: 图书/文献聚合检索，适合找教材、电子书、论文线索。
- arxiv: 学术论文预印本，适合科研主题、综述、技术前沿。
- github: 开源代码与项目资料，适合实现方案、仓库、issue/discussion。
- cos: 对象存储文件，适合已上传资料、讲义、课件、压缩包。
- course: 课程索引元信息，适合按课程代码/名称定位课程。
- course_read: 课程 README 详情，适合课程细节、评价与经验。
- hit_teacher: 教师信息检索，适合授课教师与相关背景查询。

要求:
1) 子查询彼此尽量不重叠，覆盖原问题关键方面。
2) 每个子查询必须给出 1-4 个 sources。
3) 按问题本质选择 sources：
    - 问“资料/课件/试卷”优先 `cos`,`rag`,`course_read`。
    - 问“政策/通知/事实更新”优先 `brave`,`rag`。
    - 问“论文/综述/研究”优先 `arxiv`,`annas`,`github`,`rag`。
    - 问“课程详情/老师”优先 `course`,`course_read`,`hit_teacher`,`rag`。
4) 返回 JSON，格式如下:
{{
  "tasks": [
    {{
      "query": "...",
      "sources": ["rag", "github"],
      "reason": "为什么这样拆"
    }}
  ]
}}
5) 不要输出任何 JSON 之外的内容。"""

        try:
            text = await self._chat_completion_json(
                prompt=prompt,
                model=self.complex_model,
                temperature=0.2,
                max_tokens=4096,
            )
            obj = self._parse_json_payload(text)

            allowed_sources = {
                "rag",
                "brave",
                "annas",
                "arxiv",
                "github",
                "cos",
                "course",
                "course_read",
                "hit_teacher",
            }
            tasks: List[Dict[str, Any]] = []
            for item in (obj.get("tasks") or [])[:safe_max]:
                q = str(item.get("query") or "").strip()
                if not q:
                    continue
                raw_sources = item.get("sources") or []
                sources = [s for s in raw_sources if s in allowed_sources]
                if not sources:
                    sources = ["rag", "brave"]
                tasks.append(
                    {
                        "query": q,
                        "sources": sources[:4],
                        "reason": str(item.get("reason") or "").strip(),
                    }
                )

            if tasks:
                return tasks
        except Exception as e:
            logger.warning(
                f"搜索任务拆解失败，回退单查询: {e}; 原始返回预览={self._preview_text_for_debug(text if 'text' in locals() else '')}"
            )

        return [
            {
                "query": raw_query,
                "sources": ["rag", "brave", "arxiv", "github"],
                "reason": "fallback_single_task",
            }
        ]

    def format_intent_for_display(self, result: IntentResult) -> str:
        """
        格式化意图结果用于显示

        Args:
            result: 意图识别结果

        Returns:
            格式化后的文本
        """
        if not result.has_valid_intent:
            return "未检测到有效意图"

        intent_names = {
            IntentType.COURSE_QUERY: "课程查询",
            IntentType.SEARCH: "搜索",
            IntentType.CONTRIBUTION: "贡献内容",
            IntentType.CHAT: "普通聊天",
            IntentType.UNKNOWN: "未知",
        }

        msg = f"意图: {intent_names.get(result.intent, result.intent.value)}\n"
        msg += f"置信度: {result.confidence:.2%}\n"
        msg += f"理由: {result.reasoning}\n"

        if result.extracted_info:
            msg += "提取信息:\n"
            for key, value in result.extracted_info.items():
                if value:
                    msg += f"  - {key}: {value}\n"

        return msg
