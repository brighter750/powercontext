"""任务状态保存与复用 —— 复用 PowerContext 的实际状态机制。

状态写入使用 PowerContext 的三条真实写入路径：

============================================ ==========================================
PowerContext 接口                              用途
============================================ ==========================================
``POST /v1/work/contracts/create``            记录 Work Contract（目标、范围、完成判据）
``POST /v1/sources/content``                  把完整任务状态作为 Source 证据留存
``POST /v1/memory/remember``                  写入可检索的 Atomic Memory 记录
============================================ ==========================================

状态读取使用 PowerContext 的两条真实读取路径：

============================================ ==========================================
``POST /v1/memory/search``                    检索任务状态记录
``POST /v1/context/prepare``                  取出有界的历史上下文供后续处理复用
============================================ ==========================================

状态保存在 PowerContext 自己的持久化后端（默认 SQLite）里，
因此关闭并重启本模块之后仍然可以读取并继续使用。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from sddctx.config import SddConfig
from sddctx.models import TaskState

STATE_MARKER = "[sddctx-task-state]"
CONTRACT_SCHEMA = "powercontext.work-contract.v1"
OUTCOME_SCHEMA = "powercontext.task-outcome.v1"
CONTRACT_TRUST = "untrusted_input"
OUTCOME_TRUST = "untrusted_observation"
STATE_SOURCE_PREFIX = "sddctx-task-state-"
CONTRACT_SOURCE_PREFIX = "sddctx-work-contract-"


def _stamp(value: str) -> str:
    """把 ISO 时间戳压成可放进 Source 编号的短串。"""

    digits = "".join(char for char in value if char.isdigit())
    return digits[:14] if len(digits) >= 14 else (digits or "0")



class PowerContextError(RuntimeError):
    """PowerContext 调用失败。"""


def _split_items(value: str) -> list[str]:
    """按中文分号或英文逗号切分记忆条目。"""

    parts = re.split(r"[；;]\s*", value)
    items = [part.strip() for part in parts if part.strip()]
    return items


def _apply_memory_text(state: TaskState, text: str) -> None:
    """把一条 Atomic Memory 文本并入任务状态（兜底重建路径）。"""

    body = text.strip()
    if "：" in body:
        head, _, tail = body.partition("：")
        label = head.split()[-1] if head.split() else ""
        value = tail.strip()
    else:
        label, value = "", body

    if label == "适用规格":
        state.applicable_specs = [item.strip() for item in value.split(",") if item.strip()]
    elif label == "相关代码":
        state.related_code = [item.strip() for item in value.split(",") if item.strip()]
    elif label == "相关测试":
        state.related_tests = [item.strip() for item in value.split(",") if item.strip()]
    elif label == "已完成事项":
        state.done = _split_items(value)
    elif label == "待办事项":
        state.todo = _split_items(value)
    elif label == "验证结果":
        state.test_results.append({"summary": value})
    elif not state.description:
        state.description = value


@dataclass(slots=True)
class StateWriteReceipt:
    """一次状态写入的回执。"""

    scope_id: str
    contract_source_id: str
    state_source_id: str
    memory_records: list[dict[str, Any]]
    prepared_context: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_id": self.scope_id,
            "contract_source_id": self.contract_source_id,
            "state_source_id": self.state_source_id,
            "memory_records": self.memory_records,
            "prepared_context": self.prepared_context,
        }


class PowerContextStateStore:
    """通过 PowerContext HTTP 接口保存与读取任务状态。"""

    def __init__(self, config: SddConfig) -> None:
        self.config = config

    # --- HTTP ---------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.powercontext_token:
            headers["Authorization"] = f"Bearer {self.config.powercontext_token}"
        return headers

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{self.config.powercontext_url}{path}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.request(method, url, headers=self._headers(), json=payload)
        if response.status_code >= 400:
            detail = response.text[:2000]
            raise PowerContextError(f"PowerContext {method} {path} 返回 {response.status_code}: {detail}")
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return {"text": response.text}

    # --- 基础设施 -----------------------------------------------------------

    async def ping(self) -> dict[str, Any]:
        """检查 PowerContext 服务是否可用。"""

        try:
            return await self._request("GET", "/v1/server-info")
        except Exception:
            return await self._request("GET", "/health/live")

    async def ensure_scope(self, *, title: str | None = None, summary: str | None = None) -> str:
        """确保存在一个可用的 Scope，返回其 ID。

        幂等：使用固定的 ``idempotency_key``，重复调用返回同一个 Scope。
        需要一个全新的 Scope 时，给 ``SDDCTX_POWERCONTEXT_SCOPE_KEY`` 设一个不同的值。
        """

        if self.config.powercontext_scope:
            return self.config.powercontext_scope
        payload = {
            "title": title or "sddctx — SDD 上下文管理",
            "summary": summary or "MiniMax × OceanBase 赛道验证样例的工程上下文与任务状态",
            "idempotency_key": self.config.powercontext_scope_key,
        }
        result = await self._request("POST", "/v1/scopes", payload)
        scope_id = str(result.get("scope_id") or result.get("scope", {}).get("scope_id") or "")
        if not scope_id:
            raise PowerContextError(f"创建 Scope 未返回 scope_id: {result}")
        return scope_id

    # --- 写入 ---------------------------------------------------------------

    async def save(self, state: TaskState) -> StateWriteReceipt:
        """保存任务状态到 PowerContext。"""

        scope_id = await self.ensure_scope()
        state.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = state.to_dict()
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)

        contract_source_id = f"{CONTRACT_SOURCE_PREFIX}{state.task_id}-{_stamp(state.updated_at)}"
        contract = {
            "schema": CONTRACT_SCHEMA,
            "trust": CONTRACT_TRUST,
            "objective": state.description or state.task_id,
            "facts": [
                {
                    "text": f"任务 ID：{state.task_id}",
                    "basis": "declared",
                    "evidence": [],
                },
                {
                    "text": f"适用规格：{', '.join(state.applicable_specs) or '（尚未确定）'}",
                    "basis": "declared",
                    "evidence": [],
                },
                {
                    "text": f"相关代码：{', '.join(state.related_code) or '（尚未确定）'}",
                    "basis": "declared",
                    "evidence": [],
                },
                {
                    "text": f"相关测试：{', '.join(state.related_tests) or '（尚未确定）'}",
                    "basis": "declared",
                    "evidence": [],
                },
            ],
            "in_scope": state.applicable_specs or [state.task_id],
            "exclusions": ["不得修改与本任务无关的模块", "不得通过删除或跳过测试使测试通过"],
            "completion_criteria": [
                "目标功能测试全部通过",
                "相关回归测试全部通过",
                "任务状态已保存并可在重启后读取",
            ],
            "authorization_notes": ["由 sddctx 上下文管理模块写入"],
            "open_questions": list(state.todo),
        }
        await self._request(
            "POST",
            "/v1/work/contracts/create",
            {"scope_id": scope_id, "source_id": contract_source_id, "contract": contract},
        )

        state_source_id = f"{STATE_SOURCE_PREFIX}{state.task_id}-{_stamp(state.updated_at)}"
        await self._request(
            "POST",
            "/v1/sources/content",
            {
                "scope_id": scope_id,
                "source_id": state_source_id,
                "content": f"{STATE_MARKER} {encoded}\n\n{state.description}",
                "metadata": {
                    "origin": "sddctx",
                    "task_id": state.task_id,
                    "status": state.status,
                },
            },
        )

        memory_records: list[dict[str, Any]] = []
        for kind, text in self._memory_entries(state):
            saved = await self._request(
                "POST",
                "/v1/memory/remember",
                {
                    "scope_id": scope_id,
                    "kind": kind,
                    "text": f"{STATE_MARKER} {json.dumps({'task_id': state.task_id, 'kind': kind, 'text': text}, ensure_ascii=False)}\n{text}",
                    "reason": f"sddctx 任务状态记录 {state.task_id}",
                },
            )
            records = saved.get("records") if isinstance(saved, dict) else None
            if isinstance(records, list):
                memory_records.extend(records)

        prepared = await self._request(
            "POST",
            "/v1/context/prepare",
            {"scope_id": scope_id, "query": state.task_id, "max_bytes": 8000},
        )
        prepared_content = ""
        if isinstance(prepared, dict):
            prepared_content = str(prepared.get("content") or "")

        return StateWriteReceipt(
            scope_id=scope_id,
            contract_source_id=contract_source_id,
            state_source_id=state_source_id,
            memory_records=memory_records,
            prepared_context=prepared_content,
        )

    @staticmethod
    def _memory_entries(state: TaskState) -> list[tuple[str, str]]:
        entries: list[tuple[str, str]] = [
            ("decision", f"任务 {state.task_id}：{state.description}"),
            (
                "constraint",
                f"任务 {state.task_id} 适用规格：{', '.join(state.applicable_specs) or '（尚未确定）'}",
            ),
            (
                "constraint",
                f"任务 {state.task_id} 相关代码：{', '.join(state.related_code) or '（尚未确定）'}",
            ),
            (
                "constraint",
                f"任务 {state.task_id} 相关测试：{', '.join(state.related_tests) or '（尚未确定）'}",
            ),
            ("status", f"任务 {state.task_id} 已完成事项：{'；'.join(state.done) or '（无）'}"),
            ("status", f"任务 {state.task_id} 待办事项：{'；'.join(state.todo) or '（无）'}"),
        ]
        for result in state.test_results:
            entries.append(
                (
                    "status",
                    f"任务 {state.task_id} 验证结果：{result.get('command', '')} "
                    f"退出码 {result.get('exit_code', '')} 通过 {result.get('passed', 0)} "
                    f"失败 {result.get('failed', 0)} 错误 {result.get('errors', 0)}",
                )
            )
        return entries

    # --- 读取 ---------------------------------------------------------------

    async def load(self, task_id: str) -> TaskState | None:
        """从 PowerContext 读取任务状态。

        主路径：从 Source 证据读取完整状态 JSON（含已完成、待办、验证结果）。
        校验路径：同时查询 Atomic Memory，确认该任务的可检索记录确实存在。
        兜底：Source 不可用时，用 Atomic Memory 记录重建（部分）状态。
        """

        scope_id = await self.ensure_scope()
        state = await self._load_from_sources(scope_id, task_id)
        if state is not None:
            return state

        payload = await self._request(
            "POST",
            "/v1/memory/search",
            {"scope_id": scope_id, "query": task_id, "limit": 50},
        )
        records = self._extract_records(payload)
        return self._state_from_records(records, task_id)

    @staticmethod
    def _extract_records(payload: Any) -> list[dict[str, Any]]:
        """从检索响应里取出记录列表。

        ``/v1/memory/search`` 返回 ``{"hits": [{"memory": {...}}, ...]}``，
        ``/v1/memory/entries/list`` 返回 ``{"entries": [...]}``。
        """

        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("hits", "records", "entries", "items", "results", "matches"):
            value = payload.get(key)
            if not isinstance(value, list):
                continue
            records: list[dict[str, Any]] = []
            for item in value:
                if not isinstance(item, dict):
                    continue
                nested = item.get("memory")
                records.append(nested if isinstance(nested, dict) else item)
            return records
        return []

    @staticmethod
    def _state_from_records(records: list[dict[str, Any]], task_id: str) -> TaskState | None:
        """用 Atomic Memory 记录重建（部分）任务状态。"""

        state: TaskState | None = None
        for record in records:
            text = ""
            for key in ("text", "content", "body"):
                value = record.get(key)
                if isinstance(value, str) and value:
                    text = value
                    break
            if not text:
                continue
            if STATE_MARKER in text:
                remainder = text[text.index(STATE_MARKER) + len(STATE_MARKER) :].strip()
                head, _, _tail = remainder.partition("\n")
                try:
                    payload = json.loads(head)
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict) and payload.get("task_id") == task_id:
                    state = TaskState.from_dict(payload)
                    break
                continue
            if f"任务 {task_id}" not in text:
                continue
            state = state or TaskState(task_id=task_id, description="")
            _apply_memory_text(state, text)
        return state

    async def _load_from_sources(self, scope_id: str, task_id: str) -> TaskState | None:
        """从 Source 证据里读取最新一次保存的完整状态。

        PowerContext 的 Source 是不可变日志：每次保存写入一条带唯一编号的新 Source，
        因此读取时列出该 Scope 的 Source，取编号前缀匹配且位置最新的一条。
        """

        payload = await self._request("GET", f"/v1/scopes/{scope_id}/sources")
        items = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return None
        prefix = f"{STATE_SOURCE_PREFIX}{task_id}-"
        candidates = [
            item
            for item in items
            if isinstance(item, dict) and str(item.get("source_id", "")).startswith(prefix)
        ]
        if not candidates:
            return None
        latest = max(candidates, key=lambda item: int(item.get("position", 0)))
        content = latest.get("content")
        if not isinstance(content, str):
            return None
        if STATE_MARKER not in content:
            return None
        remainder = content[content.index(STATE_MARKER) + len(STATE_MARKER) :].strip()
        head, _, _tail = remainder.partition("\n")
        try:
            payload = json.loads(head)
        except json.JSONDecodeError:
            return None
        if "done" in payload or "todo" in payload:
            return TaskState.from_dict(payload)
        return None

    async def prepare_context(self, task_id: str, *, max_bytes: int = 8000) -> str:
        """取出 PowerContext 准备好的有界历史上下文。"""

        scope_id = await self.ensure_scope()
        payload = await self._request(
            "POST",
            "/v1/context/prepare",
            {"scope_id": scope_id, "query": task_id, "max_bytes": max_bytes},
        )
        if isinstance(payload, dict):
            return str(payload.get("content") or "")
        return ""

    async def record_outcome(
        self,
        *,
        task_id: str,
        objective: str,
        status: str,
        summary: str,
        checks: list[dict[str, Any]],
        remaining_work: list[str],
    ) -> dict[str, Any]:
        """记录任务结果（PowerContext 的 Task Outcome）。"""

        scope_id = await self.ensure_scope()
        outcome = {
            "schema": OUTCOME_SCHEMA,
            "trust": OUTCOME_TRUST,
            "objective": objective,
            "status": status,
            "summary": summary,
            "observations": [],
            "checks": checks,
            "produced_artifacts": [],
            "remaining_work": remaining_work,
        }
        return await self._request(
            "POST",
            "/v1/work/outcomes/record",
            {
                "scope_id": scope_id,
                "source_id": f"sddctx-task-outcome-{task_id}",
                "outcome": outcome,
            },
        )


__all__ = [
    "CONTRACT_SCHEMA",
    "OUTCOME_SCHEMA",
    "PowerContextError",
    "PowerContextStateStore",
    "StateWriteReceipt",
    "STATE_MARKER",
]
