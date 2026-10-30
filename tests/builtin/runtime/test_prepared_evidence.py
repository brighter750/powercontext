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

from __future__ import annotations

import json
from typing import TypedDict, cast

from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryHit
from powercontext.builtin.runtime import PrepareContextRequest
from powercontext.builtin.runtime.prepared_context import (
    PreparedContextBuilder,
    PreparedMemoryCandidates,
)

MEMORY_REF = ArtifactRef(family="memory", artifact_id="memory", revision=3)


class _EvidenceItem(TypedDict, total=False):
    citation: dict[str, object]
    content: str
    start: int
    end: int
    truncated: bool


def _hit(entry_id: str, text: str) -> MemoryHit:
    return MemoryHit(
        memory_ref=MEMORY_REF,
        entry_id=entry_id,
        entry_version_id=f"{entry_id}-v1",
        text=text,
        score=1.0,
        matched_by=("fts",),
    )


def _build(
    *,
    query: str,
    hits: list[MemoryHit],
    max_bytes: int = 8000,
    scope_id: str = "scope:one",
    selection: bool = True,
):
    request = PrepareContextRequest(query=query, max_bytes=max_bytes, evidence_selection=selection)
    return PreparedContextBuilder().build_scopes_result(
        request=request,
        current_scope_id=scope_id,
        memory_candidates=(PreparedMemoryCandidates(scope_id=scope_id, memory_ref=MEMORY_REF, hits=tuple(hits)),),
    )


def _items(content: str | None) -> list[_EvidenceItem]:
    assert content is not None
    return cast(list[_EvidenceItem], json.loads(content.splitlines()[-2])["items"])


def test_evidence_selection_is_off_by_default() -> None:
    request = PrepareContextRequest(query="cobalt")

    assert request.evidence_selection is False


def test_plain_prepare_still_delivers_whole_entries() -> None:
    build = _build(query="cobalt", hits=[_hit("alpha", "A cobalt sensor.")], selection=False)

    assert build.fragments == ()
    assert build.origins
    items = _items(build.context.content)
    assert items[0]["content"] == "A cobalt sensor."
    assert items[0]["truncated"] is False


def test_selected_fragment_quotes_its_entry_verbatim() -> None:
    text = "The cobalt sensor is calibrated at 37 degrees. "
    build = _build(query="cobalt sensor calibrated", hits=[_hit("alpha", text)])

    assert build.fragments
    fragment = build.fragments[0]
    assert type(fragment.start) is int
    assert type(fragment.end) is int
    assert 0 <= fragment.start < fragment.end <= len(text)
    assert fragment.text == text[fragment.start : fragment.end]


def test_query_selects_the_matching_region() -> None:
    text = "Solar panels feed the grid. " * 30 + "The cobalt sensor is calibrated at 37 degrees. "

    build = _build(query="cobalt sensor calibrated", hits=[_hit("alpha", text)])

    assert build.fragments
    assert "cobalt" in build.fragments[0].text
    assert "37" in build.fragments[0].text


def test_evidence_in_the_later_part_of_an_entry_is_reachable() -> None:
    prefix = "General administrative notes. " * 70
    text = prefix + "The cobalt sensor is calibrated at 37 degrees. "

    build = _build(query="cobalt sensor calibrated", hits=[_hit("alpha", text)], max_bytes=1200)

    assert build.fragments
    fragment = build.fragments[0]
    assert fragment.start > len(prefix) // 2
    assert "37" in fragment.text


def test_a_different_query_selects_different_evidence() -> None:
    prefix = "General administrative notes. " * 70
    first = _hit("alpha", prefix + "The cobalt sensor is calibrated at 37 degrees. ")
    second = _hit("beta", prefix + "The amber valve opens at 19 degrees. ")

    cobalt = _build(query="cobalt sensor calibrated", hits=[first, second], max_bytes=1200)
    amber = _build(query="amber valve opens", hits=[first, second], max_bytes=1200)

    assert cobalt.fragments
    assert amber.fragments
    assert cobalt.fragments[0].address.entry_id == "alpha"
    assert amber.fragments[0].address.entry_id == "beta"


def test_one_entry_may_contribute_more_than_one_fragment() -> None:
    text = (
        "The cobalt sensor is calibrated at 37 degrees. "
        + "Filler sentence about office stationery and printers. " * 40
        + "The amber valve opens at 19 degrees. "
    )

    build = _build(query="cobalt sensor calibrated amber valve opens", hits=[_hit("alpha", text)], max_bytes=1200)

    assert len(build.fragments) > 1
    assert all(fragment.address.entry_id == "alpha" for fragment in build.fragments)
    for fragment in build.fragments:
        assert fragment.text == text[fragment.start : fragment.end]
    assert any("cobalt" in fragment.text for fragment in build.fragments)
    assert any("amber" in fragment.text for fragment in build.fragments)


