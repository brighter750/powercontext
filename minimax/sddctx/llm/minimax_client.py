"""MiniMax 接入。

支持两种兼容协议：

* ``anthropic`` —— ``POST {base_url}/messages``，请求头 ``x-api-key``；
* ``openai`` —— ``POST {base_url}/chat/completions``，请求头 ``Authorization: Bearer``。

两种协议都返回纯文本，调用方据此拿到模型生成的补丁。

凭据只从环境变量读取（``SDDCTX_MINIMAX_API_KEY`` / ``MINIMAX_API_KEY``），
既不落盘也不写进任何日志。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from sddctx.config import SddConfig


@dataclass(slots=True)
class LlmCall:
    """一次模型调用的记录（不含任何凭据）。"""

    model: str
    protocol: str
    base_url: str
    endpoint: str
    system: str
    prompt: str
    response_text: str
    elapsed_seconds: float
    request_bytes: int
    response_bytes: int
    usage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "protocol": self.protocol,
            "base_url": self.base_url,
            "endpoint": self.endpoint,
            "system": self.system,
            "prompt": self.prompt,
            "response_text": self.response_text,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "request_bytes": self.request_bytes,
            "response_bytes": self.response_bytes,
            "usage": self.usage,
        }


class MiniMaxError(RuntimeError):
    """MiniMax 调用失败。"""


class MiniMaxClient:
    """MiniMax 文本模型客户端。"""

    def __init__(self, config: SddConfig) -> None:
        self.config = config

    # --- 协议细节 -----------------------------------------------------------

    def _endpoint(self) -> str:
        base = self.config.minimax_base_url.rstrip("/")
        if self.config.minimax_protocol == "anthropic":
            return f"{base}/messages"
        return f"{base}/chat/completions"

    def _headers(self) -> dict[str, str]:
        key = self.config.minimax_api_key
        headers = {"Content-Type": "application/json"}
        if self.config.minimax_protocol == "anthropic":
            headers["x-api-key"] = key
            headers["anthropic-version"] = "2023-06-01"
        else:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _build_body(self, system: str, prompt: str) -> dict[str, Any]:
        if self.config.minimax_protocol == "anthropic":
            return {
                "model": self.config.minimax_model,
                "max_tokens": self.config.minimax_max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": prompt}],
            }
        return {
            "model": self.config.minimax_model,
            "max_tokens": self.config.minimax_max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }

    @staticmethod
    def _extract_text(payload: dict[str, Any]) -> str:
        if "content" in payload and isinstance(payload["content"], list):
            parts = [
                block.get("text", "")
                for block in payload["content"]
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            if parts:
                return "".join(parts)
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") or {}
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return ""

    # --- 调用 ---------------------------------------------------------------

    def call(self, *, system: str, prompt: str) -> LlmCall:
        """发起一次真实模型调用并返回记录。"""

        self.config.require_minimax()
        body = self._build_body(system, prompt)
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        endpoint = self._endpoint()
        request = urllib.request.Request(
            endpoint,
            data=raw,
            headers=self._headers(),
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.config.minimax_timeout) as response:
                payload_bytes = response.read()
        except urllib.error.HTTPError as error:  # pragma: no cover - 依赖外部服务
            detail = error.read().decode("utf-8", errors="replace")[:2000]
            raise MiniMaxError(f"MiniMax HTTP {error.code}: {detail}") from error
        except urllib.error.URLError as error:  # pragma: no cover - 依赖外部服务
            raise MiniMaxError(f"MiniMax 连接失败: {error.reason}") from error
        elapsed = time.perf_counter() - started

        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except json.JSONDecodeError as error:
            raise MiniMaxError("MiniMax 返回了非 JSON 响应") from error

        text = self._extract_text(payload)
        if not text.strip():
            raise MiniMaxError(f"MiniMax 返回空响应: {json.dumps(payload, ensure_ascii=False)[:500]}")

        usage = payload.get("usage") or {}
        return LlmCall(
            model=self.config.minimax_model,
            protocol=self.config.minimax_protocol,
            base_url=self.config.minimax_base_url,
            endpoint=endpoint,
            system=system,
            prompt=prompt,
            response_text=text,
            elapsed_seconds=elapsed,
            request_bytes=len(raw),
            response_bytes=len(payload_bytes),
            usage=usage if isinstance(usage, dict) else {},
        )


__all__ = ["LlmCall", "MiniMaxClient", "MiniMaxError"]
