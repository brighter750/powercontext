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
from typing import Any

import pytest
from pydantic import ValidationError
from pypdf import PdfReader, PdfWriter

from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    SourceDefinitionRegistry,
    SourceMaterialization,
    pdf_pages,
    project_source_for_transport,
)
from powercontext.sources.pdf_pages import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
    PDFPageInput,
    PDFPageSource,
    PDFPageSourceAdapter,
    PDFPageTextEvidenceProjection,
    _reject_unreadable_content,
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


def _pdf_with_declared_contents(
    specs: Sequence[tuple[str | None, str | None]],
    extra: dict[int, bytes] | None = None,
    streams: dict[int, str] | None = None,
) -> bytes:
    """Build a PDF whose pages declare the given ``/Contents`` values.

    Every spec is a pair: the raw value of the ``/Contents`` entry of one page
    - ``None`` leaves the key out of the page - and the text the content
    stream of that page carries. A reference the document does not hold is
    written into the page as it is, so the reader has to resolve it to reach
    the content of the page. ``streams`` adds content stream objects under the
    given object numbers and ``extra`` adds raw objects, so a test can point a
    page at an array of streams that the document holds elsewhere.
    """

    count = len(specs)
    kids = " ".join(f"{4 + 2 * index} 0 R" for index in range(count))
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{kids}] /Count {count} >>".encode("latin-1"),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for index, (contents, text) in enumerate(specs):
        page_number = 4 + 2 * index
        content_number = 5 + 2 * index
        declared = "" if contents is None else f"/Contents {contents} "
        objects[page_number] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> {declared}>>"
        ).encode("latin-1")
        objects[content_number] = _content_stream(text)
    for number, text in (streams or {}).items():
        objects[number] = _content_stream(text)
    objects.update(extra or {})

    document = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(document)
        document += f"{number} 0 obj\n".encode("latin-1") + objects[number] + b"\nendobj\n"
    xref_offset = len(document)
    size = max(objects) + 1
    document += f"xref\n0 {size}\n".encode("latin-1")
    document += b"0000000000 65535 f \n"
    for number in range(1, size):
        document += (
            f"{offsets[number]:010d} 00000 n \n".encode("latin-1") if number in offsets else b"0000000000 65535 f \n"
        )
    document += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("latin-1")
    return bytes(document)


def _content_stream(text: str | None) -> bytes:
    """Build one content stream object that carries the given text."""

    payload = b"" if text is None else f"BT /F1 12 Tf 72 720 Td ({_escape_pdf_text(text)}) Tj ET\n".encode("latin-1")
    return b"<< /Length " + str(len(payload)).encode("latin-1") + b" >>\nstream\n" + payload + b"endstream"


def _parse(content: bytes, document_id: str = "report"):
    return parse_pdf_document(PDFDocumentInput(document_id=document_id, content=content))


class _UnreadablePages:
    """A reader whose pages cannot be listed."""

    def __init__(self, reader: Any) -> None:
        self._reader = reader

    @property
    def pages(self):
        raise RuntimeError("the page tree is damaged")  # noqa: TRY003


class _FewerPages:
    """A reader that holds fewer pages than the document declares."""

    def __init__(self, reader: Any, count: int) -> None:
        self._reader = reader
        self._count = count

    @property
    def pages(self):
        return self._reader.pages[: self._count]


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


def test_parse_rejects_a_page_whose_content_stream_the_document_does_not_hold() -> None:
    """A page that points at content the document lost must fail as a whole.

    The reader repairs such a page by handing it back without any text, which
    would turn a damaged document into a document with a silently empty page.
    """

    content = _pdf_with_declared_contents([("99 0 R", None), ("7 0 R", "Beta revenue")])

    with pytest.raises(ValueError, match="PDF page 1 declares a content stream"):
        _parse(content)


def test_parse_rejects_a_page_whose_content_array_loses_one_of_its_streams() -> None:
    """A content array that lost one of its streams must fail as a whole.

    The reader keeps the streams it can resolve and drops the others, so the
    page would come back with only part of its content.
    """

    content = _pdf_with_declared_contents([("[5 0 R 99 0 R]", "Alpha revenue"), ("7 0 R", "Beta revenue")])

    with pytest.raises(ValueError, match="PDF page 1 declares a content stream"):
        _parse(content)


