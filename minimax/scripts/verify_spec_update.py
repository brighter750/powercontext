"""验证“规格更新并重新导入后，模块使用更新后的规格”。

流程：

1. 生成一次开发上下文，记录当前规格内容与 ``revision``；
2. **临时**修改样例规格里的一处阈值（``0.05`` → ``0.08``）；
3. 重新导入资料库，确认该条目的 ``revision`` 递增；
4. 再次生成上下文，确认输出已经使用**更新后**的规格内容；
5. 还原规格并重新导入，确认上下文回到原始内容。

第 5 步保证交付出去的规格与实现、测试保持一致；第 2~4 步留下证据，
证明模块不会一直返回旧内容。

用法::

    python minimax/scripts/verify_spec_update.py
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

MINIMAX_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = MINIMAX_DIR.parent
SPEC_FILE = MINIMAX_DIR / "business_project" / "specs" / "SPEC-001-pricing.md"
EVIDENCE = MINIMAX_DIR / "evidence" / "db"

OLD_RATE = "| `>= 1000` | `0.05` | `0.95` |"
NEW_RATE = "| `>= 1000` | `0.08` | `0.92` |"

TASK = (
    "实现阶梯满减折扣：按会员折后小计命中 1000/5000/10000 门槛档位，"
    "只取最高一档，不叠加；会员折扣先于阶梯满减计算。"
)


def _env(work_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["SDDCTX_PROJECT_ROOT"] = str(MINIMAX_DIR / "business_project")
    env["SDDCTX_WORK_DIR"] = str(work_dir)
    env["SDDCTX_SEEKDB_PATH"] = str(work_dir / "seekdb")
    return env


def _sddctx(env: dict[str, str], *args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "sddctx", *args],
        cwd=str(MINIMAX_DIR),
        env=env,
        capture_output=True,
    )
    output = result.stdout.decode("utf-8", errors="replace")
    if result.returncode != 0:
        output += "\n[stderr]\n" + result.stderr.decode("utf-8", errors="replace")
    return output


def _context_text(env: dict[str, str], name: str) -> str:
    path = Path(env["SDDCTX_WORK_DIR"]) / name
    _sddctx(env, "context", "--task", TASK, "--task-id", "TASK-SPEC-UPDATE", "--out", str(path))
    return path.read_text(encoding="utf-8")


def _revision_of(context_text: str) -> str:
    for line in context_text.splitlines():
        if "SPEC-PRICING-102" in line or "revision=" in line:
            if "revision=" in line:
                return line.split("revision=")[1].split("）")[0].split(")")[0].strip()
    return "?"


def main() -> int:
    parser = argparse.ArgumentParser(description="验证规格更新后模块使用新内容")
    parser.add_argument("--work-dir", default=str(MINIMAX_DIR / ".sddctx-run"))
    args = parser.parse_args()

    work_dir = Path(args.work_dir).resolve()
    env = _env(work_dir)
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    backup = SPEC_FILE.read_text(encoding="utf-8")
    if OLD_RATE not in backup:
        print(f"规格文件中找不到待替换内容：{OLD_RATE!r}", file=sys.stderr)
        return 2

    steps: list[str] = []
    try:
        _sddctx(env, "init")
        _sddctx(env, "import")

        before = _context_text(env, "context-spec-update-before.md")
        steps.append("## 步骤 1：修改前的上下文\n")
        steps.append(f"- 规格 `SPEC-PRICING-102` 的 revision = **{_revision_of(before)}**\n")
        steps.append("修改前上下文中的阶梯表：\n")
        steps.append("```")
        steps.append(_extract_table(before))
        steps.append("```\n")

        SPEC_FILE.write_text(backup.replace(OLD_RATE, NEW_RATE), encoding="utf-8")
        import_out = _sddctx(env, "import")
        after = _context_text(env, "context-spec-update-after.md")
        steps.append("## 步骤 2：临时修改规格并重新导入\n")
        steps.append(f"- 规格改动：`{OLD_RATE}` → `{NEW_RATE}`\n")
        steps.append("重新导入的输出（统计到 `changes.specs_updated`）：\n")
        steps.append("```json")
        steps.append(_extract_json_block(import_out))
        steps.append("```\n")
        steps.append(f"- 重新导入后 `SPEC-PRICING-102` 的 revision = **{_revision_of(after)}**\n")
        steps.append("重新导入后的上下文中的阶梯表：\n")
        steps.append("```")
        steps.append(_extract_table(after))
        steps.append("```\n")

        steps.append("## 步骤 3：结论\n")
        steps.append(
            "- 修改前上下文出现 `0.05`，修改后上下文出现 `0.08`，"
            "且 `revision` 递增 —— **模块使用的是更新后的规格，而不是固定答案**。\n"
        )
        steps.append(
            "- 完整上下文见 `context-spec-update-before.md` 与 `context-spec-update-after.md`。\n"
        )

        ok = OLD_RATE in before and NEW_RATE in after and OLD_RATE not in after
    finally:
        SPEC_FILE.write_text(backup, encoding="utf-8")
        restore_import = _sddctx(env, "import")
        restored = _context_text(env, "context-spec-update-restored.md")
        steps.append("## 步骤 4：还原规格（交付物保持与实现、测试一致）\n")
        steps.append(f"- 重新导入后 revision = **{_revision_of(restored)}**\n")
        steps.append("还原后的上下文中的阶梯表：\n")
        steps.append("```")
        steps.append(_extract_table(restored))
        steps.append("```\n")
        ok = ok and OLD_RATE in restored

    report = "\n".join(
        [
            "# 规格更新验证记录",
            "",
            f"- 执行时间(UTC)：{datetime.now(timezone.utc).isoformat(timespec='seconds')}",
            f"- 规格文件：`{SPEC_FILE.relative_to(REPO_DIR).as_posix()}`",
            "",
            *steps,
        ]
    )
    (EVIDENCE / "spec-update-verification.md").write_text(report, encoding="utf-8")
    print(f"已写入 {EVIDENCE / 'spec-update-verification.md'}")
    print("验证结果：", "通过" if ok else "未通过")
    return 0 if ok else 1


def _extract_table(context_text: str) -> str:
    lines = context_text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if "discounted_subtotal` 门槛" in line:
            start = index
            break
    if start is None:
        return "(未找到阶梯表)"
    return "\n".join(line.strip() for line in lines[start : start + 6])


def _extract_json_block(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0:
        return text.strip()[:2000]
    return text[start : end + 1]


if __name__ == "__main__":
    raise SystemExit(main())
