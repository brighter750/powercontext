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

"""Behavior tests for the HTTP Memory reranking adapter."""

from __future__ import annotations

import asyncio
import json
from typing import TypedDict

import httpx
import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.artifacts.memory.http_reranker import HTTPMemoryReranker
from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts


class _Candidate(TypedDict):
    rank: int
    text: str


class _Request(TypedDict):
    query: str
    candidates: list[_Candidate]


def _hit(rank: int) -> MemoryHit:
    return MemoryHit(
        memory_ref=ArtifactRef(family="memory", artifact_id="memory", revision=1),
        entry_id=f"entry-{rank}",
        entry_version_id=f"entry-{rank}-v1",
        text=f"candidate {rank}",
        score=1 / rank,
        matched_by=("fts",),
    )


def _scoring_client(scores: dict[int, float], seen: list[_Request]) -> httpx.AsyncClient:
    """Return a client whose service scores every requested candidate from a fixed table."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(
            200,
            json={"scores": [{"rank": item["rank"], "score": scores[item["rank"]]} for item in body["candidates"]]},
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_batches_candidates_and_selects_the_global_best_ranks() -> None:
    async def scenario() -> None:
        seen: list[_Request] = []
        # Ranks 2 and 3 tie on score, so the original coarse order decides between them.
        scores = {1: 0.10, 2: 0.90, 3: 0.90, 4: 0.20, 5: 0.95}
        async with _scoring_client(scores, seen) as client:
            reranker = HTTPMemoryReranker("https://reranker.invalid/score", client=client, batch_size=2)

            decision = await reranker.rerank("query", tuple(_hit(rank) for rank in range(1, 6)), 3)

        assert decision.selected_ranks == (5, 2, 3)
        assert decision.usage.requests == 3
        assert decision.used_fallback is False
        assert decision.discarded_rank_count == 0
        assert len(seen) == 3
        assert [len(body["candidates"]) for body in seen] == [2, 2, 1]
        assert sorted(item["rank"] for body in seen for item in body["candidates"]) == [1, 2, 3, 4, 5]
        assert all(body["query"] == "query" for body in seen)
        assert all(item["text"] == _hit(item["rank"]).text for body in seen for item in body["candidates"])

    asyncio.run(scenario())


def test_a_later_batch_can_outrank_an_earlier_batch() -> None:
    async def scenario() -> None:
        seen: list[_Request] = []
        scores = {1: 0.99, 2: 0.01, 3: 0.50}
        async with _scoring_client(scores, seen) as client:
            reranker = HTTPMemoryReranker("https://reranker.invalid/score", client=client, batch_size=2)

            decision = await reranker.rerank("query", tuple(_hit(rank) for rank in range(1, 4)), 2)

        assert decision.selected_ranks == (1, 3)
        assert decision.usage.requests == 2

    asyncio.run(scenario())


def test_accepts_out_of_order_scores() -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={"scores": [{"rank": 2, "score": 0.1}, {"rank": 1, "score": 0.8}]},
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            reranker = HTTPMemoryReranker("https://reranker.invalid/score", client=client, batch_size=2)

            decision = await reranker.rerank("query", (_hit(1), _hit(2)), 2)

        assert decision.selected_ranks == (1, 2)
        assert decision.usage.requests == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "payload",
    [
        {"scores": [], "extra": True},
        {"scores": [{"rank": 1, "score": 0.5}], "extra": True},
        {"scores": [{"rank": 1}]},
        {"scores": [{"score": 0.5}]},
        {"scores": [{"rank": 1, "score": 0.5, "extra": True}]},
        {"scores": [{"rank": 1, "score": float("nan")}]},
        {"scores": [{"rank": 1, "score": float("inf")}]},
        {"scores": [{"rank": 1, "score": "0.5"}]},
        {"scores": [{"rank": 1, "score": 0.5}, {"rank": 1, "score": 0.4}]},
        {"scores": [{"rank": 2, "score": 0.5}]},
        {"scores": [{"rank": 1, "score": 0.5}, {"rank": 2, "score": 0.4}]},
        {"scores": {}},
        {"results": []},
        [],
    ],
)
def test_rejects_invalid_response_schema(payload: object) -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=json.dumps(payload).encode()))
        async with httpx.AsyncClient(transport=transport) as client:
            reranker = HTTPMemoryReranker("https://reranker.invalid/score", client=client, batch_size=2)

            with pytest.raises(ValueError):
                await reranker.rerank("query", (_hit(1),), 1)

    asyncio.run(scenario())


def test_rejects_a_response_that_is_not_json() -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(lambda _request: httpx.Response(200, content=b"not json"))
        async with httpx.AsyncClient(transport=transport) as client:
            reranker = HTTPMemoryReranker("https://reranker.invalid/score", client=client)

            with pytest.raises(ValueError):
                await reranker.rerank("query", (_hit(1),), 1)

    asyncio.run(scenario())


def test_rejects_invalid_arguments() -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"scores": [{"rank": 1, "score": 0.5}]})
        )
        async with httpx.AsyncClient(transport=transport) as client:
            reranker = HTTPMemoryReranker("https://reranker.invalid/score", client=client, batch_size=2)

            with pytest.raises(ValueError):
                await reranker.rerank("query", (), 1)
            with pytest.raises(ValueError):
                await reranker.rerank("query", (_hit(1),), 0)
            with pytest.raises(ValueError):
                await reranker.rerank("query", (_hit(1),), 2)
            with pytest.raises(ValueError):
                await reranker.rerank("query", (_hit(1),), 1.5)  # ty: ignore[invalid-argument-type]
            with pytest.raises(ValueError):
                HTTPMemoryReranker("https://reranker.invalid/score", client=client, batch_size=0)
            with pytest.raises(ValueError):
                HTTPMemoryReranker("https://reranker.invalid/score", client=client, batch_size=-1)

    asyncio.run(scenario())


def test_preserves_http_status_errors() -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(lambda _request: httpx.Response(503, json={"scores": []}))
        async with httpx.AsyncClient(transport=transport) as client:
            reranker = HTTPMemoryReranker("https://reranker.invalid/score", client=client)

            with pytest.raises(httpx.HTTPStatusError):
                await reranker.rerank("query", (_hit(1),), 1)

    asyncio.run(scenario())


def test_preserves_transport_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reranker = HTTPMemoryReranker("https://reranker.invalid/score", client=client)

            with pytest.raises(httpx.ConnectError):
                await reranker.rerank("query", (_hit(1),), 1)

    asyncio.run(scenario())


def test_reranker_selects_hits_inside_native_memory_search() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={"scores": [{"rank": item["rank"], "score": float(item["rank"])} for item in body["candidates"]]},
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reranker = HTTPMemoryReranker("https://reranker.invalid/score", client=client, batch_size=1)
            async with open_builtin_contexts(
                BuiltinConfig(database=SQLiteConfig()), memory_reranker=reranker
            ) as contexts:
                service = (await contexts.get("http-reranker-native")).artifacts.memory
                memory = await service.remember(
                    memory=None,
                    entries=(
                        MemoryEntryInput(kind="fact", text="Project alpha fact."),
                        MemoryEntryInput(kind="fact", text="Project beta fact."),
                    ),
                    mode="append",
                )
                assert memory is not None

                result = await service.search("project", memories=(memory,), limit=1, mode="fts")

        assert result.rerank is not None
        assert result.rerank.policy_id == "http-cross-encoder-v1"
        assert result.rerank.usage.requests == 2
        assert result.rerank.selected_ranks == (2,)
        # Reranking reorders the existing hits; it never rewrites their identity.
        assert len(result.hits) == 1
        assert result.hits[0].entry_version_id == result.rerank.candidate_hits[1].entry_version_id
        assert result.hits[0].entry_id == result.rerank.candidate_hits[1].entry_id
        assert result.hits[0].text == result.rerank.candidate_hits[1].text
