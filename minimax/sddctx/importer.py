"""导入业务工程的规格、代码与测试资料。

* 规格：解析 Markdown 中的 ``## <规格编号> <标题>`` 二级标题，切成一条一条的规格条目。
* 代码：用 :mod:`ast` 解析 Python 文件，抽取函数、类等符号及其位置与签名。
* 测试：用 :mod:`ast` 解析测试文件，抽取测试用例，并回溯其引用的规格编号。

导入是**幂等且可重入**的：同一份资料重复导入不会产生重复条目；
内容发生变化时条目的 ``revision`` 递增、``content_hash`` 更新。
"""

from __future__ import annotations

import ast
import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from sddctx.models import CodeSymbol, SpecItem, TestCase

SPEC_HEADING = re.compile(r"^##\s+(?P<id>[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+)\s+(?P<title>.+?)\s*$")
SPEC_ID_IN_TEXT = re.compile(r"SPEC-[A-Z0-9]+(?:-[A-Z0-9]+)*")
TEST_FUNCTION = re.compile(r"^test_[A-Za-z0-9_]*$")


@dataclass(slots=True)
class ImportBundle:
    """一次导入产生的全部资料。"""

    specs: list[SpecItem]
    code: list[CodeSymbol]
    tests: list[TestCase]
    source_files: list[str]

    @property
    def counts(self) -> dict[str, int]:
        return {
            "specs": len(self.specs),
            "code_symbols": len(self.code),
            "tests": len(self.tests),
            "source_files": len(self.source_files),
        }


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _spec_tags(spec_id: str, title: str, body: str) -> tuple[str, ...]:
    tags = {part for part in spec_id.split("-")[1:-1] if part}
    lowered = f"{title}\n{body[:200]}"
    if "既有行为" in lowered or "必须保持" in lowered:
        tags.add("既有行为")
    if "目标行为" in lowered or "本次任务" in lowered:
        tags.add("目标行为")
    return tuple(sorted(tags))


def parse_spec_markdown(text: str, *, source_path: str, root: Path | None = None) -> list[SpecItem]:
    """把一份 Markdown 规格文档解析成规格条目列表。"""

    lines = text.splitlines()
    blocks: list[tuple[int, str, str, list[str]]] = []
    current: tuple[int, str, str, list[str]] | None = None
    for index, line in enumerate(lines, start=1):
        match = SPEC_HEADING.match(line)
        if match:
            if current is not None:
                blocks.append(current)
            current = (index, match.group("id"), match.group("title"), [])
            continue
        if current is not None:
            current[3].append(line)
    if current is not None:
        blocks.append(current)

    items: list[SpecItem] = []
    for line_no, spec_id, title, body_lines in blocks:
        body = "\n".join(body_lines).strip()
        items.append(
            SpecItem(
                spec_id=spec_id,
                title=title.strip(),
                body=body,
                source_path=source_path,
                source_line=line_no,
                content_hash=_digest(f"{title}\n{body}"),
                tags=_spec_tags(spec_id, title, body),
            )
        )
    return items


def _symbol_signature(source_lines: list[str], node: ast.stmt) -> str:
    start = node.lineno - 1
    end = getattr(node, "end_lineno", node.lineno) or node.lineno
    segment = "\n".join(source_lines[start : min(end, start + 8)])
    head = segment.splitlines()[0] if segment.splitlines() else ""
    return head.strip()


