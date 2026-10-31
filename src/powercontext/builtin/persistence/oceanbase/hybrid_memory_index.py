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

"""OceanBase hybrid Memory search over one native candidate statement."""

from __future__ import annotations

from pyobvector import VECTOR
from sqlalchemy import Table, TextClause, bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import (
    CapabilityNotSupportedError,
    EmbeddingProfile,
    MemoryCapabilities,
    MemoryProjection,
    MemorySearchChannels,
    MemorySearchRequest,
)
from powercontext.builtin.artifacts.memory.canonical import validate_embedding
from powercontext.builtin.persistence.memory_index import memory_channel_hits_by_channel
from powercontext.builtin.persistence.oceanbase.memory_index import (
    OceanBaseMemoryFTSIndex,
    OceanBaseMemoryVectorIndex,
)
from powercontext.builtin.persistence.tags import memory_tag_parameters, memory_tag_sql

_OCEANBASE_HYBRID_SEARCH_SQL = """
(SELECT 'fts' AS channel,
        f.memory_artifact_id AS memory_artifact_id,
        f.head_revision AS head_revision,
        f.entry_id AS entry_id,
        f.entry_version_id AS entry_version_id,
        v.text AS text,
        NULL AS distance
 FROM pc_memory_entry_heads AS f
 JOIN pc_memory_entry_versions AS v
   ON v.scope_id = f.scope_id
  AND v.memory_artifact_id = f.memory_artifact_id
  AND v.entry_version_id = f.entry_version_id
 WHERE f.scope_id = :scope_id
   AND f.memory_artifact_id IN :memory_artifact_ids
   AND MATCH (f.searchable_text) AGAINST (:analyzed_query)
 ORDER BY MATCH (f.searchable_text) AGAINST (:analyzed_query) DESC,
          f.memory_artifact_id, f.entry_id, f.entry_version_id
 LIMIT :candidate_limit)
UNION ALL
(SELECT 'vector' AS channel,
        m.memory_artifact_id AS memory_artifact_id,
        m.head_revision AS head_revision,
        m.entry_id AS entry_id,
        m.entry_version_id AS entry_version_id,
        v.text AS text,
        l2_distance(m.embedding, :query_vector) AS distance
 FROM pc_memory_vector_entries AS m
 JOIN pc_memory_entry_versions AS v
   ON v.scope_id = m.scope_id
  AND v.memory_artifact_id = m.memory_artifact_id
  AND v.entry_version_id = m.entry_version_id
 WHERE m.scope_id = :scope_id
   AND m.memory_artifact_id IN :memory_artifact_ids
 ORDER BY l2_distance(m.embedding, :query_vector) APPROXIMATE
 LIMIT :candidate_limit)
"""
# The tagged statement filters both branches in the database and ranks the
# vector branch exactly: an approximate top-k truncates the candidate set
# before the tag predicate applies, so it would drop legitimate candidates.
# Breaking the remaining ties by entry id reproduces the frozen tagged order.
_OCEANBASE_HYBRID_TAGGED_SEARCH_SQL = _OCEANBASE_HYBRID_SEARCH_SQL.replace(
    " ORDER BY MATCH (f.searchable_text) AGAINST (:analyzed_query) DESC,",
    memory_tag_sql("f") + " ORDER BY MATCH (f.searchable_text) AGAINST (:analyzed_query) DESC,",
).replace(
    " ORDER BY l2_distance(m.embedding, :query_vector) APPROXIMATE",
    memory_tag_sql("m") + " ORDER BY l2_distance(m.embedding, :query_vector), m.entry_id",
)


