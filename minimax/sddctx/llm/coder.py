"""把开发上下文交给 MiniMax，并把模型返回的补丁落盘。

提示词要求模型输出**标准 unified diff**（```diff 代码块），
补丁先经过解析与容错应用，再写入业务工程；模型原始响应完整留档。
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from sddctx.llm.minimax_client import LlmCall, MiniMaxClient

SYSTEM_PROMPT = """你是一名资深 Python 工程师，正在按规格驱动开发（SDD）的方式修改一个已有业务工程。

规则：
1. 严格遵守下面给出的“适用的规格条目”，不得擅自改变规格语义。
2. 不得删除、跳过（skip/xfail）或修改既有测试的断言与预期结果。
3. 只修改“需要修改的代码文件或符号”中列出的文件；如果确实需要改动其他文件，先在回复中说明理由。
4. 保持既有行为兼容（见兼容性规格）。
5. 输出必须是一个 ```diff 代码块，内容为标准 unified diff，路径相对业务工程根目录，例如：
   a/src/order_pricing/discounts.py
   b/src/order_pricing/discounts.py
6. diff 之后不要再输出解释性文字；若需要解释，放在 diff 代码块之前。
"""

DIFF_FENCE = re.compile(r"```(?:diff|patch)?\s*\n(?P<body>.*?)```", re.DOTALL)
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass(slots=True)
class PatchResult:
    """补丁生成与应用的结果。"""

    applied: bool
    diff: str
    changed_files: list[str]
    log: str
    call: LlmCall | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "applied": self.applied,
            "diff": self.diff,
            "changed_files": self.changed_files,
            "log": self.log,
            "call": self.call.to_dict() if self.call else None,
        }


def build_prompt(context_markdown: str, *, task: str, extra_instruction: str = "") -> str:
    """组装发给 MiniMax 的用户提示词。"""

    parts = [
        "下面是本次开发任务的工程上下文。请据此完成代码修改。",
        "",
        "## 本次任务",
        "",
        task.strip() or "(见上下文中的任务描述)",
        "",
        "## 工程上下文",
        "",
        context_markdown.strip(),
    ]
    if extra_instruction.strip():
        parts.extend(["", "## 额外要求", "", extra_instruction.strip()])
    parts.extend(
        [
            "",
            "## 输出要求",
            "",
            "请只输出一个 ```diff 代码块，内容为可直接应用的标准 unified diff。",
        ]
    )
    return "\n".join(parts)


def extract_diff(response_text: str) -> str:
    """从模型响应中提取 unified diff。

    保留结尾换行：``git apply`` 要求补丁以换行结束，否则会报 “corrupt patch”。
    """

    for match in DIFF_FENCE.finditer(response_text):
        body = match.group("body")
        if body.lstrip().startswith("---") or "@@" in body:
            return body.strip("\n") + "\n"
    # 没有代码块时，退回到直接包含 diff 头的片段。
    start = response_text.find("--- ")
    if start >= 0:
        return response_text[start:].strip("\n") + "\n"
    return ""


def changed_files_from_diff(diff: str) -> list[str]:
    """从 diff 中解析出被修改的文件路径。"""

    files: list[str] = []
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:].strip()
            if path and path != "/dev/null" and path not in files:
                files.append(path)
    return files


def _apply_with_git(project_root: Path, diff: str) -> tuple[bool, str]:
    result = subprocess.run(
        ["git", "apply", "--verbose", "--whitespace=nowarn", "-"],
        cwd=str(project_root),
        input=diff.encode("utf-8"),
        capture_output=True,
    )
    log = (result.stdout.decode("utf-8", errors="replace") + result.stderr.decode("utf-8", errors="replace")).strip()
    return result.returncode == 0, log


def _split_hunks(diff: str) -> list[tuple[str, str]]:
    """把 diff 拆成 ``(file_header, hunk_text)``。"""

    hunks: list[tuple[str, str]] = []
    current_header: list[str] = []
    current_body: list[str] = []
    in_hunk = False
    for line in diff.splitlines():
        if line.startswith("@@"):
            if current_body:
                hunks.append(("\n".join(current_header), "\n".join(current_body)))
            current_header = [line for line in current_header if not line.startswith("@@")]
            current_body = [line]
            in_hunk = True
            continue
        if in_hunk:
            if line.startswith(("--- ", "+++ ", "diff ", "index ")):
                current_header.append(line)
                continue
            current_body.append(line)
        else:
            current_header.append(line)
    if current_body:
        hunks.append(("\n".join(current_header), "\n".join(current_body)))
    return hunks