def test_parse_rejects_an_indirect_content_array_that_loses_one_of_its_streams() -> None:
    """An array the document holds elsewhere must fail as a whole as well.

    The array is reached through a reference, so the streams it holds are only
    found by resolving that reference first. A stream inside it that the
    document does not hold is still a stream the page lost.
    """

    content = _pdf_with_declared_contents(
        [("20 0 R", "Alpha revenue"), ("7 0 R", "Beta revenue")],
        extra={20: b"[5 0 R 99 0 R]"},
    )

    with pytest.raises(ValueError, match="PDF page 1 declares a content stream"):
        _parse(content)


def test_parse_rejects_a_page_whose_content_is_not_a_stream() -> None:
    """Content that cannot be read as a stream must fail as a whole."""

    content = _pdf_with_declared_contents([("90 0 R", None)], extra={90: b"<< /Type /Bogus >>"})

    with pytest.raises(ValueError, match="PDF page 1 declares a content stream"):
        _parse(content)


def test_parse_reads_a_page_whose_content_is_an_indirect_array_of_streams() -> None:
    """A page may hold an array of streams that the document holds elsewhere.

    The array is a container of content streams, and every stream it holds is
    read, so the page keeps the text of all of them.
    """

    content = _pdf_with_declared_contents(
        [("20 0 R", "Alpha revenue")],
        extra={20: b"[5 0 R 6 0 R]"},
        streams={6: "Beta revenue"},
    )

    pages = _parse(content)

    assert [(page.page_number, page.text) for page in pages] == [(1, "Alpha revenueBeta revenue")]


def test_parse_reads_a_page_whose_content_is_an_indirect_array_of_one_stream() -> None:
    """An array that holds one stream carries the text of that stream."""

    content = _pdf_with_declared_contents([("20 0 R", "Alpha revenue")], extra={20: b"[5 0 R]"})

    pages = _parse(content)

    assert [(page.page_number, page.text) for page in pages] == [(1, "Alpha revenue")]


def test_parse_reads_a_page_whose_content_is_a_direct_array_of_streams() -> None:
    """A page may hold the array of its streams directly."""

    content = _pdf_with_declared_contents(
        [("[5 0 R 6 0 R]", "Alpha revenue")],
        streams={6: "Beta revenue"},
    )

    pages = _parse(content)

    assert [(page.page_number, page.text) for page in pages] == [(1, "Alpha revenueBeta revenue")]


def test_parse_reads_a_page_whose_content_is_an_indirect_stream() -> None:
    """The plain shape of a page that holds one stream keeps its text."""

    content = _pdf_with_declared_contents([("5 0 R", "Alpha revenue")])

    pages = _parse(content)

    assert [(page.page_number, page.text) for page in pages] == [(1, "Alpha revenue")]


def test_parse_keeps_a_page_that_declares_no_content() -> None:
    """A page without a content entry is a blank page, not a damaged one."""

    content = _pdf_with_declared_contents([(None, None), ("7 0 R", "Beta revenue")])

    pages = _parse(content)

    assert [(page.page_number, page.text) for page in pages] == [(1, ""), (2, "Beta revenue")]


def test_parse_keeps_a_page_whose_content_is_null() -> None:
    """A page that declares no content through a null object is a blank page.

    The reader reads such a page as a page without any text, so refusing it
    would refuse a document the reader holds together.
    """

    content = _pdf_with_declared_contents([("null", None), ("7 0 R", "Beta revenue")])

    pages = _parse(content)

    assert [(page.page_number, page.text) for page in pages] == [(1, ""), (2, "Beta revenue")]


def test_parse_keeps_the_streams_of_an_array_that_also_holds_a_null() -> None:
    """A null entry in a content array costs no text of the other streams."""

    content = _pdf_with_declared_contents(
        [("20 0 R", "Alpha revenue")],
        extra={20: b"[5 0 R null]"},
    )

    pages = _parse(content)

    assert [(page.page_number, page.text) for page in pages] == [(1, "Alpha revenue")]


def test_parse_keeps_a_page_with_an_empty_content_stream() -> None:
    """A page whose content stream is empty is a blank page, not a damaged one."""

    content = _pdf_with_declared_contents([("5 0 R", None), ("7 0 R", "Beta revenue")])

    pages = _parse(content)

    assert [(page.page_number, page.text) for page in pages] == [(1, ""), (2, "Beta revenue")]


