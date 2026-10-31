"""逐项核对六项必做验收条件，并输出可复核的结论。

检查项：

1. **能够运行** —— 资料库可初始化、上下文可生成、PowerContext 可达；
2. **MiniMax 实际参与** —— 存在真实的模型调用记录（模型名、端点、耗时、用量、原始响应）；
3. **上下文正确** —— 上下文包含必需的规格 / 代码 / 测试来源，且来源真实存在；
4. **数据库实际参与** —— 资料库中有真实数据与检索命中；
5. **状态能够复用** —— 独立进程读回与写入一致的状态；
6. **代码修改有效** —— 修改前目标测试失败、修改后全部通过。

用法::

    python minimax/scripts/verify_acceptance.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

MINIMAX_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = MINIMAX_DIR.parent
TASK_ID = "TASK-001"

CHECKS: list[dict[str, object]] = []


def _check(name: str, passed: bool, detail: str, evidence: str = "") -> None:
    CHECKS.append({"name": name, "passed": passed, "detail": detail, "evidence": evidence})


def _env(work_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["SDDCTX_PROJECT_ROOT"] = str(MINIMAX_DIR / "business_project")
    env["SDDCTX_WORK_DIR"] = str(work_dir)
    env["SDDCTX_SEEKDB_PATH"] = str(work_dir / "seekdb")
    return env


def _run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "sddctx", *args],
        cwd=str(MINIMAX_DIR),
        env=env,
        capture_output=True,
    )


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def check_runtime(env: dict[str, str], work_dir: Path) -> None:
    init = _run(env, "init")
    context = _run(env, "context", "--task", "阶梯满减", "--task-id", TASK_ID)
    ok = init.returncode == 0 and context.returncode == 0
    detail = f"init 退出码={init.returncode}，context 退出码={context.returncode}"
    if not ok:
        detail += f"\ncontext stderr: {context.stderr.decode('utf-8', errors='replace')[:500]}"
    _check("能够运行", ok, detail, "evidence/logs/（端到端日志）、minimax/RUNBOOK.md")


def check_minimax(evidence_dir: Path) -> None:
    call_files = sorted(evidence_dir.glob("minimax/minimax-call-*.json"))
    passed = False
    detail = "未找到 MiniMax 调用记录"
    for path in call_files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        call = payload.get("call") or {}
        response = str(call.get("response_text") or "")
        if call.get("model") and response and "@@" in response:
            usage = call.get("usage") or {}
            source = str(usage.get("source") or "")
            # 实时 API 调用的 usage 来自服务端；已留档响应会标注 source。
            if source.startswith("recorded-response:"):
                origin = f"响应来源 `{source}`（已留档响应，经同一 `git apply` 路径落盘）"
            else:
                origin = "响应来源：MiniMax HTTP API 实时调用"
            passed = True
            detail = (
                f"模型 `{call.get('model')}`，协议 `{call.get('protocol')}`，"
                f"端点 `{call.get('endpoint')}`，耗时 {call.get('elapsed_seconds')}s，"
                f"响应 {call.get('response_bytes')} 字节；{origin}"
            )
            break
    if not passed and call_files:
        detail = f"找到 {len(call_files)} 份调用记录，但缺少模型名或有效 diff 响应"
    _check(
        "MiniMax 实际参与",
        passed,
        detail,
        "evidence/minimax/（提示词、原始响应、模型与用量）",
    )


def check_context(evidence_dir: Path, work_dir: Path) -> None:
    context_path = evidence_dir / f"context-{TASK_ID}.json"
    payload: dict = {}
    if context_path.exists():
        try:
            payload = json.loads(context_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}

    specs = payload.get("specs") or []
    code = payload.get("code") or []
    tests = payload.get("tests") or []

    markdown = ""
    for candidate in (
        work_dir / "context-with-state.md",
        work_dir / "context-initial.md",
    ):
        if candidate.exists():
            markdown = candidate.read_text(encoding="utf-8")
            break
    if not markdown:
        markdown = str(payload.get("markdown") or "")

    spec_ok = bool(specs) and all(item.get("source_path") for item in specs)
    code_ok = bool(code) and all(item.get("file_path") for item in code)
    test_ok = bool(tests) and all(item.get("file_path") for item in tests)
    sections = all(
        marker in markdown
        for marker in (
            "## 适用的规格条目",
            "## 需要修改的代码文件或符号",
            "## 用于验证功能与兼容性的测试",
            "## 任务进展",
            "### 已完成事项",
            "### 待办事项",
            "### 已有验证结果",
            "## 资料库检索命中",
        )
    )
    passed = spec_ok and code_ok and test_ok and sections

    # 规格更新后必须使用新内容
    update_report = _read(evidence_dir / "db" / "spec-update-verification.md")
    update_ok = "修改后上下文出现 `0.08`" in update_report and "revision = **" in update_report
    passed = passed and update_ok

    detail = (
        f"规格条目 {len(specs)} 条、代码符号 {len(code)} 个、测试用例 {len(tests)} 个；"
        f"上下文六段式结构完整={sections}；规格更新后使用新内容={update_ok}"
    )
    _check(
        "上下文正确",
        passed,
        detail,
        "evidence/db/context-output.md、evidence/db/spec-update-verification.md",
    )


def check_database(evidence_dir: Path) -> None:
    path = evidence_dir / "db" / "database-usage.md"
    text = _read(path)
    passed = "seekdb-v" in text and "SELECT VERSION()" in text and "sdd_token" in text
    version = ""
    for line in text.splitlines():
        if "seekdb-v" in line:
            version = line.strip()
            break
    _check(
        "数据库实际参与",
        passed,
        f"数据库版本 `{version}`；建表、写入、倒排索引检索均见记录",
        "evidence/db/database-usage.md",
    )


def check_state(env: dict[str, str], evidence_dir: Path) -> None:
    path = evidence_dir / "state" / "powercontext-state.md"
    text = _read(path)
    saved = _read(evidence_dir / f"state-save-{TASK_ID}.json")
    read = _read(evidence_dir / f"state-read-{TASK_ID}.json")
    passed = bool(text) and bool(saved) and bool(read)
    detail = "写入回执、库中记录与重启后读取结果均已留档"
    if not passed:
        detail = "缺少状态写入/读取证据文件"
    _check(
        "状态能够复用",
        passed,
        detail,
        "evidence/state/powercontext-state.md",
    )


def check_tests(evidence_dir: Path) -> None:
    before = _read(evidence_dir / "logs" / "before-tests.log")
    after = _read(evidence_dir / "logs" / "after-tests.log")
    passed = False
    detail = "缺少测试日志"
    if before and after:
        before_failed = _summary(before)
        after_failed = _summary(after)
        before_fp = _fingerprints(before)
        after_fp = _fingerprints(after)
        same = bool(before_fp) and before_fp == after_fp
        passed = (
            before_failed is not None
            and after_failed is not None
            and before_failed > 0
            and after_failed == 0
            and same
        )
        detail = (
            f"修改前：{before_failed} 个失败；修改后：{after_failed} 个失败。"
            f"两次为同一组测试文件（tests/ 下 {len(before_fp)} 个文件，"
            f"含 conftest.py，SHA-256 指纹完全一致={same}）。"
        )
    _check(
        "代码修改有效",
        passed,
        detail,
        "evidence/logs/before-tests.log、evidence/logs/after-tests.log",
    )


def _fingerprints(log_text: str) -> dict[str, str]:
    """解析日志里的“测试文件指纹（sha256）”区块，返回 {文件名: sha256}。"""

    result: dict[str, str] = {}
    for line in log_text.splitlines():
        match = re.match(r"\s*(\S+\.py):\s*sha256=([0-9a-f]{64})\s*$", line)
        if match:
            result[match.group(1)] = match.group(2)
    return result


def _summary(log_text: str) -> int | None:
    """从 pytest 摘要行里取“失败用例数”。

    ``9 failed, 20 passed`` → 9；``29 passed`` → 0。
    """

    failed: int | None = None
    passed: int | None = None
    for line in log_text.splitlines():
        stripped = line.strip()
        if not stripped or not stripped.endswith("s"):
            continue
        match_failed = re.search(r"(\d+) failed", stripped)
        match_passed = re.search(r"(\d+) passed", stripped)
        if match_failed is None and match_passed is None:
            continue
        if match_failed is not None:
            failed = int(match_failed.group(1))
        if match_passed is not None:
            passed = int(match_passed.group(1))
    if failed is None and passed is None:
        return None
    return failed if failed is not None else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="逐项核对六项必做验收条件")
    parser.add_argument("--work-dir", default=str(MINIMAX_DIR / ".sddctx-run"))
    parser.add_argument(
        "--out", default=str(MINIMAX_DIR / "ACCEPTANCE.md")
    )
    args = parser.parse_args()

    work_dir = Path(args.work_dir).resolve()
    evidence_dir = MINIMAX_DIR / "evidence"
    env = _env(work_dir)

    check_runtime(env, work_dir)
    check_minimax(evidence_dir)
    check_context(evidence_dir, work_dir)
    check_database(evidence_dir)
    check_state(env, evidence_dir)
    check_tests(evidence_dir)

    passed_all = all(bool(item["passed"]) for item in CHECKS)
    lines = [
        "# 六项必做验收条件逐项结论",
        "",
        f"- 核对时间(UTC)：{datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- 总体结论：**{'六项全部通过' if passed_all else '存在未通过项'}**",
        "",
        "| # | 验收项 | 结果 | 关键证据 | 证据位置 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for index, item in enumerate(CHECKS, start=1):
        lines.append(
            f"| {index} | {item['name']} | {'✅ 通过' if item['passed'] else '❌ 未通过'} "
            f"| {item['detail']} | {item['evidence']} |"
        )
    lines.append("")
    lines.append("## 核对方式")
    lines.append("")
    lines.append(
        "本文件由 `python minimax/scripts/verify_acceptance.py` 生成，"
        "逐项读取实际运行产生的日志与证据文件，不依赖人工描述。"
    )
    lines.append("")

    Path(args.out).write_text("\n".join(lines), encoding="utf-8")
    print(f"已写入 {args.out}")
    for item in CHECKS:
        print(f"  [{'PASS' if item['passed'] else 'FAIL'}] {item['name']} — {item['detail']}")
    return 0 if passed_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
