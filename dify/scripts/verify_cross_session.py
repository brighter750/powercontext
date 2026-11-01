#!/usr/bin/env python3
"""Verify that the innkeeper keeps its memory across sessions, and that it keeps it per player.

Every turn runs in a brand-new process, so the only thing that survives from session to
session is what the PowerContext Server stores. The script checks four things:

1. session 1 writes the fact into PowerContext (direct API read, not the runner's report);
2. session 2 -- a new process, same player id, question that does not repeat the fact --
   reads the memory back and uses it in the answer;
3. a different player id, same question, same fresh process, does NOT know the fact;
4. session 2 adds a new entry, so the memory book grows instead of being overwritten.

Usage:
    python verify_cross_session.py --player-id afu --evidence-dir ../evidence/cross-session
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

SCRIPT_DIR = Path(__file__).resolve().parent
DSL_PATH = SCRIPT_DIR.parent / "apps" / "innkeeper-chatflow.yml"

SESSION_ONE_MESSAGE = "掌柜好，我叫{name}，最爱喝桂花酿。以后我大概会常来。"
SESSION_TWO_QUESTION = "掌柜，我上回说过我喜欢喝什么酒来着？"
SESSION_THREE_MESSAGE = "对了掌柜，我这次要赊一坛女儿红走，下月初还你钱，别记错了。"
SECRET_FACT = "桂花酿"
NEW_FACT = "女儿红"


def run_turn(
    player_id: str,
    message: str,
    trace_path: Path,
    powercontext_base_url: str,
    powercontext_token: str,
) -> dict:
    """Run one workflow turn in a fresh process and return its trace."""
    command = [
        sys.executable,
        str(SCRIPT_DIR / "dsl_runner.py"),
        "--dsl",
        str(DSL_PATH),
        "--player-id",
        player_id,
        "--message",
        message,
        "--trace",
        str(trace_path),
        "--powercontext-base-url",
        powercontext_base_url,
        "--powercontext-token",
        powercontext_token,
    ]
    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(command, capture_output=True, env=environment, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            "一轮执行失败（退出码 "
            f"{completed.returncode}）：\n{completed.stderr.decode('utf-8', errors='replace')}"
        )
    return json.loads(trace_path.read_text(encoding="utf-8"))


def scope_id_from_trace(trace: dict) -> str:
    for step in trace["trace"]:
        if step["node"] == "scope_parse":
            return step["outputs"]["scope_id"]
    raise RuntimeError("执行轨迹里没有 scope_parse 节点")


def read_memory_book(base_url: str, scope_id: str, token: str) -> list[dict]:
    response = httpx.post(
        f"{base_url.rstrip('/')}/v1/memory/entries/list",
        json={"scope_id": scope_id, "limit": 50, "include_inactive": False},
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["entries"]


def main() -> int:
    parser = argparse.ArgumentParser(description="验证忘忧酒肆的跨会话记忆")
    parser.add_argument("--player-id", default="阿福", help="旅人代号")
    parser.add_argument("--other-player-id", default="路人乙", help="用于反向对照的另一个旅人代号")
    parser.add_argument("--powercontext-base-url", default=os.environ.get("POWERCONTEXT_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--powercontext-token", default=os.environ.get("POWERCONTEXT_TOKEN", "disabled"))
    parser.add_argument("--evidence-dir", default=str(SCRIPT_DIR.parent / "evidence" / "cross-session"))
    args = parser.parse_args()

    evidence_dir = Path(args.evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    base_url = args.powercontext_base_url
    token = args.powercontext_token
    checks: list[dict] = []

    def record(name: str, passed: bool, detail: str) -> None:
        checks.append({"check": name, "passed": passed, "detail": detail})
        print(f"[{'PASS' if passed else 'FAIL'}] {name}: {detail}")

    print("=== 会话 1：第一次到访，说出自己的名字和喜好 ===")
    session_one_trace = evidence_dir / "session-1-turn-1.json"
    trace_one = run_turn(
        args.player_id,
        SESSION_ONE_MESSAGE.format(name=args.player_id),
        session_one_trace,
        base_url,
        token,
    )
    scope_id = scope_id_from_trace(trace_one)
    entries_after_one = read_memory_book(base_url, scope_id, token)
    record(
        "会话1把新记忆写进 PowerContext",
        any(SECRET_FACT in (entry.get("text") or "") for entry in entries_after_one),
        f"scope={scope_id}，写入后共 {len(entries_after_one)} 条："
        + " / ".join(f"[{entry.get('kind')}] {entry.get('text')}" for entry in entries_after_one),
    )

    print("\n=== 结束会话 1（进程退出，不保留任何内存状态）===")
    time.sleep(1)

    print("\n=== 会话 2：新进程，同一个旅人代号，不重复说出喜好，直接提问 ===")
    session_two_trace = evidence_dir / "session-2-turn-2.json"
    trace_two = run_turn(args.player_id, SESSION_TWO_QUESTION, session_two_trace, base_url, token)
    answer_two = trace_two["answer"]
    record(
        "会话2从 PowerContext 读到记忆并在回答里用上",
        SECRET_FACT in answer_two and SECRET_FACT not in SESSION_TWO_QUESTION,
        f"提问里没有出现「{SECRET_FACT}」，回答中出现：{answer_two.splitlines()[0] if answer_two else '(空)'}",
    )

    entries_after_two = read_memory_book(base_url, scope_id, token)
    record(
        "会话2没有把同一件事重复记一遍",
        len(entries_after_two) == len(entries_after_one),
        f"会话1后 {len(entries_after_one)} 条 → 会话2后 {len(entries_after_two)} 条"
        "（纯回忆轮没有新事实，不应重复落库）",
    )

    print("\n=== 会话 3：再开一个新进程，交代一件新的事，看记忆册会不会长 ===")
    session_three_trace = evidence_dir / "session-3-turn-3.json"
    trace_three = run_turn(args.player_id, SESSION_THREE_MESSAGE, session_three_trace, base_url, token)
    entries_after_three = read_memory_book(base_url, scope_id, token)
    record(
        "记忆随互动更新",
        len(entries_after_three) > len(entries_after_two)
        and any(NEW_FACT in (entry.get("text") or "") for entry in entries_after_three),
        f"会话2后 {len(entries_after_two)} 条 → 会话3后 {len(entries_after_three)} 条："
        + " / ".join(f"[{entry.get('kind')}] {entry.get('text')}" for entry in entries_after_three),
    )

    print("\n=== 反向对照：另一个旅人代号，同一个问题，全新进程 ===")
    other_trace = evidence_dir / "control-other-player.json"
    trace_other = run_turn(args.other_player_id, SESSION_TWO_QUESTION, other_trace, base_url, token)
    answer_other = trace_other["answer"]
    record(
        "另一个旅人读不到这本记忆册",
        SECRET_FACT not in answer_other,
        f"对照旅人回答：{answer_other.splitlines()[0] if answer_other else '(空)'}",
    )

    summary = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "powercontext_base_url": base_url,
        "dsl": str(DSL_PATH),
        "player_id": args.player_id,
        "other_player_id": args.other_player_id,
        "scope_id": scope_id,
        "session_one_message": SESSION_ONE_MESSAGE.format(name=args.player_id),
        "session_two_question": SESSION_TWO_QUESTION,
        "session_three_message": SESSION_THREE_MESSAGE,
        "secret_fact": SECRET_FACT,
        "new_fact": NEW_FACT,
        "entries_after_session_one": entries_after_one,
        "entries_after_session_two": entries_after_two,
        "entries_after_session_three": entries_after_three,
        "session_two_answer": answer_two,
        "session_three_answer": trace_three["answer"],
        "control_answer": answer_other,
        "checks": checks,
        "all_passed": all(check["passed"] for check in checks),
    }
    summary_path = evidence_dir / "verification-summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n证据已写入 {evidence_dir}")
    print("全部检查通过" if summary["all_passed"] else "存在未通过的检查")
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
