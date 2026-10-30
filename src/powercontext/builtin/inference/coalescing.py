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

"""Cross-call embedding request coalescing and bounded vector reuse.

Memory writes and vector searches both embed text. One call already batches its
own texts and unchanged entries keep their stored index vector, but calls that
overlap in time still ask the provider for the same text once per call.
:class:`CoalescingEmbeddingModel` closes that gap in two ways: identical texts
that overlap in time share one provider request, and successful vectors are
reused from a bounded LRU cache by later calls.

The wrapper is opt-in. A model that is not wrapped keeps its current
behaviour, and the wrapper can be handed to ``open_builtin_contexts`` wherever
a plain embedding model is accepted.

Two properties are deliberate rather than incidental:

* The cache is keyed by text identity, so a retrieval query reuses the vector
  that a previous write already paid for. A deployment whose provider
  distinguishes query and document embeddings therefore sees the query path
  reuse document vectors, which is the reuse the cache exists to provide.
* Cancelling one caller never cancels the shared provider work. Waiters shield
  the shared future, so a cancelled caller leaves the batch running for the
  callers that still wait for it.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.memory.models import EmbeddingProfile
    from powercontext.builtin.inference.protocols import EmbeddingModel

EmbedDelegate = Callable[[tuple[str, ...]], Awaitable[EmbeddingResult]]

_DOCUMENT_PATH = "document"
_QUERY_PATH = "query"

__all__ = ["CoalescingEmbeddingModel"]


@dataclass(slots=True)
class _Batch:
    """One provider request shared by every waiter of its texts."""

    identifier: int
    path: str
    texts: tuple[str, ...]
    usage: InferenceUsage | None = None


@dataclass(slots=True)
class _Flight:
    """One text waiting for, or sharing, a provider batch."""

    text: str
    path: str
    future: asyncio.Future[EmbeddingVector]
    batch: _Batch | None = None


class CoalescingEmbeddingModel:
    """Share concurrent embedding work and reuse successful vectors across calls.

    The wrapper keeps the provider call count proportional to the distinct
    texts a group of callers needs, not to the number of callers. Each call
    still returns one vector per input text, in the input order, and keeps its
    own usage: the requests and tokens of the distinct provider batches that
    call waited for, counted once per batch.
    """

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        """Wrap one embedding model with request coalescing and a vector cache.

        ``max_batch_size`` bounds the deduplicated texts of one provider
        request. ``coalesce_ms`` opens a merging window during which texts
        from concurrent calls are batched together; zero keeps the window but
        does not wait for it. ``cache_size`` bounds the LRU cache of successful
        vectors; zero disables cross-call reuse while in-flight sharing stays
        active.
        """

        if max_batch_size < 1:
            raise ValueError("max_batch_size must be at least 1")  # noqa: TRY003
        if coalesce_ms < 0:
            raise ValueError("coalesce_ms must not be negative")  # noqa: TRY003
        if cache_size < 0:
            raise ValueError("cache_size must not be negative")  # noqa: TRY003
        self._model = model
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = coalesce_ms / 1000
        self._cache_size = cache_size
        self._document_delegate: EmbedDelegate = model.embed
        query_delegate = getattr(model, "embed_query", None)
        self._query_delegate: EmbedDelegate = self._document_delegate if query_delegate is None else query_delegate
        self._cache: OrderedDict[str, EmbeddingVector] = OrderedDict()
        self._pending: dict[str, _Flight] = {}
        self._inflight: dict[str, _Flight] = {}
        self._scheduler: asyncio.Task[None] | None = None
        self._batches = 0
        self._running: set[asyncio.Task[None]] = set()
        self.profile: EmbeddingProfile = model.profile

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Embed an ordered text batch, sharing provider work with concurrent calls."""

        return await self._embed(_DOCUMENT_PATH, texts)

    async def embed_query(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Embed retrieval queries through the same shared pipeline.

        A provider that distinguishes query and document embeddings keeps that
        distinction here. A provider that does not embed queries as documents.
        """

        return await self._embed(_QUERY_PATH, texts)

    async def _embed(self, path: str, texts: tuple[str, ...]) -> EmbeddingResult:
        """Resolve one ordered batch against the cache, the shared work, and the provider."""

        ordered = tuple(texts)
        if not ordered:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))

        resolved: dict[str, EmbeddingVector] = {}
        flights: list[_Flight] = []
        for text in dict.fromkeys(ordered):
            cached = self._cached_vector(text)
            if cached is None:
                flights.append(self._flight_for(text, path))
            else:
                resolved[text] = cached

        if flights:
            vectors = await asyncio.gather(*(self._await_flight(flight) for flight in flights))
            for flight, vector in zip(flights, vectors, strict=True):
                resolved[flight.text] = vector

        return EmbeddingResult(
            vectors=tuple(resolved[text] for text in ordered),
            usage=self._usage_for(flights),
        )

    async def _await_flight(self, flight: _Flight) -> EmbeddingVector:
        """Wait for one shared vector without letting a cancelled waiter cancel the work."""

        return await asyncio.shield(flight.future)

    def _flight_for(self, text: str, path: str) -> _Flight:
        """Return the shared flight for one text, creating it when the text is new.

        A text that is already running joins the batch that owns it, so a late
        caller reuses work that is in flight instead of starting a second
        provider request for the same text.
        """

        flight = self._inflight.get(text)
        if flight is not None:
            return flight
        flight = self._pending.get(text)
        if flight is not None:
            return flight
        flight = _Flight(text=text, path=path, future=asyncio.get_running_loop().create_future())
        self._pending[text] = flight
        self._start_scheduler()
        return flight

    def _start_scheduler(self) -> None:
        """Ensure one scheduler owns the pending queue."""

        if self._scheduler is None:
            self._scheduler = asyncio.create_task(self._drain_pending())

    async def _drain_pending(self) -> None:
        """Dispatch pending texts in bounded batches after each merging window."""

        try:
            while self._pending:
                await asyncio.sleep(self._coalesce_seconds)
                self._dispatch()
        finally:
            self._scheduler = None

    def _dispatch(self) -> None:
        """Move pending texts into bounded provider batches."""

        while self._pending:
            texts, path = self._next_batch()
            self._batches += 1
            batch = _Batch(identifier=self._batches, path=path, texts=texts)
            for text in texts:
                flight = self._pending.pop(text)
                flight.batch = batch
                self._inflight[text] = flight
            task = asyncio.create_task(self._run_batch(batch))
            self._running.add(task)
            task.add_done_callback(self._running.discard)

    def _next_batch(self) -> tuple[tuple[str, ...], str]:
        """Take the next pending texts that share one provider entry point."""

        selected: list[str] = []
        path = _DOCUMENT_PATH
        for text, flight in self._pending.items():
            if selected and flight.path != path:
                break
            path = flight.path
            selected.append(text)
            if len(selected) >= self._max_batch_size:
                break
        return tuple(selected), path

    async def _run_batch(self, batch: _Batch) -> None:
        """Run one provider batch and settle every waiter of its texts."""

        try:
            result = await self._delegate_for(batch.path)(batch.texts)
            vectors = tuple(result.vectors)
        except asyncio.CancelledError:
            self._settle_failure(batch, asyncio.CancelledError())
            raise
        except Exception as error:
            self._settle_failure(batch, error)
            return
        if len(vectors) != len(batch.texts):
            self._settle_failure(batch, ValueError("embedding provider returned a mismatched vector count"))
            return
        batch.usage = result.usage
        for text, vector in zip(batch.texts, vectors, strict=True):
            self._cache_vector(text, vector)
            flight = self._inflight.pop(text, None)
            if flight is not None and not flight.future.done():
                flight.future.set_result(vector)

    def _settle_failure(self, batch: _Batch, error: BaseException) -> None:
        """Fail every waiter of one batch and release its shared state.

        A failed vector is never cached, and the texts of the failed batch stay
        available so a later call can request them again.
        """

        for text in batch.texts:
            flight = self._inflight.pop(text, None)
            if flight is not None and not flight.future.done():
                flight.future.set_exception(error)

    def _delegate_for(self, path: str) -> EmbedDelegate:
        """Return the provider entry point of one embedding path."""

        return self._query_delegate if path == _QUERY_PATH else self._document_delegate

    def _cached_vector(self, text: str) -> EmbeddingVector | None:
        """Return a cached vector and refresh its recency."""

        if self._cache_size == 0:
            return None
        vector = self._cache.get(text)
        if vector is None:
            return None
        self._cache.move_to_end(text)
        return vector

    def _cache_vector(self, text: str, vector: EmbeddingVector) -> None:
        """Store one successful vector, evicting the least recently used entry."""

        if self._cache_size == 0:
            return
        self._cache[text] = vector
        self._cache.move_to_end(text)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def _usage_for(self, flights: list[_Flight]) -> InferenceUsage:
        """Aggregate the usage of the distinct batches one call waited for.

        A shared batch is counted once per call that waited for it, and a cache
        hit adds no request at all.
        """

        batches: dict[int, InferenceUsage] = {}
        for flight in flights:
            batch = flight.batch
            if batch is None or batch.usage is None:
                continue
            batches[batch.identifier] = batch.usage
        if not batches:
            return InferenceUsage(requests=0)
        usages = tuple(batches.values())
        return InferenceUsage(
            requests=sum(usage.requests for usage in usages),
            input_tokens=_sum_reported(usage.input_tokens for usage in usages),
            output_tokens=_sum_reported(usage.output_tokens for usage in usages),
        )


def _sum_reported(values: Iterable[int | None]) -> int | None:
    """Sum the reported token counts and keep an unreported total unknown."""

    reported = [value for value in values if value is not None]
    if not reported:
        return None
    return sum(reported)
