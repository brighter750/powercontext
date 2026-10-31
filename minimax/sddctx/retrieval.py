"""任务检索：从资料库倒排索引中找出与任务相关的规格、代码与测试。

检索链路完全由数据库驱动：

1. 把任务描述分词后，对 ``{prefix}_token`` 做一次 ``IN`` 查询并按词频求和排序；
2. 按命中项回表读取规格条目、代码符号与测试用例的完整内容；
3. 再按“规格 → 测试”的引用关系补齐功能与兼容性测试。

因此上下文里出现的每一条资料，都能追溯到一次真实的数据库查询。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sddctx.models import CodeSymbol, DevContext, SpecItem, TaskState, TestCase
from sddctx.store.base import MaterialStore
from sddctx.store.mysql_like import CODE_KIND, SPEC_KIND, TEST_KIND

DEFAULT_MAX_SPECS = 8
DEFAULT_MAX_CODE = 14
DEFAULT_MAX_TESTS = 14

#: 规格正文里出现的标识符，用来把“适用的规格”直接连到“需要修改的代码符号”。
IDENTIFIER_IN_TEXT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")


def _identifiers_in(text: str) -> set[str]:
    """从规格正文中挑出可能的代码标识符。"""

    found: set[str] = set()
    for token in IDENTIFIER_IN_TEXT.findall(text):
        if "_" in token or any(char.isupper() for char in token):
            found.add(token)
    return found



@dataclass(slots=True)
class RetrievalPlan:
    """检索过程的可审计记录。"""

    query: str
    spec_ids: list[str]
    code_item_ids: list[str]
    test_item_ids: list[str]
    hits: list[dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "spec_ids": self.spec_ids,
            "code_item_ids": self.code_item_ids,
            "test_item_ids": self.test_item_ids,
            "hits": self.hits,
        }


async def retrieve(
    store: MaterialStore,
    *,
    task: str,
    task_id: str,
    state: TaskState | None = None,
    max_specs: int = DEFAULT_MAX_SPECS,
    max_code: int = DEFAULT_MAX_CODE,
    max_tests: int = DEFAULT_MAX_TESTS,
) -> DevContext:
    """根据任务描述检索资料并组装开发上下文。"""

    hits = await store.search(task, limit=60)

    spec_ids: list[str] = []
    code_item_ids: list[str] = []
    test_item_ids: list[str] = []
    for hit in hits:
        if hit.item_kind == SPEC_KIND and hit.item_id not in spec_ids:
            spec_ids.append(hit.item_id)
        elif hit.item_kind == CODE_KIND and hit.item_id not in code_item_ids:
            code_item_ids.append(hit.item_id)
        elif hit.item_kind == TEST_KIND and hit.item_id not in test_item_ids:
            test_item_ids.append(hit.item_id)

    specs = await store.get_specs(spec_ids[:max_specs])

    # 规格 -> 代码：规格正文里点名的符号就是最该修改的代码位置。
    named: set[str] = set()
    for spec in specs:
        named |= _identifiers_in(f"{spec.title}\n{spec.body}")
    linked_code = await store.get_code_symbols_by_names(sorted(named)) if named else []
    hit_code = await store.get_code_symbols_by_ids(code_item_ids[:max_code])

    code: list[CodeSymbol] = []
    seen_symbols: set[tuple[str, str]] = set()
    for symbol in [*linked_code, *hit_code]:
        key = (symbol.file_path, symbol.symbol)
        if key in seen_symbols:
            continue
        seen_symbols.add(key)
        code.append(symbol)
    code = code[: max_code * 2]

    tests = await store.get_tests_by_ids(test_item_ids[:max_tests])

    # 规格引用的测试必须补齐（功能测试 + 兼容性回归测试）。
    selected_spec_ids = [spec.spec_id for spec in specs]
    if selected_spec_ids:
        linked = await store.get_tests_for_specs(selected_spec_ids)
        known = {case.test_name for case in tests}
        known_paths = {case.file_path for case in tests}
        for case in linked:
            if case.test_name in known and case.file_path in known_paths:
                continue
            tests.append(case)
            known.add(case.test_name)
            known_paths.add(case.file_path)

    # 状态里已经登记的代码与测试也一并带回，保证重启后上下文仍然完整。
    if state is not None:
        for file_path in state.related_code:
            extra = await store.get_code_symbols([file_path])
            known_symbols = {(item.file_path, item.symbol) for item in code}
            for item in extra:
                if (item.file_path, item.symbol) not in known_symbols:
                    code.append(item)
        for file_path in state.related_tests:
            extra = await store.get_tests_by_path([file_path])
            known_tests = {(item.file_path, item.test_name) for item in tests}
            for item in extra:
                if (item.file_path, item.test_name) not in known_tests:
                    tests.append(item)

    plan = RetrievalPlan(
        query=task,
        spec_ids=[spec.spec_id for spec in specs],
        code_item_ids=[f"{item.file_path}::{item.symbol}" for item in code],
        test_item_ids=[f"{case.file_path}::{case.test_name}" for case in tests],
        hits=[hit.to_dict() for hit in hits],
    )

    return DevContext(
        task_id=task_id,
        task=task,
        specs=list(specs),
        code=list(code),
        tests=list(tests),
        state=state,
        retrieval=plan.to_dict(),
    )


def _source(spec: SpecItem) -> str:
    return f"{spec.source_path}:{spec.source_line}"


def _code_source(symbol: CodeSymbol) -> str:
    return f"{symbol.file_path}:{symbol.line}"


def render_markdown(context: DevContext) -> str:
    """把开发上下文渲染成可直接交给开发助手的 Markdown。"""

    lines: list[str] = []
    lines.append(f"# 开发上下文：{context.task_id}")
    lines.append("")
    lines.append(f"## 任务描述")
    lines.append("")
    lines.append(context.task.strip() or "(未提供)")
    lines.append("")

    lines.append("## 适用的规格条目")
    lines.append("")
    if context.specs:
        for spec in context.specs:
            lines.append(f"### {spec.spec_id} {spec.title}")
            lines.append(f"- 来源：`{_source(spec)}`（规格修订版本 revision={spec.revision}）")
            if spec.tags:
                lines.append(f"- 标签：{', '.join(spec.tags)}")
            lines.append("")
            lines.append(spec.body.strip())
            lines.append("")
    else:
        lines.append("_检索未命中规格条目。_")
        lines.append("")

    lines.append("## 需要修改的代码文件或符号")
    lines.append("")
    if context.code:
        current_file = ""
        for symbol in context.code:
            if symbol.file_path != current_file:
                current_file = symbol.file_path
                lines.append(f"### `{current_file}`")
                lines.append("")
            lines.append(f"- L{symbol.line} `{symbol.symbol}` ({symbol.kind}) — `{symbol.signature}`")
        lines.append("")
    else:
        lines.append("_检索未命中代码符号。_")
        lines.append("")

    lines.append("## 用于验证功能与兼容性的测试")
    lines.append("")
    if context.tests:
        for case in context.tests:
            references = ", ".join(case.requirement_ids) if case.requirement_ids else "-"
            lines.append(
                f"- `{case.file_path}:{case.line}` `{case.test_name}` — 覆盖规格：{references}"
            )
        lines.append("")
    else:
        lines.append("_检索未命中测试用例。_")
        lines.append("")

    lines.append("## 任务进展")
    lines.append("")
    state = context.state
    if state is None:
        lines.append("_尚未保存任务状态。_")
        lines.append("")
    else:
        lines.append(f"- 任务状态：{state.status}")
        lines.append(f"- 任务描述：{state.description}")
        lines.append("")
        lines.append("### 已完成事项")
        lines.append("")
        if state.done:
            lines.extend(f"- {item}" for item in state.done)
        else:
            lines.append("_（无）_")
        lines.append("")
        lines.append("### 待办事项")
        lines.append("")
        if state.todo:
            lines.extend(f"- {item}" for item in state.todo)
        else:
            lines.append("_（无）_")
        lines.append("")
        lines.append("### 已有验证结果")
        lines.append("")
        if state.test_results:
            for result in state.test_results:
                lines.append(
                    f"- `{result.get('command', '')}` → 退出码 {result.get('exit_code', '')}"
                    f"，通过 {result.get('passed', 0)} / 失败 {result.get('failed', 0)}"
                    f" / 错误 {result.get('errors', 0)}（代码版本 {result.get('revision', '') or 'n/a'}）"
                )
        else:
            lines.append("_（无）_")
        lines.append("")

    lines.append("## 资料库检索命中（来自 seekdb / OceanBase 的真实查询）")
    lines.append("")
    hits = context.retrieval.get("hits", []) if isinstance(context.retrieval, dict) else []
    if hits:
        lines.append("| 类型 | 条目 | 词频得分 |")
        lines.append("| --- | --- | --- |")
        for hit in hits[:20]:
            lines.append(
                f"| {hit.get('item_kind')} | `{hit.get('item_id')}` | {hit.get('score')} |"
            )
    else:
        lines.append("_（无命中）_")
    lines.append("")
    return "\n".join(lines)


__all__ = ["RetrievalPlan", "render_markdown", "retrieve"]
