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

"""HTTP scoring service adapter for coarse Memory search candidates."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import (
    MemoryRerankDecision,
    validate_rerank_bounds,
)
from powercontext.builtin.inference import InferenceUsage

HTTP_RERANK_POLICY_ID = "http-cross-encoder-v1"

# One coarse candidate as the scoring service receives it: the global rank it keeps
# across batches, and the unchanged Memory text the service scores.
_RerankCandidate = tuple[int, MemoryHit]


class _ScoreRecord(BaseModel):
    """One score the service returned for one candidate of the requested batch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rank: int = Field(ge=1, strict=True)
    score: float = Field(allow_inf_nan=False)

    @field_validator("score", mode="before")
    @classmethod
    def _require_numeric_score(cls, value: object) -> object:
        # A service reports a finite number. Booleans and numeric strings are not
        # numbers, so they are rejected instead of being coerced.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("score must be a number")  # noqa: TRY003, TRY004
        return value


class _ScoreResponse(BaseModel):
    """The strict response envelope of the scoring protocol."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scores: tuple[_ScoreRecord, ...]


class HTTPMemoryReranker:
    """Select Memory hits with an external HTTP scoring service.

    The service scores a bounded batch of candidates against one query and returns one
    finite score per candidate. PowerContext splits the coarse pool into batches that
    respect the service's batch ceiling, keeps each candidate's original global rank
    across batches, and finally selects the globally best ranks. Because the service only
    ever returns scores, reranking cannot invent or rewrite Memory identity: the decision
    carries ranks that are resolved against the unchanged candidate pool.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = HTTP_RERANK_POLICY_ID,
    ) -> None:
        """Bind one scoring endpoint and the batch ceiling its protocol allows."""
        if not isinstance(endpoint, str) or not endpoint.strip():
            raise ValueError("rerank endpoint must be a non-empty URL")  # noqa: TRY003
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
            raise ValueError("rerank batch size must be a positive integer")  # noqa: TRY003
        if not isinstance(policy_id, str) or not policy_id.strip():
            raise ValueError("rerank policy id must be a non-empty string")  # noqa: TRY003
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Score the whole pool and select the globally best ranks.

        Every candidate is scored exactly once, but the selection is made over the
        complete pool: a high score in a later batch still outranks a lower score in an
        earlier one. Ties keep the original coarse order.
        """
        validate_rerank_bounds(len(candidates), limit)
        scores: dict[int, float] = {}
        request_count = 0
        for batch in _batches(candidates, self._batch_size):
            request_count += 1
            scores.update(await self._score_batch(query, batch))
        ordered = sorted(scores.items(), key=lambda scored: (-scored[1], scored[0]))
        return MemoryRerankDecision(
            selected_ranks=tuple(rank for rank, _ in ordered[:limit]),
            usage=InferenceUsage(requests=request_count),
        )

    async def _score_batch(
        self,
        query: str,
        batch: tuple[_RerankCandidate, ...],
    ) -> dict[int, float]:
        """Score one batch and return its scores keyed by the original global rank."""
        response = await self._client.post(
            self._endpoint,
            json={
                "query": query,
                "candidates": [{"rank": rank, "text": candidate.text} for rank, candidate in batch],
            },
        )
        response.raise_for_status()
        scores = _parse_scores(response)
        if set(scores) != {rank for rank, _ in batch}:
            raise ValueError("rerank response must cover exactly the ranks of the requested batch")  # noqa: TRY003
        return scores


def _batches(
    candidates: tuple[MemoryHit, ...],
    batch_size: int,
) -> Iterator[tuple[_RerankCandidate, ...]]:
    """Split the pool into bounded batches that keep each candidate's global rank."""
    for start in range(0, len(candidates), batch_size):
        chunk = candidates[start : start + batch_size]
        yield tuple((start + offset + 1, candidate) for offset, candidate in enumerate(chunk))


def _parse_scores(response: httpx.Response) -> dict[int, float]:
    """Validate the strict response schema and return its scores keyed by rank."""
    try:
        body = response.json()
    except ValueError as error:
        raise ValueError("rerank response is not valid JSON") from error  # noqa: TRY003
    try:
        parsed = _ScoreResponse.model_validate(body)
    except ValidationError as error:
        raise ValueError("rerank response does not match the score schema") from error  # noqa: TRY003
    scores: dict[int, float] = {}
    for record in parsed.scores:
        if record.rank in scores:
            raise ValueError(f"rerank response repeated rank {record.rank}")  # noqa: TRY003
        scores[record.rank] = record.score
    return scores


__all__ = [
    "HTTP_RERANK_POLICY_ID",
    "HTTPMemoryReranker",
]
