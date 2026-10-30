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

"""Request coalescing and vector reuse for embedding providers.

The wrapper only changes how much work a provider does, never what a caller
receives: every call still gets one vector per input text, in the input order,
and its own usage. These tests pin the provider call count that the wrapper
saves, the isolation between callers that share or fail independently, and the
Memory write and search paths that run through the wrapper.
"""

from __future__ import annotations

import asyncio

import pytest

from powercontext.builtin.artifacts.memory import EmbeddingProfile, MemoryEntryInput
from powercontext.builtin.inference import EmbeddingResult, InferenceUsage
from powercontext.builtin.inference.coalescing import CoalescingEmbeddingModel
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts


class RecordingEmbedding:
    """Deterministic provider that records every text batch it receives."""

    profile = EmbeddingProfile(profile_id="coalescing-test", model="offline", dimension=1)

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def embed(self, texts: tuple[str, ...]) -> EmbeddingResult:
        self.calls.append(tuple(texts))
        return EmbeddingResult(
            vectors=tuple((float(len(text)),) for text in texts),
            usage=InferenceUsage(requests=1, input_tokens=len(texts) * 3, output_tokens=0),
        )


def test_concurrent_calls_merge_distinct_texts_into_one_provider_request() -> None:
    async def scenario() -> None:
        base = RecordingEmbedding()
        model = CoalescingEmbeddingModel(base, max_batch_size=8, coalesce_ms=20)

        first, second = await asyncio.gather(model.embed(("alpha",)), model.embed(("beta",)))

        assert base.calls == [("alpha", "beta")]
        assert first.vectors == ((5.0,),)
        assert second.vectors == ((4.0,),)

    asyncio.run(scenario())


def test_duplicate_texts_cost_one_request_and_keep_their_positions() -> None:
    async def scenario() -> None:
        base = RecordingEmbedding()
        model = CoalescingEmbeddingModel(base, coalesce_ms=0)

        result = await model.embed(("apple", "banana", "apple"))

        assert base.calls == [("apple", "banana")]
        assert result.vectors == ((5.0,), (6.0,), (5.0,))
        assert result.usage.requests == 1
        assert result.usage.input_tokens == 6

    asyncio.run(scenario())


def test_late_caller_reuses_the_running_provider_request() -> None:
    class Blocking(RecordingEmbedding):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def embed(self, texts: tuple[str, ...]) -> EmbeddingResult:
            self.calls.append(tuple(texts))
            self.started.set()
            await self.release.wait()
            return EmbeddingResult(vectors=tuple((1.0,) for _ in texts))

    async def scenario() -> None:
        base = Blocking()
        model = CoalescingEmbeddingModel(base, coalesce_ms=0)

        early = asyncio.create_task(model.embed(("shared",)))
        await base.started.wait()
        late = asyncio.create_task(model.embed(("shared",)))
        await asyncio.sleep(0)
        base.release.set()

        assert (await early).vectors == ((1.0,),)
        assert (await late).vectors == ((1.0,),)
        assert base.calls == [("shared",)]

    asyncio.run(scenario())


def test_disabled_cache_still_shares_running_requests() -> None:
    class Blocking(RecordingEmbedding):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def embed(self, texts: tuple[str, ...]) -> EmbeddingResult:
            self.calls.append(tuple(texts))
            self.started.set()
            await self.release.wait()
            return EmbeddingResult(vectors=tuple((1.0,) for _ in texts))

    async def scenario() -> None:
        base = Blocking()
        model = CoalescingEmbeddingModel(base, coalesce_ms=0, cache_size=0)

        early = asyncio.create_task(model.embed(("shared",)))
        await base.started.wait()
        late = asyncio.create_task(model.embed(("shared",)))
        await asyncio.sleep(0)
        base.release.set()

        assert (await early).vectors == ((1.0,),)
        assert (await late).vectors == ((1.0,),)
        assert base.calls == [("shared",)]

        repeated = await model.embed(("shared",))
        assert repeated.vectors == ((1.0,),)
        assert len(base.calls) == 2

    asyncio.run(scenario())


