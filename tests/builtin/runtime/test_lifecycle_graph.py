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

"""Dependency graph rules of the Source lifecycle."""

from __future__ import annotations

import pytest

from powercontext.builtin.persistence.source_lifecycle import SourceLifecycleNode, SourceLifecyclePolicy
from powercontext.builtin.runtime._lifecycle_graph import (
    SourceLifecycleGraphError,
    evaluate_availability,
    validate_parents,
)


def _source(node_id: str, available: bool = True) -> SourceLifecycleNode:
    return SourceLifecycleNode(
        node_id=node_id,
        kind="source",
        available=available,
        policy=None,
        revision=1,
        sequence=None,
        source_type="content",
        source_id=node_id,
        reference=None,
        parents=(),
    )


def _derived(node_id: str, parents: tuple[str, ...], policy: SourceLifecyclePolicy) -> SourceLifecycleNode:
    return SourceLifecycleNode(
        node_id=node_id,
        kind="memory",
        available=True,
        policy=policy,
        revision=None,
        sequence=None,
        source_type=None,
        source_id=None,
        reference=None,
        parents=parents,
    )


def test_all_policy_requires_every_parent() -> None:
    nodes = {
        "root0": _source("root0"),
        "root1": _source("root1"),
        "child": _derived("child", ("root0", "root1"), "all"),
    }
    availability = evaluate_availability(nodes)
    assert availability == {"root0": True, "root1": True, "child": True}
    availability = evaluate_availability(nodes, source_overrides={"root1": False})
    assert availability["child"] is False


def test_any_policy_accepts_one_supporting_parent() -> None:
    nodes = {
        "root0": _source("root0"),
        "root1": _source("root1"),
        "child": _derived("child", ("root0", "root1"), "any"),
    }
    availability = evaluate_availability(nodes, source_overrides={"root0": False})
    assert availability["child"] is True
    availability = evaluate_availability(
        nodes,
        source_overrides={"root0": False, "root1": False},
    )
    assert availability["child"] is False


def test_availability_propagates_through_several_layers() -> None:
    nodes = {
        "root0": _source("root0"),
        "child": _derived("child", ("root0",), "all"),
        "grandchild": _derived("grandchild", ("child",), "all"),
    }
    availability = evaluate_availability(nodes, source_overrides={"root0": False})
    assert availability["grandchild"] is False


def test_parents_must_exist_and_stay_unique() -> None:
    nodes = {"root0": _source("root0")}
    assert validate_parents("child", ("root0",), nodes) == ("root0",)
    with pytest.raises(SourceLifecycleGraphError):
        validate_parents("child", (), nodes)
    with pytest.raises(SourceLifecycleGraphError):
        validate_parents("child", ("root0", "root0"), nodes)
    with pytest.raises(SourceLifecycleGraphError):
        validate_parents("child", ("missing",), nodes)
    with pytest.raises(SourceLifecycleGraphError):
        validate_parents("child", ("child",), nodes)


def test_a_dependency_cycle_is_rejected() -> None:
    nodes = {
        "root0": _source("root0"),
        "child": _derived("child", ("root0",), "all"),
    }
    with pytest.raises(SourceLifecycleGraphError):
        validate_parents("root0", ("child",), nodes)


def test_a_stored_cycle_is_rejected() -> None:
    nodes = {
        "root0": _source("root0"),
        "child": _derived("child", ("root0",), "all"),
        "loop": _derived("loop", ("child",), "all"),
    }
    nodes["child"] = _derived("child", ("loop",), "all")
    with pytest.raises(SourceLifecycleGraphError):
        evaluate_availability(nodes)


def test_a_derived_node_without_a_parent_is_rejected() -> None:
    nodes = {"child": _derived("child", (), "all")}
    with pytest.raises(SourceLifecycleGraphError):
        evaluate_availability(nodes)
