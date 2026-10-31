"""资料库接口。

资料库保存工程规格、代码符号与测试用例，并为**倒排索引**提供词元表；
检索结果直接决定开发上下文里出现哪些规格、代码与测试。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from sddctx.importer import ImportBundle
from sddctx.models import CodeSymbol, SpecItem, TestCase


@dataclass(slots=True)
class SearchHit:
    """一条检索命中。"""

    item_kind: str
    item_id: str
    score: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ImportStats:
    """一次导入的结果统计。"""

    specs_inserted: int = 0
    specs_updated: int = 0
    specs_unchanged: int = 0
    code_inserted: int = 0
    code_updated: int = 0
    code_unchanged: int = 0
    tests_inserted: int = 0
    tests_updated: int = 0
    tests_unchanged: int = 0
    tokens_written: int = 0
    source_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class MaterialStore(Protocol):
    """资料库的统一协议（seekdb 与 OceanBase 共用实现）。"""

    async def initialize(self) -> None:
        """建表并确保索引可用。"""

    async def upsert(self, bundle: ImportBundle) -> ImportStats:
        """写入资料；内容变化时递增 revision。"""

    async def search(self, query: str, *, limit: int = 20) -> list[SearchHit]:
        """按任务描述检索相关资料。"""

    async def get_spec(self, spec_id: str) -> SpecItem | None: ...

    async def get_specs(self, spec_ids: list[str]) -> list[SpecItem]: ...

    async def get_code_symbols(self, file_paths: list[str]) -> list[CodeSymbol]: ...

    async def get_code_symbols_by_ids(self, item_ids: list[str]) -> list[CodeSymbol]: ...

    async def get_code_symbols_by_names(self, names: list[str]) -> list[CodeSymbol]: ...

    async def get_tests_for_specs(self, spec_ids: list[str]) -> list[TestCase]: ...

    async def get_tests_by_path(self, file_paths: list[str]) -> list[TestCase]: ...

    async def get_tests_by_ids(self, item_ids: list[str]) -> list[TestCase]: ...

    async def stats(self) -> dict[str, Any]: ...

    async def close(self) -> None: ...


__all__ = ["ImportStats", "MaterialStore", "SearchHit"]
