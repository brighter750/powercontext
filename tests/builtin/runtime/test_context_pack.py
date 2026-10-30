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

"""Behavior tests for the incremental context pack transfer protocol."""

from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import json
from collections.abc import Mapping

import pytest

from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, PrepareContextRequest, open_builtin_runtime
from powercontext.builtin.runtime.context_pack import (
    CONTEXT_PACK_DELTA_SCHEMA,
    CONTEXT_PACK_SCHEMA,
    ContextPack,
    ContextPackDelta,
    ContextPackError,
    apply_context_pack_delta,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import ContentSource


class _CandidatePipeline:
    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(kind="conversation-turn", text=source.content, sources=(source,))
            for source in request.sources
            if isinstance(source, ContentSource)
        )


def _canonical(payload: Mapping[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def test_pack_wire_form_matches_an_independent_digest_oracle() -> None:
    items = (b"alpha\n", "海洋".encode())
    unsigned = {
        "schema": CONTEXT_PACK_SCHEMA,
        "items": [base64.b64encode(item).decode("ascii") for item in items],
    }
    pack = ContextPack.build(items)

    payload = json.loads(pack.to_bytes())

    assert payload == {
        **unsigned,
        "digest": "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest(),
        "byte_count": len(pack.to_bytes()),
    }
    assert pack.to_bytes() == _canonical(payload)
    assert set(payload) == {"schema", "items", "digest", "byte_count"}


def test_pack_round_trips_and_keeps_its_byte_count() -> None:
    pack = ContextPack.build((b"alpha", b"beta"))

    restored = ContextPack.from_bytes(pack.to_bytes())

    assert restored == pack
    assert restored.digest == pack.digest
    assert restored.byte_count == len(restored.to_bytes())
    assert pack.byte_count == len(pack.to_bytes())


def test_empty_pack_is_valid_and_round_trips() -> None:
    empty = ContextPack.build(())

    assert empty.items == ()
    assert ContextPack.from_bytes(empty.to_bytes()) == empty
    assert b"".join(empty.items) == b""


def test_content_chunking_keeps_bytes_and_stays_within_the_limit() -> None:
    content = "alpha 海洋 beta\nsecond line\n"

    for limit in (4, 7, 16, 512):
        pack = ContextPack.from_content(content, chunk_bytes=limit)
        assert b"".join(pack.items).decode("utf-8") == content
        assert all(len(item) <= limit for item in pack.items)
        assert all(item.decode("utf-8") for item in pack.items)


def test_chunking_refuses_a_limit_that_cannot_hold_one_character() -> None:
    with pytest.raises(ContextPackError):
        ContextPack.from_content("海", chunk_bytes=2)
    with pytest.raises(ContextPackError):
        ContextPack.from_content("alpha", chunk_bytes=0)


def test_content_chunking_is_deterministic() -> None:
    first = ContextPack.from_content("alpha\nbeta\n")
    second = ContextPack.from_content("alpha\nbeta\n")

    assert first == second
    assert first.to_bytes() == second.to_bytes()


@pytest.mark.parametrize(
    "target_items",
    [
        (b"a", b"b", b"new", b"tail"),
        (b"new", b"b", b"old"),
        (b"a", b"new", b"old"),
        (b"new",),
        (),
    ],
)
def test_delta_reconstructs_changes_at_any_position(target_items: tuple[bytes, ...]) -> None:
    base = ContextPack.build((b"a", b"b", b"old"))
    target = ContextPack.build(target_items)

    delta = ContextPackDelta.between(base, target)
    rebuilt = apply_context_pack_delta(
        ContextPack.from_bytes(base.to_bytes()),
        ContextPackDelta.from_bytes(delta.to_bytes()),
    )

    assert rebuilt == target
    assert delta.byte_count == len(delta.to_bytes())


def test_delta_wire_form_carries_only_the_protocol_fields() -> None:
    base = ContextPack.build((b"alpha",))
    target = ContextPack.build((b"alpha", b"beta"))
    delta = ContextPackDelta.between(base, target)

    payload = json.loads(delta.to_bytes())

    assert set(payload) == {"schema", "base_digest", "target_digest", "prefix_items", "appended"}
    assert payload["schema"] == CONTEXT_PACK_DELTA_SCHEMA
    assert payload["base_digest"] == base.digest
    assert payload["target_digest"] == target.digest
    assert payload["prefix_items"] == 1
    assert payload["appended"] == [base64.b64encode(b"beta").decode("ascii")]
    assert "byte_count" not in payload


def test_any_valid_common_prefix_rebuilds_the_target() -> None:
    base = ContextPack.build((b"a", b"b", b"old"))
    target = ContextPack.build((b"a", b"b", b"new", b"tail"))
    # A prefix is valid when the base and the target agree on those leading chunks.
    valid_prefixes = [prefix for prefix in range(len(base.items) + 1) if base.items[:prefix] == target.items[:prefix]]

    assert valid_prefixes == [0, 1, 2]
    for prefix in valid_prefixes:
        delta = ContextPackDelta(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix,
            appended=target.items[prefix:],
        )
        rebuilt = apply_context_pack_delta(base, delta)
        assert rebuilt == target
        assert b"".join(rebuilt.items) == b"".join(target.items)


def test_a_zero_prefix_still_rebuilds_the_whole_target() -> None:
    base = ContextPack.build((b"a", b"b"))
    target = ContextPack.build((b"x", b"y", b"z"))
    delta = ContextPackDelta(base.digest, target.digest, 0, target.items)

    rebuilt = apply_context_pack_delta(base, delta)

    assert rebuilt == target


def test_delta_rejects_a_wrong_base_prefix_or_digest() -> None:
    base = ContextPack.build((b"one",))
    target = ContextPack.build((b"one", b"two"))
    delta = ContextPackDelta.between(base, target)

    with pytest.raises(ContextPackError):
        apply_context_pack_delta(ContextPack.build((b"other",)), delta)
    with pytest.raises(ContextPackError):
        apply_context_pack_delta(base, ContextPackDelta(base.digest, "sha256:" + "0" * 64, 0, ()))
    with pytest.raises(ContextPackError):
        apply_context_pack_delta(base, ContextPackDelta(base.digest, target.digest, 5, ()))
    with pytest.raises(ContextPackError):
        apply_context_pack_delta(base, ContextPackDelta(base.digest, target.digest, 1, (b"wrong",)))


@pytest.mark.parametrize("prefix", [True, 1.5, -1, "1", None])
def test_delta_decoder_rejects_an_invalid_prefix(prefix: object) -> None:
    base = ContextPack.build((b"alpha",))
    delta = ContextPackDelta.between(base, ContextPack.build((b"alpha", b"beta")))
    payload = json.loads(delta.to_bytes())
    payload["prefix_items"] = prefix

    with pytest.raises(ContextPackError):
        ContextPackDelta.from_bytes(_canonical(payload))


def test_decoders_reject_missing_and_extra_fields() -> None:
    pack = ContextPack.build((b"alpha",))
    payload = json.loads(pack.to_bytes())

    for field in payload:
        incomplete = {key: value for key, value in payload.items() if key != field}
        with pytest.raises(ContextPackError):
            ContextPack.from_bytes(_canonical(incomplete))
    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(_canonical({**payload, "extra": True}))

    delta = ContextPackDelta.between(pack, ContextPack.build((b"alpha", b"beta")))
    delta_payload = json.loads(delta.to_bytes())
    for field in delta_payload:
        incomplete = {key: value for key, value in delta_payload.items() if key != field}
        with pytest.raises(ContextPackError):
            ContextPackDelta.from_bytes(_canonical(incomplete))
    with pytest.raises(ContextPackError):
        ContextPackDelta.from_bytes(_canonical({**delta_payload, "extra": True}))


def test_decoders_reject_invalid_base64_and_tampered_digests() -> None:
    pack = ContextPack.build((b"alpha",))
    payload = json.loads(pack.to_bytes())

    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(_canonical({**payload, "items": ["not base64!"]}))
    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(_canonical({**payload, "items": "not-a-list"}))
    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(pack.to_bytes().replace(pack.digest.encode(), b"sha256:" + b"0" * 64))
    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(_canonical({**payload, "byte_count": payload["byte_count"] + 1}))
    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(b"not json")
    with pytest.raises(ContextPackError):
        ContextPack.from_bytes(_canonical({"schema": "other", "items": [], "digest": pack.digest, "byte_count": 0}))


def test_an_independent_receiver_rebuilds_the_target_from_serialized_bytes_only() -> None:
    base = ContextPack.build((b"header\n", b"body one\n"))
    target = ContextPack.build((b"header\n", b"body one\n", b"body two\n"))
    delta = ContextPackDelta.between(base, target)

    received_base = ContextPack.from_bytes(base.to_bytes())
    received_delta = ContextPackDelta.from_bytes(delta.to_bytes())
    rebuilt = apply_context_pack_delta(received_base, received_delta)

    assert rebuilt == target
    assert b"".join(rebuilt.items) == b"".join(target.items)


def test_runtime_prepare_incremental_reuses_the_previous_context() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=_CandidatePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(
                    title="Context pack",
                    summary="Runtime integration",
                    idempotency_key="context-pack-runtime",
                )
            )
            sources = runtime.sources.for_scope(scope.scope_id)
            memory = runtime.memory.for_scope(scope.scope_id)
            context = runtime.context.for_scope(scope.scope_id)

            await sources.capture(CaptureSource(source_id="turn-1", content="alpha beta remote", metadata={}))
            while (await memory.flush()).processed:
                pass

            request = PrepareContextRequest(query="alpha", max_bytes=4096)
            first = await context.prepare_incremental(request)
            assert isinstance(first, ContextPack)

            await sources.capture(CaptureSource(source_id="turn-2", content="alpha gamma remote", metadata={}))
            while (await memory.flush()).processed:
                pass

            second = await context.prepare_incremental(request, base_pack=first)
            assert isinstance(second, ContextPackDelta)
            expected = await context.prepare(request)

            rebuilt = apply_context_pack_delta(
                ContextPack.from_bytes(first.to_bytes()),
                ContextPackDelta.from_bytes(second.to_bytes()),
            )

            assert b"".join(rebuilt.items).decode("utf-8") == expected.content
            assert expected.content
            assert second.byte_count == len(second.to_bytes())

    asyncio.run(scenario())


def test_runtime_prepare_incremental_reports_an_empty_context_as_an_empty_pack() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=_CandidatePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(
                    title="Empty context",
                    summary="Runtime integration",
                    idempotency_key="context-pack-empty",
                )
            )
            context = runtime.context.for_scope(scope.scope_id)
            request = PrepareContextRequest(query="nothing matches this", max_bytes=4096)

            prepared = await context.prepare(request)
            assert prepared.content is None

            pack = await context.prepare_incremental(request)
            assert isinstance(pack, ContextPack)
            assert pack.items == ()

    asyncio.run(scenario())


