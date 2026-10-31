# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Hybrid Memory search: one candidate statement and unchanged channels."""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, event, text

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import (
    EmbeddingProfile,
    MemoryEntryInput,
    MemorySearchRequest,
    MemoryUsedSearchMode,
)
from powercontext.builtin.artifacts.search import analyze_fts_query
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.memory_index import memory_channel_hits_by_channel
from powercontext.builtin.persistence.oceanbase.hybrid_memory_index import (
    _OCEANBASE_HYBRID_SEARCH_SQL,
    _OCEANBASE_HYBRID_TAGGED_SEARCH_SQL,
    OceanBaseHybridMemoryIndex,
)
from powercontext.builtin.persistence.oceanbase.memory_index import (
    OceanBaseMemoryFTSIndex,
    OceanBaseMemoryVectorIndex,
)
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.tags import MemoryEntryTagTarget, TagFilter


def _profile() -> EmbeddingProfile:
    return EmbeddingProfile(
        profile_id="hybrid-memory-search-test-v1",
        model="deterministic",
        dimension=3,
        distance="l2",
        normalization="unit",
    )


class _Embedding:
    """Embeds every "alpha" text to one vector so distances tie."""

    profile = _profile()

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return EmbeddingResult(
            vectors=tuple(
                (1.0, 0.0, 0.0)
                if "alpha" in text.casefold()
                else (0.0, 0.0, 1.0)
                if "gamma" in text.casefold()
                else (0.0, 1.0, 0.0)
                for text in texts
            )
        )


