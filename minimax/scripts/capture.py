"""证据采集脚本：运行命令并记录命令、退出码与对应代码版本。

用法::

    python minimax/scripts/capture.py --out minimax/evidence/logs/before-tests.log \\
        --label "修改前：目标功能测试" -- python -m pytest tests -q

每个日志文件开头写入元数据：命令、退出码、运行时间、Git commit、Git 工作区状态摘要。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip() or result.stderr.strip()


def _code_revision(cwd: Path) -> str:
    """记录被测代码版本：commit + 相关目录的改动摘要。"""

    commit = _git("rev-parse", "HEAD", cwd=cwd)
    branch = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
    status = _git("status", "--porcelain", "--", "minimax", cwd=cwd)
    changed = [line for line in status.splitlines() if line.strip()]
    summary = f"{commit} ({branch})"
    if changed:
        summary += f" | 工作区改动 {len(changed)} 个文件: " + ", ".join(
            line[3:].strip() for line in changed[:20]
        )
    return summary


def _test_digests(cwd: Path) -> list[str]:
    """记录测试文件的 SHA-256，用于证明修改前后使用同一组测试。"""

    tests_dir = cwd / "tests"
    if not tests_dir.is_dir():
        return []
    lines = []
    for path in sorted(tests_dir.glob("*.py")):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"    {path.name}: sha256={digest}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="运行命令并保存带元数据的日志")
    parser.add_argument("--out", required=True, help="日志输出路径")
    parser.add_argument("--label", default="", help="日志标签")
    parser.add_argument("--cwd", default=".", help="运行目录")
    parser.add_argument("--repo", default=".", help="用于记录代码版本的仓库目录")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="要执行的命令")
    args = parser.parse_args()

    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("必须提供要执行的命令")

    cwd = Path(args.cwd).resolve()
    repo = Path(args.repo).resolve()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["PYTHONUTF8"] = "1"

    started = datetime.now(timezone.utc)
    completed = subprocess.run(command, cwd=str(cwd), env=env, capture_output=True)
    finished = datetime.now(timezone.utc)

    stdout = completed.stdout.decode("utf-8", errors="replace")
    stderr = completed.stderr.decode("utf-8", errors="replace")

    header = [
        f"# 标签: {args.label or '（未命名）'}",
        f"# 命令: {' '.join(command)}",
        f"# 运行目录: {cwd}",
        f"# 开始时间(UTC): {started.isoformat(timespec='seconds')}",
        f"# 结束时间(UTC): {finished.isoformat(timespec='seconds')}",
        f"# 耗时(秒): {(finished - started).total_seconds():.2f}",
        f"# 退出码: {completed.returncode}",
        f"# 代码版本: {_code_revision(repo)}",
        f"# Python: {sys.version.split()[0]}",
        "# 测试文件指纹（sha256）：",
        *_test_digests(cwd),
        "# " + "-" * 70,
        "",
    ]
    body = ["===== STDOUT =====", stdout.rstrip(), "", "===== STDERR =====", stderr.rstrip(), ""]
    out_path.write_text("\n".join(header + body), encoding="utf-8")

    print(f"日志已写入 {out_path}（退出码 {completed.returncode}）")
    sys.stdout.write(stdout)
    if stderr.strip():
        sys.stderr.write(stderr)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