class OceanBaseHybridMemoryIndex:
    """Coordinate FULLTEXT and vector candidates through one native statement.

    The hybrid statement returns both channels' candidates in one round trip.
    Each branch keeps the filters, the ordering and the candidate limit of the
    statement it replaces, so the rows of one channel arrive in exactly the
    order the separate per-channel statements produced. There is no outer
    ``ORDER BY``: the branches stay contiguous and each keeps the ranking its
    own native index produced, which an outer sort would not preserve.

    The coordinator owns the hybrid query only. Single-channel searches keep
    running through the index that owns them, so their statements and their
    observable behavior are unchanged.
    """

    def __init__(
        self,
        fts_index: OceanBaseMemoryFTSIndex,
        vector_index: OceanBaseMemoryVectorIndex | None = None,
    ) -> None:
        self.fts_index = fts_index
        self.vector_index = vector_index
        self.profile = vector_index.profile if vector_index is not None else None
        self.capabilities = MemoryCapabilities(
            fts=True,
            vector=vector_index is not None,
            hybrid=vector_index is not None,
            tag_filter=True,
            embedding_profile=self.profile,
        )
        self.tables: tuple[Table, ...] = fts_index.tables + (vector_index.tables if vector_index is not None else ())
        self._search_sql = self._bind(_OCEANBASE_HYBRID_SEARCH_SQL)
        self._tagged_search_sql = self._bind(_OCEANBASE_HYBRID_TAGGED_SEARCH_SQL)

    def _bind(self, statement: str) -> TextClause:
        """Bind the shared parameters of the hybrid statement.

        The same expanding parameters occur in both branches; SQLAlchemy
        expands and duplicates them per occurrence, so each branch filters on
        the same scope, artifact set and tag set.
        """

        binds = [bindparam("memory_artifact_ids", expanding=True)]
        if ":tag_keys" in statement:
            binds.append(bindparam("tag_keys", expanding=True))
            binds.append(bindparam("tag_hashes", expanding=True))
        if self.profile is not None:
            binds.append(bindparam("query_vector", type_=VECTOR(self.profile.dimension)))
        return text(statement).bindparams(*binds)

    async def initialize(self, connection: AsyncConnection, /) -> None:
        await self.fts_index.initialize(connection)
        if self.vector_index is not None:
            await self.vector_index.initialize(connection)

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memory_ref: ArtifactRef,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> None:
        await self.fts_index.replace(connection, scope_id, memory_ref, projections)
        if self.vector_index is not None:
            await self.vector_index.replace(connection, scope_id, memory_ref, projections)

    async def delete(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memory_ref: ArtifactRef,
        entry_ids: tuple[str, ...],
        /,
    ) -> None:
        await self.fts_index.delete(connection, scope_id, memory_ref, entry_ids)
        if self.vector_index is not None:
            await self.vector_index.delete(connection, scope_id, memory_ref, entry_ids)

    async def upsert(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memory_ref: ArtifactRef,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> None:
        await self.fts_index.upsert(connection, scope_id, memory_ref, projections)
        if self.vector_index is not None:
            await self.vector_index.upsert(connection, scope_id, memory_ref, projections)

    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: MemorySearchRequest,
        /,
    ) -> MemorySearchChannels:
        if request.mode == "fts":
            return await self.fts_index.search(connection, scope_id, request)
        if request.mode == "vector":
            if self.vector_index is None:
                raise CapabilityNotSupportedError("vector")
            return await self.vector_index.search(connection, scope_id, request)
        if request.mode != "hybrid":
            return MemorySearchChannels()
        if self.vector_index is None or self.profile is None:
            raise CapabilityNotSupportedError("hybrid")
        if request.embedding_profile != self.profile or request.query_vector is None:
            raise CapabilityNotSupportedError("embedding-profile")
        if not await self.vector_index.vector_complete(connection, scope_id, request.memories, self.profile):
            raise CapabilityNotSupportedError("vector")
        rows = (
            await connection.execute(
                self._search_sql if request.tag_filter is None else self._tagged_search_sql,
                {
                    "scope_id": scope_id,
                    "memory_artifact_ids": tuple(memory.artifact_id for memory in request.memories),
                    "analyzed_query": request.analyzed_query,
                    "query_vector": validate_embedding(
                        request.query_vector,
                        dimension=self.profile.dimension,
                    ),
                    "candidate_limit": request.candidate_limit,
                    **memory_tag_parameters(request.tag_filter),
                },
            )
        ).mappings()
        return memory_channel_hits_by_channel(rows, request.memories)

    async def vector_complete(
        self,
        connection: AsyncConnection,
        scope_id: str,
        memories: tuple[ArtifactRef, ...],
        profile: EmbeddingProfile,
        /,
    ) -> bool:
        if self.vector_index is None:
            return False
        return await self.vector_index.vector_complete(connection, scope_id, memories, profile)

    async def hydrate(
        self,
        connection: AsyncConnection,
        scope_id: str,
        projections: tuple[MemoryProjection, ...],
        /,
    ) -> tuple[MemoryProjection, ...]:
        # Only the vector index carries embeddings; the FULLTEXT index reads
        # the relational projection and hydrates nothing.
        if self.vector_index is None:
            return projections
        return await self.vector_index.hydrate(connection, scope_id, projections)
