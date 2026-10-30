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

# ruff: noqa: TRY003

"""Source lifecycle: propagate Source availability to Memory and Topic Memory.

A Source that stops being available withdraws the support its dependent Memory
entries and Topic Memory artifacts relied on. The lifecycle keeps the evidence
graph explicit, propagates availability along it, and never rewrites a body:
only the right to use a node changes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator
from pydantic_core import InitErrorDetails
from pydantic_core import ValidationError as CoreValidationError

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.artifacts.memory import MemoryService
from powercontext.builtin.artifacts.memory.models import Memory
from powercontext.builtin.persistence.source_lifecycle import (
    SourceLifecycleEventRecord,
    SourceLifecycleNode,
    SourceLifecyclePolicy,
    SourceLifecycleRepository,
    SourceLifecycleState,
)
from powercontext.builtin.runtime._lifecycle_graph import (
    SourceLifecycleGraphError,
    evaluate_availability,
    validate_parents,
)
from powercontext.builtin.runtime.models import GetMemoryEntryRequest
from powercontext.builtin.sources import validate_scope_id
from powercontext.errors import SourceDefinitionNotFoundError
from powercontext.limits import MAX_SOURCE_LIFECYCLE_NODE_ID_LENGTH
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime

__all__ = [
    "ScopedSourceLifecycleService",
    "SourceLifecycleApplication",
    "SourceLifecycleError",
    "SourceLifecycleEvent",
]

_LIFECYCLE_PURPOSES = ("generation", "audit")
_TOPIC_MEMORY_FAMILY = "topic-memory"


class SourceLifecycleError(CoreValidationError):
    """A Source lifecycle contract violation.

    The error derives from the pydantic validation error, which is itself a
    ``ValueError``. A caller may therefore catch the lifecycle error, a plain
    validation error, or a ``ValueError`` and observe the same rejection.
    """

    @classmethod
    def from_message(cls, message: str) -> SourceLifecycleError:
        """Build one lifecycle error carrying a single readable reason."""

        return cls.from_exception_data(
            title="SourceLifecycleError",
            line_errors=[
                {
                    "type": "value_error",
                    "loc": (),
                    "input": message,
                    "ctx": {"error": ValueError(message)},
                }
            ],
        )


class SourceLifecycleEvent(BaseModel):
    """One Source availability change accepted by the lifecycle service."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str
    reason: str
    revision: StrictInt = Field(gt=0)
    sequence: StrictInt = Field(gt=0)
    available: StrictBool

    @field_validator("node_id", "reason")
    @classmethod
    def validate_not_blank(cls, value: str) -> str:
        """Reject an empty or whitespace-only identifier and reason."""

        if not value.strip():
            raise ValueError("must not be blank")
        return value

    def __init__(self, **data: object) -> None:
        try:
            super().__init__(**data)
        except SourceLifecycleError:
            raise
        except CoreValidationError as error:
            raise _as_lifecycle_error(error) from None


class SourceLifecycleApplication:
    """Select the scoped Source lifecycle service."""

    def __init__(self, runtime: BuiltinRuntime, repository: SourceLifecycleRepository | None) -> None:
        self._runtime = runtime
        self._repository = repository

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycleService:
        return ScopedSourceLifecycleService(self._runtime, self._repository, scope_id)


