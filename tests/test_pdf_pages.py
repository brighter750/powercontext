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

import asyncio
import hashlib
import io
import re
from collections.abc import Sequence

import pytest
from pydantic import ValidationError
from pypdf import PdfWriter

from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    SourceDefinitionRegistry,
    SourceMaterialization,
    project_source_for_transport,
)
from powercontext.sources.pdf_pages import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
    PDFPageInput,
    PDFPageSource,
    PDFPageSourceAdapter,
    PDFPageTextEvidenceProjection,
    parse_pdf_document,
    pdf_page_source_id,
)


def _escape_pdf_text(value: str) -> str:
    return value.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _pdf_bytes(pages: Sequence[Sequence[str]], *, broken_page: int | None = None) -> bytes:
    """Build a minimal PDF whose pages carry the given text lines.

    The document is written by hand so a test can place an unreadable content
    stream on one page while the other pages stay readable.
    """

    page_count = len(pages)
    kids = " ".join(f"{4 + 2 * index} 0 R" for index in range(page_count))
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode("latin-1"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for index, lines in enumerate(pages):
        content_object = 5 + 2 * index
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_object} 0 R >>"
            ).encode("latin-1")
        )
        if broken_page == index:
            payload = b"content the reader cannot decode"
            objects.append(
                b"<< /Length "
                + str(len(payload)).encode("latin-1")
                + b" /Filter /NoSuchCodec >>\nstream\n"
                + payload
                + b"\nendstream"
            )
            continue
        if not lines:
            objects.append(b"<< /Length 0 >>\nstream\n\nendstream")
            continue
        commands: list[str] = []
        baseline = 720
        for line in lines:
            commands.append(f"BT /F1 12 Tf 72 {baseline} Td ({_escape_pdf_text(line)}) Tj ET")
            baseline -= 14
        payload = ("\n".join(commands) + "\n").encode("latin-1")
        objects.append(b"<< /Length " + str(len(payload)).encode("latin-1") + b" >>\nstream\n" + payload + b"endstream")

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


def _blank_pdf_bytes(count: int) -> bytes:
    writer = PdfWriter()
    for _ in range(count):
        writer.add_blank_page(width=100, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _parse(content: bytes, document_id: str = "report"):
    return parse_pdf_document(PDFDocumentInput(document_id=document_id, content=content))


# --- parsing -----------------------------------------------------------------


def test_parse_returns_every_physical_page_in_document_order() -> None:
    content = _pdf_bytes([["Alpha revenue"], ["Beta revenue"], ["Gamma revenue"]])

    pages = _parse(content)

    assert [page.page_number for page in pages] == [1, 2, 3]
    assert all(page.document_id == "report" for page in pages)
    assert "Alpha revenue" in pages[0].text
    assert "Beta revenue" in pages[1].text
    assert "Gamma revenue" in pages[2].text


def test_parse_reports_the_digest_of_the_complete_document() -> None:
    content = _pdf_bytes([["Alpha revenue"], ["Beta revenue"]])

    pages = _parse(content)

    expected = hashlib.sha256(content).hexdigest()
    assert {page.document_sha256 for page in pages} == {expected}
    assert expected == expected.lower()


def test_parse_keeps_blank_pages_as_empty_text() -> None:
    pages = _parse(_blank_pdf_bytes(2))

    assert [(page.page_number, page.text) for page in pages] == [(1, ""), (2, "")]


def test_parse_keeps_a_blank_page_between_text_pages() -> None:
    content = _pdf_bytes([["Alpha revenue"], [], ["Gamma revenue"]])

    pages = _parse(content)

    assert [(page.page_number, page.text) for page in pages] == [
        (1, "Alpha revenue"),
        (2, ""),
        (3, "Gamma revenue"),
    ]


def test_parse_does_not_truncate_page_text() -> None:
    lines = [f"Line {index} of the financial statement" for index in range(40)]
    content = _pdf_bytes([lines])

    page = _parse(content)[0]

    for line in lines:
        assert line in page.text


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"not a PDF",
        b"%PDF-1.7\ntruncated",
        b"%PDF-1.4\n",
    ],
)
def test_parse_rejects_unusable_documents(content: bytes) -> None:
    with pytest.raises(ValueError):
        _parse(content)


