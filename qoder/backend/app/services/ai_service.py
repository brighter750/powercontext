"""AI model service for StudyFlow.

The model is responsible for understanding learner input, explaining concepts,
generating practice, and turning a learning activity into a memory narrative. It is
never responsible for deciding what the learner actually knows — that stays in the
domain layer, derived from stored records.

Two wire protocols are supported:

* ``openai``    — ``POST {base}/chat/completions``
* ``anthropic`` — ``POST {base}/messages``

When no model is configured the service raises :class:`AiNotConfiguredError`; callers
must surface that honestly instead of inventing an answer.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.config import Settings

LOGGER = logging.getLogger("studyflow.ai")

SYSTEM_PROMPT = (
    "你是一名严格、诚实的中文学习教练。"
    "规则："
    "1) 只依据提供给你的学习者长期记忆与当前输入作答，绝不编造学习者过去的学习记录、"
    "练习结果或掌握程度。"
    "2) 如果提供的记忆中没有相关信息，必须明确说明“我没有找到相关记忆”，并请学习者补充。"
    "3) 解释要具体、可执行，优先给出解题思路和自测方法。"
    "4) 语气简洁、鼓励但不空泛。"
)


class AiNotConfiguredError(RuntimeError):
    """No AI model endpoint or credential is configured."""


class AiProviderError(RuntimeError):
    """The configured model endpoint failed."""


@dataclass(slots=True)
class TopicInsight:
    """One knowledge point mentioned by the learner."""

    topic: str
    status: str = "unknown"  # weak | familiar | mastered | unknown
    note: str = ""


@dataclass(slots=True)
class LearningInsights:
    """Structured understanding of one piece of learner input."""

    is_learning_activity: bool = False
    course: str = ""
    goal: str = ""
    topics: list[TopicInsight] = field(default_factory=list)
    practice_count: int | None = None
    practice_correct: int | None = None
    practice_wrong: int | None = None
    self_rating: int | None = None
    preferred_style: str = ""
    notes: str = ""

    @property
    def practice_total(self) -> int | None:
        """Total practice size, when the input made it clear."""
        if self.practice_count is not None:
            return self.practice_count
        if self.practice_correct is not None and self.practice_wrong is not None:
            return self.practice_correct + self.practice_wrong
        return None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> LearningInsights:
        """Build insights from a decoded model payload."""
        topics: list[TopicInsight] = []
        for raw_topic in payload.get("topics") or []:
            if not isinstance(raw_topic, dict):
                continue
            name = str(raw_topic.get("topic") or "").strip()
            if not name:
                continue
            topics.append(
                TopicInsight(
                    topic=name,
                    status=str(raw_topic.get("status") or "unknown").strip().lower(),
                    note=str(raw_topic.get("note") or "").strip(),
                )
            )
        return cls(
            is_learning_activity=bool(payload.get("is_learning_activity")),
            course=str(payload.get("course") or "").strip(),
            goal=str(payload.get("goal") or "").strip(),
            topics=topics,
            practice_count=_as_int(payload.get("practice_count")),
            practice_correct=_as_int(payload.get("practice_correct")),
            practice_wrong=_as_int(payload.get("practice_wrong")),
            self_rating=_as_int(payload.get("self_rating")),
            preferred_style=str(payload.get("preferred_style") or "").strip(),
            notes=str(payload.get("notes") or "").strip(),
        )


@dataclass(slots=True)
class PracticeQuestion:
    """One generated practice question."""

    question: str
    answer: str = ""
    hint: str = ""


class AiProvider(Protocol):
    """Minimal contract every wire protocol implements."""

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        system: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.4,
    ) -> str:
        """Return the model's reply text."""
        ...


def _endpoint(base: str, style: str) -> str:
    """Build the request URL, tolerating a base that already names the endpoint."""
    normalized = base.rstrip("/")
    if style == "anthropic":
        return normalized if normalized.endswith("/messages") else f"{normalized}/messages"
    return (
        normalized if normalized.endswith("/chat/completions") else f"{normalized}/chat/completions"
    )


