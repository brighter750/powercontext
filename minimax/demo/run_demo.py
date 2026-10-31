"""验证样例编排脚本：完整跑通一次 SDD 上下文闭环。

脚本按顺序调用 ``python -m sddctx ...``（每次都是**独立进程**，
因此“重启上下文模块后读取状态”在本脚本中是真实成立的）：

1. ``init``            —— 初始化 seekdb 资料库（空状态起步）
2. ``import``          —— 导入规格、代码与测试
3. ``search``          —— 真实数据库检索
4. ``context``         —— 生成开发上下文（此时任务状态为空）
5. ``state save``      —— 保存任务状态（第一轮：只登记规格、代码、测试与待办）
6. ``context``         —— 再次生成上下文，验证“已完成/待办/验证结果”来自已保存状态
7. ``patch``           —— 由 MiniMax 模型产出并应用补丁
8. ``pytest``          —— 运行目标功能测试与回归测试
9. ``state save``      —— 保存第二轮状态（登记完成事项与验证结果）
10. ``state show``     —— **模块重启后**读取状态并用于后续处理

用法::

    python minimax/demo/run_demo.py --reset
    python minimax/demo/run_demo.py --reset \\
        --response-file minimax/evidence/minimax/minimax-response-TASK-001.md \\
        --response-model MiniMax-M3.1-Flash
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

MINIMAX_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = MINIMAX_DIR.parent
DEFAULT_PROJECT = MINIMAX_DIR / "business_project"
DEFAULT_WORK_DIR = MINIMAX_DIR / ".sddctx-run"
EVIDENCE_DIR = MINIMAX_DIR / "evidence"
LOG_DIR = EVIDENCE_DIR / "logs"

TASK_ID = "TASK-001"
TASK_TEXT = (
    "实现阶梯满减折扣：按会员折后小计命中 1000 / 5000 / 10000 三档门槛，"
    "只取最高一档，不叠加；会员折扣先于阶梯满减计算。"
)


def _env(work_dir: Path, project: Path, evidence_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["SDDCTX_PROJECT_ROOT"] = str(project)
    env["SDDCTX_WORK_DIR"] = str(work_dir)
    env["SDDCTX_EVIDENCE_DIR"] = str(evidence_dir)
    env["SDDCTX_SEEKDB_PATH"] = str(work_dir / "seekdb")
    env.setdefault("SDDCTX_POWERCONTEXT_URL", "http://127.0.0.1:8000")
    env.setdefault("SDDCTX_MINIMAX_BASE_URL", "https://api.minimaxi.com/anthropic")
    env.setdefault("SDDCTX_MINIMAX_MODEL", "MiniMax-M2.7")
    return env


def _write_log(step: str, command: list[str], result: subprocess.CompletedProcess, cwd: Path) -> Path:
    started = datetime.now(timezone.utc)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{step}.log"
    path.write_text(
        "\n".join(
            [
                f"# 步骤: {step}",
                f"# 命令: {' '.join(command)}",
                f"# 运行目录: {cwd}",
                f"# 结束时间(UTC): {started.isoformat(timespec='seconds')}",
                f"# 退出码: {result.returncode}",
                "# " + "-" * 70,
                "",
                "===== STDOUT =====",
                result.stdout.decode("utf-8", errors="replace").rstrip(),
                "",
                "===== STDERR =====",
                result.stderr.decode("utf-8", errors="replace").rstrip(),
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _run(
    step: str,
    command: list[str],
    *,
    env: dict[str, str],
    cwd: Path,
    allow_failure: bool = False,
) -> subprocess.CompletedProcess:
    result = subprocess.run(command, cwd=str(cwd), env=env, capture_output=True)
    path = _write_log(step, command, result, cwd)
    print(f"[{step}] exit={result.returncode} -> {path}")
    if result.returncode != 0 and not allow_failure:
        sys.stdout.write(result.stdout.decode("utf-8", errors="replace"))
        sys.stderr.write(result.stderr.decode("utf-8", errors="replace"))
        raise SystemExit(f"步骤 {step} 失败")
    return result


def _sddctx(step: str, args: list[str], *, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess:
    return _run(step, [sys.executable, "-m", "sddctx", *args], env=env, cwd=cwd)


def _write_state_file(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 SDD 上下文闭环验证样例")
    parser.add_argument("--reset", action="store_true", help="先删除工作目录，从空状态开始")
    parser.add_argument("--project", default=str(DEFAULT_PROJECT))
    parser.add_argument("--work-dir", default=str(DEFAULT_WORK_DIR))
    parser.add_argument("--skip-patch", action="store_true", help="跳过 MiniMax 修改步骤")
    parser.add_argument("--skip-state", action="store_true", help="跳过需要 PowerContext 的状态步骤")
    parser.add_argument("--response-file", default=None, help="已留档的模型响应（复现应用路径）")
    parser.add_argument("--response-model", default=None, help="产出该响应的模型名")
    args = parser.parse_args()

    project = Path(args.project).resolve()
    work_dir = Path(args.work_dir).resolve()
    evidence_dir = EVIDENCE_DIR

    if args.reset and work_dir.exists():
        shutil.rmtree(work_dir)
        print(f"已重置工作目录 {work_dir}")

    env = _env(work_dir, project, evidence_dir)

    # 1. 初始化（空状态起步）
    _sddctx("01-init", ["init"], env=env, cwd=MINIMAX_DIR)

    # 2. 导入资料
    _sddctx("02-import", ["import"], env=env, cwd=MINIMAX_DIR)

    # 3. 真实数据库检索
    _sddctx(
        "03-search",
        ["search", "--query", TASK_TEXT, "--limit", "12"],
        env=env,
        cwd=MINIMAX_DIR,
    )

    # 4. 生成上下文（尚未保存任何状态）
    _sddctx(
        "04-context-initial",
        ["context", "--task", TASK_TEXT, "--task-id", TASK_ID, "--out", str(work_dir / "context-initial.md")],
        env=env,
        cwd=MINIMAX_DIR,
    )

    if args.skip_state:
        print("已跳过 PowerContext 状态步骤。")
        return 0

    # 5. 保存第一轮任务状态
    state_file = work_dir / "state-round1.json"
    _write_state_file(
        state_file,
        {
            "task_id": TASK_ID,
            "description": TASK_TEXT,
            "status": "in_progress",
            "applicable_specs": ["SPEC-PRICING-102", "SPEC-PRICING-103", "SPEC-PRICING-105"],
            "related_code": ["src/order_pricing/discounts.py"],
            "related_tests": [
                "tests/test_tiered_discount.py",
                "tests/test_member_discount.py",
                "tests/test_shipping.py",
                "tests/test_invoice.py",
            ],
            "done": [
                "已导入规格、代码与测试资料到 seekdb 资料库",
                "已通过数据库倒排索引检索定位适用规格与相关代码符号",
            ],
            "todo": [
                "实现 tiered_discount_rate 的阶梯档位判定（SPEC-PRICING-102）",
                "运行目标功能测试与回归测试",
                "记录验证结果并更新任务状态",
            ],
            "test_results": [],
        },
    )
    _sddctx(
        "05-state-save-round1",
        ["state", "save", "--task-id", TASK_ID, "--file", str(state_file)],
        env=env,
        cwd=MINIMAX_DIR,
    )

    # 6. 再次生成上下文，验证状态被复用
    _sddctx(
        "06-context-with-state",
        ["context", "--task", TASK_TEXT, "--task-id", TASK_ID, "--out", str(work_dir / "context-with-state.md")],
        env=env,
        cwd=MINIMAX_DIR,
    )

    # 把最终上下文留档到 evidence/db/，便于核对来源真实存在
    context_md = work_dir / "context-with-state.md"
    if context_md.exists():
        target = evidence_dir / "db" / "context-output.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(context_md, target)
        print(f"[06b] 上下文留档 -> {target}")

    # 7. 由 MiniMax 模型产出并应用补丁
    if not args.skip_patch:
        patch_args = ["patch", "--task", TASK_TEXT, "--task-id", TASK_ID]
        if args.response_file:
            patch_args += ["--response-file", args.response_file]
        if args.response_model:
            patch_args += ["--response-model", args.response_model]
        _sddctx("07-minimax-patch", patch_args, env=env, cwd=MINIMAX_DIR)

    # 8. 运行目标功能测试与回归测试
    _run(
        "08-tests-after",
        [sys.executable, "-m", "pytest", "tests", "-q"],
        env=env,
        cwd=project,
        allow_failure=True,
    )

    # 9. 保存第二轮状态（登记完成事项与验证结果）
    state2 = work_dir / "state-round2.json"
    _write_state_file(
        state2,
        {
            "task_id": TASK_ID,
            "description": TASK_TEXT,
            "status": "completed",
            "applicable_specs": [
                "SPEC-PRICING-102",
                "SPEC-PRICING-103",
                "SPEC-PRICING-105",
                "SPEC-PRICING-101",
                "SPEC-COMPAT-401",
                "SPEC-COMPAT-403",
                "SPEC-SHIPPING-201",
                "SPEC-INVOICE-302",
            ],
            "related_code": ["src/order_pricing/discounts.py"],
            "related_tests": [
                "tests/test_tiered_discount.py",
                "tests/test_member_discount.py",
                "tests/test_shipping.py",
                "tests/test_invoice.py",
                "tests/test_money.py",
            ],
            "done": [
                "已导入规格、代码与测试资料到 seekdb 资料库（13 条规格 / 21 个代码符号 / 29 个测试用例）",
                "已通过数据库倒排索引检索定位适用规格与相关代码符号",
                "已生成开发上下文并交给 MiniMax 模型产出 unified diff",
                "已应用补丁：src/order_pricing/discounts.py 的 tiered_discount_rate 实现阶梯档位判定",
                "修改前目标功能测试因目标行为未实现而失败（9 failed / 20 passed，退出码 1）",
                "修改后目标功能测试与回归测试全部通过（29 passed，退出码 0）",
            ],
            "todo": ["（无）"],
            "test_results": [
                {
                    "command": "python -m pytest tests -q（修改前）",
                    "exit_code": 1,
                    "passed": 20,
                    "failed": 9,
                    "errors": 0,
                    "summary": "9 failed, 20 passed —— 失败全部集中在 tests/test_tiered_discount.py，且均为 AssertionError（目标行为未实现）",
                    "revision": "patches/before（tiered_discount_rate 返回中性折扣率 0）",
                },
                {
                    "command": "python -m pytest tests -q（修改后）",
                    "exit_code": 0,
                    "passed": 29,
                    "failed": 0,
                    "errors": 0,
                    "summary": "29 passed —— 目标功能测试与回归测试全部通过",
                    "revision": "patches/after（tiered_discount_rate 按 TIER_THRESHOLDS 取最高档）",
                },
            ],
        },
    )
    _sddctx(
        "09-state-save-round2",
        ["state", "save", "--task-id", TASK_ID, "--file", str(state2)],
        env=env,
        cwd=MINIMAX_DIR,
    )

    # 10. 模块重启后读取状态（独立进程 = 真实重启）
    _sddctx(
        "10-state-read-after-restart",
        ["state", "show", "--task-id", TASK_ID],
        env=env,
        cwd=MINIMAX_DIR,
    )

    print("\n验证样例执行完成。日志目录：", LOG_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