def test_parse_rejects_a_document_without_pages() -> None:
    writer = PdfWriter()
    buffer = io.BytesIO()
    writer.write(buffer)

    with pytest.raises(ValueError):
        _parse(buffer.getvalue())


def test_parse_rejects_a_document_with_a_damaged_header() -> None:
    content = bytearray(_pdf_bytes([["Alpha revenue"]]))
    content[:5] = b"XXXXX"

    with pytest.raises(ValueError):
        _parse(bytes(content))


def test_parse_fails_atomically_when_a_page_cannot_be_read() -> None:
    content = _pdf_bytes([["Alpha revenue"], ["Beta revenue"]], broken_page=1)

    with pytest.raises(ValueError):
        _parse(content)


def test_parse_rejects_a_document_that_lost_one_of_its_pages() -> None:
    """A damaged page tree must not turn into a document with fewer pages."""

    content = _pdf_bytes([["Alpha revenue"], ["Beta revenue"]])
    damaged = re.sub(rb"\n4 0 obj\n<< /Type /Page .*?endobj\n", b"", content, count=1, flags=re.DOTALL)

    assert damaged != content
    with pytest.raises(ValueError):
        _parse(damaged)


def test_parse_rejects_a_document_whose_page_count_does_not_match_its_pages() -> None:
    content = _pdf_bytes([["Alpha revenue"], ["Beta revenue"]])
    damaged = content.replace(b"/Count 2", b"/Count 3")

    assert damaged != content
    with pytest.raises(ValueError):
        _parse(damaged)


def test_parse_reads_a_document_whose_cross_reference_table_can_be_repaired() -> None:
    """A damaged cross reference table is repaired, so every page is still there."""

    content = _pdf_bytes([["Alpha revenue"], ["Beta revenue"]])
    damaged = re.sub(rb"\n(\d{10}) 00000 n", rb"\n9999999999 00000 n", content)

    assert damaged != content
    pages = _parse(damaged)

    assert [page.page_number for page in pages] == [1, 2]
    assert [page.text for page in pages] == ["Alpha revenue", "Beta revenue"]


def test_parse_reads_a_document_with_an_empty_user_password() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=200)
    writer.encrypt(user_password="", owner_password="owner")  # noqa: S106
    buffer = io.BytesIO()
    writer.write(buffer)

    pages = _parse(buffer.getvalue())

    assert len(pages) == 1
    assert pages[0].page_number == 1


def test_parse_rejects_a_document_that_needs_a_real_password() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=200)
    writer.encrypt(user_password="secret", owner_password="owner")  # noqa: S106
    buffer = io.BytesIO()
    writer.write(buffer)

    with pytest.raises(ValueError):
        _parse(buffer.getvalue())


@pytest.mark.parametrize("document_id", ["", " ", "\t\n"])
def test_document_id_rejects_empty_or_blank(document_id: str) -> None:
    with pytest.raises(ValidationError):
        PDFDocumentInput(document_id=document_id, content=b"%PDF-1.4\n")


# --- identity ----------------------------------------------------------------


def test_source_id_carries_the_document_digest_and_page_number() -> None:
    digest = "a" * 64

    source_id = pdf_page_source_id("PEPSICO_2022_10K", digest, 7)

    assert source_id == f"pdf-page:PEPSICO_2022_10K:{digest}:7"
    assert len(source_id) <= 256


def test_source_id_is_stable_for_the_same_page() -> None:
    digest = "b" * 64

    assert pdf_page_source_id("report", digest, 3) == pdf_page_source_id("report", digest, 3)


def test_source_id_changes_with_the_document_digest() -> None:
    assert pdf_page_source_id("report", "c" * 64, 1) != pdf_page_source_id("report", "d" * 64, 1)


def test_source_id_changes_with_the_page_number() -> None:
    digest = "e" * 64

    assert pdf_page_source_id("report", digest, 1) != pdf_page_source_id("report", digest, 2)


def test_source_id_separates_documents_that_share_their_bytes() -> None:
    digest = "f" * 64

    assert pdf_page_source_id("report-a", digest, 1) != pdf_page_source_id("report-b", digest, 1)