class ScopedSourceLifecycleService:
    """Register evidence bindings and propagate Source availability in one scope."""

    def __init__(
        self,
        runtime: BuiltinRuntime,
        repository: SourceLifecycleRepository | None,
        scope_id: str,
    ) -> None:
        self._runtime = runtime
        self._repository = repository
        self.scope_id = validate_scope_id(scope_id)

    async def register_source(self, node_id: str, source_ref: SourceRef, /, *, revision: int = 1) -> None:
        """Register one Source and the lifecycle revision its events must match."""

        identity = _require_node_id(node_id)
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise SourceLifecycleError.from_message("lifecycle revision must be a positive integer")
        if not isinstance(source_ref, SourceRef):
            raise SourceLifecycleError.from_message("source reference must be a SourceRef")
        repository = self._require_repository()
        await self._require_readable_source(source_ref)
        state = await repository.load(self.scope_id)
        existing = state.node(identity)
        if existing is not None:
            if (
                existing.kind == "source"
                and existing.source_type == source_ref.source_type
                and existing.source_id == source_ref.source_id
                and existing.revision == revision
            ):
                return
            raise SourceLifecycleError.from_message(f"lifecycle node {identity!r} is already bound")
        await repository.register(
            self.scope_id,
            SourceLifecycleNode(
                node_id=identity,
                kind="source",
                available=True,
                policy=None,
                revision=revision,
                sequence=None,
                source_type=source_ref.source_type,
                source_id=source_ref.source_id,
                reference=None,
                parents=(),
            ),
        )

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        /,
        *,
        parents: Sequence[str],
        policy: str,
    ) -> None:
        """Register one Memory entry and the evidence it depends on."""

        identity = _require_node_id(node_id)
        if not isinstance(citation, MemoryCitation):
            raise SourceLifecycleError.from_message("memory citation must be a MemoryCitation")
        selected = _require_policy(policy)
        repository = self._require_repository()
        await self._require_readable_memory(citation)
        state = await repository.load(self.scope_id)
        existing = state.node(identity)
        if existing is not None:
            if not _is_same_binding(existing, reference=_encode_reference(citation), parents=parents, policy=selected):
                raise SourceLifecycleError.from_message(f"lifecycle node {identity!r} is already bound")
            await self._align_memory(
                existing, _is_supported(existing.parents, selected, evaluate_availability(state.nodes))
            )
            return
        ordered = _require_parents(identity, parents, state.nodes)
        availability = evaluate_availability(state.nodes)
        supported = _is_supported(ordered, selected, availability)
        node = SourceLifecycleNode(
            node_id=identity,
            kind="memory",
            available=supported,
            policy=selected,
            revision=None,
            sequence=None,
            source_type=None,
            source_id=None,
            reference=_encode_reference(citation),
            parents=ordered,
        )
        await repository.register(self.scope_id, node)
        await self._align_memory(node, supported)

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        /,
        *,
        parents: Sequence[str],
        policy: str,
    ) -> None:
        """Register one Topic Memory artifact and the evidence it depends on."""

        identity = _require_node_id(node_id)
        if not isinstance(artifact_ref, ArtifactRef):
            raise SourceLifecycleError.from_message("topic reference must be an ArtifactRef")
        if artifact_ref.family != _TOPIC_MEMORY_FAMILY:
            raise SourceLifecycleError.from_message("topic reference must address a topic-memory artifact")
        selected = _require_policy(policy)
        repository = self._require_repository()
        await self._require_readable_topic(artifact_ref)
        state = await repository.load(self.scope_id)
        existing = state.node(identity)
        if existing is not None:
            if _is_same_binding(existing, reference=_encode_reference(artifact_ref), parents=parents, policy=selected):
                return
            raise SourceLifecycleError.from_message(f"lifecycle node {identity!r} is already bound")
        ordered = _require_parents(identity, parents, state.nodes)
        availability = evaluate_availability(state.nodes)
        await repository.register(
            self.scope_id,
            SourceLifecycleNode(
                node_id=identity,
                kind="topic",
                available=_is_supported(ordered, selected, availability),
                policy=selected,
                revision=None,
                sequence=None,
                source_type=None,
                source_id=None,
                reference=_encode_reference(artifact_ref),
                parents=ordered,
            ),
        )

    async def apply(self, event: SourceLifecycleEvent) -> list[str]:
        """Apply one Source lifecycle event and return every available node."""

        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError.from_message("lifecycle event must be a SourceLifecycleEvent")
        repository = self._require_repository()
        state = await repository.load(self.scope_id)
        node = state.node(event.node_id)
        if node is None or node.kind != "source":
            raise SourceLifecycleError.from_message(f"unknown lifecycle source {event.node_id!r}")
        if node.revision != event.revision:
            raise SourceLifecycleError.from_message(
                f"lifecycle source {event.node_id!r} expects revision {node.revision}",
            )
        recorded = await repository.event(self.scope_id, event.node_id, event.sequence)
        if recorded is None:
            last_sequence = node.sequence or 0
            if event.sequence <= last_sequence:
                raise SourceLifecycleError.from_message(
                    f"lifecycle sequence {event.sequence} is not newer than {last_sequence}",
                )
            # The event is durable before any native state changes, so a failed
            # propagation can be retried with the same event.
            await repository.record_event(
                self.scope_id,
                SourceLifecycleEventRecord(
                    node_id=event.node_id,
                    sequence=event.sequence,
                    revision=event.revision,
                    available=event.available,
                    reason=event.reason,
                    applied=False,
                ),
            )
        else:
            if (recorded.revision, recorded.available, recorded.reason) != (
                event.revision,
                event.available,
                event.reason,
            ):
                raise SourceLifecycleError.from_message(
                    f"lifecycle sequence {event.sequence} already recorded a different event",
                )
            if recorded.applied:
                # A replayed event is already propagated and must not change
                # the native state a second time.
                return _available_nodes(evaluate_availability(state.nodes))
        availability = evaluate_availability(
            state.nodes,
            source_overrides={event.node_id: event.available},
        )
        await self._propagate(state, availability, event)
        await repository.complete(
            self.scope_id,
            source_node_id=event.node_id,
            revision=event.revision,
            sequence=event.sequence,
            availability=availability,
        )
        return _available_nodes(availability)

    async def resolve(
        self,
        node_id: str,
        /,
        *,
        purpose: str,
    ) -> MemoryCitation | ArtifactRef | SourceRef:
        """Return one node's original reference for the requested purpose."""

        identity = _require_node_id(node_id)
        if purpose not in _LIFECYCLE_PURPOSES:
            raise SourceLifecycleError.from_message(f"unknown lifecycle purpose {purpose!r}")
        repository = self._require_repository()
        state = await repository.load(self.scope_id)
        node = state.node(identity)
        if node is None:
            raise SourceLifecycleError.from_message(f"unknown lifecycle node {identity!r}")
        if purpose == "generation" and not evaluate_availability(state.nodes).get(identity, False):
            raise SourceLifecycleError.from_message(
                f"lifecycle node {identity!r} is not available for generation",
            )
        return _original_reference(node)

    async def _propagate(
        self,
        state: SourceLifecycleState,
        availability: dict[str, bool],
        event: SourceLifecycleEvent,
    ) -> None:
        """Align the native Memory state with the propagated availability."""

        nodes = state.nodes
        memory_nodes = sorted(
            (node for node in nodes.values() if node.kind == "memory"),
            key=lambda node: node.node_id,
        )
        if not memory_nodes:
            return
        reason = f"source lifecycle {event.node_id} {'restored' if event.available else 'withdrawn'}"
        async with self._runtime._context(self.scope_id) as context:
            service = context.artifacts.memory
            for node in memory_nodes:
                await self._align_entry(
                    service,
                    context.artifacts.memory_artifact_id,
                    node,
                    availability.get(node.node_id, False),
                    reason,
                )

    async def _align_memory(self, node: SourceLifecycleNode, target: bool) -> None:
        """Align one registered Memory entry with the availability it computed."""

        async with self._runtime._context(self.scope_id) as context:
            await self._align_entry(
                context.artifacts.memory,
                context.artifacts.memory_artifact_id,
                node,
                target,
                "source lifecycle registration",
            )

    async def _align_entry(
        self,
        service: MemoryService,
        artifact_id: str,
        node: SourceLifecycleNode,
        target: bool,
        reason: str,
    ) -> None:
        """Deactivate or restore one Memory entry without rewriting its body."""

        citation = _decode_citation(node.reference)
        head = await _memory_head(service, artifact_id)
        if head is None:
            return
        entry = next((item for item in await service.entries(head) if item.entry_id == citation.entry_id), None)
        if entry is None:
            return
        active = _manifest_state(head, citation.entry_id) == "active"
        if active == target:
            return
        if target:
            await service.reactivate(head, entries=(entry,), reason=reason)  # type: ignore[attr-defined]
        else:
            await service.forget(head, entries=(entry,), reason=reason)  # type: ignore[attr-defined]

    async def _require_readable_source(self, source_ref: SourceRef) -> None:
        """Require one registered Source to exist and stay readable."""

        async with self._runtime._context(self.scope_id) as context:
            catalog = context.sources.catalog
            matched = None
            for source in await context.sources.list():
                try:
                    reference = catalog.as_ref(source)
                except SourceDefinitionNotFoundError:
                    continue
                if reference == source_ref:
                    matched = source
                    break
            if matched is None:
                raise SourceLifecycleError.from_message(
                    f"source {source_ref.source_id!r} is not registered in scope {self.scope_id!r}",
                )
            await context.sources.read(matched)

    async def _require_readable_memory(self, citation: MemoryCitation) -> None:
        """Require one registered Memory citation to exist and stay readable."""

        try:
            await self._runtime.memory.for_scope(self.scope_id).get(GetMemoryEntryRequest(citation=citation))
        except Exception as error:
            raise SourceLifecycleError.from_message(
                f"memory citation {citation.entry_id!r} is not readable in scope {self.scope_id!r}",
            ) from error

    async def _require_readable_topic(self, artifact_ref: ArtifactRef) -> None:
        """Require one registered Topic Memory artifact to exist and stay readable."""

        try:
            await self._runtime.records.for_scope(self.scope_id).get_artifact_revision(
                artifact_ref.family,
                artifact_ref.artifact_id,
                artifact_ref.revision,
            )
        except Exception as error:
            raise SourceLifecycleError.from_message(
                f"topic artifact {artifact_ref.artifact_id!r} is not readable in scope {self.scope_id!r}",
            ) from error

    def _require_repository(self) -> SourceLifecycleRepository:
        if self._repository is None:
            raise SourceLifecycleError.from_message("source lifecycle is not available in this runtime")
        return self._repository