class _HttpProvider:
    """Shared HTTP plumbing for the concrete providers."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def _send(
        self,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self._settings.ai_timeout) as client:
                response = await client.post(url, headers=headers, json=body)
        except httpx.TimeoutException as error:
            raise AiProviderError(f"模型服务请求超时（{self._settings.ai_timeout}s）：{error}") from error
        except httpx.HTTPError as error:
            raise AiProviderError(f"无法连接模型服务：{error}") from error
        if response.status_code >= 400:
            raise AiProviderError(
                f"模型服务返回 HTTP {response.status_code}：{response.text[:400]}"
            )
        try:
            return response.json()
        except ValueError as error:
            raise AiProviderError("模型服务返回了非 JSON 响应") from error


class OpenAiCompatibleProvider(_HttpProvider):
    """Provider for OpenAI-compatible ``/chat/completions`` endpoints."""

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        system: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.4,
    ) -> str:
        payload_messages = list(messages)
        if system:
            payload_messages = [{"role": "system", "content": system}, *payload_messages]
        body = {
            "model": self._settings.ai_model,
            "messages": payload_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        url = _endpoint(str(self._settings.ai_api_base), "openai")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._settings.ai_api_key}",
        }
        payload = await self._send(url, headers, body)
        choices = payload.get("choices") or []
        if not choices:
            raise AiProviderError("模型服务没有返回任何候选结果")
        content = choices[0].get("message", {}).get("content", "")
        return str(content)


class AnthropicCompatibleProvider(_HttpProvider):
    """Provider for Anthropic-compatible ``/messages`` endpoints."""

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        system: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.4,
    ) -> str:
        body: dict[str, Any] = {
            "model": self._settings.ai_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            body["system"] = system
        url = _endpoint(str(self._settings.ai_api_base), "anthropic")
        headers = {
            "Content-Type": "application/json",
            "x-api-key": str(self._settings.ai_api_key),
            "anthropic-version": "2023-06-01",
        }
        payload = await self._send(url, headers, body)
        blocks = payload.get("content") or []
        text = "".join(block.get("text", "") for block in blocks if isinstance(block, dict))
        if not text:
            raise AiProviderError("模型服务没有返回文本内容")
        return text


class AiService:
    """Application-facing AI operations built on top of a configured provider."""

    def __init__(self, settings: Settings, provider: AiProvider | None = None) -> None:
        self._settings = settings
        self._provider = provider or _build_provider(settings)

    @property
    def is_configured(self) -> bool:
        """True when a model endpoint and credential are available."""
        return self._provider is not None

    async def _complete(
        self,
        messages: list[dict[str, str]],
        *,
        system: str | None = SYSTEM_PROMPT,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        if self._provider is None:
            raise AiNotConfiguredError(
                "尚未配置 AI 模型服务。请设置 STUDYFLOW_AI_API_BASE、STUDYFLOW_AI_API_KEY、"
                "STUDYFLOW_AI_MODEL 后重启服务。"
            )
        return await self._provider.complete(
            messages,
            system=system,
            max_tokens=max_tokens or self._settings.ai_max_tokens,
            temperature=self._settings.ai_temperature if temperature is None else temperature,
        )

    async def extract_insights(self, text: str, *, context: str = "") -> LearningInsights:
        """Understand one piece of learner input.

        The model is asked for strict JSON. When it does not comply the raw reply is
        still surfaced to the learner as notes, so no information is silently lost.
        """
        instruction = (
            "请理解下面这位学习者说的话，并只输出一个 JSON 对象（不要输出解释、不要用代码块）。\n"
            "字段说明：\n"
            '- "is_learning_activity": 是否描述了一次具体的学习或练习活动（布尔值）\n'
            '- "course": 正在学习的课程名称，没有就填空字符串\n'
            '- "goal": 学习目标，没有就填空字符串\n'
            '- "topics": 数组，每项含 "topic"（知识点名称）、"status"（weak/familiar/mastered/unknown 之一）、'
            '"note"（一句说明）\n'
            '- "practice_count": 本次练习题数，没有就填 null\n'
            '- "practice_correct": 答对题数，没有就填 null\n'
            '- "practice_wrong": 答错题数，没有就填 null\n'
            '- "self_rating": 学习者自评掌握程度 1-5，没有就填 null\n'
            '- "preferred_style": 学习者偏好的讲解方式，没有就填空字符串\n'
            '- "notes": 一句话概括这条输入的核心信息\n'
            "只输出 JSON。"
        )
        parts = [instruction]
        if context:
            parts.append(f"已知的学习者长期记忆（仅供参考，不要覆盖这里的原始信息）：\n{context}")
        parts.append(f"学习者输入：\n{text}")
        reply = await self._complete([{"role": "user", "content": "\n\n".join(parts)}])
        payload = _decode_json_object(reply)
        if payload is None:
            return LearningInsights(notes=reply.strip())
        return LearningInsights.from_payload(payload)

    async def answer(self, question: str, *, memory_context: str) -> str:
        """Answer a learner question using the recalled long-term memory."""
        if memory_context.strip():
            context_block = (
                "以下是从 PowerContext 读取到的学习者长期记忆（这是唯一可信的历史信息来源）：\n"
                f"{memory_context}\n\n"
            )
        else:
            context_block = (
                "PowerContext 中没有找到该学习者的相关长期记忆。"
                "必须明确告诉学习者你没有找到相关记忆，并请其补充，不要编造历史。\n\n"
            )
        prompt = f"{context_block}学习者的问题：\n{question}"
        return await self._complete([{"role": "user", "content": prompt}])

    async def generate_practice(
        self,
        *,
        topic: str,
        count: int = 5,
        level: str = "",
        memory_context: str = "",
    ) -> list[PracticeQuestion]:
        """Generate practice questions for one knowledge point."""
        prompt = (
            f"请针对知识点「{topic}」出 {count} 道练习题。"
            f"{('难度要求：' + level + '。') if level else ''}"
            "只输出 JSON 数组，每项含 question、answer、hint 三个字段，不要输出其他文字。\n"
            + (f"学习者背景：\n{memory_context}\n" if memory_context else "")
        )
        reply = await self._complete([{"role": "user", "content": prompt}])
        payload = _decode_json_array(reply)
        if payload is None:
            return []
        questions: list[PracticeQuestion] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            if not question:
                continue
            questions.append(
                PracticeQuestion(
                    question=question,
                    answer=str(item.get("answer") or "").strip(),
                    hint=str(item.get("hint") or "").strip(),
                )
            )
        return questions

    async def summarize_progress(self, *, memory_context: str, user_id: str) -> str:
        """Summarize a learner's recent progress from stored memory only."""
        if not memory_context.strip():
            return "PowerContext 中还没有该学习者的历史学习记录，暂时无法总结学习进步。"
        prompt = (
            f"请根据下面这位学习者（{user_id}）的长期记忆，总结他最近的学习进步与仍需加强的地方。"
            "只依据给出的记忆内容，不要编造。\n\n"
            f"{memory_context}"
        )
        return await self._complete([{"role": "user", "content": prompt}])


