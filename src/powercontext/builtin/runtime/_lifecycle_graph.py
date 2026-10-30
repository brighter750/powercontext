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

"""Evaluate one Source lifecycle dependency graph."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from powercontext.builtin.persistence.source_lifecycle import SourceLifecycleNode


class SourceLifecycleGraphError(ValueError):
    """A dependency graph that is not a valid directed acyclic graph."""


def validate_parents(
    node_id: str,
    parents: Sequence[str],
    nodes: Mapping[str, SourceLifecycleNode],
) -> tuple[str, ...]:
    """Validate one derived node's dependency edges before they are stored.

    A derived node needs at least one parent, may not repeat a parent, may not
    depend on itself, may only depend on already registered nodes, and may not
    close a cycle in the persisted graph.
    """

    if not parents:
        raise SourceLifecycleGraphError(f"lifecycle node {node_id!r} requires at least one parent")
    ordered = tuple(parents)
    if len(set(ordered)) != len(ordered):
        raise SourceLifecycleGraphError(f"lifecycle node {node_id!r} repeats a parent")
    if node_id in ordered:
        raise SourceLifecycleGraphError(f"lifecycle node {node_id!r} depends on itself")
    for parent in ordered:
        if parent not in nodes:
            raise SourceLifecycleGraphError(f"lifecycle node {node_id!r} depends on unknown node {parent!r}")
    if _reaches(ordered, node_id, nodes):
        raise SourceLifecycleGraphError(f"lifecycle node {node_id!r} closes a dependency cycle")
    return ordered


def evaluate_availability(
    nodes: Mapping[str, SourceLifecycleNode],
    *,
    source_overrides: Mapping[str, bool] | None = None,
) -> dict[str, bool]:
    """Return every node's availability under the given Source states.

    A Source carries its own availability. A derived node is available when all
    of its parents are available under the ``all`` policy, or when any of them
    is available under the ``any`` policy.
    """

    overrides = {} if source_overrides is None else source_overrides
    resolved: dict[str, bool] = {}
    visiting: set[str] = set()

    def resolve(node_id: str) -> bool:
        if node_id in resolved:
            return resolved[node_id]
        node = nodes.get(node_id)
        if node is None:
            raise SourceLifecycleGraphError(f"unknown lifecycle node {node_id!r}")
        if node_id in visiting:
            raise SourceLifecycleGraphError(f"lifecycle dependency cycle at {node_id!r}")
        visiting.add(node_id)
        try:
            if node.kind == "source":
                if node.parents:
                    raise SourceLifecycleGraphError(f"lifecycle source {node_id!r} must not depend on other nodes")
                value = overrides.get(node_id, node.available)
            else:
                if not node.parents:
                    raise SourceLifecycleGraphError(f"lifecycle node {node_id!r} requires at least one parent")
                if node.policy not in {"all", "any"}:
                    raise SourceLifecycleGraphError(f"lifecycle node {node_id!r} has an unsupported policy")
                states = [resolve(parent) for parent in node.parents]
                value = all(states) if node.policy == "all" else any(states)
        finally:
            visiting.discard(node_id)
        resolved[node_id] = value
        return value

    for node_id in sorted(nodes):
        resolve(node_id)
    return resolved


def _reaches(parents: Sequence[str], node_id: str, nodes: Mapping[str, SourceLifecycleNode]) -> bool:
    """Report whether any parent already depends on the node being registered."""

    pending = list(parents)
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current == node_id:
            return True
        if current in seen:
            continue
        seen.add(current)
        node = nodes.get(current)
        if node is not None:
            pending.extend(node.parents)
    return False