@contextmanager
def _statements() -> Iterator[list[tuple[str, Any]]]:
    statements: list[tuple[str, Any]] = []

    def record(_connection, _cursor, statement, parameters, _context, _executemany) -> None:
        statements.append((statement, parameters))

    event.listen(Engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(Engine, "before_cursor_execute", record)


def _is_native(statement: str) -> bool:
    return (
        "pc_memory_entry_heads" in statement
        and "pc_memory_vector_entries" in statement
        and "AGAINST" in statement.upper()
        and "l2_distance" in statement.lower()
    )


def _request(
    memories: tuple[ArtifactRef, ...],
    *,
    mode: MemoryUsedSearchMode = "hybrid",
    candidate_limit: int = 32,
    tag_filter: TagFilter | None = None,
    query: str = "alpha",
    query_vector: tuple[float, ...] = (1.0, 0.0, 0.0),
) -> MemorySearchRequest:
    return MemorySearchRequest(
        query=query,
        analyzed_query=analyze_fts_query(query),
        memories=memories,
        candidate_limit=candidate_limit,
        mode=mode,
        query_vector=query_vector,
        embedding_profile=_profile(),
        tag_filter=tag_filter,
    )


def _identity(hits: Any) -> list[tuple[str, str, str, float | None]]:
    return [(hit.entry_id, hit.entry_version_id, hit.text, hit.distance) for hit in hits]


def test_hybrid_statement_returns_both_channels_from_one_native_union_all() -> None:
    statement = _OCEANBASE_HYBRID_SEARCH_SQL
    assert statement.upper().count("UNION ALL") == 1
    assert statement.upper().count("SELECT ") == 2
    assert "pc_memory_entry_heads" in statement
    assert "pc_memory_vector_entries" in statement
    assert "AGAINST" in statement.upper()
    assert "l2_distance" in statement
    # Each branch keeps its own ranking and candidate limit; no outer sort,
    # window function, common table expression or derived table is applied.
    assert statement.count("ORDER BY") == 2
    assert statement.count("LIMIT :candidate_limit") == 2
    assert "ROW_NUMBER" not in statement.upper()
    assert "WITH " not in statement.upper()
    assert "FROM (" not in statement.upper()
    assert statement.rstrip().endswith("LIMIT :candidate_limit)")
    # The channels are labelled, and the filters stay inside the database.
    assert "'fts' AS channel" in statement
    assert "'vector' AS channel" in statement
    assert statement.count(":scope_id") == 2
    assert statement.count(":memory_artifact_ids") == 2


def test_tagged_hybrid_statement_filters_in_database_and_ranks_exactly() -> None:
    statement = _OCEANBASE_HYBRID_TAGGED_SEARCH_SQL
    # An approximate top-k truncates before the tag filter applies, so the
    # tagged branch ranks exactly and breaks the remaining ties by entry id.
    assert "APPROXIMATE" not in statement.upper()
    assert "l2_distance(m.embedding, :query_vector), m.entry_id" in statement
    assert statement.count(":tag_minimum") == 2
    assert statement.count(":tag_keys") == 2
    assert statement.count(":tag_hashes") == 2


def test_hybrid_coordinator_reports_the_capabilities_of_the_indexes_it_coordinates() -> None:
    fts_only = OceanBaseHybridMemoryIndex(OceanBaseMemoryFTSIndex())
    assert fts_only.capabilities.fts is True
    assert fts_only.capabilities.vector is False
    assert fts_only.capabilities.hybrid is False

    hybrid = OceanBaseHybridMemoryIndex(OceanBaseMemoryFTSIndex(), OceanBaseMemoryVectorIndex(_profile()))
    assert hybrid.capabilities.fts is True
    assert hybrid.capabilities.vector is True
    assert hybrid.capabilities.hybrid is True
    assert hybrid.capabilities.tag_filter is True
    assert hybrid.capabilities.embedding_profile == _profile()


def test_channel_split_keeps_the_database_order_of_each_channel() -> None:
    memories = (ArtifactRef(family="memory", artifact_id="memory", revision=3),)
    rows = [
        {"channel": "fts", "memory_artifact_id": "memory", "entry_id": "e1", "entry_version_id": "v1", "text": "a"},
        {"channel": "vector", "memory_artifact_id": "memory", "entry_id": "e2", "entry_version_id": "v2", "text": "b"},
        {"channel": "fts", "memory_artifact_id": "memory", "entry_id": "e3", "entry_version_id": "v3", "text": "c"},
        {"channel": "vector", "memory_artifact_id": "other", "entry_id": "e4", "entry_version_id": "v4", "text": "d"},
        {"channel": "other", "memory_artifact_id": "memory", "entry_id": "e5", "entry_version_id": "v5", "text": "e"},
    ]
    channels = memory_channel_hits_by_channel(rows, memories)
    assert _identity(channels.fts) == [("e1", "v1", "a", None), ("e3", "v3", "c", None)]
    assert _identity(channels.vector) == [("e2", "v2", "b", None)]
    assert all(hit.memory_ref == memories[0] for hit in channels.fts + channels.vector)


def test_hybrid_search_matches_the_frozen_two_statement_path_on_a_real_engine() -> None:
    pytest.importorskip("pylibseekdb", reason="embedded seekdb is an optional dependency")

    async def scenario() -> None:
        database_path = Path(tempfile.mkdtemp(prefix="pc-hybrid-search-")) / "db"
        async with open_builtin_contexts(
            BuiltinConfig(database=SeekDBConfig(path=database_path)), embedding_model=_Embedding()
        ) as contexts:
            memory = (await contexts.get("project")).artifacts.memory
            other = (await contexts.get("other-scope")).artifacts.memory
            head = await memory.remember(
                memory=None,
                entries=(
                    MemoryEntryInput(kind="fact", text="tie alpha one"),
                    MemoryEntryInput(kind="fact", text="tie alpha two"),
                    MemoryEntryInput(kind="fact", text="tie alpha three"),
                    MemoryEntryInput(kind="fact", text="oth beta four"),
                ),
                mode="append",
            )
            other_head = await other.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="fact", text="tie alpha decoy"),),
                mode="append",
            )
            assert head is not None
            assert other_head is not None

            tagged_entry = next(entry for entry in await memory.entries(head) if entry.text == "tie alpha one")
            target = MemoryEntryTagTarget(artifact_id=head.artifact_id, entry_id=tagged_entry.entry_id)
            empty = await contexts.records.get_tags("project", target)
            await contexts.records.replace_tags("project", target, ("selected",), expected_etag=empty.etag)

            hybrid = OceanBaseHybridMemoryIndex(OceanBaseMemoryFTSIndex(), OceanBaseMemoryVectorIndex(_profile()))
            frozen_fts = OceanBaseMemoryFTSIndex()
            frozen_vector = OceanBaseMemoryVectorIndex(_profile())
            request = _request((head.as_ref(),))

            async with contexts.database.transaction() as connection:
                with _statements() as statements:
                    channels = await hybrid.search(connection, "project", request)
                native = [(text, params) for text, params in statements if _is_native(text)]
                assert len(native) == 1
                statement, parameters = native[0]

                plan = "\n".join(
                    str(row[0]) for row in (await connection.exec_driver_sql(f"EXPLAIN {statement}", parameters)).all()
                )
                assert "TEXT RETRIEVAL SCAN" in plan
                assert "VECTOR INDEX" in plan

                frozen = await frozen_fts.search(connection, "project", request)
                frozen_channels = await frozen_vector.search(connection, "project", request)
                assert _identity(channels.fts) == _identity(frozen.fts)
                assert _identity(channels.vector) == _identity(frozen_channels.vector)
                # The tied candidates keep the order of the frozen statement.
                assert [hit.distance for hit in channels.vector[:3]] == [0.0, 0.0, 0.0]

                repeated = await hybrid.search(connection, "project", request)
                assert _identity(repeated.fts) == _identity(channels.fts)
                assert _identity(repeated.vector) == _identity(channels.vector)

                limited = await hybrid.search(connection, "project", _request((head.as_ref(),), candidate_limit=1))
                assert len(limited.fts) <= 1
                assert len(limited.vector) <= 1

                scoped = await hybrid.search(connection, "other-scope", _request((other_head.as_ref(),)))
                assert scoped.fts
                assert all(hit.memory_ref.artifact_id == other_head.artifact_id for hit in scoped.fts + scoped.vector)

                tag_filter = TagFilter(tags=("selected",))
                tagged_request = _request((head.as_ref(),), tag_filter=tag_filter)
                with _statements() as statements:
                    tagged = await hybrid.search(connection, "project", tagged_request)
                tagged_native = [(text, params) for text, params in statements if _is_native(text)]
                assert len(tagged_native) == 1
                frozen_tagged = await frozen_fts.search(connection, "project", tagged_request)
                frozen_tagged_vector = await frozen_vector.search(connection, "project", tagged_request)
                assert _identity(tagged.fts) == _identity(frozen_tagged.fts)
                assert _identity(tagged.vector) == _identity(frozen_tagged_vector.vector)
                # The tagged entry is the only candidate, found by both channels.
                assert {hit.entry_id for hit in tagged.fts + tagged.vector} == {tagged_entry.entry_id}

                with _statements() as statements:
                    await hybrid.search(connection, "project", _request((head.as_ref(),), mode="fts"))
                fts_only = [text for text, _ in statements if "AGAINST" in text.upper()]
                assert len(fts_only) == 1
                assert "pc_memory_vector_entries" not in fts_only[0]

                with _statements() as statements:
                    await hybrid.search(connection, "project", _request((head.as_ref(),), mode="vector"))
                vector_only = [text for text, _ in statements if "l2_distance" in text.lower()]
                assert len(vector_only) == 1
                assert "pc_memory_entry_heads" not in vector_only[0]

    asyncio.run(scenario())


