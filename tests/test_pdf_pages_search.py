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

"""Behaviour tests for searching stored PDF pages through the Memory API."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    SubmitSourceObservation,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    SourceDefinitionRegistry,
    manifest_for_definition,
    project_source_for_transport,
)
from powercontext.sources.pdf_pages import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
    parse_pdf_document,
    search_pdf_pages,
)

from .test_pdf_pages import _pdf_bytes


class _EvidencePipeline:
    """Store one Memory entry per captured page, like the acceptance harness."""

    async def extract(self, request, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(
                kind="source-evidence",
                text=source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"],
                sources=(source,),
            )
            for source in request.sources
            if source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"].strip()
        )


def _config(tmp_path: Path) -> BuiltinConfig:
    return BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'pages.db'}"))


async def _ingest(
    config: BuiltinConfig,
    tmp_path: Path,
    documents: Sequence[tuple[str, bytes]],
) -> tuple[str, int]:
    """Submit every page of every document and return the scope and page count."""

    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    count = 0
    async with open_builtin_runtime(config, scheduler_path=tmp_path / "scheduler.db") as runtime:
        assert runtime.scopes is not None
        scope = await runtime.scopes.create(ScopeDraft(title="PDF", summary="Pages", idempotency_key="pdf-pages"))
        await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
        for document_id, content in documents:
            for page in parse_pdf_document(PDFDocumentInput(document_id=document_id, content=content)):
                source = await registry.resolve(page)
                observation = project_source_for_transport(registry, source)
                await runtime.ingestion.submit(
                    SubmitSourceObservation(scope_id=scope.scope_id, observation=observation)
                )
                count += 1
    return scope.scope_id, count


async def _store(config: BuiltinConfig, tmp_path: Path, scope_id: str, pages: int) -> None:
    """Flush the captured pages into the Memory index."""

    async with open_builtin_runtime(
        config,
        candidate_pipeline=_EvidencePipeline(),
        scheduler_path=tmp_path / "scheduler.db",
    ) as runtime:
        memory = runtime.memory.for_scope(scope_id)
        for _ in range(pages + 1):
            if not (await memory.flush()).processed:
                break


async def _prepare(tmp_path: Path, documents: Sequence[tuple[str, bytes]]):
    config = _config(tmp_path)
    scope_id, pages = await _ingest(config, tmp_path, documents)
    await _store(config, tmp_path, scope_id, pages)
    return config, scope_id, pages


_ALPHA = _pdf_bytes([
    ["PepsiCo operates in seven geographic regions"],
    ["Legal proceedings and litigation matters are disclosed"],
    ["Restructuring costs and charges for fiscal 2022"],
    ["Net revenue increased in the North America segment"],
])
_BETA = _pdf_bytes([
    ["Legal proceedings for the beta entity are immaterial"],
    ["Restructuring costs for the beta entity were recorded"],
])
_REPEATED = _pdf_bytes([
    ["The company and its business and its results are reported every year"],
    ["The company and its business and its results are reported every year"],
    ["The company and its business and its results are reported every year"],
    ["The company and its distinctive subject matter is reported once"],
])
# A document whose evidence page names its subject in a heading, against pages
# that repeat the wording of the question in running prose. The evidence page
# shares the fewest words with the question, so only the line that states the
# subject can lift it into the answer.
_HEADED_LINES = {
    "evidence": [
        "Item 3. Legal Proceedings.",
        "We and our subsidiaries are party to a variety of litigation, claims, legal or regulatory",
        "proceedings, inquiries and investigations whose results cannot be predicted with certainty.",
        "Management believes that the final outcome of the foregoing will not have a material adverse",
        "effect on the financial condition, results of operations or cash flows of the registrant.",
    ],
    "subject": [
        "The legal department reviews every legal claim that the business receives.",
        "Each legal matter is handled by the legal counsel of the registrant.",
        "The legal obligations of the entity are listed in the notes to the accounts.",
    ],
    "common": [
        "The board and its committees assist the management in its oversight of the",
        "compliance with legal and regulatory requirements across the business units.",
    ],
    "materially": [
        "There were no changes in internal control over financial reporting during the fourth",
        "quarter of the year that have materially affected the ability to report on time.",
    ],
    "important": [
        "The summary is not complete, and the reader should read the underlying agreement for",
        "the provisions which may be important to you before deciding whether to invest.",
    ],
    "ongoing": [
        "The committee meets regularly to identify, assess, prioritize and address the ongoing",
        "operational risks that the business faces and to report the outcome of that work.",
    ],
    "reported": [
        "The registrant reported that its financial statements were prepared in accordance with",
        "the accounting principles generally accepted in the United States of America.",
    ],
    "filler": [
        "The notes to the consolidated financial statements summarize the basis of presentation and",
        "the significant accounting policies applied in the preparation of those statements.",
    ],
}
_HEADED = _pdf_bytes(
    [list(_HEADED_LINES["evidence"]), list(_HEADED_LINES["subject"])]
    + [list(_HEADED_LINES["common"])] * 8
    + [list(_HEADED_LINES[kind]) for kind in ("materially", "important", "ongoing", "reported")]
    + [list(_HEADED_LINES["filler"])] * 8
)


async def _pages_of(memory, citations) -> set[tuple[str, int]]:
    """Resolve every citation back to the document and physical page it names."""

    found: set[tuple[str, int]] = set()
    for citation in citations:
        record = await memory.get(GetMemoryEntryRequest(citation=citation))
        for reference in record.entry.sources:
            source_id = reference.source_id
            _prefix, remainder = source_id.split(":", 1)
            document_id, _digest, page_number = remainder.rsplit(":", 2)
            found.add((document_id, int(page_number)))
    return found


def test_search_returns_real_citations_that_resolve_to_their_page(tmp_path: Path) -> None:
    async def scenario() -> None:
        config, scope_id, pages = await _prepare(tmp_path, [("alpha", _ALPHA)])
        assert pages == 4

        async with open_builtin_runtime(config, scheduler_path=tmp_path / "scheduler.db") as runtime:
            memory = runtime.memory.for_scope(scope_id)
            citations = await search_pdf_pages(memory, "What are the geographies Pepsico operates in?", limit=3)

            assert 1 <= len(citations) <= 3
            identities = {
                (citation.memory_ref.artifact_id, citation.entry_id, citation.entry_version_id)
                for citation in citations
            }
            assert len(identities) == len(citations)

            pages_found = await _pages_of(memory, citations)
            assert len(pages_found) == len(citations)
            assert ("alpha", 1) in pages_found

    asyncio.run(scenario())


def test_search_respects_the_limit_and_returns_distinct_pages(tmp_path: Path) -> None:
    async def scenario() -> None:
        config, scope_id, _pages = await _prepare(tmp_path, [("alpha", _ALPHA)])

        async with open_builtin_runtime(config, scheduler_path=tmp_path / "scheduler.db") as runtime:
            memory = runtime.memory.for_scope(scope_id)
            citations = await search_pdf_pages(memory, "legal proceedings litigation", limit=2)

            assert len(citations) <= 2
            pages_found = await _pages_of(memory, citations)
            assert len(pages_found) == len(citations)

    asyncio.run(scenario())


def test_search_returns_nothing_for_a_blank_question(tmp_path: Path) -> None:
    async def scenario() -> None:
        config, scope_id, _pages = await _prepare(tmp_path, [("alpha", _ALPHA)])

        async with open_builtin_runtime(config, scheduler_path=tmp_path / "scheduler.db") as runtime:
            memory = runtime.memory.for_scope(scope_id)

            assert await search_pdf_pages(memory, "", limit=5) == ()
            assert await search_pdf_pages(memory, "   ", limit=5) == ()

    asyncio.run(scenario())


def test_search_returns_nothing_when_the_limit_is_not_positive(tmp_path: Path) -> None:
    async def scenario() -> None:
        config, scope_id, _pages = await _prepare(tmp_path, [("alpha", _ALPHA)])

        async with open_builtin_runtime(config, scheduler_path=tmp_path / "scheduler.db") as runtime:
            memory = runtime.memory.for_scope(scope_id)

            assert await search_pdf_pages(memory, "legal proceedings", limit=0) == ()

    asyncio.run(scenario())


def test_search_returns_nothing_when_no_page_matches(tmp_path: Path) -> None:
    async def scenario() -> None:
        config, scope_id, _pages = await _prepare(tmp_path, [("alpha", _ALPHA)])

        async with open_builtin_runtime(config, scheduler_path=tmp_path / "scheduler.db") as runtime:
            memory = runtime.memory.for_scope(scope_id)

            assert await search_pdf_pages(memory, "zzzznotpresentzzzz", limit=5) == ()

    asyncio.run(scenario())


def test_search_keeps_same_page_numbers_of_different_documents_apart(tmp_path: Path) -> None:
    async def scenario() -> None:
        config, scope_id, _pages = await _prepare(tmp_path, [("alpha", _ALPHA), ("beta", _BETA)])

        async with open_builtin_runtime(config, scheduler_path=tmp_path / "scheduler.db") as runtime:
            memory = runtime.memory.for_scope(scope_id)
            citations = await search_pdf_pages(memory, "legal proceedings", limit=10)

            pages_found = await _pages_of(memory, citations)
            assert ("alpha", 2) in pages_found
            assert ("beta", 1) in pages_found

    asyncio.run(scenario())


def test_search_merges_pages_found_by_different_sub_queries(tmp_path: Path) -> None:
    async def scenario() -> None:
        config, scope_id, _pages = await _prepare(tmp_path, [("alpha", _ALPHA)])

        async with open_builtin_runtime(config, scheduler_path=tmp_path / "scheduler.db") as runtime:
            memory = runtime.memory.for_scope(scope_id)
            citations = await search_pdf_pages(
                memory,
                "What are the restructuring costs and the legal proceedings disclosed?",
                limit=10,
            )

            pages_found = await _pages_of(memory, citations)
            assert ("alpha", 2) in pages_found
            assert ("alpha", 3) in pages_found

    asyncio.run(scenario())


def test_search_handles_a_long_question_that_no_single_query_can_admit(tmp_path: Path) -> None:
    async def scenario() -> None:
        config, scope_id, _pages = await _prepare(tmp_path, [("alpha", _ALPHA)])

        long_question = (
            "What is the fiscal 2022 unadjusted EBITDA less capex for the company? "
            "Define unadjusted EBITDA as unadjusted operating income plus depreciation "
            "and amortization from the cash flow statement. Answer in USD millions, "
            "assuming the perspective of an investment analyst who can only use the "
            "details shown within the statement of cash flows and the income statement."
        )

        async with open_builtin_runtime(config, scheduler_path=tmp_path / "scheduler.db") as runtime:
            memory = runtime.memory.for_scope(scope_id)
            citations = await search_pdf_pages(memory, long_question, limit=10)

            assert citations
            pages_found = await _pages_of(memory, citations)
            assert len(pages_found) == len(citations)

    asyncio.run(scenario())


def test_search_returns_the_page_that_names_a_distinctive_term(tmp_path: Path) -> None:
    async def scenario() -> None:
        config, scope_id, _pages = await _prepare(tmp_path, [("repeated", _REPEATED)])

        async with open_builtin_runtime(config, scheduler_path=tmp_path / "scheduler.db") as runtime:
            memory = runtime.memory.for_scope(scope_id)
            citations = await search_pdf_pages(
                memory,
                "company distinctive subject matter",
                limit=1,
            )

            assert len(citations) == 1
            pages_found = await _pages_of(memory, citations)
            assert pages_found == {("repeated", 4)}

    asyncio.run(scenario())


def test_search_returns_the_page_that_states_the_subject_in_a_heading(tmp_path: Path) -> None:
    async def scenario() -> None:
        config, scope_id, _pages = await _prepare(tmp_path, [("headed", _HEADED)])

        async with open_builtin_runtime(config, scheduler_path=tmp_path / "scheduler.db") as runtime:
            memory = runtime.memory.for_scope(scope_id)
            citations = await search_pdf_pages(
                memory,
                "Has the company reported any materially important ongoing legal battles?",
                limit=5,
            )

            assert len(citations) == 5
            pages_found = await _pages_of(memory, citations)
            # The page that names the subject in its heading answers the question,
            # even though the other pages repeat more of the wording of it.
            assert ("headed", 1) in pages_found

    asyncio.run(scenario())


def test_search_ignores_terms_the_stored_pages_do_not_contain(tmp_path: Path) -> None:
    async def scenario() -> None:
        config, scope_id, _pages = await _prepare(tmp_path, [("alpha", _ALPHA)])

        async with open_builtin_runtime(config, scheduler_path=tmp_path / "scheduler.db") as runtime:
            memory = runtime.memory.for_scope(scope_id)
            citations = await search_pdf_pages(
                memory,
                "ebitda capex nonexistent restructuring costs",
                limit=3,
            )

            assert citations
            pages_found = await _pages_of(memory, citations)
            assert ("alpha", 3) in pages_found

    asyncio.run(scenario())


def test_search_finds_the_same_pages_after_the_runtime_is_reopened(tmp_path: Path) -> None:
    async def scenario() -> None:
        config, scope_id, _pages = await _prepare(tmp_path, [("alpha", _ALPHA), ("beta", _BETA)])

        async with open_builtin_runtime(config, scheduler_path=tmp_path / "scheduler.db") as runtime:
            memory = runtime.memory.for_scope(scope_id)
            before = await search_pdf_pages(memory, "legal proceedings litigation", limit=5)

        async with open_builtin_runtime(config, scheduler_path=tmp_path / "scheduler.db") as runtime:
            memory = runtime.memory.for_scope(scope_id)
            after = await search_pdf_pages(memory, "legal proceedings litigation", limit=5)

            assert after == before
            assert after

    asyncio.run(scenario())


def test_resubmitting_the_same_pages_does_not_add_sources(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = _config(tmp_path)
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))

        async with open_builtin_runtime(config, scheduler_path=tmp_path / "scheduler.db") as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(ScopeDraft(title="PDF", summary="Pages", idempotency_key="pdf-pages"))
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            pages = parse_pdf_document(PDFDocumentInput(document_id="alpha", content=_ALPHA))
            observations = []
            for page in pages:
                source = await registry.resolve(page)
                observation = project_source_for_transport(registry, source)
                observations.append(observation)
                await runtime.ingestion.submit(
                    SubmitSourceObservation(scope_id=scope.scope_id, observation=observation)
                )
            for observation in observations:
                await runtime.ingestion.submit(
                    SubmitSourceObservation(scope_id=scope.scope_id, observation=observation)
                )

        from powercontext.builtin.runtime import open_builtin_contexts

        async with open_builtin_contexts(config) as contexts:
            journal = await (await contexts.get(scope.scope_id)).sources.journal.entries()

        assert len(journal) == len(observations)

    asyncio.run(scenario())


@pytest.mark.parametrize("limit", [1, 3, 10])
def test_search_returns_at_most_the_requested_number_of_citations(tmp_path: Path, limit: int) -> None:
    async def scenario() -> None:
        config, scope_id, _pages = await _prepare(tmp_path, [("alpha", _ALPHA)])

        async with open_builtin_runtime(config, scheduler_path=tmp_path / "scheduler.db") as runtime:
            memory = runtime.memory.for_scope(scope_id)
            citations = await search_pdf_pages(memory, "PepsiCo revenue segment fiscal 2022", limit=limit)

            assert len(citations) <= limit

    asyncio.run(scenario())
