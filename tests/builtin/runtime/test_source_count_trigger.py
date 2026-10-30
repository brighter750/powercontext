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

"""Count-triggered admission for Memory and Topic Memory.

The count condition starts processing on its own, so these tests pin the
observable admission decisions: how many pending Source identities a scope
owes a Family, when that count starts the work, and what an explicit request,
a failed Worker, and a restart do to the accepted work.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest
from pydantic import ValidationError

from powercontext.builtin.artifacts.handoff import HandoffContent, HandoffSourceCitation, HandoffStatement
from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.inference import character_token_estimator
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import CaptureSource, open_builtin_runtime
from powercontext.builtin.runtime.artifact_processing import ArtifactProcessingSupervisor
from powercontext.builtin.runtime.composition import _artifact_processing_bindings
from powercontext.builtin.runtime.config import BuiltinConfig, InferenceConfig, RuntimeConfig
from powercontext.builtin.runtime.processing_discovery import SourceProcessingPendingCounter
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import SourceCursor
from powercontext.builtin.triggers import SOURCE_WINDOW_TRIGGER_NAME
from powercontext.sources import SourceRef
from tests.builtin.runtime.test_processing_scheduler import _Launcher
from tests.builtin.runtime.test_statistics import _ContentCandidatePipeline

MEMORY_BINDING = SOURCE_WINDOW_TRIGGER_NAME
TOPIC_BINDING = TOPIC_MEMORY_SOURCE_WINDOW_BINDING


def _config(tmp_path, *, families=("memory",), **runtime):
    return BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'count-trigger.db'}"),
        inference=InferenceConfig(generation_model="test"),
        runtime=RuntimeConfig(artifact_processing_families=families, **runtime),
    )


async def _scope(contexts, name):
    return (await contexts.scopes.create(ScopeDraft(title=name, summary=name, idempotency_key=name))).scope_id


async def _capture(contexts, scope, source_id, content=None):
    return await contexts.records.capture_source(
        scope,
        "content",
        source_id,
        content or f"Recorded evidence {source_id}.",
        {},
    )


async def _intent(database, binding, scope):
    async with database.transaction() as connection:
        return await ArtifactProcessingIntentRepository().load(connection, scope, binding)


async def _cursor(database, binding, scope):
    async with database.transaction() as connection:
        stored = await SourceCursorRepository().load(connection, scope, binding)
    return 0 if stored is None else stored.cursor.sequence


async def _commit_cursor(database, binding, scope, sequence):
    async with database.transaction() as connection:
        await SourceCursorRepository().save(
            connection,
            scope,
            binding,
            SourceCursor(sequence=sequence),
            expected_generation=None,
        )


async def _pending(contexts, binding, scope):
    counter = SourceProcessingPendingCounter(contexts.repositories.sources, binding)
    async with contexts.database.transaction() as connection:
        counts = await counter.pending_counts(connection, (scope,))
    return counts.get(scope, 0)


def _binding(config, contexts, launcher, *, family="memory"):
    bindings = _artifact_processing_bindings(config, contexts, ())
    selected = next(binding for binding in bindings if binding.artifact_family == family)
    return replace(selected, launcher=launcher)


async def _admit(supervisor, state):
    """Run one count-triggered admission pass."""
    await supervisor._discover_counted(state)


async def _dispatch(supervisor, state):
    await supervisor._dispatch(state)
    results = await asyncio.gather(*(worker.task for worker in state.running.values()), return_exceptions=True)
    await supervisor._reap(state)
    return results


def test_source_count_thresholds_default_to_disabled():
    config = RuntimeConfig()
    assert config.memory_source_count_threshold is None
    assert config.topic_memory_source_count_threshold is None


@pytest.mark.parametrize("threshold", [None, 1, 3, 100])
def test_source_count_threshold_accepts_a_positive_integer(threshold: int | None):
    config = RuntimeConfig(
        memory_source_count_threshold=threshold,
        topic_memory_source_count_threshold=threshold,
    )
    assert config.memory_source_count_threshold == threshold
    assert config.topic_memory_source_count_threshold == threshold


@pytest.mark.parametrize(
    "overrides",
    [
        {"memory_source_count_threshold": True},
        {"memory_source_count_threshold": False},
        {"memory_source_count_threshold": 0},
        {"memory_source_count_threshold": -1},
        {"memory_source_count_threshold": 1.0},
        {"memory_source_count_threshold": "3"},
        {"topic_memory_source_count_threshold": True},
        {"topic_memory_source_count_threshold": False},
        {"topic_memory_source_count_threshold": 0},
        {"topic_memory_source_count_threshold": -1},
        {"topic_memory_source_count_threshold": 1.0},
        {"topic_memory_source_count_threshold": "3"},
    ],
)
def test_source_count_threshold_rejects_every_other_number(overrides: dict[str, Any]):
    with pytest.raises(ValidationError):
        RuntimeConfig(**overrides)


def test_count_trigger_waits_for_the_threshold_and_then_accepts_one_invocation(tmp_path):
    async def scenario():
        config = _config(tmp_path, memory_source_count_threshold=3)
        async with SQLiteProfile.open(config.database, tables=BUILTIN_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database)
            scope = await _scope(contexts, "counted")
            await _capture(contexts, scope, "one")
            await _capture(contexts, scope, "two")
            launcher = _Launcher(profile.database)
            binding = _binding(config, contexts, launcher)
            supervisor = ArtifactProcessingSupervisor(
                database=profile.database, bindings=(binding,), lease_mode="single-process"
            )
            await supervisor._acquire()
            state = supervisor._families[MEMORY_BINDING]
            try:
                await _admit(supervisor, state)
                await _dispatch(supervisor, state)
                assert launcher.assignments == []
                intent = await _intent(profile.database, MEMORY_BINDING, scope)
                assert intent is not None
                assert intent.requested_generation == intent.handled_generation == 0

                await _capture(contexts, scope, "three")
                await _admit(supervisor, state)
                await _dispatch(supervisor, state)
                assert [assignment.scope_id for assignment in launcher.assignments] == [scope]
                intent = await _intent(profile.database, MEMORY_BINDING, scope)
                assert intent is not None
                assert intent.requested_generation == 1
                assert intent.handled_generation == 1
            finally:
                await supervisor._lose_leadership()

    asyncio.run(scenario())


def test_count_trigger_restarts_from_the_last_committed_cursor(tmp_path):
    async def scenario():
        config = _config(tmp_path, memory_source_count_threshold=2)
        async with SQLiteProfile.open(config.database, tables=BUILTIN_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database)
            scope = await _scope(contexts, "cursor")
            for name in ("one", "two", "three"):
                await _capture(contexts, scope, name)
            launcher = _Launcher(profile.database)
            binding = _binding(config, contexts, launcher)
            supervisor = ArtifactProcessingSupervisor(
                database=profile.database, bindings=(binding,), lease_mode="single-process"
            )
            await supervisor._acquire()
            state = supervisor._families[MEMORY_BINDING]
            try:
                await _admit(supervisor, state)
                await _dispatch(supervisor, state)
                assert [assignment.scope_id for assignment in launcher.assignments] == [scope]

                # One consumed window leaves two pending identities, so the
                # remaining count no longer reaches the threshold.
                await _commit_cursor(profile.database, MEMORY_BINDING, scope, 1)
                await _admit(supervisor, state)
                await _dispatch(supervisor, state)
                assert len(launcher.assignments) == 1
                intent = await _intent(profile.database, MEMORY_BINDING, scope)
                assert intent is not None
                assert intent.requested_generation == intent.handled_generation == 1

                # A new identity brings the pending count back to the threshold.
                await _capture(contexts, scope, "four")
                await _admit(supervisor, state)
                await _dispatch(supervisor, state)
                assert len(launcher.assignments) == 2
            finally:
                await supervisor._lose_leadership()

    asyncio.run(scenario())


def test_count_trigger_keeps_scopes_and_families_independent(tmp_path):
    async def scenario():
        config = _config(
            tmp_path,
            families=("memory", "topic-memory"),
            memory_source_count_threshold=2,
            topic_memory_source_count_threshold=3,
        )
        async with SQLiteProfile.open(config.database, tables=BUILTIN_TABLES) as profile:
            contexts = RelationalContexts(
                database=profile.database,
                token_estimator=character_token_estimator(),
            )
            left = await _scope(contexts, "left")
            right = await _scope(contexts, "right")
            await _capture(contexts, left, "left-one")
            await _capture(contexts, right, "right-one")
            launcher = _Launcher(profile.database)
            memory = _binding(config, contexts, launcher, family="memory")
            topic = _binding(config, contexts, launcher, family="topic-memory")
            supervisor = ArtifactProcessingSupervisor(
                database=profile.database, bindings=(memory, topic), lease_mode="single-process"
            )
            await supervisor._acquire()
            memory_state = supervisor._families[MEMORY_BINDING]
            topic_state = supervisor._families[TOPIC_BINDING]
            try:
                await _admit(supervisor, memory_state)
                await _admit(supervisor, topic_state)
                await _dispatch(supervisor, memory_state)
                await _dispatch(supervisor, topic_state)
                assert launcher.assignments == []

                await _capture(contexts, left, "left-two")
                await _admit(supervisor, memory_state)
                await _dispatch(supervisor, memory_state)
                assert [assignment.scope_id for assignment in launcher.assignments] == [left]
                assert all(assignment.binding_name == MEMORY_BINDING for assignment in launcher.assignments)

                # The Topic Memory Family keeps its own threshold and cursor.
                topic_intent = await _intent(profile.database, TOPIC_BINDING, left)
                assert topic_intent is not None
                assert topic_intent.requested_generation == topic_intent.handled_generation == 0
                assert await _pending(contexts, TOPIC_BINDING, left) == 2
                assert await _pending(contexts, MEMORY_BINDING, right) == 1
            finally:
                await supervisor._lose_leadership()

    asyncio.run(scenario())


def test_pending_count_follows_journal_identities_only(tmp_path):
    async def scenario():
        config = _config(tmp_path)
        async with SQLiteProfile.open(config.database, tables=BUILTIN_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database)
            scope = await _scope(contexts, "identities")
            first = await _capture(contexts, scope, "one")
            assert await _pending(contexts, MEMORY_BINDING, scope) == 1

            # A replayed identity keeps its journal position.
            replay = await _capture(contexts, scope, "one")
            assert replay.position == first.position
            assert await _pending(contexts, MEMORY_BINDING, scope) == 1

            # A lineage-only Artifact occupies one journal position.
            await contexts.records.create_artifact(
                scope,
                "handoff",
                ArtifactWrite(
                    content=HandoffContent(
                        objective="Continue the recorded work",
                        state=(
                            HandoffStatement(
                                text="The captured source is available.",
                                citations=(
                                    HandoffSourceCitation(
                                        source_ref=SourceRef(
                                            source_type=first.source_type,
                                            source_id=first.source_id,
                                        )
                                    ),
                                ),
                            ),
                        ),
                        disposition="continuable",
                    ).model_dump(mode="json", by_alias=True)
                ),
            )
            assert await _pending(contexts, MEMORY_BINDING, scope) == 2

            # The committed cursor retires the consumed identities.
            await _commit_cursor(profile.database, MEMORY_BINDING, scope, 2)
            assert await _pending(contexts, MEMORY_BINDING, scope) == 0
            assert await _pending(contexts, TOPIC_BINDING, scope) == 2

    asyncio.run(scenario())


def test_failed_worker_keeps_the_accepted_work_for_a_later_term(tmp_path):
    async def scenario():
        config = _config(tmp_path, memory_source_count_threshold=1)
        async with SQLiteProfile.open(config.database, tables=BUILTIN_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database)
            scope = await _scope(contexts, "failing")
            await _capture(contexts, scope, "one")

            async def fail(assignment):
                raise RuntimeError("controlled processing failure")  # noqa: TRY003

            launcher = _Launcher(profile.database, on_work=fail)
            binding = _binding(config, contexts, launcher)
            supervisor = ArtifactProcessingSupervisor(
                database=profile.database, bindings=(binding,), lease_mode="single-process"
            )
            await supervisor._acquire()
            state = supervisor._families[MEMORY_BINDING]
            try:
                await _admit(supervisor, state)
                await _dispatch(supervisor, state)
                intent = await _intent(profile.database, MEMORY_BINDING, scope)
                assert intent is not None
                assert intent.requested_generation == 1
                assert intent.handled_generation == 0
            finally:
                await supervisor._lose_leadership()

            # A later term finds the same accepted work and retries it.
            recovered = _Launcher(profile.database)
            resumed = replace(binding, launcher=recovered)
            supervisor = ArtifactProcessingSupervisor(
                database=profile.database, bindings=(resumed,), lease_mode="single-process"
            )
            await supervisor._acquire()
            state = supervisor._families[MEMORY_BINDING]
            try:
                await supervisor._discover_requested(state)
                await _dispatch(supervisor, state)
                assert [assignment.scope_id for assignment in recovered.assignments] == [scope]
                intent = await _intent(profile.database, MEMORY_BINDING, scope)
                assert intent is not None
                assert intent.requested_generation == intent.handled_generation == 1
            finally:
                await supervisor._lose_leadership()

    asyncio.run(scenario())


def test_pending_input_below_the_threshold_survives_a_restart(tmp_path):
    async def scenario():
        config = _config(tmp_path, memory_source_count_threshold=3)
        async with SQLiteProfile.open(config.database, tables=BUILTIN_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database)
            scope = await _scope(contexts, "restart")
            await _capture(contexts, scope, "one")
            launcher = _Launcher(profile.database)
            binding = _binding(config, contexts, launcher)
            supervisor = ArtifactProcessingSupervisor(
                database=profile.database, bindings=(binding,), lease_mode="single-process"
            )
            await supervisor._acquire()
            state = supervisor._families[MEMORY_BINDING]
            try:
                await _admit(supervisor, state)
                await _dispatch(supervisor, state)
                assert launcher.assignments == []
            finally:
                await supervisor._lose_leadership()

            # The same database still owes the same pending identity.
            assert await _pending(contexts, MEMORY_BINDING, scope) == 1
            assert await _cursor(profile.database, MEMORY_BINDING, scope) == 0

    asyncio.run(scenario())


def test_explicit_memory_flush_bypasses_the_count_threshold(tmp_path):
    async def scenario():
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'flush.db'}"),
            runtime=RuntimeConfig(memory_source_count_threshold=3),
        )
        async with open_builtin_runtime(config, candidate_pipeline=_ContentCandidatePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = (
                await runtime.scopes.create(
                    ScopeDraft(title="Flush", summary="Explicit flush", idempotency_key="flush")
                )
            ).scope_id
            captured = await runtime.sources.for_scope(scope).capture(
                CaptureSource(
                    source_id="one",
                    content="A durable note that the threshold has not reached.",
                    metadata={},
                )
            )
            result = await runtime.memory.for_scope(scope).flush()
            assert result.source_count == 1
            assert result.current_cursor == captured.sequence
            entries = (await runtime.memory.for_scope(scope).list()).entries
            assert [entry.entry.text for entry in entries] == ["A durable note that the threshold has not reached."]
            assert {reference.source_id for reference in entries[0].entry.sources} == {"one"}

    asyncio.run(scenario())