def test_hybrid_search_keeps_entries_whose_projection_revision_is_stale() -> None:
    pytest.importorskip("pylibseekdb", reason="embedded seekdb is an optional dependency")

    async def scenario() -> None:
        database_path = Path(tempfile.mkdtemp(prefix="pc-hybrid-projection-")) / "db"
        async with open_builtin_contexts(
            BuiltinConfig(database=SeekDBConfig(path=database_path)), embedding_model=_Embedding()
        ) as contexts:
            memory = (await contexts.get("project")).artifacts.memory
            first = await memory.remember(
                memory=None,
                entries=(
                    MemoryEntryInput(kind="fact", text="alpha one"),
                    MemoryEntryInput(kind="fact", text="alpha two"),
                ),
                mode="append",
            )
            assert first is not None
            second = await memory.remember(
                memory=first,
                entries=(MemoryEntryInput(kind="fact", text="gamma three"),),
                mode="append",
            )
            assert second is not None
            assert second.revision > first.revision

            hybrid = OceanBaseHybridMemoryIndex(OceanBaseMemoryFTSIndex(), OceanBaseMemoryVectorIndex(_profile()))
            by_text = {entry.text: entry.entry_id for entry in await memory.entries(second)}
            async with contexts.database.transaction() as connection:
                rows = (
                    await connection.execute(
                        text(
                            "SELECT entry_id, head_revision FROM pc_memory_entry_heads"
                            " WHERE scope_id = :scope_id AND memory_artifact_id = :memory_artifact_id"
                        ),
                        {"scope_id": "project", "memory_artifact_id": second.artifact_id},
                    )
                ).all()
                revisions = {str(row[0]): int(row[1]) for row in rows}
                assert len(revisions) == 3
                # Only the appended entry carries the current projection revision.
                assert revisions[by_text["gamma three"]] == second.revision
                assert revisions[by_text["alpha one"]] == first.revision
                assert revisions[by_text["alpha two"]] == first.revision

                channels = await hybrid.search(connection, "project", _request((second.as_ref(),)))
                # The unchanged entries keep their stale projection revision but
                # stay retrievable, and every hit cites the requested head.
                assert channels.fts
                assert all(hit.memory_ref.revision == second.revision for hit in channels.fts + channels.vector)
                assert {hit.text for hit in channels.fts} == {"alpha one", "alpha two"}

    asyncio.run(scenario())


