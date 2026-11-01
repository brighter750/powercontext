"""An in-memory stand-in for the PowerContext HTTP client.

The fake mirrors the real client's semantics closely enough to exercise the
repository's scope lifecycle and create-or-revise behaviour:

* every ``remember`` creates a new Atomic Memory artifact with its own id;
* ``revise_artifact`` bumps the revision of one artifact and replaces its text;
* scopes are isolated per learner and reachable through their external reference.

These tests verify StudyFlow's own logic. They are **not** evidence that the real
PowerContext Server behaves this way — that is covered by the integration tests in
``test_integration_powercontext.py``.
"""

from __future__ import annotations

import itertools
from typing import Any

from app.powercontext.client import MemoryEntry, ScopeInfo


class FakePowerContextClient:
    """A deterministic in-memory PowerContext double."""

    def __init__(self) -> None:
        self.scopes: dict[str, dict[str, Any]] = {}
        self.entries: dict[str, dict[str, MemoryEntry]] = {}
        self.bindings: dict[tuple[str, str, str], str] = {}
        self._scope_ids = itertools.count(1)
        self._artifact_ids = itertools.count(1)
        self.calls: list[str] = []

    # --- health ---------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        """Pretend the Server is healthy."""
        self.calls.append("health")
        return {"status": "ready"}

    async def server_info(self) -> dict[str, Any]:
        """Pretend the Server reports a v1.4 contract."""
        self.calls.append("server_info")
        return {"package_version": "fake", "api_contract_version": {"major": 1, "minor": 4}}

    # --- scopes ---------------------------------------------------------

    async def create_scope(
        self,
        *,
        title: str,
        summary: str,
        external_references: list[dict[str, str]],
        idempotency_key: str,
    ) -> ScopeInfo:
        """Create a Scope."""
        self.calls.append("create_scope")
        scope_id = f"scp_fake_{next(self._scope_ids)}"
        self.scopes[scope_id] = {
            "scope_id": scope_id,
            "title": title,
            "summary": summary,
            "version": 1,
            "external_references": list(external_references),
        }
        self.entries[scope_id] = {}
        return ScopeInfo(scope_id=scope_id, title=title, summary=summary, version=1)

    async def find_scope_by_external_reference(
        self,
        *,
        kind: str,
        value: str,
    ) -> ScopeInfo | None:
        """Find a Scope by one of its external references."""
        self.calls.append("find_scope_by_external_reference")
        for scope in self.scopes.values():
            for reference in scope["external_references"]:
                if reference["kind"] == kind and reference["value"] == value:
                    return ScopeInfo(
                        scope_id=scope["scope_id"],
                        title=scope["title"],
                        summary=scope["summary"],
                        version=scope["version"],
                    )
        return None

    async def get_scope(self, scope_id: str) -> ScopeInfo:
        """Read one Scope."""
        scope = self.scopes[scope_id]
        return ScopeInfo(
            scope_id=scope_id,
            title=scope["title"],
            summary=scope["summary"],
            version=scope["version"],
        )

    async def set_scope_binding(
        self,
        *,
        integration: str,
        kind: str,
        external_id: str,
        scope_id: str,
    ) -> None:
        """Bind an external identity to a Scope."""
        self.calls.append("set_scope_binding")
        self.bindings[(integration, kind, external_id)] = scope_id

    async def resolve_scope_binding(
        self,
        *,
        integration: str,
        kind: str,
        external_id: str,
    ) -> ScopeInfo | None:
        """Resolve the Scope bound to an external identity."""
        self.calls.append("resolve_scope_binding")
        scope_id = self.bindings.get((integration, kind, external_id))
        if scope_id is None or scope_id not in self.scopes:
            return None
        scope = self.scopes[scope_id]
        return ScopeInfo(
            scope_id=scope_id,
            title=scope["title"],
            summary=scope["summary"],
            version=scope["version"],
        )

    # --- memory ---------------------------------------------------------

    async def remember(
        self,
        *,
        scope_id: str,
        kind: str,
        text: str,
        reason: str | None = None,
    ) -> list[MemoryEntry]:
        """Store a new Atomic Memory artifact."""
        self.calls.append("remember")
        artifact_id = f"art_fake_{next(self._artifact_ids)}"
        entry = MemoryEntry(
            artifact_id=artifact_id,
            revision=1,
            kind=kind,
            text=text,
            state="active",
        )
        self.entries.setdefault(scope_id, {})[artifact_id] = entry
        return [entry]

    async def list_memory_entries(
        self,
        *,
        scope_id: str,
        limit: int = 100,
        include_inactive: bool = False,
    ) -> list[MemoryEntry]:
        """List the Atomic Memory entries of one Scope."""
        self.calls.append("list_memory_entries")
        entries = list(self.entries.get(scope_id, {}).values())
        return entries[:limit]

    async def search_memory(
        self,
        *,
        scope_id: str,
        query: str,
        limit: int = 10,
    ) -> list[tuple[MemoryEntry, float]]:
        """Search by a crude substring match, like the Server's text mode."""
        self.calls.append("search_memory")
        hits: list[tuple[MemoryEntry, float]] = []
        for entry in self.entries.get(scope_id, {}).values():
            if query.lower() in entry.text.lower():
                hits.append((entry, 1.0))
        return hits[:limit]

    async def get_artifact(self, *, scope_id: str, artifact_id: str) -> tuple[str, str]:
        """Read one Atomic Memory artifact."""
        self.calls.append("get_artifact")
        entry = self.entries[scope_id][artifact_id]
        return entry.text, f'"revision:{entry.revision}"'

    async def revise_artifact(
        self,
        *,
        scope_id: str,
        artifact_id: str,
        kind: str,
        text: str,
        if_match: str,
    ) -> int:
        """Replace an Atomic Memory artifact and bump its revision."""
        self.calls.append("revise_artifact")
        entry = self.entries[scope_id][artifact_id]
        entry.revision += 1
        entry.text = text
        entry.kind = kind
        return entry.revision

    async def aclose(self) -> None:
        """Nothing to release."""
        self.calls.append("aclose")


__all__ = ["FakePowerContextClient"]
