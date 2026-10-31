"""MySQL 协议资料库：seekdb 与 OceanBase 共用同一套实现。

两者都提供 MySQL 兼容接口，因此建表、写入、倒排索引与检索逻辑完全一致；
差别只在于“怎么连上去”：

* :class:`~sddctx.store.seekdb_store.SeekDBStore` —— 本地嵌入式 seekdb 进程；
* :class:`~sddctx.store.oceanbase_store.OceanBaseStore` —— 远端 OceanBase 服务端。

倒排索引表 ``{prefix}_token`` 保存 ``(token, item_kind, item_id, tf)``，
检索时用任务描述分词后做 ``IN`` 查询并按词频求和排序，
返回的命中项**就是**最终上下文里出现的规格、代码与测试。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from typing import Any

import aiomysql

from sddctx.importer import ImportBundle
from sddctx.models import CodeSymbol, SpecItem, TestCase
from sddctx.store.base import ImportStats, SearchHit
from sddctx.tokenizer import tokenize

SPEC_KIND = "spec"
CODE_KIND = "code"
TEST_KIND = "test"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _placeholders(values: Sequence[Any]) -> str:
    return ", ".join(["%s"] * len(values))


class MySqlMaterialStore:
    """基于 MySQL 协议的资料库实现。"""

    def __init__(self, *, table_prefix: str = "sdd", echo: bool = False) -> None:
        self.table_prefix = table_prefix
        self.echo = echo
        self._connection: aiomysql.Connection | None = None

    # --- 表名 ---------------------------------------------------------------

    @property
    def specs_table(self) -> str:
        return f"{self.table_prefix}_spec_item"

    @property
    def code_table(self) -> str:
        return f"{self.table_prefix}_code_symbol"

    @property
    def tests_table(self) -> str:
        return f"{self.table_prefix}_test_case"

    @property
    def token_table(self) -> str:
        return f"{self.table_prefix}_token"

    @property
    def import_log_table(self) -> str:
        return f"{self.table_prefix}_import_log"

    # --- 连接 ---------------------------------------------------------------

    async def _connect(self) -> aiomysql.Connection:
        raise NotImplementedError

    async def _ensure_connection(self) -> aiomysql.Connection:
        if self._connection is None:
            self._connection = await self._connect()
        return self._connection

    async def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    async def _execute(self, sql: str, args: Sequence[Any] = ()) -> int:
        connection = await self._ensure_connection()
        async with connection.cursor() as cursor:
            await cursor.execute(sql, tuple(args))
            return cursor.rowcount

    async def _query(self, sql: str, args: Sequence[Any] = ()) -> list[dict[str, Any]]:
        connection = await self._ensure_connection()
        async with connection.cursor(aiomysql.DictCursor) as cursor:
            await cursor.execute(sql, tuple(args))
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # --- 建表 ---------------------------------------------------------------

    async def initialize(self) -> None:
        statements = [
            f"""
            CREATE TABLE IF NOT EXISTS {self.specs_table} (
                spec_id VARCHAR(160) NOT NULL,
                title VARCHAR(512) NOT NULL,
                body TEXT NOT NULL,
                source_path VARCHAR(512) NOT NULL,
                source_line INT NOT NULL,
                revision INT NOT NULL,
                content_hash VARCHAR(64) NOT NULL,
                tags VARCHAR(512) NOT NULL,
                updated_at VARCHAR(40) NOT NULL,
                PRIMARY KEY (spec_id)
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS {self.code_table} (
                item_id VARCHAR(512) NOT NULL,
                symbol VARCHAR(256) NOT NULL,
                kind VARCHAR(32) NOT NULL,
                file_path VARCHAR(512) NOT NULL,
                line INT NOT NULL,
                signature VARCHAR(1024) NOT NULL,
                source_path VARCHAR(512) NOT NULL,
                revision INT NOT NULL,
                content_hash VARCHAR(64) NOT NULL,
                updated_at VARCHAR(40) NOT NULL,
                PRIMARY KEY (item_id)
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS {self.tests_table} (
                item_id VARCHAR(512) NOT NULL,
                test_name VARCHAR(256) NOT NULL,
                file_path VARCHAR(512) NOT NULL,
                line INT NOT NULL,
                source_path VARCHAR(512) NOT NULL,
                requirement_ids VARCHAR(512) NOT NULL,
                docstring TEXT NOT NULL,
                revision INT NOT NULL,
                content_hash VARCHAR(64) NOT NULL,
                updated_at VARCHAR(40) NOT NULL,
                PRIMARY KEY (item_id)
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS {self.token_table} (
                token VARCHAR(128) NOT NULL,
                item_kind VARCHAR(16) NOT NULL,
                item_id VARCHAR(512) NOT NULL,
                tf INT NOT NULL,
                PRIMARY KEY (token, item_kind, item_id)
            )
            """,
            f"""
            CREATE TABLE IF NOT EXISTS {self.import_log_table} (
                log_id VARCHAR(64) NOT NULL,
                imported_at VARCHAR(40) NOT NULL,
                source_files TEXT NOT NULL,
                counts TEXT NOT NULL,
                PRIMARY KEY (log_id)
            )
            """,
        ]
        for statement in statements:
            await self._execute(statement)

    # --- 写入 ---------------------------------------------------------------

    async def _upsert_specs(self, specs: list[SpecItem]) -> tuple[int, int, int]:
        inserted = updated = unchanged = 0
        for spec in specs:
            existing = await self._query(
                f"SELECT revision, content_hash FROM {self.specs_table} WHERE spec_id = %s",
                (spec.spec_id,),
            )
            if not existing:
                revision = 1
                await self._execute(
                    f"""
                    INSERT INTO {self.specs_table}
                        (spec_id, title, body, source_path, source_line, revision, content_hash, tags, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        spec.spec_id,
                        spec.title,
                        spec.body,
                        spec.source_path,
                        spec.source_line,
                        revision,
                        spec.content_hash,
                        ",".join(spec.tags),
                        _now(),
                    ),
                )
                inserted += 1
            elif existing[0]["content_hash"] != spec.content_hash:
                revision = int(existing[0]["revision"]) + 1
                await self._execute(
                    f"""
                    UPDATE {self.specs_table}
                    SET title = %s, body = %s, source_path = %s, source_line = %s,
                        revision = %s, content_hash = %s, tags = %s, updated_at = %s
                    WHERE spec_id = %s
                    """,
                    (
                        spec.title,
                        spec.body,
                        spec.source_path,
                        spec.source_line,
                        revision,
                        spec.content_hash,
                        ",".join(spec.tags),
                        _now(),
                        spec.spec_id,
                    ),
                )
                updated += 1
            else:
                revision = int(existing[0]["revision"])
                await self._execute(
                    f"UPDATE {self.specs_table} SET updated_at = %s WHERE spec_id = %s",
                    (_now(), spec.spec_id),
                )
                unchanged += 1
            spec.revision = revision
        return inserted, updated, unchanged

    async def _upsert_code(self, symbols: list[CodeSymbol]) -> tuple[int, int, int]:
        inserted = updated = unchanged = 0
        for symbol in symbols:
            item_id = f"{symbol.file_path}::{symbol.symbol}"
            existing = await self._query(
                f"SELECT revision, content_hash FROM {self.code_table} WHERE item_id = %s",
                (item_id,),
            )
            if not existing:
                revision = 1
                await self._execute(
                    f"""
                    INSERT INTO {self.code_table}
                        (item_id, symbol, kind, file_path, line, signature, source_path, revision, content_hash, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        item_id,
                        symbol.symbol,
                        symbol.kind,
                        symbol.file_path,
                        symbol.line,
                        symbol.signature,
                        symbol.source_path,
                        revision,
                        symbol.content_hash,
                        _now(),
                    ),
                )
                inserted += 1
            elif existing[0]["content_hash"] != symbol.content_hash:
                revision = int(existing[0]["revision"]) + 1
                await self._execute(
                    f"""
                    UPDATE {self.code_table}
                    SET symbol = %s, kind = %s, file_path = %s, line = %s, signature = %s,
                        source_path = %s, revision = %s, content_hash = %s, updated_at = %s
                    WHERE item_id = %s
                    """,
                    (
                        symbol.symbol,
                        symbol.kind,
                        symbol.file_path,
                        symbol.line,
                        symbol.signature,
                        symbol.source_path,
                        revision,
                        symbol.content_hash,
                        _now(),
                        item_id,
                    ),
                )
                updated += 1
            else:
                revision = int(existing[0]["revision"])
                await self._execute(
                    f"UPDATE {self.code_table} SET updated_at = %s WHERE item_id = %s",
                    (_now(), item_id),
                )
                unchanged += 1
            symbol.revision = revision
        return inserted, updated, unchanged

    async def _upsert_tests(self, cases: list[TestCase]) -> tuple[int, int, int]:
        inserted = updated = unchanged = 0
        for case in cases:
            item_id = f"{case.file_path}::{case.test_name}"
            existing = await self._query(
                f"SELECT revision, content_hash FROM {self.tests_table} WHERE item_id = %s",
                (item_id,),
            )
            if not existing:
                revision = 1
                await self._execute(
                    f"""
                    INSERT INTO {self.tests_table}
                        (item_id, test_name, file_path, line, source_path, requirement_ids,
                         docstring, revision, content_hash, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        item_id,
                        case.test_name,
                        case.file_path,
                        case.line,
                        case.source_path,
                        ",".join(case.requirement_ids),
                        case.docstring,
                        revision,
                        case.content_hash,
                        _now(),
                    ),
                )
                inserted += 1
            elif existing[0]["content_hash"] != case.content_hash:
                revision = int(existing[0]["revision"]) + 1
                await self._execute(
                    f"""
                    UPDATE {self.tests_table}
                    SET test_name = %s, file_path = %s, line = %s, source_path = %s,
                        requirement_ids = %s, docstring = %s, revision = %s,
                        content_hash = %s, updated_at = %s
                    WHERE item_id = %s
                    """,
                    (
                        case.test_name,
                        case.file_path,
                        case.line,
                        case.source_path,
                        ",".join(case.requirement_ids),
                        case.docstring,
                        revision,
                        case.content_hash,
                        _now(),
                        item_id,
                    ),
                )
                updated += 1
            else:
                revision = int(existing[0]["revision"])
                await self._execute(
                    f"UPDATE {self.tests_table} SET updated_at = %s WHERE item_id = %s",
                    (_now(), item_id),
                )
                unchanged += 1
            case.revision = revision
        return inserted, updated, unchanged

    async def _reindex(self, items: Iterable[tuple[str, str, str]]) -> int:
        """重建倒排索引。

        ``items`` 为 ``(kind, item_id, text)`` 三元组；同一 ``(kind, item_id)``
        会先清掉旧词元再写入新词元，保证规格更新后检索立刻反映新内容。
        """

        collected = list(items)
        if not collected:
            return 0
        for kind, item_id, _ in collected:
            await self._execute(
                f"DELETE FROM {self.token_table} WHERE item_kind = %s AND item_id = %s",
                (kind, item_id),
            )
        rows: list[tuple[str, str, str, int]] = []
        for kind, item_id, text in collected:
            counts: dict[str, int] = {}
            for token in tokenize(text):
                counts[token] = counts.get(token, 0) + 1
            for token, frequency in counts.items():
                rows.append((token, kind, item_id, frequency))
        for start in range(0, len(rows), 500):
            chunk = rows[start : start + 500]
            # 逐行写入以兼容不支持多行 VALUES 的实现。
            for token, kind, item_id, frequency in chunk:
                await self._execute(
                    f"""
                    INSERT INTO {self.token_table} (token, item_kind, item_id, tf)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (token, kind, item_id, frequency),
                )
        return len(rows)

    async def upsert(self, bundle: ImportBundle) -> ImportStats:
        stats = ImportStats(source_files=list(bundle.source_files))
        specs_inserted, specs_updated, specs_unchanged = await self._upsert_specs(bundle.specs)
        code_inserted, code_updated, code_unchanged = await self._upsert_code(bundle.code)
        tests_inserted, tests_updated, tests_unchanged = await self._upsert_tests(bundle.tests)
        stats.specs_inserted = specs_inserted
        stats.specs_updated = specs_updated
        stats.specs_unchanged = specs_unchanged
        stats.code_inserted = code_inserted
        stats.code_updated = code_updated
        stats.code_unchanged = code_unchanged
        stats.tests_inserted = tests_inserted
        stats.tests_updated = tests_updated
        stats.tests_unchanged = tests_unchanged

        index_rows: list[tuple[str, str, str]] = []
        for spec in bundle.specs:
            index_rows.append((SPEC_KIND, spec.spec_id, f"{spec.spec_id} {spec.title}\n{spec.body}"))
        for symbol in bundle.code:
            index_rows.append(
                (
                    CODE_KIND,
                    f"{symbol.file_path}::{symbol.symbol}",
                    f"{symbol.symbol} {symbol.kind} {symbol.file_path} {symbol.signature}",
                )
            )
        for case in bundle.tests:
            index_rows.append(
                (
                    TEST_KIND,
                    f"{case.file_path}::{case.test_name}",
                    f"{case.test_name} {case.file_path} {' '.join(case.requirement_ids)} {case.docstring}",
                )
            )
        stats.tokens_written = await self._reindex(index_rows)

        await self._execute(
            f"""
            INSERT INTO {self.import_log_table} (log_id, imported_at, source_files, counts)
            VALUES (%s, %s, %s, %s)
            """,
            (
                f"import-{int(datetime.now(timezone.utc).timestamp() * 1000)}",
                _now(),
                json.dumps(bundle.source_files, ensure_ascii=False),
                json.dumps(bundle.counts, ensure_ascii=False),
            ),
        )
        return stats

    # --- 检索 ---------------------------------------------------------------

    async def search(self, query: str, *, limit: int = 20) -> list[SearchHit]:
        tokens = sorted({token for token in tokenize(query) if token})
        if not tokens:
            return []
        rows = await self._query(
            f"""
            SELECT item_kind, item_id, SUM(tf) AS score
            FROM {self.token_table}
            WHERE token IN ({_placeholders(tokens)})
            GROUP BY item_kind, item_id
            ORDER BY score DESC, item_kind ASC, item_id ASC
            LIMIT %s
            """,
            [*tokens, int(limit)],
        )
        return [
            SearchHit(item_kind=str(row["item_kind"]), item_id=str(row["item_id"]), score=int(row["score"]))
            for row in rows
        ]

    async def get_spec(self, spec_id: str) -> SpecItem | None:
        rows = await self._query(
            f"SELECT * FROM {self.specs_table} WHERE spec_id = %s",
            (spec_id,),
        )
        if not rows:
            return None
        row = rows[0]
        return SpecItem(
            spec_id=str(row["spec_id"]),
            title=str(row["title"]),
            body=str(row["body"]),
            source_path=str(row["source_path"]),
            source_line=int(row["source_line"]),
            revision=int(row["revision"]),
            content_hash=str(row["content_hash"]),
            tags=tuple(tag for tag in str(row["tags"]).split(",") if tag),
        )

    async def get_specs(self, spec_ids: list[str]) -> list[SpecItem]:
        """按规格编号批量读取规格条目。"""

        if not spec_ids:
            return []
        rows = await self._query(
            f"""
            SELECT * FROM {self.specs_table}
            WHERE spec_id IN ({_placeholders(spec_ids)})
            ORDER BY spec_id ASC
            """,
            list(spec_ids),
        )
        by_id = {str(row["spec_id"]): row for row in rows}
        ordered: list[SpecItem] = []
        for spec_id in spec_ids:
            row = by_id.get(spec_id)
            if row is None:
                continue
            ordered.append(
                SpecItem(
                    spec_id=str(row["spec_id"]),
                    title=str(row["title"]),
                    body=str(row["body"]),
                    source_path=str(row["source_path"]),
                    source_line=int(row["source_line"]),
                    revision=int(row["revision"]),
                    content_hash=str(row["content_hash"]),
                    tags=tuple(tag for tag in str(row["tags"]).split(",") if tag),
                )
            )
        return ordered

    async def get_code_symbols_by_ids(self, item_ids: list[str]) -> list[CodeSymbol]:
        """按条目编号批量读取代码符号。"""

        if not item_ids:
            return []
        rows = await self._query(
            f"""
            SELECT * FROM {self.code_table}
            WHERE item_id IN ({_placeholders(item_ids)})
            ORDER BY file_path ASC, line ASC
            """,
            list(item_ids),
        )
        return [self._row_to_code(row) for row in rows]

    async def get_code_symbols_by_names(self, names: list[str]) -> list[CodeSymbol]:
        """按符号名批量读取代码符号（规格正文里点名的符号）。"""

        if not names:
            return []
        rows = await self._query(
            f"""
            SELECT * FROM {self.code_table}
            WHERE symbol IN ({_placeholders(names)})
            ORDER BY file_path ASC, line ASC
            """,
            list(names),
        )
        return [self._row_to_code(row) for row in rows]

    @staticmethod
    def _row_to_code(row: dict[str, Any]) -> CodeSymbol:
        return CodeSymbol(
            symbol=str(row["symbol"]),
            kind=str(row["kind"]),
            file_path=str(row["file_path"]),
            line=int(row["line"]),
            signature=str(row["signature"]),
            source_path=str(row["source_path"]),
            revision=int(row["revision"]),
            content_hash=str(row["content_hash"]),
        )

    async def get_tests_by_ids(self, item_ids: list[str]) -> list[TestCase]:
        """按条目编号批量读取测试用例。"""

        if not item_ids:
            return []
        rows = await self._query(
            f"""
            SELECT * FROM {self.tests_table}
            WHERE item_id IN ({_placeholders(item_ids)})
            ORDER BY file_path ASC, line ASC
            """,
            list(item_ids),
        )
        return [self._row_to_test(row) for row in rows]

    async def get_code_symbols(self, file_paths: list[str]) -> list[CodeSymbol]:
        if not file_paths:
            return []
        rows = await self._query(
            f"""
            SELECT * FROM {self.code_table}
            WHERE file_path IN ({_placeholders(file_paths)})
            ORDER BY file_path ASC, line ASC
            """,
            list(file_paths),
        )
        return [
            CodeSymbol(
                symbol=str(row["symbol"]),
                kind=str(row["kind"]),
                file_path=str(row["file_path"]),
                line=int(row["line"]),
                signature=str(row["signature"]),
                source_path=str(row["source_path"]),
                revision=int(row["revision"]),
                content_hash=str(row["content_hash"]),
            )
            for row in rows
        ]

    async def get_tests_for_specs(self, spec_ids: list[str]) -> list[TestCase]:
        if not spec_ids:
            return []
        clauses = " OR ".join(["requirement_ids LIKE %s"] * len(spec_ids))
        args = [f"%{spec_id}%" for spec_id in spec_ids]
        rows = await self._query(
            f"""
            SELECT * FROM {self.tests_table}
            WHERE {clauses}
            ORDER BY file_path ASC, line ASC
            """,
            args,
        )
        return [self._row_to_test(row) for row in rows]

    async def get_tests_by_path(self, file_paths: list[str]) -> list[TestCase]:
        if not file_paths:
            return []
        rows = await self._query(
            f"""
            SELECT * FROM {self.tests_table}
            WHERE file_path IN ({_placeholders(file_paths)})
            ORDER BY file_path ASC, line ASC
            """,
            list(file_paths),
        )
        return [self._row_to_test(row) for row in rows]

    @staticmethod
    def _row_to_test(row: dict[str, Any]) -> TestCase:
        return TestCase(
            test_name=str(row["test_name"]),
            file_path=str(row["file_path"]),
            line=int(row["line"]),
            source_path=str(row["source_path"]),
            requirement_ids=tuple(
                value for value in str(row["requirement_ids"]).split(",") if value
            ),
            docstring=str(row["docstring"]),
            revision=int(row["revision"]),
            content_hash=str(row["content_hash"]),
        )

    async def stats(self) -> dict[str, Any]:
        specs = await self._query(f"SELECT COUNT(*) AS n FROM {self.specs_table}")
        code = await self._query(f"SELECT COUNT(*) AS n FROM {self.code_table}")
        tests = await self._query(f"SELECT COUNT(*) AS n FROM {self.tests_table}")
        tokens = await self._query(f"SELECT COUNT(*) AS n FROM {self.token_table}")
        revisions = await self._query(
            f"SELECT spec_id, revision, content_hash, updated_at FROM {self.specs_table} ORDER BY spec_id ASC"
        )
        return {
            "specs": int(specs[0]["n"]),
            "code_symbols": int(code[0]["n"]),
            "tests": int(tests[0]["n"]),
            "tokens": int(tokens[0]["n"]),
            "spec_revisions": revisions,
        }


__all__ = ["CODE_KIND", "MySqlMaterialStore", "SPEC_KIND", "TEST_KIND"]