def test_fragments_origins_and_items_line_up() -> None:
    text = "The cobalt sensor is calibrated at 37 degrees. The amber valve opens at 19 degrees. "

    build = _build(query="cobalt sensor calibrated amber valve", hits=[_hit("alpha", text)])

    items = _items(build.context.content)
    assert len(items) == len(build.fragments) == len(build.origins)
    for item, fragment, origin in zip(items, build.fragments, build.origins, strict=True):
        assert item["content"] == fragment.text
        assert item["start"] == fragment.start
        assert item["end"] == fragment.end
        assert origin == fragment.address


def test_citation_points_at_the_original_entry_version() -> None:
    text = "The cobalt sensor is calibrated at 37 degrees. "

    build = _build(query="cobalt sensor calibrated", hits=[_hit("alpha", text)])

    item = _items(build.context.content)[0]
    assert item["citation"] == {
        "scope_id": "scope:one",
        "memory_ref": MEMORY_REF.model_dump(mode="json"),
        "entry_id": "alpha",
        "entry_version_id": "alpha-v1",
    }


def test_identical_text_in_two_scopes_keeps_its_own_identity() -> None:
    text = "The cobalt sensor is calibrated at 37 degrees. "
    request = PrepareContextRequest(query="cobalt sensor calibrated", max_bytes=8000, evidence_selection=True)
    build = PreparedContextBuilder().build_scopes_result(
        request=request,
        current_scope_id="scope:one",
        memory_candidates=(
            PreparedMemoryCandidates(scope_id="scope:one", memory_ref=MEMORY_REF, hits=(_hit("alpha", text),)),
            PreparedMemoryCandidates(scope_id="scope:two", memory_ref=MEMORY_REF, hits=(_hit("alpha", text),)),
        ),
    )

    scopes = {fragment.address.memory.scope_id for fragment in build.fragments}
    assert scopes == {"scope:one", "scope:two"}
    addresses = {fragment.address.memory.scope_id for fragment in build.fragments}
    assert len(addresses) == len(build.fragments)


def test_output_keeps_the_trust_policy_and_envelope() -> None:
    text = "The cobalt sensor is calibrated at 37 degrees. "

    build = _build(query="cobalt sensor calibrated", hits=[_hit("alpha", text)])

    content = build.context.content
    assert content is not None
    assert "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1" in content
    assert "END_POWERCONTEXT_PREPARED_CONTEXT_V1" in content
    envelope = json.loads(content.splitlines()[-2])
    assert envelope["trust"] == "untrusted_history"
    assert isinstance(envelope["items"], list)


def test_content_bytes_match_the_rendered_output() -> None:
    text = "海洋观测 station records temperatures. " * 40

    build = _build(query="海洋观测 station", hits=[_hit("alpha", text)], max_bytes=1200)

    assert build.context.content is not None
    assert build.context.content_bytes == len(build.context.content.encode("utf-8"))
    assert build.context.content_bytes <= 1200


def test_multibyte_content_is_charged_in_utf8_bytes() -> None:
    text = "海洋观测 station records temperatures. " * 40

    build = _build(query="海洋观测 station", hits=[_hit("alpha", text)], max_bytes=1200)

    assert build.fragments
    assert build.context.content_bytes > len(build.context.content or "")


def test_a_budget_that_fits_no_fragment_yields_an_empty_context() -> None:
    text = "The cobalt sensor is calibrated at 37 degrees. "

    build = _build(query="cobalt sensor calibrated", hits=[_hit("alpha", text)], max_bytes=512)

    assert build.context.status == "empty"
    assert build.context.content is None
    assert build.context.content_bytes == 0
    assert build.fragments == ()


def test_unrelated_entries_do_not_take_the_budget() -> None:
    related = "The cobalt sensor is calibrated at 37 degrees. "
    unrelated = "Unrelated administrative notes about office supplies and stationery. "

    build = _build(query="cobalt sensor calibrated", hits=[_hit("alpha", related), _hit("beta", unrelated)])

    assert build.fragments
    assert all(fragment.address.entry_id == "alpha" for fragment in build.fragments)


def test_the_selection_is_stable_for_the_same_inputs() -> None:
    text = "The cobalt sensor is calibrated at 37 degrees. The amber valve opens at 19 degrees. "

    first = _build(query="cobalt sensor calibrated", hits=[_hit("alpha", text)])
    second = _build(query="cobalt sensor calibrated", hits=[_hit("alpha", text)])

    assert first.fragments == second.fragments


def test_memory_candidates_from_another_scope_keep_their_own_address() -> None:
    text = "The cobalt sensor is calibrated at 37 degrees. "
    request = PrepareContextRequest(query="cobalt sensor calibrated", max_bytes=8000, evidence_selection=True)

    build = PreparedContextBuilder().build_scopes_result(
        request=request,
        current_scope_id="scope:one",
        memory_candidates=(
            PreparedMemoryCandidates(scope_id="scope:two", memory_ref=MEMORY_REF, hits=(_hit("alpha", text),)),
        ),
    )

    assert build.fragments
    address = build.fragments[0].address
    assert isinstance(address.memory, ArtifactAddress)
    assert address.memory.scope_id == "scope:two"
