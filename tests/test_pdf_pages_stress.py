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

"""Stress tests for retrieving PDF pages out of a group of documents.

The tests cover the situations a group of documents creates: two revisions of
one document, two documents that hold the same page number, a group that holds
several documents, and a document that repeats one page many times.
"""

from __future__ import annotations

import asyncio
import io
import unicodedata
from pathlib import Path

import pytest
from pypdf import PdfReader

from powercontext.builtin.runtime import GetMemoryEntryRequest, open_builtin_runtime
from powercontext.sources.pdf_pages import PDFDocumentInput, parse_pdf_document, search_pdf_pages

from .test_pdf_pages import _pdf_bytes
from .test_pdf_pages_search import _pages_of, _prepare

# Two revisions of one document. They differ in their bytes, so they differ in
# their digest and in their identity, while they name the same logical page.
_REVISION_OLD = _pdf_bytes([
    ["The registrant discloses its legacy contingencies in the notes to the accounts."],
])
_REVISION_NEW = _pdf_bytes([
    ["The registrant discloses its modern contingencies in the notes to the accounts."],
])

# One page that two documents hold under the same page number.
_SHARED_PAGE = _pdf_bytes([
    ["The registrant states its shared subject in the notes to the accounts."],
])

# A document that repeats one page and holds a page of its own.
_REPEATED = _pdf_bytes(
    [
        ["The company states its ordinary subject in the notes to the accounts."],
    ]
    * 12
    + [
        ["The company states its distinctive subject in the notes to the accounts."],
    ]
)

# Two revisions of one document that carry the same wording. They differ in
# their bytes, so they differ in their digest and in their identity, while they
# name the same logical page and answer a question equally well.
_REVISION_SAME_OLD = _REVISION_OLD + b"% first copy of the same wording\n"
_REVISION_SAME_NEW = _REVISION_OLD + b"% second copy of the same wording\n"


async def _search(config, scope_id, tmp_path: Path, question: str, limit: int):
    """Search a scope and return the citations, their pages and their texts."""

    async with open_builtin_runtime(config, scheduler_path=tmp_path / "scheduler.db") as runtime:
        memory = runtime.memory.for_scope(scope_id)
        citations = await search_pdf_pages(memory, question, limit=limit)
        locations = await _pages_of(memory, citations)
        texts = [record.entry.text for record in await _records(memory, citations)]
    return citations, locations, texts


async def _records(memory, citations):
    return [await memory.get(GetMemoryEntryRequest(citation=citation)) for citation in citations]


def test_two_revisions_of_one_document_do_not_answer_with_the_same_page_twice(tmp_path: Path) -> None:
    async def scenario() -> None:
        config, scope_id, _pages = await _prepare(tmp_path, [("revision", _REVISION_OLD), ("revision", _REVISION_NEW)])

        citations, locations, _texts = await _search(
            config, scope_id, tmp_path, "contingencies notes accounts registrant", limit=10
        )

        assert citations
        assert len(locations) == len(citations)
        assert locations == {("revision", 1)}

    asyncio.run(scenario())


def test_the_citation_of_an_old_revision_still_resolves_to_the_old_revision(tmp_path: Path) -> None:
    async def scenario() -> None:
        config, scope_id, _pages = await _prepare(tmp_path, [("revision", _REVISION_OLD), ("revision", _REVISION_NEW)])

        citations, _locations, texts = await _search(config, scope_id, tmp_path, "legacy contingencies", limit=10)

        assert citations
        assert any("legacy contingencies" in text for text in texts)
        assert not any("modern contingencies" in text for text in texts)

    asyncio.run(scenario())


def test_the_same_page_number_of_two_documents_stays_separate(tmp_path: Path) -> None:
    async def scenario() -> None:
        config, scope_id, _pages = await _prepare(tmp_path, [("first", _SHARED_PAGE), ("second", _SHARED_PAGE)])

        citations, locations, _texts = await _search(
            config, scope_id, tmp_path, "shared subject statements notes", limit=10
        )

        assert citations
        assert len(locations) == len(citations)
        assert ("first", 1) in locations
        assert ("second", 1) in locations

    asyncio.run(scenario())


def test_a_group_of_documents_keeps_the_pages_of_every_document(tmp_path: Path) -> None:
    async def scenario() -> None:
        config, scope_id, _pages = await _prepare(
            tmp_path,
            [("first", _REVISION_OLD), ("second", _SHARED_PAGE), ("third", _REPEATED)],
        )

        citations, locations, _texts = await _search(
            config, scope_id, tmp_path, "subject statements notes accounts", limit=20
        )

        assert citations
        assert len(locations) == len(citations)
        assert {document for document, _page in locations} == {"first", "second", "third"}

    asyncio.run(scenario())


def test_repeated_pages_of_one_document_stay_distinct_pages(tmp_path: Path) -> None:
    async def scenario() -> None:
        config, scope_id, _pages = await _prepare(tmp_path, [("repeated", _REPEATED)])

        citations, locations, _texts = await _search(
            config, scope_id, tmp_path, "company ordinary subject notes", limit=5
        )

        assert len(citations) == 5
        assert len(locations) == 5
        assert {page for _document, page in locations} <= set(range(1, 14))

    asyncio.run(scenario())


