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

"""Source lifecycle behaviour of the built-in Runtime.

The tests cover the lifecycle contract end to end: strict event validation,
dependency registration, availability propagation into the native Memory and
Topic Memory paths, reference purposes, restart durability, interrupted
propagation, and scope isolation.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.artifacts.memory.service import MemoryService
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    RememberMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.source_lifecycle import SourceLifecycleError, SourceLifecycleEvent
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import SourceRef


def _config(tmp_path: Path) -> BuiltinConfig:
    return BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'lifecycle.db'}"))


def _event(
    node_id: str, sequence: int, available: bool, *, revision: int = 3, reason: str = "controlled"
) -> SourceLifecycleEvent:
    return SourceLifecycleEvent(
        node_id=node_id,
        reason=reason,
        revision=revision,
        sequence=sequence,
        available=available,
    )


async def _scope(runtime, key: str):
    return await runtime.scopes.create(
        ScopeDraft(title="Lifecycle", summary="Dependency evidence", idempotency_key=key)
    )


async def _source(runtime, scope_id: str, node_id: str):
    receipt = await runtime.sources.for_scope(scope_id).capture(
        CaptureSource(source_id=node_id, content=f"Origin {node_id}", metadata={})
    )
    return receipt.source_ref


async def _memory(runtime, scope_id: str, text: str):
    created = await runtime.memory.for_scope(scope_id).remember(
        RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text=text),))
    )
    assert created.entry is not None
    return created.entry.citation


async def _topic(runtime, scope_id: str, title: str):
    created = await runtime.records.for_scope(scope_id).create_artifact(
        "topic-memory",
        ArtifactWrite(
            content={
                "title": title,
                "summary": f"{title} supported summary",
                "detail": f"{title} detailed evidence",
            }
        ),
    )
    return ArtifactRef(family="topic-memory", artifact_id=created.artifact_id, revision=created.revision)


async def _build(runtime, key: str = "primary"):
    """Register two Sources, two Memory entries, and one Topic Memory artifact."""

    scope = await _scope(runtime, key)
    lifecycle = runtime.lifecycle.for_scope(scope.scope_id)
    root_refs = {}
    for node_id in ("root0", "root1"):
        root_refs[node_id] = await _source(runtime, scope.scope_id, node_id)
        await lifecycle.register_source(node_id, root_refs[node_id], revision=3)
    citations = {
        "memory-a": await _memory(runtime, scope.scope_id, "memory-a fixture evidence"),
        "memory-b": await _memory(runtime, scope.scope_id, "memory-b fixture evidence"),
    }
    await lifecycle.register_memory("memory-a", citations["memory-a"], parents=("root0",), policy="all")
    await lifecycle.register_memory("memory-b", citations["memory-b"], parents=("root1", "root0"), policy="any")
    topic_ref = await _topic(runtime, scope.scope_id, "topic-a")
    await lifecycle.register_topic("topic-a", topic_ref, parents=("memory-a",), policy="all")
    return scope.scope_id, lifecycle, root_refs, citations, topic_ref


def test_event_accepts_a_valid_payload() -> None:
    event = _event("root0", 1, False)
    assert event.node_id == "root0"
    assert event.revision == 3
    assert event.sequence == 1
    assert event.available is False


@pytest.mark.parametrize(
    "payload",
    [
        {"node_id": "", "reason": "r", "revision": 1, "sequence": 1, "available": True},
        {"node_id": "   ", "reason": "r", "revision": 1, "sequence": 1, "available": True},
        {"node_id": "n", "reason": "", "revision": 1, "sequence": 1, "available": True},
        {"node_id": "n", "reason": "   ", "revision": 1, "sequence": 1, "available": True},
        {"node_id": "n", "reason": "r", "revision": 0, "sequence": 1, "available": True},
        {"node_id": "n", "reason": "r", "revision": -1, "sequence": 1, "available": True},
        {"node_id": "n", "reason": "r", "revision": 1, "sequence": 0, "available": True},
        {"node_id": "n", "reason": "r", "revision": 1, "sequence": -1, "available": True},
        {"node_id": "n", "reason": "r", "revision": True, "sequence": 1, "available": True},
        {"node_id": "n", "reason": "r", "revision": 1, "sequence": True, "available": True},
        {"node_id": "n", "reason": "r", "revision": 1, "sequence": 1, "available": "yes"},
        {"node_id": "n", "reason": "r", "revision": 1, "sequence": 1, "available": 1},
        {"node_id": "n", "reason": "r", "revision": 1, "sequence": 1, "available": True, "extra": 1},
    ],
)
def test_event_rejects_invalid_fields(payload: dict[str, object]) -> None:
    with pytest.raises(SourceLifecycleError):
        SourceLifecycleEvent(**payload)
    with pytest.raises(ValidationError):
        SourceLifecycleEvent(**payload)
    with pytest.raises(ValueError):
        SourceLifecycleEvent(**payload)


def test_registration_identity_is_idempotent_and_conflicting_registrations_fail(tmp_path) -> None:
    async def run():
        async with open_builtin_runtime(_config(tmp_path)) as runtime:
            _scope, lifecycle, root_refs, citations, topic_ref = await _build(runtime)
            # The same binding is idempotent.
            await lifecycle.register_source("root0", root_refs["root0"], revision=3)
            await lifecycle.register_memory("memory-a", citations["memory-a"], parents=("root0",), policy="all")
            await lifecycle.register_topic("topic-a", topic_ref, parents=("memory-a",), policy="all")
            # A different binding for the same identity conflicts.
            with pytest.raises(SourceLifecycleError):
                await lifecycle.register_source("root0", root_refs["root1"], revision=3)
            with pytest.raises(SourceLifecycleError):
                await lifecycle.register_source("root0", root_refs["root0"], revision=4)
            with pytest.raises(SourceLifecycleError):
                await lifecycle.register_memory("memory-a", citations["memory-a"], parents=("root1",), policy="all")
            with pytest.raises(SourceLifecycleError):
                await lifecycle.register_memory("memory-a", citations["memory-b"], parents=("root0",), policy="all")
            with pytest.raises(SourceLifecycleError):
                await lifecycle.register_topic("topic-a", topic_ref, parents=("memory-a",), policy="any")

    asyncio.run(run())


def test_registration_requires_readable_references(tmp_path) -> None:
    async def run():
        async with open_builtin_runtime(_config(tmp_path)) as runtime:
            _scope, lifecycle, _root_refs, citations, _topic_ref = await _build(runtime)
            with pytest.raises(SourceLifecycleError):
                await lifecycle.register_source(
                    "ghost",
                    SourceRef(source_type="content", source_id="never-captured"),
                    revision=3,
                )
            with pytest.raises(SourceLifecycleError):
                await lifecycle.register_memory(
                    "ghost-memory",
                    citations["memory-a"].model_copy(update={"entry_id": "never-stored"}),
                    parents=("root0",),
                    policy="all",
                )
            with pytest.raises(SourceLifecycleError):
                await lifecycle.register_topic(
                    "ghost-topic",
                    ArtifactRef(family="topic-memory", artifact_id="never-created", revision=1),
                    parents=("root0",),
                    policy="all",
                )

    asyncio.run(run())


def test_registration_rejects_invalid_dependencies(tmp_path) -> None:
    async def run():
        async with open_builtin_runtime(_config(tmp_path)) as runtime:
            _scope, lifecycle, _root_refs, citations, _topic_ref = await _build(runtime)
            with pytest.raises(SourceLifecycleError):
                await lifecycle.register_memory("missing", citations["memory-a"], parents=("root9",), policy="all")
            with pytest.raises(SourceLifecycleError):
                await lifecycle.register_memory(
                    "repeated", citations["memory-a"], parents=("root0", "root0"), policy="all"
                )
            with pytest.raises(SourceLifecycleError):
                await lifecycle.register_memory("self", citations["memory-a"], parents=("self",), policy="all")
            with pytest.raises(SourceLifecycleError):
                await lifecycle.register_memory("empty", citations["memory-a"], parents=(), policy="all")
            with pytest.raises(SourceLifecycleError):
                await lifecycle.register_memory("policy", citations["memory-a"], parents=("root0",), policy="some")
            with pytest.raises(SourceLifecycleError):
                await lifecycle.register_memory("cycle-a", citations["memory-a"], parents=("cycle-b",), policy="all")

    asyncio.run(run())


def test_registration_computes_state_immediately(tmp_path) -> None:
    async def run():
        async with open_builtin_runtime(_config(tmp_path)) as runtime:
            scope, lifecycle, _root_refs, _citations, _topic_ref = await _build(runtime)
            await lifecycle.apply(_event("root0", 1, False))
            # A Memory registered under withdrawn evidence is unavailable at once.
            late = await _memory(runtime, scope, "late fixture evidence")
            await lifecycle.register_memory("late", late, parents=("root0",), policy="all")
            entries = await runtime.memory.for_scope(scope).list(include_inactive=True)
            states = {record.entry.text.split()[0]: record.state for record in entries.entries}
            assert states["late"] == "inactive"
            # A Topic registered under an unavailable Memory is unavailable too.
            late_topic = await _topic(runtime, scope, "late-topic")
            await lifecycle.register_topic("late-topic", late_topic, parents=("late",), policy="all")
            with pytest.raises(SourceLifecycleError):
                await lifecycle.resolve("late-topic", purpose="generation")
            assert await lifecycle.resolve("late-topic", purpose="audit") == late_topic
            # A Memory registered under available evidence stays usable.
            fresh = await _memory(runtime, scope, "fresh fixture evidence")
            await lifecycle.register_memory("fresh", fresh, parents=("root1",), policy="all")
            assert await lifecycle.resolve("fresh", purpose="generation") == fresh

    asyncio.run(run())


def test_event_revision_and_sequence_rules(tmp_path) -> None:
    async def run():
        async with open_builtin_runtime(_config(tmp_path)) as runtime:
            _scope, lifecycle, _root_refs, _citations, _topic_ref = await _build(runtime)
            with pytest.raises(SourceLifecycleError):
                await lifecycle.apply(_event("unknown", 1, False))
            with pytest.raises(SourceLifecycleError):
                await lifecycle.apply(_event("root0", 1, False, revision=2))
            with pytest.raises(SourceLifecycleError):
                await lifecycle.apply(_event("root0", 1, False, revision=4))
            await lifecycle.apply(_event("root0", 2, False))
            # A lower sequence is rejected.
            with pytest.raises(SourceLifecycleError):
                await lifecycle.apply(_event("root0", 1, True))
            # The same sequence with a different payload is rejected.
            with pytest.raises(SourceLifecycleError):
                await lifecycle.apply(_event("root0", 2, True))
            # The same sequence with the same payload is idempotent.
            assert await lifecycle.apply(_event("root0", 2, False)) == await lifecycle.apply(_event("root0", 2, False))

    asyncio.run(run())


def test_propagation_follows_all_and_any_policies(tmp_path) -> None:
    async def run():
        async with open_builtin_runtime(_config(tmp_path)) as runtime:
            scope, lifecycle, _root_refs, _citations, _topic_ref = await _build(runtime)
            assert await lifecycle.apply(_event("root0", 1, False)) == [
                "memory-b",
                "root1",
            ]
            # memory-a requires every parent, memory-b only one of them, and the
            # Topic follows its only parent.
            with pytest.raises(SourceLifecycleError):
                await lifecycle.resolve("memory-a", purpose="generation")
            assert await lifecycle.resolve("memory-b", purpose="generation") is not None
            with pytest.raises(SourceLifecycleError):
                await lifecycle.resolve("topic-a", purpose="generation")
            # The withdrawn evidence leaves the native Memory state.
            entries = await runtime.memory.for_scope(scope).list(include_inactive=True)
            states = {record.entry.text.split()[0]: record.state for record in entries.entries}
            assert states["memory-a"] == "inactive"
            assert states["memory-b"] == "active"
            # Restoring the Source restores the dependent content.
            await lifecycle.apply(_event("root0", 2, True))
            assert await lifecycle.resolve("memory-a", purpose="generation") is not None
            entries = await runtime.memory.for_scope(scope).list(include_inactive=True)
            states = {record.entry.text.split()[0]: record.state for record in entries.entries}
            assert states["memory-a"] == "active"

    asyncio.run(run())


def test_withdrawn_topic_leaves_search_and_context_but_stays_readable(tmp_path) -> None:
    async def run():
        async with open_builtin_runtime(_config(tmp_path)) as runtime:
            scope, lifecycle, _root_refs, _citations, topic_ref = await _build(runtime)
            await lifecycle.apply(_event("root0", 1, False))
            from powercontext.builtin.runtime import SearchTopicMemoryRequest

            page = await runtime.topic_memory.for_scope(scope).search(
                SearchTopicMemoryRequest(query="topic-a", limit=20)
            )
            assert not page.hits
            record = await runtime.records.for_scope(scope).get_artifact_revision(
                "topic-memory", topic_ref.artifact_id, topic_ref.revision
            )
            assert record.content["title"] == "topic-a"

    asyncio.run(run())


def test_resolve_separates_generation_from_audit(tmp_path) -> None:
    async def run():
        async with open_builtin_runtime(_config(tmp_path)) as runtime:
            _scope, lifecycle, root_refs, citations, topic_ref = await _build(runtime)
            assert await lifecycle.resolve("root0", purpose="generation") == root_refs["root0"]
            assert await lifecycle.resolve("memory-a", purpose="generation") == citations["memory-a"]
            assert await lifecycle.resolve("topic-a", purpose="audit") == topic_ref
            with pytest.raises(SourceLifecycleError):
                await lifecycle.resolve("unknown-node", purpose="generation")
            with pytest.raises(SourceLifecycleError):
                await lifecycle.resolve("memory-a", purpose="something-else")
            await lifecycle.apply(_event("root0", 1, False))
            with pytest.raises(SourceLifecycleError):
                await lifecycle.resolve("memory-a", purpose="generation")
            # Audit still reads the historical body.
            assert await lifecycle.resolve("memory-a", purpose="audit") == citations["memory-a"]

    asyncio.run(run())


def test_state_survives_a_restart(tmp_path) -> None:
    async def run():
        config = _config(tmp_path)
        async with open_builtin_runtime(config) as runtime:
            scope, lifecycle, _root_refs, citations, _topic_ref = await _build(runtime)
            await lifecycle.apply(_event("root0", 1, False))
        async with open_builtin_runtime(config) as runtime:
            lifecycle = runtime.lifecycle.for_scope(scope)
            with pytest.raises(SourceLifecycleError):
                await lifecycle.resolve("memory-a", purpose="generation")
            assert await lifecycle.resolve("memory-a", purpose="audit") == citations["memory-a"]
            # The recorded sequence and revision survive the restart.
            with pytest.raises(SourceLifecycleError):
                await lifecycle.apply(_event("root0", 1, True))
            with pytest.raises(SourceLifecycleError):
                await lifecycle.apply(_event("root0", 1, False, revision=4))
            await lifecycle.apply(_event("root0", 2, True))
            assert await lifecycle.resolve("memory-a", purpose="generation") == citations["memory-a"]

    asyncio.run(run())


def test_interrupted_propagation_is_retried_without_extra_revisions(tmp_path, monkeypatch) -> None:
    async def run():
        async with open_builtin_runtime(_config(tmp_path)) as runtime:
            scope, lifecycle, _root_refs, citations, _topic_ref = await _build(runtime)

            def failing_forget(self, memory, *, entries, reason=None):
                raise RuntimeError("native memory write failed")  # noqa: TRY003

            monkeypatch.setattr(MemoryService, "forget", failing_forget)
            with pytest.raises(RuntimeError, match="native memory write failed"):
                await lifecycle.apply(_event("root0", 1, False))
            monkeypatch.undo()

            # The same event completes the interrupted propagation.
            await lifecycle.apply(_event("root0", 1, False))
            entries = await runtime.memory.for_scope(scope).list(include_inactive=True)
            states = {record.entry.text.split()[0]: record.state for record in entries.entries}
            assert states["memory-a"] == "inactive"
            with pytest.raises(SourceLifecycleError):
                await lifecycle.resolve("memory-a", purpose="generation")

            # Replaying the successful event adds no Memory revision.
            before = (await runtime.memory.for_scope(scope).list()).memory_ref
            await lifecycle.apply(_event("root0", 1, False))
            assert (await runtime.memory.for_scope(scope).list()).memory_ref == before
            assert await lifecycle.resolve("memory-a", purpose="audit") == citations["memory-a"]

    asyncio.run(run())


def test_scopes_are_independent(tmp_path) -> None:
    async def run():
        async with open_builtin_runtime(_config(tmp_path)) as runtime:
            _first, lifecycle_a, _roots_a, citations_a, _topic_a = await _build(runtime, "first")
            second, lifecycle_b, _roots_b, citations_b, _topic_b = await _build(runtime, "second")
            await lifecycle_a.apply(_event("root0", 1, False))
            with pytest.raises(SourceLifecycleError):
                await lifecycle_a.resolve("memory-a", purpose="generation")
            # The same node identifiers in another scope keep their own state.
            assert await lifecycle_b.resolve("memory-a", purpose="generation") == citations_b["memory-a"]
            assert citations_a["memory-a"] != citations_b["memory-a"]
            entries = await runtime.memory.for_scope(second).list(include_inactive=True)
            states = {record.entry.text.split()[0]: record.state for record in entries.entries}
            assert states["memory-a"] == "active"

    asyncio.run(run())