def _apply_with_fuzzy(project_root: Path, diff: str) -> tuple[bool, str]:
    """git apply 失败时的容错应用：按上下文块在原文件中定位并替换。"""

    logs: list[str] = []
    touched: set[str] = set()
    for header, body in _split_hunks(diff):
        target = ""
        for line in header.splitlines():
            if line.startswith("+++ b/"):
                target = line[6:].strip()
        if not target:
            continue
        path = project_root / target
        if not path.exists():
            logs.append(f"跳过不存在的文件：{target}")
            continue
        original = path.read_text(encoding="utf-8")
        lines = original.splitlines(keepends=True)
        old_block: list[str] = []
        new_block: list[str] = []
        for line in body.splitlines():
            if line.startswith("-"):
                old_block.append(line[1:] + "\n")
            elif line.startswith("+"):
                new_block.append(line[1:] + "\n")
            elif line.startswith(" "):
                old_block.append(line[1:] + "\n")
                new_block.append(line[1:] + "\n")
        old_text = "".join(old_block)
        new_text = "".join(new_block)
        if old_text and old_text in original:
            path.write_text(original.replace(old_text, new_text, 1), encoding="utf-8")
            touched.add(target)
            logs.append(f"容错应用成功：{target}")
            continue
        old_text_noeol = old_text.rstrip("\n")
        if old_text_noeol and old_text_noeol in original:
            path.write_text(original.replace(old_text_noeol, new_text.rstrip("\n"), 1), encoding="utf-8")
            touched.add(target)
            logs.append(f"容错应用成功（忽略行尾差异）：{target}")
            continue
        logs.append(f"容错应用失败：{target}")
        return False, "\n".join(logs)
    return bool(touched), "\n".join(logs)


def apply_diff(project_root: Path, diff: str) -> tuple[bool, str, list[str]]:
    """应用 unified diff，返回 ``(是否成功, 日志, 被修改文件)``。"""

    if not diff.strip():
        return False, "模型未返回可应用的 diff", []
    ok, log = _apply_with_git(project_root, diff)
    if ok:
        return True, log, changed_files_from_diff(diff)
    fuzzy_ok, fuzzy_log = _apply_with_fuzzy(project_root, diff)
    combined = f"git apply 失败：\n{log}\n容错应用：\n{fuzzy_log}"
    if fuzzy_ok:
        return True, combined, changed_files_from_diff(diff)
    return False, combined, []


class CodeModifier:
    """调用 MiniMax 完成代码修改。"""

    def __init__(self, client: MiniMaxClient) -> None:
        self.client = client

    def generate(
        self,
        *,
        context_markdown: str,
        task: str,
        project_root: Path,
        extra_instruction: str = "",
        dry_run: bool = False,
        response_file: Path | None = None,
        response_model: str | None = None,
    ) -> PatchResult:
        prompt = build_prompt(context_markdown, task=task, extra_instruction=extra_instruction)
        if response_file is not None:
            call = self._recorded_call(prompt, response_file, response_model)
        else:
            call = self.client.call(system=SYSTEM_PROMPT, prompt=prompt)
        diff = extract_diff(call.response_text)
        if dry_run:
            return PatchResult(
                applied=False,
                diff=diff,
                changed_files=changed_files_from_diff(diff),
                log="dry-run：未写入文件",
                call=call,
            )
        ok, log, changed = apply_diff(project_root, diff)
        return PatchResult(
            applied=ok,
            diff=diff,
            changed_files=changed,
            log=log,
            call=call,
        )

    def _recorded_call(
        self, prompt: str, response_file: Path, response_model: str | None = None
    ) -> LlmCall:
        """使用已留档的模型响应复现同一条应用路径。

        用于在没有独立 API 凭据的环境里复现“上下文 → 模型输出 → 应用补丁”
        的完整链路；调用记录会明确标注响应来源，不冒充实时 API 调用。
        """

        response_text = response_file.read_text(encoding="utf-8")
        raw = prompt.encode("utf-8")
        model = response_model or self.client.config.minimax_model
        endpoint = (
            f"{self.client.config.minimax_base_url.rstrip('/')}"
            f"/{'messages' if self.client.config.minimax_protocol == 'anthropic' else 'chat/completions'}"
        )
        return LlmCall(
            model=model,
            protocol=self.client.config.minimax_protocol,
            base_url=self.client.config.minimax_base_url,
            endpoint=endpoint,
            system=SYSTEM_PROMPT,
            prompt=prompt,
            response_text=response_text,
            elapsed_seconds=0.0,
            request_bytes=len(raw),
            response_bytes=len(response_text.encode("utf-8")),
            usage={"source": f"recorded-response:{response_file.name}"},
        )


__all__ = [
    "CodeModifier",
    "PatchResult",
    "SYSTEM_PROMPT",
    "apply_diff",
    "build_prompt",
    "changed_files_from_diff",
    "extract_diff",
]
