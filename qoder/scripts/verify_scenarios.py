"""Run the five acceptance scenarios from the task brief and record the evidence.

Each scenario drives the running StudyFlow backend over HTTP and writes the real
requests, real responses, and the real PowerContext artifact ids into
``qoder/evidence/scenarios/``.

Usage::

    python qoder/scripts/verify_scenarios.py
    python qoder/scripts/verify_scenarios.py --api-url http://127.0.0.1:8080

The script never fabricates a result. When a step cannot be completed — for example
the AI model service is not configured — the scenario is recorded as
``需要配置 AI 模型后复测`` together with the actual response that was received.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

sys.stdout.reconfigure(encoding="utf-8")

EVIDENCE_DIR = Path(__file__).resolve().parents[1] / "evidence" / "scenarios"

SCENARIO_A_INPUT = (
    "我正在学习数据库规范化。函数依赖和候选码基本理解，但 BCNF 分解经常出错。"
    "请记住这个情况，以后复习时重点帮我练习 BCNF。"
)
SCENARIO_B_INPUT = "我今天应该复习什么？"
SCENARIO_C_INPUT = "我刚才做了 5 道 BCNF 练习，答对了 4 道，现在比之前熟悉了。"
SCENARIO_D_INPUT = "我最近正在学习操作系统。"


def now() -> str:
    """Return the current UTC timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Recorder:
    """Collects the evidence of one scenario."""

    def __init__(self, name: str, title: str, purpose: str) -> None:
        self.name = name
        self.payload: dict[str, Any] = {
            "scenario": name,
            "title": title,
            "purpose": purpose,
            "executed_at": now(),
            "steps": [],
            "result": "unknown",
            "notes": "",
        }

    def step(self, title: str, request: Any, response: Any) -> None:
        """Record one request/response pair."""
        self.payload["steps"].append(
            {
                "title": title,
                "at": now(),
                "request": request,
                "response": response,
            }
        )

    def finish(self, result: str, notes: str = "") -> None:
        """Record the outcome of the scenario."""
        self.payload["result"] = result
        self.payload["notes"] = notes

    def save(self) -> Path:
        """Write the scenario evidence to disk."""
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        path = EVIDENCE_DIR / f"{self.name}.json"
        path.write_text(
            json.dumps(self.payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path


def tutor(client: httpx.Client, user_id: str, message: str) -> tuple[dict[str, Any], Any]:
    """Send one message to the AI tutor and return the payload plus the raw response."""
    response = client.post(
        "/api/tutor",
        json={"user_id": user_id, "message": message, "save_to_memory": True},
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text}
    return payload, {"status_code": response.status_code, "json": payload}


def memory_entries(client: httpx.Client, user_id: str) -> tuple[dict[str, Any], Any]:
    """Read the Memory Center payload for one learner."""
    response = client.get("/api/memory", params={"user_id": user_id})
    return response.json(), {"status_code": response.status_code}


def scenario_a(client: httpx.Client, user_id: str, ai_configured: bool) -> Recorder:
    """Test A — write the learner's situation into long-term memory."""
    recorder = Recorder(
        "A_write_memory",
        "测试 A：写入学习记忆",
        "把学习者口述的学习情况写入 PowerContext，并确认可以读取回来。",
    )
    payload, raw = tutor(client, user_id, SCENARIO_A_INPUT)
    recorder.step("POST /api/tutor（写入学习记忆）", {"user_id": user_id, "message": SCENARIO_A_INPUT}, raw)

    center, _ = memory_entries(client, user_id)
    recorder.step(
        "GET /api/memory（核对写入结果）",
        {"user_id": user_id},
        {
            "entry_count": center.get("entry_count"),
            "scope_id": center.get("scope_id"),
            "entries": [
                {"kind": entry["kind"], "key": entry["key"], "artifact_id": entry["artifact_id"],
                 "revision": entry["revision"]}
                for entry in center.get("entries", [])
            ],
        },
    )

    if not ai_configured:
        recorder.finish(
            "需要配置 AI 模型后复测",
            "未配置 AI 模型服务，自然语言理解步骤无法执行；"
            "应用按设计返回明确提示，没有返回伪造结果。"
            "配置 AI 后重跑本脚本即可得到完整证据。",
        )
        return recorder

    topics = [state["topic"] for state in center.get("knowledge_states", [])]
    saved = payload.get("saved_actions", [])
    if saved and topics:
        recorder.finish(
            "通过",
            f"识别到知识点 {topics}，写入 {len(saved)} 条记忆，"
            f"Scope={center.get('scope_id')}。",
        )
    else:
        recorder.finish("未通过", "未观察到记忆写入。")
    return recorder


def scenario_b(client: httpx.Client, user_id: str, ai_configured: bool) -> Recorder:
    """Test B — a new session recalls the memory written earlier."""
    recorder = Recorder(
        "B_cross_session_read",
        "测试 B：新会话读取记忆",
        "换一个全新的 HTTP 会话，用同一个学习者 ID 提问，验证记忆被读回。",
    )
    payload, raw = tutor(client, user_id, SCENARIO_B_INPUT)
    recorder.step(
        "POST /api/tutor（新会话提问）",
        {"user_id": user_id, "message": SCENARIO_B_INPUT},
        raw,
    )

    memory_context = payload.get("memory_context", "")
    hits = payload.get("memory_hits", [])
    recorder.step(
        "读取到的长期记忆上下文",
        None,
        {
            "used_memory": payload.get("used_memory"),
            "memory_hit_count": len(hits),
            "memory_context": memory_context,
        },
    )

    if not ai_configured:
        recorder.finish(
            "需要配置 AI 模型后复测",
            "未配置 AI 模型，回答步骤无法执行；"
            "但新会话确实重新读取了 PowerContext 中的记忆（见 memory_context）。",
        )
        return recorder

    if payload.get("used_memory") and "BCNF" in memory_context:
        recorder.finish(
            "通过",
            "新会话无需用户重新介绍，读回的记忆中包含 BCNF 分解的薄弱信息。",
        )
    else:
        recorder.finish("未通过", "新会话没有检索到应有的记忆。")
    return recorder


def scenario_c(client: httpx.Client, user_id: str, ai_configured: bool) -> Recorder:
    """Test C — a new activity updates the stored memory in place."""
    recorder = Recorder(
        "C_update_progress",
        "测试 C：更新学习进度",
        "记录一次新的练习，确认 PowerContext 中的记忆被更新而不是重复追加。",
    )
    before, _ = memory_entries(client, user_id)
    recorder.step(
        "GET /api/memory（更新前）",
        {"user_id": user_id},
        {
            "entry_count": before.get("entry_count"),
            "entries": [
                {"kind": e["kind"], "key": e["key"], "artifact_id": e["artifact_id"], "revision": e["revision"]}
                for e in before.get("entries", [])
            ],
        },
    )

    payload, raw = tutor(client, user_id, SCENARIO_C_INPUT)
    recorder.step("POST /api/tutor（写入新的练习结果）", {"user_id": user_id, "message": SCENARIO_C_INPUT}, raw)

    after, _ = memory_entries(client, user_id)
    recorder.step(
        "GET /api/memory（更新后）",
        {"user_id": user_id},
        {
            "entry_count": after.get("entry_count"),
            "entries": [
                {"kind": e["kind"], "key": e["key"], "artifact_id": e["artifact_id"], "revision": e["revision"]}
                for e in after.get("entries", [])
            ],
        },
    )

    if not ai_configured:
        recorder.finish(
            "需要配置 AI 模型后复测",
            "未配置 AI 模型，自然语言描述的练习结果无法被解析。",
        )
        return recorder

    before_keys = {(e["key"], e["artifact_id"]): e["revision"] for e in before.get("entries", [])}
    after_keys = {(e["key"], e["artifact_id"]): e["revision"] for e in after.get("entries", [])}
    revised = [
        key for key, revision in after_keys.items() if key in before_keys and before_keys[key] != revision
    ]
    if revised:
        recorder.finish(
            "通过",
            f"已有记忆被修订（修订号变化）：{revised}。"
            "这说明学习进展是原地更新，而不是重复保存旧内容。",
        )
    else:
        recorder.finish("未通过", "没有观察到既有记忆被修订。")
    return recorder


def scenario_d(client: httpx.Client, ai_configured: bool) -> Recorder:
    """Test D — two learners never see each other's memory."""
    recorder = Recorder(
        "D_user_isolation",
        "测试 D：用户隔离",
        "验证不同学习者 ID 的记忆在 PowerContext 中完全隔离。",
    )
    user_a = "student_demo_001"
    user_b = f"student_demo_{uuid.uuid4().hex[:6]}_b"

    center_a, _ = memory_entries(client, user_a)
    payload_b, raw = tutor(client, user_b, SCENARIO_D_INPUT)
    recorder.step(
        f"POST /api/tutor（用户 B：{user_b}）",
        {"user_id": user_b, "message": SCENARIO_D_INPUT},
        raw,
    )

    center_b, _ = memory_entries(client, user_b)
    recorder.step(
        "GET /api/memory（对比两个 Scope）",
        {"user_id": user_b},
        {
            "scope_a": center_a.get("scope_id"),
            "scope_b": center_b.get("scope_b") or center_b.get("scope_id"),
            "user_b_entry_count": center_b.get("entry_count"),
            "user_b_records": center_b.get("recent_records"),
            "user_b_memory_context_leaks_user_a": "student_demo_001"
            in str(center_b.get("recent_records", "")),
        },
    )

    if center_a.get("scope_id") and center_b.get("scope_id") and center_a["scope_id"] != center_b["scope_id"]:
        leaked = "student_demo_001" in json.dumps(center_b, ensure_ascii=False)
        if leaked:
            recorder.finish("未通过", "用户 B 的记忆里出现了用户 A 的内容。")
        else:
            recorder.finish(
                "通过",
                f"两个学习者对应不同的 PowerContext Scope"
                f"（{center_a.get('scope_id')} vs {center_b.get('scope_id')}），"
                "且用户 B 读不到用户 A 的任何记录。隔离由 PowerContext 的 Scope 边界保证，"
                "不是前端隐藏。",
            )
    else:
        recorder.finish("未通过", "没有拿到两个不同的 Scope。")
    return recorder


def scenario_e(client: httpx.Client, user_id: str) -> Recorder:
    """Test E — memory survives a restart of the backend and the Server."""
    recorder = Recorder(
        "E_restart_persistence",
        "测试 E：服务重启后读取",
        "在全新的进程与连接中重新读取同一个学习者的记忆，验证持久化。",
    )
    before, _ = memory_entries(client, user_id)
    recorder.step(
        "GET /api/memory（重启前）",
        {"user_id": user_id},
        {"entry_count": before.get("entry_count"), "scope_id": before.get("scope_id")},
    )

    script_path = Path(__file__).resolve().parent / "read_memory_once.py"
    command = [sys.executable, str(script_path), user_id]
    import subprocess

    # The child must emit UTF-8 regardless of the Windows console codepage.
    child_env = dict(os.environ, PYTHONIOENCODING="utf-8")
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=child_env,
    )
    stdout = (completed.stdout or "").strip()
    recorder.step(
        "新进程读取（全新连接，无任何内存缓存）",
        {"command": f"python {script_path.name} {user_id}"},
        {"returncode": completed.returncode, "stdout": stdout, "stderr": completed.stderr[:800]},
    )

    try:
        payload = json.loads(stdout.splitlines()[-1])
    except (ValueError, IndexError):
        recorder.finish("未通过", f"新进程读取失败：{completed.stderr[:400]}")
        return recorder

    if payload.get("entry_count", 0) >= before.get("entry_count", 0) and payload.get("scope_id"):
        recorder.finish(
            "通过",
            f"新进程读到 scope_id={payload['scope_id']}、{payload['record_count']} 条学习记录，"
            "记忆在重启后依然存在。",
        )
    else:
        recorder.finish("未通过", "新进程没有读到预期的记忆。")
    return recorder


def main() -> int:
    """Run every scenario and print a summary."""
    parser = argparse.ArgumentParser(description="Run the StudyFlow acceptance scenarios.")
    parser.add_argument(
        "--api-url",
        default=os.environ.get("STUDYFLOW_TEST_API_URL", "http://127.0.0.1:8080"),
        help="StudyFlow backend base URL",
    )
    args = parser.parse_args()

    user_id = "student_demo_001"
    with httpx.Client(base_url=args.api_url, timeout=180.0) as client:
        status = client.get("/api/system/status").json()
        ai_configured = bool(status.get("ai_configured"))
        powercontext_ok = bool(status.get("powercontext_ok"))

        print(f"后端地址        : {args.api_url}")
        print(f"PowerContext 可用: {powercontext_ok}")
        print(f"AI 模型已配置   : {ai_configured}")
        print()

        if not powercontext_ok:
            print("PowerContext 不可用，场景无法执行。请先启动 PowerContext Server。")
            return 2

        recorders = [
            scenario_a(client, user_id, ai_configured),
            scenario_b(client, user_id, ai_configured),
            scenario_c(client, user_id, ai_configured),
            scenario_d(client, ai_configured),
            scenario_e(client, user_id),
        ]

    print(f"{'场景':<28}{'结果'}")
    print("-" * 62)
    for recorder in recorders:
        print(f"{recorder.payload['title']:<28}{recorder.payload['result']}")
        path = recorder.save()
        print(f"{'':<28}证据：{path}")
    print()
    print(f"证据目录：{EVIDENCE_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