def test_parse_keeps_a_page_whose_content_is_reached_through_a_chain_of_references() -> None:
    """A reference may point at another reference, and the two are equivalent.

    The specification lets any object value be a direct or an indirect
    reference, so a page may reach its content through a chain. The reader
    does not follow such a chain while it extracts the text, so the page
    carries no text, but the structure is sound and the document must not be
    refused for it.
    """

    content = _pdf_with_declared_contents(
        [("20 0 R", None)],
        extra={20: b"21 0 R"},
        streams={21: "Alpha revenue"},
    )

    pages = _parse(content)

    assert [(page.page_number, page.text) for page in pages] == [(1, "")]


def test_parse_keeps_a_page_whose_content_array_is_reached_through_a_chain_of_references() -> None:
    """A chain may end at an array of streams as well as at a single stream."""

    content = _pdf_with_declared_contents(
        [("20 0 R", None)],
        extra={20: b"21 0 R", 21: b"[5 0 R]"},
        streams={5: "Alpha revenue"},
    )

    pages = _parse(content)

    assert [(page.page_number, page.text) for page in pages] == [(1, "")]


def test_parse_keeps_a_page_whose_content_is_reached_through_a_long_chain_of_references() -> None:
    """A chain of several references is still a chain and not damage."""

    content = _pdf_with_declared_contents(
        [("20 0 R", None)],
        extra={20: b"21 0 R", 21: b"22 0 R"},
        streams={22: "Alpha revenue"},
    )

    pages = _parse(content)

    assert [(page.page_number, page.text) for page in pages] == [(1, "")]


def test_parse_keeps_a_page_whose_content_is_reached_through_a_deep_chain_of_references() -> None:
    """A chain may be as long as the document holds objects.

    The specification puts no limit on the number of references that lead to
    a content, and the check walks the chain without recursion, so a deep
    chain is followed to its end instead of being refused.
    """

    depth = 64
    numbers = [20 + index for index in range(depth)]
    extra = {number: f"{numbers[index + 1]} 0 R".encode("latin-1") for index, number in enumerate(numbers[:-1])}
    content = _pdf_with_declared_contents(
        [("20 0 R", None)],
        extra=extra,
        streams={numbers[-1]: "Alpha revenue"},
    )

    pages = _parse(content)

    assert [(page.page_number, page.text) for page in pages] == [(1, "")]


def test_parse_rejects_a_deep_chain_that_refers_back_to_itself() -> None:
    """A chain that closes on itself is a cycle at any depth.

    The loop is found by the references the chain met, not by the depth it
    reached, so a chain that is long and closed is refused as the cycle it
    is.
    """

    depth = 40
    numbers = [20 + index for index in range(depth)]
    extra = {number: f"{numbers[(index + 1) % depth]} 0 R".encode("latin-1") for index, number in enumerate(numbers)}
    content = _pdf_with_declared_contents([("20 0 R", None)], extra=extra)

    with pytest.raises(ValueError, match="PDF page 1 declares a content stream that refers to itself"):
        _parse(content)


def test_parse_keeps_the_streams_of_an_array_that_names_one_stream_twice() -> None:
    """One stream named twice is not a loop.

    The array holds the same stream under two references, which is a legal
    shape. Only a reference that is met again on the way to the content
    closes a loop.
    """

    content = _pdf_with_declared_contents([("[5 0 R 5 0 R]", "Alpha revenue")])

    pages = _parse(content)

    assert [(page.page_number, page.text) for page in pages] == [(1, "Alpha revenueAlpha revenue")]


def test_parse_rejects_a_content_array_that_refers_to_itself() -> None:
    """An array that holds itself cannot be read and must fail as a whole."""

    content = _pdf_with_declared_contents([("[20 0 R]", None)], extra={20: b"[20 0 R]"})

    with pytest.raises(ValueError, match="PDF page 1 declares a content stream that refers to itself"):
        _parse(content)


def test_parse_rejects_a_content_chain_that_refers_back_to_itself() -> None:
    """A chain of references that closes on itself cannot be read either."""

    content = _pdf_with_declared_contents([("20 0 R", None)], extra={20: b"21 0 R", 21: b"20 0 R"})

    with pytest.raises(ValueError, match="PDF page 1 declares a content stream that refers to itself"):
        _parse(content)


def test_parse_rejects_a_content_array_and_reference_that_close_on_each_other() -> None:
    """An array reached through a reference that closes the loop is damage."""

    content = _pdf_with_declared_contents(
        [("20 0 R", None)],
        extra={20: b"[21 0 R]", 21: b"[20 0 R]"},
    )

    with pytest.raises(ValueError, match="PDF page 1 declares a content stream that refers to itself"):
        _parse(content)