def parse_python_symbols(text: str, *, file_path: str, source_path: str) -> list[CodeSymbol]:
    """抽取一个 Python 文件中的函数、类与模块级常量。"""

    source_lines = text.splitlines()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    symbols: list[CodeSymbol] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(
                CodeSymbol(
                    symbol=node.name,
                    kind="function",
                    file_path=file_path,
                    line=node.lineno,
                    signature=_symbol_signature(source_lines, node),
                    source_path=source_path,
                    content_hash=_digest(f"{node.name}\n{_symbol_signature(source_lines, node)}"),
                )
            )
        elif isinstance(node, ast.ClassDef):
            symbols.append(
                CodeSymbol(
                    symbol=node.name,
                    kind="class",
                    file_path=file_path,
                    line=node.lineno,
                    signature=_symbol_signature(source_lines, node),
                    source_path=source_path,
                    content_hash=_digest(f"{node.name}\n{_symbol_signature(source_lines, node)}"),
                )
            )
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append(
                        CodeSymbol(
                            symbol=f"{node.name}.{child.name}",
                            kind="method",
                            file_path=file_path,
                            line=child.lineno,
                            signature=_symbol_signature(source_lines, child),
                            source_path=source_path,
                            content_hash=_digest(f"{child.name}\n{_symbol_signature(source_lines, child)}"),
                        )
                    )
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            names: list[str] = []
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.append(target.id)
            elif isinstance(node.target, ast.Name):
                names.append(node.target.id)
            for name in names:
                if not name.isupper():
                    continue
                symbols.append(
                    CodeSymbol(
                        symbol=name,
                        kind="constant",
                        file_path=file_path,
                        line=node.lineno,
                        signature=_symbol_signature(source_lines, node),
                        source_path=source_path,
                        content_hash=_digest(f"{name}\n{_symbol_signature(source_lines, node)}"),
                    )
                )
    return symbols


def parse_test_cases(text: str, *, file_path: str, source_path: str) -> list[TestCase]:
    """抽取测试文件中的测试用例，并回溯其引用的规格编号。"""

    source_lines = text.splitlines()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    cases: list[TestCase] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not TEST_FUNCTION.match(node.name):
            continue
        start = node.lineno - 1
        end = getattr(node, "end_lineno", node.lineno) or node.lineno
        segment = "\n".join(source_lines[start:end])
        docstring = ast.get_docstring(node) or ""
        requirement_ids = tuple(sorted(set(SPEC_ID_IN_TEXT.findall(segment + "\n" + docstring))))
        cases.append(
            TestCase(
                test_name=node.name,
                file_path=file_path,
                line=node.lineno,
                source_path=source_path,
                requirement_ids=requirement_ids,
                docstring=docstring.strip(),
                content_hash=_digest(segment),
            )
        )
    return sorted(cases, key=lambda item: (item.file_path, item.line))


def _iter_files(root: Path, patterns: Iterable[str]) -> list[Path]:
    seen: dict[Path, None] = {}
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            if path.is_file():
                seen[path] = None
    return list(seen)


def import_project(project_root: Path) -> ImportBundle:
    """导入业务工程的规格、代码与测试资料。"""

    project_root = project_root.resolve()
    specs: list[SpecItem] = []
    code: list[CodeSymbol] = []
    tests: list[TestCase] = []
    source_files: list[str] = []

    for path in _iter_files(project_root, ("specs/**/*.md", "docs/specs/**/*.md")):
        relative = _relative(path, project_root)
        source_files.append(relative)
        specs.extend(
            parse_spec_markdown(
                path.read_text(encoding="utf-8"),
                source_path=relative,
                root=project_root,
            )
        )

    for path in _iter_files(project_root, ("src/**/*.py",)):
        relative = _relative(path, project_root)
        source_files.append(relative)
        code.extend(
            parse_python_symbols(
                path.read_text(encoding="utf-8"),
                file_path=relative,
                source_path=relative,
            )
        )

    for path in _iter_files(project_root, ("tests/**/*.py",)):
        relative = _relative(path, project_root)
        source_files.append(relative)
        tests.extend(
            parse_test_cases(
                path.read_text(encoding="utf-8"),
                file_path=relative,
                source_path=relative,
            )
        )

    return ImportBundle(specs=specs, code=code, tests=tests, source_files=sorted(set(source_files)))


__all__ = [
    "ImportBundle",
    "import_project",
    "parse_python_symbols",
    "parse_spec_markdown",
    "parse_test_cases",
]