def _as_lifecycle_error(error: CoreValidationError) -> SourceLifecycleError:
    """Re-raise one model validation failure as a lifecycle error."""

    line_errors: list[InitErrorDetails] = []
    for item in error.errors():
        line_errors.append(
            InitErrorDetails(
                type=item["type"],
                loc=item["loc"],
                input=item["input"],
                ctx=item.get("ctx", {}),
            )
        )
    try:
        return SourceLifecycleError.from_exception_data(title="SourceLifecycleEvent", line_errors=line_errors)
    except (TypeError, ValueError):
        return SourceLifecycleError.from_message(str(error))


def _require_node_id(node_id: str) -> str:
    """Validate one lifecycle node identifier."""

    if not isinstance(node_id, str) or not node_id.strip():
        raise SourceLifecycleError.from_message("lifecycle node id must be a non-empty string")
    if len(node_id) > MAX_SOURCE_LIFECYCLE_NODE_ID_LENGTH:
        raise SourceLifecycleError.from_message(
            f"lifecycle node id must not exceed {MAX_SOURCE_LIFECYCLE_NODE_ID_LENGTH} characters",
        )
    return node_id


def _require_policy(policy: str) -> SourceLifecyclePolicy:
    """Validate one dependency policy."""

    if policy not in {"all", "any"}:
        raise SourceLifecycleError.from_message(f"unsupported lifecycle policy {policy!r}")
    return cast(SourceLifecyclePolicy, policy)