def test_source_id_bounds_a_long_document_id() -> None:
    digest = "0" * 64
    document_id = "x" * 400

    source_id = pdf_page_source_id(document_id, digest, 1)

    assert len(source_id) <= 256
    assert source_id.startswith("pdf-page:")
    assert source_id.endswith(f":{digest}:1")
    assert source_id == pdf_page_source_id(document_id, digest, 1)
    assert source_id != pdf_page_source_id("y" * 400, digest, 1)


@pytest.mark.parametrize(
    ("document_id", "digest", "page_number"),
    [
        ("", "a" * 64, 1),
        (" ", "a" * 64, 1),
        ("report", "", 1),
        ("report", "a" * 64, 0),
    ],
)
def test_source_id_rejects_invalid_arguments(document_id: str, digest: str, page_number: int) -> None:
    with pytest.raises(ValueError):
        pdf_page_source_id(document_id, digest, page_number)


# --- definition, projection, transport ---------------------------------------


def test_definition_has_the_expected_identity() -> None:
    definition = PDF_PAGE_SOURCE_DEFINITION

    assert definition.name == "pdf-page"
    assert definition.version == "1"
    assert definition.input_class is PDFPageInput
    assert definition.source_class is PDFPageSource
    assert len(definition.projections) == 1
    assert isinstance(definition.projections[0], PDFPageTextEvidenceProjection)


def test_adapter_resolves_and_reads_a_captured_page() -> None:
    page = PDFPageInput(
        document_id="report",
        document_sha256="a" * 64,
        page_number=4,
        text="Revenue grew",
    )
    adapter = PDFPageSourceAdapter()

    async def scenario() -> None:
        source = await adapter.resolve(page)

        assert source.name == source.source_id
        assert source.source_id == f"pdf-page:report:{'a' * 64}:4"
        assert source.materialization is SourceMaterialization.CAPTURED
        assert await adapter.read(source) == page

    asyncio.run(scenario())


def test_projection_has_exact_content_and_metadata() -> None:
    source = PDFPageSource(
        name="pdf-page:report:" + "a" * 64 + ":4",
        source_id="pdf-page:report:" + "a" * 64 + ":4",
        materialization=SourceMaterialization.CAPTURED,
        document_id="report",
        document_sha256="a" * 64,
        page_number=4,
        text="Revenue grew",
    )

    evidence = PDFPageTextEvidenceProjection().project(source)

    assert evidence.source_type == "pdf-page"
    assert evidence.source_id == source.source_id
    assert evidence.content == "report\nRevenue grew"
    assert evidence.metadata == {
        "document_id": "report",
        "document_sha256": "a" * 64,
        "page_number": 4,
    }
    assert len(evidence.metadata) == 3


def test_registry_resolves_reads_projects_and_transports_a_page() -> None:
    async def scenario() -> None:
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        pages = _parse(_pdf_bytes([["Alpha revenue"], ["Beta revenue"]]))
        assert len(pages) == 2

        source = await registry.resolve(pages[0])

        assert isinstance(source, PDFPageSource)
        assert source.materialization is SourceMaterialization.CAPTURED
        assert await registry.read(source) == pages[0]
        assert registry.projection_keys(source) == (TEXT_EVIDENCE_PROJECTION_KEY,)

        evidence = registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY)
        assert evidence == {
            "source_type": "pdf-page",
            "source_id": source.source_id,
            "content": "report\nAlpha revenue",
            "metadata": {
                "document_id": "report",
                "document_sha256": pages[0].document_sha256,
                "page_number": 1,
            },
        }

        observation = project_source_for_transport(registry, source)

        assert observation.source_type == "pdf-page"
        assert observation.name == source.source_id
        assert observation.materialization is SourceMaterialization.CAPTURED

    asyncio.run(scenario())


def test_every_page_of_a_document_gets_its_own_identity() -> None:
    async def scenario() -> None:
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        pages = _parse(_pdf_bytes([["Alpha revenue"], ["Beta revenue"], ["Gamma revenue"]]))

        sources = [await registry.resolve(page) for page in pages]

        assert len({source.name for source in sources}) == len(pages)

    asyncio.run(scenario())