def test_repeated_prepares_transfer_far_less_than_the_full_context() -> None:
    """The point of the protocol: a receiver that already holds the context keeps it."""

    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=_CandidatePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(
                    title="Transfer cost",
                    summary="Runtime integration",
                    idempotency_key="context-pack-cost",
                )
            )
            sources = runtime.sources.for_scope(scope.scope_id)
            memory = runtime.memory.for_scope(scope.scope_id)
            context = runtime.context.for_scope(scope.scope_id)

            for index in range(4):
                await sources.capture(
                    CaptureSource(source_id=f"turn-{index}", content=f"alpha remote turn number {index}", metadata={})
                )
            while (await memory.flush()).processed:
                pass

            request = PrepareContextRequest(query="alpha", max_bytes=4096)
            raw_full_bytes = 0
            incremental_bytes = 0
            gzip_bytes = 0
            base: ContextPack | None = None
            for _ in range(10):
                prepared = await context.prepare(request)
                assert prepared.content
                raw = prepared.content.encode("utf-8")
                raw_full_bytes += len(raw)
                gzip_bytes += len(gzip.compress(raw, compresslevel=9, mtime=0))
                transfer = await context.prepare_incremental(request, base_pack=base)
                if isinstance(transfer, ContextPack):
                    base = transfer
                else:
                    base = apply_context_pack_delta(
                        ContextPack.from_bytes(base.to_bytes()) if base else ContextPack.build(()),
                        ContextPackDelta.from_bytes(transfer.to_bytes()),
                    )
                incremental_bytes += len(transfer.to_bytes())

            assert incremental_bytes < raw_full_bytes
            assert gzip_bytes > 0

    asyncio.run(scenario())
