"""
意图分析服务 - 使用Gemini 2.5 Flash进行意图识别
"""

import json
import asyncio
from datetime import datetime, date
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from enum import Enum

import httpx
from astrbot.api import logger


class IntentType(Enum):
    """意图类型"""

    COURSE_QUERY = "course_query"  # 课程查询
    FILE_SEARCH = "file_search"  # 资料搜索
    DEEP_SEARCH = "deep_search"  # 深度搜索
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
        model: str = "gemini-2.5-flash-preview-05-20",
        daily_quota: int = 250,
        context_window_minutes: int = 3,
    ):
        self.api_key = api_key
        self.model = model
        self.daily_quota = daily_quota
        self.context_window_minutes = context_window_minutes

        # 配额管理
        self._calls_today = 0
        self._last_reset_date = date.today()
        self._quota_lock = asyncio.Lock()

        # 意图检测历史（用于去重）
        self._recent_intents: Dict[str, datetime] = {}
        self._intent_lock = asyncio.Lock()

    async def _check_and_reset_quota(self):
        """检查并重置每日配额"""
        async with self._quota_lock:
            today = date.today()
            if today != self._last_reset_date:
                self._calls_today = 0
                self._last_reset_date = today
                logger.info(
                    f"🔄 Gemini配额已重置: {self._calls_today}/{self.daily_quota}"
                )

    async def _consume_quota(self) -> bool:
        """
        消耗一次调用额度

        Returns:
            是否成功消耗额度
        """
        await self._check_and_reset_quota()

        async with self._quota_lock:
            if self._calls_today >= self.daily_quota:
                logger.warning(
                    f"⚠️ Gemini日额度已用完: {self._calls_today}/{self.daily_quota}"
                )
                return False

            self._calls_today += 1
            remaining = self.daily_quota - self._calls_today
            logger.debug(
                f"🤖 Gemini调用: {self._calls_today}/{self.daily_quota}, 剩余: {remaining}"
            )
            return True

    def get_quota_status(self) -> Dict[str, Any]:
        """获取配额状态"""
        return {
            "calls_today": self._calls_today,
            "daily_quota": self.daily_quota,
            "remaining": self.daily_quota - self._calls_today,
            "last_reset": self._last_reset_date.isoformat(),
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
            logger.error("❌ Gemini API密钥未配置")
            return None

        try:
            result = await self._call_gemini(context_text)

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

    async def _call_gemini(self, context_text: str) -> Optional[IntentResult]:
        """
        调用Gemini API进行意图识别

        Args:
            context_text: 对话上下文

        Returns:
            IntentResult或None
        """
        prompt = f"""分析以下对话上下文，判断用户的真实意图。

对话上下文:
{context_text}

请仔细分析对话内容，判断用户是否有明确的查询或操作意图。

输出JSON格式:
{{
  "has_valid_intent": true/false,
  "intent": "course_query|file_search|deep_search|contribution|chat",
  "confidence": 0.95,
  "extracted_info": {{
    "course_code": "课程代码，如CS101",
    "course_name": "课程名称",
    "teacher_name": "教师姓名",
    "keywords": ["关键词1", "关键词2"],
    "semester": "学期信息",
    "additional_context": "其他相关信息"
  }},
  "reasoning": "判断理由，简要说明为什么识别为这个意图"
}}

意图类型说明:
- course_query: 查询课程信息（如"自动控制原理怎么样"、"CS101给分好吗"）
- file_search: 搜索学习资料（如"求课件"、"有没有往年试卷"）
- deep_search: 需要深度搜索的问题（如"哈工大的保研政策"、"深圳校区有哪些实验室"）
- contribution: 用户想分享或贡献内容（如"我有资料要分享"、"这门课我上过，给分很好"）
- chat: 普通闲聊，没有明确意图

注意:
1. has_valid_intent为false时，intent应该是"chat"
2. confidence应该在0-1之间，表示置信度
3. 对于模糊的问题（如"这门课怎么样"），如果没有明确课程名称，confidence应该较低"""

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
                    headers={"Content-Type": "application/json"},
                    params={"key": self.api_key},
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {
                            "responseMimeType": "application/json",
                            "temperature": 0.3,  # 低温度以获得更确定的结果
                        },
                    },
                )
                resp.raise_for_status()
                data = resp.json()

                # 解析响应
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                result_json = json.loads(text)

                # 映射意图类型
                intent_str = result_json.get("intent", "chat")
                intent_map = {
                    "course_query": IntentType.COURSE_QUERY,
                    "file_search": IntentType.FILE_SEARCH,
                    "deep_search": IntentType.DEEP_SEARCH,
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

        except httpx.TimeoutException:
            logger.error("⏱️ Gemini API请求超时")
            return None
        except httpx.HTTPStatusError as e:
            logger.error(
                f"❌ Gemini API HTTP错误: {e.response.status_code} - {e.response.text}"
            )
            return None
        except json.JSONDecodeError as e:
            logger.error(f"❌ Gemini响应JSON解析失败: {e}")
            return None
        except Exception as e:
            logger.error(f"❌ 调用Gemini失败: {e}")
            return None

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
            IntentType.FILE_SEARCH: "资料搜索",
            IntentType.DEEP_SEARCH: "深度搜索",
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