def test_tagged_tie_group_beyond_the_candidate_limit_keeps_the_frozen_candidates() -> None:
    pytest.importorskip("pylibseekdb", reason="embedded seekdb is an optional dependency")

    async def scenario() -> None:
        database_path = Path(tempfile.mkdtemp(prefix="pc-hybrid-tagged-tie-")) / "db"
        async with open_builtin_contexts(
            BuiltinConfig(database=SeekDBConfig(path=database_path)), embedding_model=_Embedding()
        ) as contexts:
            memory = (await contexts.get("project")).artifacts.memory
            # Every entry embeds to the same vector, so every candidate has the
            # same distance and the tie group is larger than the limit.
            tie_count = 12
            candidate_limit = 5
            head = await memory.remember(
                memory=None,
                entries=tuple(
                    MemoryEntryInput(kind="fact", text=f"beta tie candidate {index:02d}") for index in range(tie_count)
                ),
                mode="append",
            )
            assert head is not None
            for entry in await memory.entries(head):
                target = MemoryEntryTagTarget(artifact_id=head.artifact_id, entry_id=entry.entry_id)
                empty = await contexts.records.get_tags("project", target)
                await contexts.records.replace_tags("project", target, ("selected",), expected_etag=empty.etag)

            hybrid = OceanBaseHybridMemoryIndex(OceanBaseMemoryFTSIndex(), OceanBaseMemoryVectorIndex(_profile()))
            frozen_vector = OceanBaseMemoryVectorIndex(_profile())
            request = _request(
                (head.as_ref(),),
                candidate_limit=candidate_limit,
                tag_filter=TagFilter(tags=("selected",)),
                query="beta",
            )
            async with contexts.database.transaction() as connection:
                frozen = await frozen_vector.search(connection, "project", request)
                channels = await hybrid.search(connection, "project", request)

            assert len(frozen.vector) == candidate_limit
            assert len(channels.vector) == candidate_limit
            # The candidates really are tied, so the limit cuts inside the group.
            assert len({hit.distance for hit in frozen.vector}) == 1
            assert len({hit.distance for hit in channels.vector}) == 1
            assert _identity(channels.vector) == _identity(frozen.vector)

    asyncio.run(scenario())
