"""StudyFlow's repository over PowerContext.

This layer owns two things the raw client should not:

* **Scope lifecycle** — one PowerContext Scope per learner, bound durably to the
  learner's identifier so the mapping survives a backend restart and never has to be
  kept in a local database.
* **Upsert semantics** — a memory entry is created once and *revised* afterwards, so a
  learner's progress updates in place instead of piling up contradictory duplicates.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.memory_document import MemoryDocument
from app.powercontext.client import (
    MemoryEntry,
    PowerContextClient,
    ScopeInfo,
)

LOGGER = logging.getLogger("studyflow.memory")

UpsertAction = str  # "created" | "revised"


@dataclass(slots=True)
class UpsertResult:
    """Outcome of writing one memory document."""

    action: UpsertAction
    entry: MemoryEntry
    document: MemoryDocument


class MemoryRepository:
    """Read/write access to one learner's StudyFlow memory in PowerContext."""

    def __init__(self, client: PowerContextClient) -> None:
        self._client = client

    @property
    def client(self) -> PowerContextClient:
        """The underlying PowerContext client."""
        return self._client

    # --- scope lifecycle -------------------------------------------------

    async def ensure_scope(
        self,
        *,
        user_id: str,
        nickname: str,
        scope_kind: str,
        integration: str,
        binding_kind: str,
    ) -> tuple[ScopeInfo, bool]:
        """Return the learner's Scope, creating and binding it on first use.

        The second element of the tuple reports whether the Scope was created by this
        call.
        """
        scope = await self._client.resolve_scope_binding(
            integration=integration,
            kind=binding_kind,
            external_id=user_id,
        )
        if scope is None:
            scope = await self._client.find_scope_by_external_reference(kind=scope_kind, value=user_id)
        if scope is None:
            scope = await self._client.create_scope(
                title=f"StudyFlow · {nickname or user_id}",
                summary=f"StudyFlow 学习者长期记忆空间（learner_id={user_id}）。",
                external_references=[{"kind": scope_kind, "value": user_id}],
                idempotency_key=f"studyflow-learner-{user_id}",
            )
            created = True
        else:
            created = False
        await self._client.set_scope_binding(
            integration=integration,
            kind=binding_kind,
            external_id=user_id,
            scope_id=scope.scope_id,
        )
        LOGGER.info(
            "scope ready learner=%s scope_id=%s created=%s",
            user_id,
            scope.scope_id,
            created,
        )
        return scope, created

    # --- reads -----------------------------------------------------------

    async def read_documents(self, scope_id: str, *, limit: int = 100) -> list[MemoryDocument]:
        """Read every stored memory document of one learner."""
        entries = await self._client.list_memory_entries(scope_id=scope_id, limit=limit)
        return [MemoryDocument.from_text(entry.text, default_kind=entry.kind) for entry in entries]

    async def read_entries(self, scope_id: str, *, limit: int = 100) -> list[MemoryEntry]:
        """Read the raw Atomic Memory entries of one learner."""
        return await self._client.list_memory_entries(scope_id=scope_id, limit=limit)

    async def search_documents(
        self,
        scope_id: str,
        query: str,
        *,
        limit: int = 10,
    ) -> list[tuple[MemoryDocument, float]]:
        """Search one learner's memory and return the ranked documents."""
        hits = await self._client.search_memory(scope_id=scope_id, query=query, limit=limit)
        return [
            (MemoryDocument.from_text(entry.text, default_kind=entry.kind), score)
            for entry, score in hits
        ]

    # --- writes ----------------------------------------------------------

    async def upsert_document(
        self,
        scope_id: str,
        document: MemoryDocument,
        *,
        reason: str = "studyflow-write",
    ) -> UpsertResult:
        """Create the document, or revise the existing entry that carries the same key.

        Revising keeps one authoritative entry per key, which is what makes a later
        learning activity *update* the learner's remembered state instead of adding a
        contradictory second copy.
        """
        text = document.to_text()
        existing = await self._find_by_key(scope_id, document.key)
        if existing is None:
            entries = await self._client.remember(
                scope_id=scope_id,
                kind=document.kind,
                text=text,
                reason=reason,
            )
            if not entries:
                raise RuntimeError("PowerContext 未返回写入后的记忆记录")
            LOGGER.info(
                "memory created scope=%s key=%s artifact=%s",
                scope_id,
                document.key,
                entries[0].artifact_id,
            )
            return UpsertResult(action="created", entry=entries[0], document=document)

        artifact_id = existing.artifact_id
        _, etag = await self._client.get_artifact(scope_id=scope_id, artifact_id=artifact_id)
        revision = await self._client.revise_artifact(
            scope_id=scope_id,
            artifact_id=artifact_id,
            kind=document.kind,
            text=text,
            if_match=etag,
        )
        LOGGER.info(
            "memory revised scope=%s key=%s artifact=%s revision=%s",
            scope_id,
            document.key,
            artifact_id,
            revision,
        )
        updated = MemoryEntry(
            artifact_id=artifact_id,
            revision=revision,
            kind=document.kind,
            text=text,
            state=existing.state,
            state_version=existing.state_version,
        )
        return UpsertResult(action="revised", entry=updated, document=document)

    async def _find_by_key(self, scope_id: str, key: str) -> MemoryEntry | None:
        entries = await self._client.list_memory_entries(scope_id=scope_id, limit=100)
        for entry in entries:
            document = MemoryDocument.from_text(entry.text, default_kind=entry.kind)
            if document.key == key:
                return entry
        return None


__all__ = ["MemoryRepository", "UpsertResult"]
