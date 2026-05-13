"""
Intent Classification Service - Using GLM Flash for cheap intent classification

This module provides intent classification using GLM Flash to filter out
chat/noise and route valid intents to the main agent.
"""

import json
import asyncio
from datetime import datetime, date
from enum import Enum
from dataclasses import dataclass
from typing import Dict, Any, List, Optional

import httpx
from astrbot.api import logger

from ..core.config import PluginConfig


class IntentType(Enum):
    """Intent types for classification."""

    COURSE_QUERY = "course_query"
    FILE_SEARCH = "file_search"
    DEEP_SEARCH = "deep_search"
    CONTRIBUTION = "contribution"
    CHAT = "chat"


@dataclass
class IntentResult:
    """Intent classification result.

    Attributes:
        has_valid_intent: Whether a valid intent was detected
        intent: The detected intent type
        confidence: Confidence score (0-1)
        extracted_info: Extracted information from the message
        reasoning: Reasoning for the classification
    """

    has_valid_intent: bool
    intent: IntentType
    confidence: float
    extracted_info: Dict[str, Any]
    reasoning: str


class IntentClassifier:
    """Intent classifier using GLM Flash.

    This service uses the cheap GLM Flash model to classify user intents
    and filter out chat/noise, saving costs on the expensive main agent.
    """

    def __init__(self, config: PluginConfig):
        """Initialize the intent classifier.

        Args:
            config: Plugin configuration
        """
        self.config = config
        self.api_key = config.glm_api_key
        self.model = config.glm_intent_model
        self.base_url = config.glm_base_url.rstrip("/")
        self.daily_quota = config.glm_daily_quota

        # Quota tracking
        self._calls_today = 0
        self._last_reset_date = date.today()
        self._quota_lock = asyncio.Lock()

        # Recent intent tracking for deduplication
        self._recent_intents: Dict[str, datetime] = {}
        self._intent_lock = asyncio.Lock()

        logger.info(f"IntentClassifier initialized with model: {self.model}")

    def is_ready(self) -> bool:
        """Check if the classifier is ready to use.

        Returns:
            True if API key is configured
        """
        return bool(self.api_key)

    async def _check_and_reset_quota(self) -> None:
        """Check and reset daily quota if needed."""
        async with self._quota_lock:
            today = date.today()
            if today != self._last_reset_date:
                self._calls_today = 0
                self._last_reset_date = today
                logger.info(f"GLM quota reset: {self._calls_today}/{self.daily_quota}")

    async def _consume_quota(self) -> bool:
        """Consume one API call from the daily quota.

        Returns:
            True if quota available and consumed, False otherwise
        """
        await self._check_and_reset_quota()

        async with self._quota_lock:
            if self._calls_today >= self.daily_quota:
                logger.warning(
                    f"GLM daily quota exhausted: {self._calls_today}/{self.daily_quota}"
                )
                return False

            self._calls_today += 1
            remaining = self.daily_quota - self._calls_today
            logger.debug(
                f"GLM call: {self._calls_today}/{self.daily_quota}, remaining: {remaining}"
            )
            return True

    async def classify(
        self, messages: List[Dict[str, Any]], min_confidence: float = 0.7
    ) -> IntentResult:
        """Classify intent from a list of messages.

        Args:
            messages: List of message dictionaries with sender_name, message, timestamp
            min_confidence: Minimum confidence threshold

        Returns:
            IntentResult with classification results
        """
        # Check quota
        if not await self._consume_quota():
            logger.warning("Intent classification skipped: quota exhausted")
            return IntentResult(
                has_valid_intent=False,
                intent=IntentType.CHAT,
                confidence=0.0,
                extracted_info={},
                reasoning="Quota exhausted",
            )

        # Check API key
        if not self.api_key:
            logger.error("GLM API key not configured")
            return IntentResult(
                has_valid_intent=False,
                intent=IntentType.CHAT,
                confidence=0.0,
                extracted_info={},
                reasoning="API not configured",
            )

        try:
            # Build context from messages
            context_text = self._build_context(messages)

            # Call GLM API
            result = await self._call_glm(context_text)

            # Check confidence
            if result.confidence < min_confidence:
                logger.debug(
                    f"Intent confidence too low: {result.confidence:.2f} < {min_confidence}"
                )
                result.has_valid_intent = False

            # Check cooldown for deduplication
            if result.has_valid_intent:
                async with self._intent_lock:
                    intent_key = f"{result.intent.value}_{hash(context_text) % 10000}"
                    now = datetime.now()

                    if intent_key in self._recent_intents:
                        elapsed = (
                            now - self._recent_intents[intent_key]
                        ).total_seconds()
                        if elapsed < 60:  # 1 minute cooldown
                            logger.debug(f"Intent in cooldown period: {elapsed:.0f}s")
                            result.has_valid_intent = False

                    self._recent_intents[intent_key] = now

            if result.has_valid_intent:
                logger.info(
                    f"Intent detected: {result.intent.value} (confidence: {result.confidence:.2f})"
                )

            return result

        except Exception as e:
            logger.error(f"Intent classification failed: {e}")
            return IntentResult(
                has_valid_intent=False,
                intent=IntentType.CHAT,
                confidence=0.0,
                extracted_info={},
                reasoning=f"Classification error: {e}",
            )

    def _build_context(self, messages: List[Dict[str, Any]]) -> str:
        """Build context text from messages.

        Args:
            messages: List of message dictionaries

        Returns:
            Formatted context string
        """
        lines = []
        for msg in messages[-10:]:  # Last 10 messages
            sender = msg.get("sender_name", "Unknown")
            content = msg.get("message", "")
            lines.append(f"{sender}: {content}")
        return "\n".join(lines)

    async def _call_glm(self, context_text: str) -> IntentResult:
        """Call GLM API for intent classification.

        Args:
            context_text: Context text to classify

        Returns:
            IntentResult with classification results
        """
        prompt = f"""You are an intent classification assistant. Analyze the following conversation context and determine the user's intent.

Conversation Context:
{context_text}

Classify the intent into one of these categories:
- course_query: Query about courses (e.g., "How is the Automatic Control course?", "What's the grading for CS101?")
- file_search: Search for learning materials (e.g., "Looking for lecture notes", "Any past exam papers?")
- deep_search: Deep search questions (e.g., "HIT's postgraduate policy", "What labs are at Shenzhen campus?")
- contribution: User wants to share/contribute (e.g., "I have materials to share", "I took this course, grading was good")
- chat: Casual chat, no clear intent

Output JSON format:
{{
  "has_valid_intent": true/false,
  "intent": "course_query|file_search|deep_search|contribution|chat",
  "confidence": 0.95,
  "extracted_info": {{
    "course_code": "Course code, e.g., CS101",
    "course_name": "Course name",
    "teacher_name": "Teacher name",
    "keywords": ["keyword1", "keyword2"],
    "additional_context": "Other relevant information"
  }},
  "reasoning": "Brief explanation of why this intent was identified"
}}

Notes:
1. Set has_valid_intent to false for casual chat, complaints, or meaningless content
2. Confidence should be between 0-1
3. For vague questions without clear course names, confidence should be low
4. Only return the JSON, no other text"""

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "thinking": {"type": "disabled"},
                    "max_tokens": 2048,
                    "temperature": 0.3,
                },
            )
            resp.raise_for_status()
            data = resp.json()

            # Parse response
            choices = data.get("choices") or []
            if not choices:
                raise ValueError("GLM response missing choices")
            text = (choices[0].get("message") or {}).get("content")
            if not text:
                raise ValueError("GLM response missing message.content")
            result_json = json.loads(text)

            # Map intent string to enum
            intent_str = result_json.get("intent", "chat")
            intent_map = {
                "course_query": IntentType.COURSE_QUERY,
                "file_search": IntentType.FILE_SEARCH,
                "deep_search": IntentType.DEEP_SEARCH,
                "contribution": IntentType.CONTRIBUTION,
                "chat": IntentType.CHAT,
            }
            intent_type = intent_map.get(intent_str, IntentType.CHAT)

            return IntentResult(
                has_valid_intent=result_json.get("has_valid_intent", False),
                intent=intent_type,
                confidence=result_json.get("confidence", 0.0),
                extracted_info=result_json.get("extracted_info", {}),
                reasoning=result_json.get("reasoning", ""),
            )

    def get_quota_status(self) -> Dict[str, Any]:
        """Get current quota status.

        Returns:
            Dictionary with quota information
        """
        return {
            "calls_today": self._calls_today,
            "daily_quota": self.daily_quota,
            "remaining": self.daily_quota - self._calls_today,
            "last_reset": self._last_reset_date.isoformat(),
        }