def test_a_failed_batch_leaves_independent_batches_and_a_retry_intact() -> None:
    class Flaky(RecordingEmbedding):
        def __init__(self) -> None:
            super().__init__()
            self.failures = 1

        async def embed(self, texts: tuple[str, ...]) -> EmbeddingResult:
            self.calls.append(tuple(texts))
            if any("bad" in text for text in texts) and self.failures > 0:
                self.failures -= 1
                raise RuntimeError("provider failure")  # noqa: TRY003
            return EmbeddingResult(vectors=tuple((float(len(text)),) for text in texts))

    async def scenario() -> None:
        base = Flaky()
        model = CoalescingEmbeddingModel(base, max_batch_size=1, coalesce_ms=0)

        failed, succeeded = await asyncio.gather(
            model.embed(("bad",)),
            model.embed(("good",)),
            return_exceptions=True,
        )

        assert isinstance(failed, RuntimeError)
        assert not isinstance(succeeded, BaseException)
        assert succeeded.vectors == ((4.0,),)
        assert sorted(text for call in base.calls for text in call) == ["bad", "good"]

        retried = await model.embed(("bad",))
        assert retried.vectors == ((3.0,),)

    asyncio.run(scenario())


def test_mismatched_vector_count_is_rejected_and_never_cached() -> None:
    class WrongCount(RecordingEmbedding):
        async def embed(self, texts: tuple[str, ...]) -> EmbeddingResult:
            self.calls.append(tuple(texts))
            return EmbeddingResult(vectors=())

    async def scenario() -> None:
        base = WrongCount()
        model = CoalescingEmbeddingModel(base, coalesce_ms=0)

        with pytest.raises(ValueError):
            await model.embed(("broken",))

        with pytest.raises(ValueError):
            await model.embed(("broken",))
        assert len(base.calls) == 2

    asyncio.run(scenario())


def test_usage_counts_a_shared_batch_once_for_each_waiting_call() -> None:
    async def scenario() -> None:
        base = RecordingEmbedding()
        model = CoalescingEmbeddingModel(base, max_batch_size=8, coalesce_ms=20)

        first, second = await asyncio.gather(model.embed(("alpha", "beta")), model.embed(("beta",)))

        assert base.calls == [("alpha", "beta")]
        assert first.usage.requests == 1
        assert second.usage.requests == 1
        assert first.usage.input_tokens == 6
        assert second.usage.input_tokens == 6

        cached = await model.embed(("alpha",))
        assert cached.usage.requests == 0
        assert cached.usage.input_tokens is None

    asyncio.run(scenario())


def test_cached_vectors_are_reused_and_evicted_by_recency() -> None:
    async def scenario() -> None:
        base = RecordingEmbedding()
        model = CoalescingEmbeddingModel(base, coalesce_ms=0, cache_size=2)

        await model.embed(("alpha",))
        await model.embed(("beta",))
        await model.embed(("alpha",))
        assert base.calls == [("alpha",), ("beta",)]

        await model.embed(("gamma",))
        await model.embed(("alpha",))
        assert base.calls == [("alpha",), ("beta",), ("gamma",)]

        await model.embed(("beta",))
        assert base.calls == [("alpha",), ("beta",), ("gamma",), ("beta",)]

    asyncio.run(scenario())


def test_wrapper_drives_memory_writes_and_vector_search() -> None:
    class SemanticEmbedding:
        profile = EmbeddingProfile(profile_id="coalescing-native", model="offline", dimension=2)

        async def embed(self, texts: tuple[str, ...]) -> EmbeddingResult:
            return EmbeddingResult(
                vectors=tuple((1.0, 0.0) if "alpha" in text.lower() else (0.0, 1.0) for text in texts)
            )

    async def scenario() -> None:
        model = CoalescingEmbeddingModel(SemanticEmbedding(), coalesce_ms=0)
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig()), embedding_model=model) as contexts:
            service = (await contexts.get("coalescing-native")).artifacts.memory
            memory = await service.remember(
                memory=None,
                entries=(
                    MemoryEntryInput(kind="fact", text="Alpha launch decision."),
                    MemoryEntryInput(kind="fact", text="Beta operational note."),
                ),
                mode="append",
            )
            assert memory is not None

            result = await service.search("alpha", memories=(memory,), limit=1, mode="vector")
            assert result.hits[0].text == "Alpha launch decision."

    asyncio.run(scenario())