@pytest.mark.parametrize("limit", [1, 10, 20])
def test_the_result_holds_no_duplicate_logical_page(tmp_path: Path, limit: int) -> None:
    async def scenario() -> None:
        config, scope_id, _pages = await _prepare(
            tmp_path, [("revision", _REVISION_OLD), ("revision", _REVISION_NEW), ("other", _SHARED_PAGE)]
        )

        citations, locations, _texts = await _search(
            config, scope_id, tmp_path, "subject statements notes accounts registrant", limit=limit
        )

        assert len(citations) <= limit
        assert len(locations) == len(citations)

    asyncio.run(scenario())


def test_the_group_still_answers_after_the_runtime_is_reopened(tmp_path: Path) -> None:
    async def scenario() -> None:
        config, scope_id, _pages = await _prepare(
            tmp_path, [("revision", _REVISION_OLD), ("revision", _REVISION_NEW), ("other", _SHARED_PAGE)]
        )

        before, _locations, _texts = await _search(
            config, scope_id, tmp_path, "subject statements notes accounts registrant", limit=20
        )
        after, _locations, _texts = await _search(
            config, scope_id, tmp_path, "subject statements notes accounts registrant", limit=20
        )

        assert before
        assert after == before

    asyncio.run(scenario())


def _pdf_with_unicode_page(text: str) -> bytes:
    """Build a PDF whose single page carries the given text through a ToUnicode map.

    The map is what lets a test place text that latin-1 cannot carry, such as a
    decomposed character, and what makes the reader report that exact text.
    """

    encoded = text.encode("utf-16-be").hex().upper()
    cmap = (
        "/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
        "/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
        "1 begincodespacerange\n<00> <FF>\nendcodespacerange\n"
        f"1 beginbfchar\n<41> <{encoded}>\nendbfchar\n"
        "endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n"
    ).encode("latin-1")
    content = b"BT /F1 12 Tf 72 720 Td <41> Tj ET\n"
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [5 0 R] /Count 1 >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /ToUnicode 4 0 R >>",
        b"<< /Length " + str(len(cmap)).encode("latin-1") + b" >>\nstream\n" + cmap + b"endstream",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 3 0 R >> >> /Contents 6 0 R >>",
        b"<< /Length " + str(len(content)).encode("latin-1") + b" >>\nstream\n" + content + b"endstream",
    ]
    document = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(document))
        document += f"{number} 0 obj\n".encode("latin-1") + body + b"\nendobj\n"
    xref_offset = len(document)
    document += f"xref\n0 {len(objects) + 1}\n".encode("latin-1")
    document += b"0000000000 65535 f \n"
    for offset in offsets:
        document += f"{offset:010d} 00000 n \n".encode("latin-1")
    document += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n").encode(
        "latin-1"
    )
    return bytes(document)


def test_a_decomposed_page_is_stored_in_the_form_the_source_reports(tmp_path: Path) -> None:
    """The stored body of a page must be the body the source reports.

    The Memory normalizes every entry body to Unicode NFC before it stores it.
    A page that carries a decomposed character would therefore be stored in
    another form than the page reports, and a reader that compares the two
    would see different texts for one and the same page.
    """

    async def scenario() -> None:
        decomposed = "cafe" + chr(0x301) + " revenue"
        content = _pdf_with_unicode_page(decomposed)
        reader = PdfReader(io.BytesIO(content))
        raw = (reader.pages[0].extract_text() or "").strip()
        assert raw == decomposed
        assert unicodedata.normalize("NFC", raw) != raw

        parsed = parse_pdf_document(PDFDocumentInput(document_id="unicode", content=content))
        assert parsed[0].text == unicodedata.normalize("NFC", decomposed)

        config, scope_id, _pages = await _prepare(tmp_path, [("unicode", content)])
        async with open_builtin_runtime(config, scheduler_path=tmp_path / "scheduler.db") as runtime:
            memory = runtime.memory.for_scope(scope_id)
            citations = await search_pdf_pages(memory, "revenue", limit=5)
            assert citations
            records = await _records(memory, citations)
            for record in records:
                assert record.entry.text == f"unicode\n{parsed[0].text}"

    asyncio.run(scenario())


def test_the_citation_of_a_revised_page_is_the_same_in_every_run(tmp_path: Path) -> None:
    """Two revisions that answer equally well must not decide the citation by chance.

    The Memory ranks entries that carry the same text by an identity that
    changes between runs, so the revision that answers a question could change
    from one run to the next. The citation of such a page is resolved by the
    stable source identity instead, so the same input always yields the same
    citation.
    """

    async def scenario() -> None:
        chosen: set[tuple[str, ...]] = set()
        for attempt in range(20):
            directory = tmp_path / f"attempt-{attempt}"
            directory.mkdir()
            config, scope_id, _pages = await _prepare(
                directory, [("revision", _REVISION_SAME_OLD), ("revision", _REVISION_SAME_NEW)]
            )
            async with open_builtin_runtime(config, scheduler_path=directory / "scheduler.db") as runtime:
                memory = runtime.memory.for_scope(scope_id)
                citations = await search_pdf_pages(memory, "contingencies notes registrant", limit=5)
                assert len(citations) == 1
                records = await _records(memory, citations)
                chosen.add(tuple(sorted(reference.source_id for reference in records[0].entry.sources)))

        assert len(chosen) == 1

    asyncio.run(scenario())
