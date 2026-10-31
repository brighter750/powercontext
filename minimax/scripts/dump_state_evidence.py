"""导出 PowerContext 状态使用记录：写入回执、库中记录、以及模块重启后的读取结果。

“模块重启”通过**另起进程**读取状态来体现（`python -m sddctx state show` 是独立进程）。

用法::

    python minimax/scripts/dump_state_evidence.py --task-id TASK-001
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

MINIMAX_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MINIMAX_DIR))

from sddctx.config import SddConfig  # noqa: E402
from sddctx.models import TaskState  # noqa: E402
from sddctx.state import PowerContextStateStore  # noqa: E402

TASK_ID = "TASK-001"
TASK_TEXT = (
    "实现阶梯满减折扣：按会员折后小计命中 1000/5000/10000 门槛档位，"
    "只取最高一档，不叠加；会员折扣先于阶梯满减计算。"
)


def _get_json(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")
    except Exception as error:  # noqa: BLE001
        return 0, f"{type(error).__name__}: {error}"


def _post_json(url: str, payload: dict) -> tuple[int, str]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", errors="replace")
    except Exception as error:  # noqa: BLE001
        return 0, f"{type(error).__name__}: {error}"


async def main_async(args: argparse.Namespace) -> int:
    config = SddConfig.from_env()
    store = PowerContextStateStore(config)
    scope_id = await store.ensure_scope()

    state = TaskState.from_dict(json.loads(Path(args.state).read_text(encoding="utf-8")))
    if not state.task_id:
        state.task_id = TASK_ID

    receipt = await store.save(state)

    base = config.powercontext_url
    out: list[str] = []
    out.append("# PowerContext 状态写入与复用记录")
    out.append("")
    out.append(f"- 执行时间(UTC)：{datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    out.append(f"- PowerContext Server：`{base}`")
    out.append(f"- Scope：`{scope_id}`")
    out.append(f"- 任务 ID：`{state.task_id}`")
    out.append("")

    out.append("## 1. 状态写入（PowerContext 真实接口）")
    out.append("")
    out.append("| 接口 | 用途 | 返回 |")
    out.append("| --- | --- | --- |")
    out.append(
        f"| `POST /v1/work/contracts/create` | 记录 Work Contract（目标、范围、完成判据） | "
        f"source_id=`{receipt.contract_source_id}` |"
    )
    out.append(
        f"| `POST /v1/sources/content` | 把完整任务状态作为 Source 证据留存 | "
        f"source_id=`{receipt.state_source_id}` |"
    )
    out.append(
        f"| `POST /v1/memory/remember` | 写入可检索的 Atomic Memory 记录 | "
        f"{len(receipt.memory_records)} 条 |"
    )
    out.append(
        f"| `POST /v1/context/prepare` | 取出有界历史上下文 | "
        f"{len(receipt.prepared_context)} 字符 |"
    )
    out.append("")
    out.append("写入回执（不含任何凭据；`prepared_context` 为 PowerContext 准备的有界历史上下文）：")
    out.append("")
    out.append("```json")
    receipt_payload = receipt.to_dict()
    prepared = str(receipt_payload.get("prepared_context") or "")
    receipt_payload["prepared_context"] = {
        "length": len(prepared),
        "head": prepared[:400],
        "note": "完整内容见本文件末尾“PowerContext 准备的有界历史上下文”",
    }
    out.append(json.dumps(receipt_payload, ensure_ascii=False, indent=2))
    out.append("```")
    out.append("")
    out.append("PowerContext 准备的有界历史上下文（`POST /v1/context/prepare` 返回）：")
    out.append("")
    out.append("```text")
    out.append(prepared)
    out.append("```")
    out.append("")

    out.append("## 2. PowerContext 中的实际记录")
    out.append("")
    status, body = _post_json(
        f"{base}/v1/memory/entries/list", {"scope_id": scope_id, "limit": 50}
    )
    out.append("```http")
    out.append(f"POST {base}/v1/memory/entries/list   -> HTTP {status}")
    out.append("```")
    out.append("")
    try:
        entries = json.loads(body).get("entries", [])
    except json.JSONDecodeError:
        entries = []
    out.append("| 类型 | Artifact | 文本 |")
    out.append("| --- | --- | --- |")
    for entry in entries:
        artifact = entry.get("artifact", {})
        reference = (
            f"{artifact.get('family')}/{artifact.get('artifact_id')}@{artifact.get('revision')}"
        )
        text = str(entry.get("text", "")).split("\n")[-1]
        out.append(f"| {entry.get('kind')} | `{reference}` | {text} |")
    out.append("")

    status, body = _get_json(f"{base}/v1/scopes/{scope_id}/sources")
    out.append("```http")
    out.append(f"GET {base}/v1/scopes/{scope_id}/sources   -> HTTP {status}")
    out.append("```")
    out.append("")
    out.append("Source 证据中保存的完整状态 JSON（最新一条任务状态 Source）：")
    out.append("")
    out.append("```json")
    try:
        items = json.loads(body).get("items", [])
        prefix = f"sddctx-task-state-{state.task_id}-"
        matches = [item for item in items if str(item.get("source_id", "")).startswith(prefix)]
        latest = max(matches, key=lambda item: int(item.get("position", 0)))
        content = str(latest.get("content", ""))
        payload = content.split("] ", 1)[-1].split("\n", 1)[0]
        out.append(
            json.dumps(
                {
                    "source_id": latest.get("source_id"),
                    "position": latest.get("position"),
                    "content_digest": latest.get("content_digest"),
                    "state": json.loads(payload),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    except Exception:  # noqa: BLE001
        out.append(body[:2000])
    out.append("```")
    out.append("")

    out.append("## 3. 模块重启后读取状态")
    out.append("")
    out.append(
        "读取命令由**独立进程**执行（与写入命令不是同一个进程），"
        "因此该结果证明状态确实落盘并可被后续运行复用："
    )
    out.append("")
    out.append("```bash")
    out.append(f"python -m sddctx state show --task-id {state.task_id}")
    out.append("```")
    out.append("")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "sddctx", "state", "show", "--task-id", state.task_id],
        cwd=str(MINIMAX_DIR),
        env=env,
        capture_output=True,
    )
    stdout = result.stdout.decode("utf-8", errors="replace")
    out.append(f"退出码：`{result.returncode}`")
    out.append("")
    out.append("```json")
    out.append(stdout.strip())
    out.append("```")
    out.append("")

    out.append("## 4. 读取到的状态被用于后续上下文构造")
    out.append("")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "sddctx",
            "context",
            "--task",
            TASK_TEXT,
            "--task-id",
            state.task_id,
            "--out",
            str(Path(args.work_dir) / "context-with-state.md"),
        ],
        cwd=str(MINIMAX_DIR),
        env=env,
        capture_output=True,
    )
    context_path = Path(args.work_dir) / "context-with-state.md"
    out.append(f"```bash")
    out.append(f"python -m sddctx context --task \"{TASK_TEXT}\" --task-id {state.task_id}")
    out.append("```")
    out.append("")
    out.append(f"退出码：`{result.returncode}`")
    out.append("")
    if context_path.exists():
        text = context_path.read_text(encoding="utf-8")
        start = text.find("## 任务进展")
        end = text.find("## 资料库检索命中")
        out.append("生成的上下文中的“任务进展”段落（内容来自重启后读到的状态）：")
        out.append("")
        out.append("```markdown")
        out.append(text[start:end].strip() if start >= 0 else "(未找到任务进展段落)")
        out.append("```")
    out.append("")

    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(out), encoding="utf-8")
    print(f"已写入 {target}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 PowerContext 状态证据")
    parser.add_argument("--task-id", default=TASK_ID)
    parser.add_argument("--work-dir", default=str(MINIMAX_DIR / ".sddctx-run"))
    parser.add_argument(
        "--state", default=str(MINIMAX_DIR / ".sddctx-run" / "state-round1.json")
    )
    parser.add_argument(
        "--out", default=str(MINIMAX_DIR / "evidence" / "state" / "powercontext-state.md")
    )
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