def test_the_content_check_leaves_the_reader_of_the_extraction_untouched() -> None:
    """The check must not decide the text the document yields.

    Resolving the content of a page fills the cache of the reader that
    resolved it, and the reader answers a later extraction from that cache.
    The check therefore runs on a reader of its own, so the page text stays
    the text a reader without the check produces.
    """

    content = _pdf_with_declared_contents(
        [("20 0 R", None)],
        extra={20: b"21 0 R"},
        streams={21: "Alpha revenue"},
    )
    reader = PdfReader(io.BytesIO(content))
    pages = tuple(reader.pages)
    before = dict(reader.resolved_objects)

    _reject_unreadable_content(content, len(pages))

    assert dict(reader.resolved_objects) == before
    assert pages[0].extract_text() == PdfReader(io.BytesIO(content)).pages[0].extract_text()
    assert _parse(content)[0].text == PdfReader(io.BytesIO(content)).pages[0].extract_text()


def test_the_content_check_fails_when_its_own_reader_cannot_be_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reader of its own that cannot be opened must not pass the check quietly.

    The check reads the document a second time. A reader that cannot open it
    leaves the content of its pages unverified, and a document whose content
    was not verified is not taken. The reader of the check is faked here,
    because a document that opens once and refuses to open again cannot be
    written down.
    """

    content = _pdf_with_declared_contents([("5 0 R", "Alpha revenue")])
    opened: list[bytes] = []
    open_reader = pdf_pages._open_reader

    def open_once(content_bytes: bytes):
        opened.append(content_bytes)
        if len(opened) == 2:
            raise ValueError("PDF document could not be opened")  # noqa: TRY003
        return open_reader(content_bytes)

    monkeypatch.setattr(pdf_pages, "_open_reader", open_once)

    with pytest.raises(ValueError, match="could not be opened for the content check"):
        _parse(content)


def test_the_content_check_fails_when_its_own_reader_cannot_read_the_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reader of its own that cannot list the pages must not pass the check quietly.

    The reader of the check is faked here, because a document whose pages the
    reader cannot list does not reach the check in the first place.
    """

    content = _pdf_with_declared_contents([("5 0 R", "Alpha revenue")])
    opened: list[bytes] = []
    open_reader = pdf_pages._open_reader

    def open_once(content_bytes: bytes):
        opened.append(content_bytes)
        reader = open_reader(content_bytes)
        if len(opened) == 2:
            return _UnreadablePages(reader)
        return reader

    monkeypatch.setattr(pdf_pages, "_open_reader", open_once)

    with pytest.raises(ValueError, match="could not be read for the content check"):
        _parse(content)


def test_the_content_check_fails_when_the_readers_disagree_about_the_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two readers that disagree about the pages leave the content unverified.

    The reader of the check is faked here, because two readers of the same
    bytes do not disagree on their own.
    """

    content = _pdf_with_declared_contents([("5 0 R", "Alpha revenue"), ("7 0 R", "Beta revenue")])
    opened: list[bytes] = []
    open_reader = pdf_pages._open_reader

    def open_once(content_bytes: bytes):
        opened.append(content_bytes)
        reader = open_reader(content_bytes)
        if len(opened) == 2:
            return _FewerPages(reader, 1)
        return reader

    monkeypatch.setattr(pdf_pages, "_open_reader", open_once)

    with pytest.raises(ValueError, match="does not hold the same pages on every read"):
        _parse(content)


def test_parse_rejects_a_document_whose_page_object_is_damaged() -> None:
    """A page the document does not hold must not turn into a shorter document."""

    content = _pdf_bytes([["Alpha revenue"], ["Beta revenue"]])
    damaged = content.replace(b"/Kids [4 0 R 6 0 R]", b"/Kids [4 0 R 99 0 R]")

    assert damaged != content
    with pytest.raises(ValueError):
        _parse(damaged)


def test_parse_rejects_a_document_that_is_truncated() -> None:
    """A document that was cut in half must fail instead of losing pages."""

    content = _pdf_bytes([["Alpha revenue"], ["Beta revenue"]])
    truncated = content[: len(content) // 2]

    assert truncated != content
    with pytest.raises(ValueError):
        _parse(truncated)


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
