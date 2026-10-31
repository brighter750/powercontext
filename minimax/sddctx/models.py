"""领域模型：规格条目、代码符号、测试用例、任务状态与开发上下文。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ItemKind = Literal["spec", "code", "test"]


@dataclass(slots=True)
class SpecItem:
    """一条规格条目（对应规格文档中的一个二级标题）。"""

    spec_id: str
    title: str
    body: str
    source_path: str
    source_line: int
    revision: int = 1
    content_hash: str = ""
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "tags": list(self.tags)}


@dataclass(slots=True)
class CodeSymbol:
    """一个代码符号（函数、类或模块级常量）。"""

    symbol: str
    kind: str
    file_path: str
    line: int
    signature: str
    source_path: str
    revision: int = 1
    content_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TestCase:
    """一个测试用例。"""

    test_name: str
    file_path: str
    line: int
    source_path: str
    requirement_ids: tuple[str, ...] = ()
    docstring: str = ""
    revision: int = 1
    content_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "requirement_ids": list(self.requirement_ids)}


@dataclass(slots=True)
class TestResult:
    """一次测试运行的结果。"""

    command: str
    exit_code: int
    passed: int
    failed: int
    errors: int
    summary: str
    revision: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TaskState:
    """任务状态：保存后可在模块重启后读取并复用。"""

    task_id: str
    description: str
    status: str = "in_progress"
    applicable_specs: list[str] = field(default_factory=list)
    related_code: list[str] = field(default_factory=list)
    related_tests: list[str] = field(default_factory=list)
    done: list[str] = field(default_factory=list)
    todo: list[str] = field(default_factory=list)
    test_results: list[dict[str, Any]] = field(default_factory=list)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TaskState:
        return cls(
            task_id=str(payload.get("task_id", "")),
            description=str(payload.get("description", "")),
            status=str(payload.get("status", "in_progress")),
            applicable_specs=list(payload.get("applicable_specs", [])),
            related_code=list(payload.get("related_code", [])),
            related_tests=list(payload.get("related_tests", [])),
            done=list(payload.get("done", [])),
            todo=list(payload.get("todo", [])),
            test_results=list(payload.get("test_results", [])),
            updated_at=str(payload.get("updated_at", "")),
        )


@dataclass(slots=True)
class DevContext:
    """一次开发任务所需的完整上下文。"""

    task_id: str
    task: str
    specs: list[SpecItem] = field(default_factory=list)
    code: list[CodeSymbol] = field(default_factory=list)
    tests: list[TestCase] = field(default_factory=list)
    state: TaskState | None = None
    retrieval: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task": self.task,
            "specs": [item.to_dict() for item in self.specs],
            "code": [item.to_dict() for item in self.code],
            "tests": [item.to_dict() for item in self.tests],
            "state": self.state.to_dict() if self.state else None,
            "retrieval": self.retrieval,
        }


__all__ = [
    "CodeSymbol",
    "DevContext",
    "ItemKind",
    "SpecItem",
    "TaskState",
    "TestCase",
    "TestResult",
]