def _build_provider(settings: Settings) -> AiProvider | None:
    """Instantiate the configured provider, or ``None`` when nothing is configured."""
    if not settings.ai_configured:
        return None
    if settings.ai_api_style == "anthropic":
        return AnthropicCompatibleProvider(settings)
    return OpenAiCompatibleProvider(settings)


def _as_int(value: Any) -> int | None:
    """Coerce a model-supplied number into an int, or ``None``."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _strip_code_fence(text: str) -> str:
    """Remove a ```json fence if the model wrapped its answer in one."""
    stripped = text.strip()
    match = re.match(r"^```[a-zA-Z]*\s*(.*?)\s*```$", stripped, flags=re.DOTALL)
    if match:
        return match.group(1).strip()
    return stripped


def _decode_json_object(text: str) -> dict[str, Any] | None:
    """Decode the first JSON object in a model reply, or ``None``."""
    candidate = _strip_code_fence(text)
    try:
        payload = json.loads(candidate)
    except (TypeError, ValueError):
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            payload = json.loads(candidate[start : end + 1])
        except (TypeError, ValueError):
            return None
    return payload if isinstance(payload, dict) else None


def _decode_json_array(text: str) -> list[Any] | None:
    """Decode the first JSON array in a model reply, or ``None``."""
    candidate = _strip_code_fence(text)
    try:
        payload = json.loads(candidate)
    except (TypeError, ValueError):
        start = candidate.find("[")
        end = candidate.rfind("]")
        if start == -1 or end <= start:
            return None
        try:
            payload = json.loads(candidate[start : end + 1])
        except (TypeError, ValueError):
            return None
    return payload if isinstance(payload, list) else None


__all__ = [
    "AiNotConfiguredError",
    "AiProvider",
    "AiProviderError",
    "AiService",
    "AnthropicCompatibleProvider",
    "LearningInsights",
    "OpenAiCompatibleProvider",
    "PracticeQuestion",
    "TopicInsight",
]
