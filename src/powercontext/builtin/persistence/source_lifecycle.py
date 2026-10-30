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

"""Relational Source lifecycle bindings, dependencies, and accepted events."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import (
    InvalidRepositoryArgumentError,
    InvalidStoredColumnError,
    RepositoryNotFoundError,
)
from powercontext.builtin.persistence.tables import (
    SOURCE_LIFECYCLE_EDGES_TABLE,
    SOURCE_LIFECYCLE_EVENTS_TABLE,
    SOURCE_LIFECYCLE_NODES_TABLE,
)
from powercontext.limits import MAX_SOURCE_LIFECYCLE_NODE_ID_LENGTH

SourceLifecycleNodeKind = Literal["source", "memory", "topic"]
SourceLifecyclePolicy = Literal["all", "any"]

SOURCE_LIFECYCLE_KINDS: frozenset[str] = frozenset({"source", "memory", "topic"})
SOURCE_LIFECYCLE_POLICIES: frozenset[str] = frozenset({"all", "any"})


@dataclass(frozen=True, slots=True)
class SourceLifecycleNode:
    """One registered lifecycle node and the binding it owns."""

    node_id: str
    kind: SourceLifecycleNodeKind
    available: bool
    policy: SourceLifecyclePolicy | None
    revision: int | None
    sequence: int | None
    source_type: str | None
    source_id: str | None
    reference: bytes | None
    parents: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceLifecycleEventRecord:
    """One accepted lifecycle event and whether its propagation completed."""

    node_id: str
    sequence: int
    revision: int
    available: bool
    reason: str
    applied: bool


@dataclass(frozen=True, slots=True)
class SourceLifecycleState:
    """The complete registered lifecycle graph of one scope."""

    nodes: Mapping[str, SourceLifecycleNode]

    def node(self, node_id: str) -> SourceLifecycleNode | None:
        return self.nodes.get(node_id)


class SourceLifecycleRepository:
    """Persist Source lifecycle state so a restart observes the same bindings."""

    def __init__(self, database: AsyncDatabase) -> None:
        self._database = database

    async def load(self, scope_id: str) -> SourceLifecycleState:
        """Return every registered node of one scope with its dependencies."""

        _require_scope(scope_id)
        async with self._database.transaction() as connection:
            node_rows = (
                await connection.execute(
                    select(SOURCE_LIFECYCLE_NODES_TABLE).where(SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope_id)
                )
            ).mappings()
            edge_rows = (
                await connection.execute(
                    select(SOURCE_LIFECYCLE_EDGES_TABLE)
                    .where(SOURCE_LIFECYCLE_EDGES_TABLE.c.scope_id == scope_id)
                    .order_by(SOURCE_LIFECYCLE_EDGES_TABLE.c.ordinal)
                )
            ).mappings()
            parents: dict[str, list[str]] = {}
            for row in edge_rows:
                parents.setdefault(str(row["node_id"]), []).append(str(row["parent_id"]))
            nodes: dict[str, SourceLifecycleNode] = {}
            for row in node_rows:
                node_id = str(row["node_id"])
                nodes[node_id] = SourceLifecycleNode(
                    node_id=node_id,
                    kind=_node_kind(str(row["kind"])),
                    available=bool(row["available"]),
                    policy=_node_policy(row["policy"]),
                    revision=None if row["revision"] is None else int(row["revision"]),
                    sequence=None if row["sequence"] is None else int(row["sequence"]),
                    source_type=None if row["source_type"] is None else str(row["source_type"]),
                    source_id=None if row["source_id"] is None else str(row["source_id"]),
                    reference=None if row["reference"] is None else bytes(row["reference"]),
                    parents=tuple(parents.get(node_id, ())),
                )
        return SourceLifecycleState(nodes=nodes)

    async def register(self, scope_id: str, node: SourceLifecycleNode) -> None:
        """Insert one node and its ordered dependency edges."""

        _require_scope(scope_id)
        _require_node_id(node.node_id)
        async with self._database.transaction() as connection:
            await connection.execute(
                insert(SOURCE_LIFECYCLE_NODES_TABLE).values(
                    scope_id=scope_id,
                    node_id=node.node_id,
                    kind=node.kind,
                    available=node.available,
                    policy=node.policy,
                    revision=node.revision,
                    sequence=node.sequence,
                    source_type=node.source_type,
                    source_id=node.source_id,
                    reference=node.reference,
                )
            )
            if node.parents:
                await connection.execute(
                    insert(SOURCE_LIFECYCLE_EDGES_TABLE),
                    [
                        {
                            "scope_id": scope_id,
                            "node_id": node.node_id,
                            "parent_id": parent,
                            "ordinal": ordinal,
                        }
                        for ordinal, parent in enumerate(node.parents)
                    ],
                )

    async def record_event(self, scope_id: str, event: SourceLifecycleEventRecord) -> None:
        """Record one accepted event before its propagation starts.

        The event is stored as not applied, so a propagation that fails halfway
        can be retried with the same event instead of being skipped.
        """

        _require_scope(scope_id)
        _require_node_id(event.node_id)
        async with self._database.transaction() as connection:
            await connection.execute(
                insert(SOURCE_LIFECYCLE_EVENTS_TABLE).values(
                    scope_id=scope_id,
                    node_id=event.node_id,
                    sequence=event.sequence,
                    revision=event.revision,
                    available=event.available,
                    reason=event.reason,
                    applied=event.applied,
                )
            )

    async def event(
        self,
        scope_id: str,
        node_id: str,
        sequence: int,
    ) -> SourceLifecycleEventRecord | None:
        """Return one recorded event, or ``None`` when it was never accepted."""

        _require_scope(scope_id)
        _require_node_id(node_id)
        async with self._database.transaction() as connection:
            row = (
                (
                    await connection.execute(
                        select(SOURCE_LIFECYCLE_EVENTS_TABLE).where(
                            SOURCE_LIFECYCLE_EVENTS_TABLE.c.scope_id == scope_id,
                            SOURCE_LIFECYCLE_EVENTS_TABLE.c.node_id == node_id,
                            SOURCE_LIFECYCLE_EVENTS_TABLE.c.sequence == sequence,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        return SourceLifecycleEventRecord(
            node_id=str(row["node_id"]),
            sequence=int(row["sequence"]),
            revision=int(row["revision"]),
            available=bool(row["available"]),
            reason=str(row["reason"]),
            applied=bool(row["applied"]),
        )

    async def complete(
        self,
        scope_id: str,
        *,
        source_node_id: str,
        revision: int,
        sequence: int,
        availability: Mapping[str, bool],
    ) -> None:
        """Record one completed propagation as a single durable transition."""

        _require_scope(scope_id)
        _require_node_id(source_node_id)
        async with self._database.transaction() as connection:
            result = await connection.execute(
                update(SOURCE_LIFECYCLE_NODES_TABLE)
                .where(
                    SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope_id,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == source_node_id,
                )
                .values(revision=revision, sequence=sequence, available=availability[source_node_id])
            )
            if result.rowcount == 0:
                raise RepositoryNotFoundError("source-lifecycle-node", (scope_id, source_node_id))
            for node_id, available in availability.items():
                if node_id == source_node_id:
                    continue
                _require_node_id(node_id)
                await connection.execute(
                    update(SOURCE_LIFECYCLE_NODES_TABLE)
                    .where(
                        SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope_id,
                        SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == node_id,
                    )
                    .values(available=available)
                )
            await connection.execute(
                update(SOURCE_LIFECYCLE_EVENTS_TABLE)
                .where(
                    SOURCE_LIFECYCLE_EVENTS_TABLE.c.scope_id == scope_id,
                    SOURCE_LIFECYCLE_EVENTS_TABLE.c.node_id == source_node_id,
                    SOURCE_LIFECYCLE_EVENTS_TABLE.c.sequence <= sequence,
                    SOURCE_LIFECYCLE_EVENTS_TABLE.c.applied.is_(False),
                )
                .values(applied=True)
            )

    async def unavailable_topic_artifact_ids(
        self,
        connection: AsyncConnection,
        scope_id: str,
    ) -> frozenset[str]:
        """Return the Topic artifacts this scope may no longer recall."""

        _require_scope(scope_id)
        rows = (
            await connection.execute(
                select(SOURCE_LIFECYCLE_NODES_TABLE.c.reference).where(
                    SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope_id,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.kind == "topic",
                    SOURCE_LIFECYCLE_NODES_TABLE.c.available.is_(False),
                )
            )
        ).mappings()
        artifact_ids: set[str] = set()
        for row in rows:
            payload = row["reference"]
            if payload is None:
                continue
            artifact_id = _reference_artifact_id(bytes(payload))
            if artifact_id is not None:
                artifact_ids.add(artifact_id)
        return frozenset(artifact_ids)


def _node_kind(value: str) -> SourceLifecycleNodeKind:
    """Read one stored lifecycle node kind."""

    if value not in SOURCE_LIFECYCLE_KINDS:
        raise InvalidStoredColumnError("kind", "a known lifecycle node kind")
    return cast(SourceLifecycleNodeKind, value)


def _node_policy(value: object) -> SourceLifecyclePolicy | None:
    """Read one stored lifecycle dependency policy."""

    if value is None:
        return None
    if value not in SOURCE_LIFECYCLE_POLICIES:
        raise InvalidStoredColumnError("policy", "a known lifecycle policy")
    return cast(SourceLifecyclePolicy, value)


def _reference_artifact_id(payload: bytes) -> str | None:
    """Read the artifact identity from one canonical reference payload."""

    try:
        decoded = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(decoded, dict):
        return None
    artifact_id = decoded.get("artifact_id")
    return artifact_id if isinstance(artifact_id, str) else None


def _require_scope(scope_id: str) -> None:
    if not isinstance(scope_id, str) or not scope_id:
        raise InvalidRepositoryArgumentError("scope_id", "must be a non-empty string")


def _require_node_id(node_id: str) -> None:
    if not isinstance(node_id, str) or not node_id:
        raise InvalidRepositoryArgumentError("node_id", "must be a non-empty string")
    if len(node_id) > MAX_SOURCE_LIFECYCLE_NODE_ID_LENGTH:
        raise InvalidRepositoryArgumentError(
            "node_id",
            f"must not exceed {MAX_SOURCE_LIFECYCLE_NODE_ID_LENGTH} characters",
        )