def _require_parents(
    node_id: str,
    parents: Sequence[str],
    nodes: Mapping[str, SourceLifecycleNode],
) -> tuple[str, ...]:
    """Validate one derived node's dependency edges."""

    if isinstance(parents, str):
        raise SourceLifecycleError.from_message(f"lifecycle node {node_id!r} requires a sequence of parents")
    try:
        return validate_parents(node_id, tuple(parents), nodes)
    except SourceLifecycleGraphError as error:
        raise SourceLifecycleError.from_message(str(error)) from None


def _is_supported(parents: Sequence[str], policy: str, availability: dict[str, bool]) -> bool:
    """Return whether the parents support the node under the given policy."""

    states = [availability.get(parent, False) for parent in parents]
    return all(states) if policy == "all" else any(states)


def _is_same_binding(
    node: SourceLifecycleNode,
    *,
    reference: bytes,
    parents: Sequence[str],
    policy: str,
) -> bool:
    """Report whether a repeated registration repeats the same binding."""

    return node.reference == reference and node.policy == policy and node.parents == tuple(parents)


def _encode_reference(reference: MemoryCitation | ArtifactRef) -> bytes:
    """Encode one original reference so it can be returned unchanged."""

    return reference.model_dump_json(by_alias=True).encode()


def _decode_citation(payload: bytes | None) -> MemoryCitation:
    """Decode the Memory citation bound to one node."""

    if payload is None:
        raise SourceLifecycleError.from_message("lifecycle memory node has no bound citation")
    try:
        return MemoryCitation.model_validate_json(payload)
    except CoreValidationError as error:
        raise SourceLifecycleError.from_message(f"lifecycle memory citation is unreadable: {error}") from None


def _original_reference(node: SourceLifecycleNode) -> MemoryCitation | ArtifactRef | SourceRef:
    """Return the original reference bound to one node."""

    if node.kind == "source":
        if node.source_type is None or node.source_id is None:
            raise SourceLifecycleError.from_message(f"lifecycle source {node.node_id!r} has no bound reference")
        return SourceRef(source_type=node.source_type, source_id=node.source_id)
    if node.reference is None:
        raise SourceLifecycleError.from_message(f"lifecycle node {node.node_id!r} has no bound reference")
    if node.kind == "memory":
        return _decode_citation(node.reference)
    try:
        return ArtifactRef.model_validate_json(node.reference)
    except CoreValidationError as error:
        raise SourceLifecycleError.from_message(f"lifecycle topic reference is unreadable: {error}") from None


def _available_nodes(availability: dict[str, bool]) -> list[str]:
    """Return every available node identifier in sorted order."""

    return sorted(node_id for node_id, available in availability.items() if available)


async def _memory_head(service: MemoryService, artifact_id: str) -> Memory | None:
    """Return the current Memory head of one scope, or ``None`` when absent."""

    try:
        return await service.head(artifact_id)  # type: ignore[attr-defined]
    except Exception:
        return None


def _manifest_state(head: Memory, entry_id: str) -> str | None:
    """Return the manifest state of one Memory entry."""

    manifest = head.content.manifest.entries  # type: ignore[attr-defined]
    for item in manifest:
        if item.entry_id == entry_id:
            return item.state
    return None
