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

"""Parse complete PDF documents into stable, projectable page Sources."""

from __future__ import annotations

import hashlib
import io
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol, TypeVar
from urllib.parse import quote

from pydantic import BaseModel, Field, field_validator
from pypdf import PasswordType, PdfReader

from powercontext.artifacts import MemoryCitation
from powercontext.builtin.artifacts.search import analyze_fts_query, analyze_text
from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

if TYPE_CHECKING:
    from powercontext.builtin.runtime.models import (
        GetMemoryEntryRequest,
        MemoryEntryRecord,
        MemoryHit,
        MemorySearchPage,
        SearchMemoryRequest,
    )

PDF_PAGE_SOURCE_NAME = "pdf-page"
PDF_PAGE_DEFINITION_VERSION = "1"

_T = TypeVar("_T")

# The retrieval plan below trades a wider candidate pool for precision. Each
# sub-query is kept short on purpose: the Memory FTS admission floor demands
# that a candidate covers a growing share of the query's distinct terms, so one
# long question admitted almost nothing while its short windows admitted the
# evidence pages. The limits below bound the cost of that widening.
_MAX_SUB_QUERIES = 48

# A single term is probed with a wider page budget than a term pair. The probe
# both measures how often the term occurs in the stored pages and contributes
# its own ranking, and a term that occurs everywhere carries almost no
# information, so its own frequency has to be measured over a wide sample.
_PROBE_LIMIT = 200
_WINDOW_LIMIT = 150

# The rank fusion constant. A small constant keeps the difference between the
# first and the tenth hit of one sub-query meaningful, which matters because
# the decisive evidence page is usually ranked near the top of the sub-query
# that names its subject.
_RANK_CONSTANT = 15

# The relative weight of the offline relevance signals.
#
# The window fusion holds the shared evidence: the pages that several
# independent term pairs rank highly. The term fusion holds the pages that a
# single term names. Coverage weighs a page by the share of the question it
# carries, weighted by how rare those terms are in the stored pages.
_WINDOW_WEIGHT = 0.40
_TERM_WEIGHT = 0.10
_COVERAGE_WEIGHT = 0.10

# The lead bonus lifts the page a distinctive term points at. A question whose
# wording differs from the document - "legal battles" against a page headed
# "Legal Proceedings" - shares only one term with its evidence page, so the
# shared evidence cannot lift it on its own. The lead bonus keeps the best
# match of every rare term visible next to the pages that match many common
# terms.
_LEAD_WEIGHT = 0.20
_LEAD_MINIMUM_WEIGHT = 1.5

# The passage focus weighs how well the question matches the line of the page
# that states the page's subject. A document announces its subject in a
# heading - "Item 3. Legal Proceedings." - and repeats the same words in the
# paragraphs below it, so the words alone cannot tell the two apart. Scoring
# the best line of a page does: a heading names the subject in a handful of
# words, while the same words inside a running sentence say little about what
# the page is about. The signal is what separates a page that states the
# subject of a question from a page that merely mentions one of its words, and
# it is added to the other signals rather than replacing them so that a page
# with strong shared evidence is not demoted by a page with a better heading.
_PASSAGE_WEIGHT = 0.40

# The line score is a small BM25: the terms a line carries are weighted by how
# rare they are in the stored pages, the repetition of a term saturates, and
# the length of the line discounts the score.
_PASSAGE_TERM_SATURATION = 1.2
_PASSAGE_LENGTH_NORMALIZATION = 0.6

# A single term alone on a line is a page artefact - a page number, a table
# marker, a footnote reference - and not a statement about the subject, so a
# line has to carry at least two terms before it is scored.
_PASSAGE_MINIMUM_TERMS = 2


class PDFDocumentInput(BaseModel):
    """A complete offline PDF document."""

    document_id: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("document_id must not be empty")  # noqa: TRY003
        return value


class PDFPageInput(BaseModel):
    """One physical page of a parsed PDF document."""

    document_id: str
    document_sha256: str
    page_number: int = Field(ge=1)
    text: str

    @field_validator("document_id")
    @classmethod
    def validate_page_document_id(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("document_id must not be empty")  # noqa: TRY003
        return value

    @field_validator("document_sha256")
    @classmethod
    def validate_document_sha256(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("document_sha256 must not be empty")  # noqa: TRY003
        return value


class PDFPageSource(Source):
    """A captured Source representing exactly one physical PDF page."""

    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Return the canonical stable identity for one physical PDF page.

    The identity carries the document ID, the digest of the complete PDF, and
    the physical page number, so a revised PDF becomes a new Source while the
    old citations keep pointing at the revision they came from. The result is
    bounded by the shared Source identity limit; an over-long document ID is
    represented by its own digest instead of being truncated, because a
    truncated ID could collide with a different document.
    """

    if not document_id or not document_id.strip():
        raise ValueError("document_id must be non-empty and non-blank")  # noqa: TRY003
    if not document_sha256 or not document_sha256.strip():
        raise ValueError("document_sha256 must be non-empty")  # noqa: TRY003
    if page_number < 1:
        raise ValueError("page_number must start at 1")  # noqa: TRY003

    quoted = quote(document_id, safe="")
    candidate = f"{PDF_PAGE_SOURCE_NAME}:{quoted}:{document_sha256}:{page_number}"
    if len(candidate) <= MAX_SOURCE_ID_LENGTH:
        return candidate

    digest = hashlib.sha256(document_id.encode("utf-8")).hexdigest()
    bounded = f"{PDF_PAGE_SOURCE_NAME}:{digest}:{document_sha256}:{page_number}"
    if len(bounded) > MAX_SOURCE_ID_LENGTH:  # pragma: no cover - defensive
        raise ValueError("PDF page source ID exceeds the maximum length")  # noqa: TRY003
    return bounded


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Parse every physical page of a complete PDF document.

    All physical pages are returned in document order and numbered from one,
    blank pages included. The parse is atomic: a document that cannot be read
    completely fails as a whole instead of returning the pages that happened
    to be readable.
    """

    content = value.content
    if not content:
        raise ValueError("PDF document must not be empty")  # noqa: TRY003

    document_sha256 = hashlib.sha256(content).hexdigest()
    pages = _read_pages(content)
    if not pages:
        raise ValueError("PDF document must contain at least one page")  # noqa: TRY003

    parsed: list[PDFPageInput] = []
    for number, page in enumerate(pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as error:
            raise ValueError(f"PDF page {number} could not be extracted") from error  # noqa: TRY003
        parsed.append(
            PDFPageInput(
                document_id=value.document_id,
                document_sha256=document_sha256,
                page_number=number,
                text=text.strip(),
            )
        )
    return tuple(parsed)


def _read_pages(content: bytes) -> tuple[Any, ...]:
    """Open one PDF document and return every physical page it declares."""

    # The PDF specification puts the file header at the very beginning of the
    # document. A file that does not carry it is not a PDF, and a reader that
    # repairs such a file would otherwise turn a damaged document into a
    # partially readable one.
    if content[:5] != b"%PDF-":
        raise ValueError("PDF document does not start with a PDF header")  # noqa: TRY003

    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception as error:
        raise ValueError("PDF document could not be opened") from error  # noqa: TRY003

    if reader.is_encrypted:
        # An empty user password is a legal permission marker: the document is
        # readable without a secret. A document that still refuses to open
        # needs a password this contract does not carry.
        try:
            outcome = reader.decrypt("")
        except Exception as error:
            raise ValueError("PDF document could not be decrypted") from error  # noqa: TRY003
        if outcome == PasswordType.NOT_DECRYPTED:
            raise ValueError("PDF document requires a password")  # noqa: TRY003

    try:
        pages = tuple(reader.pages)
    except Exception as error:
        raise ValueError("PDF document could not be read") from error  # noqa: TRY003

    _reject_incomplete_document(reader, len(pages))
    return pages


def _reject_incomplete_document(reader: Any, parsed: int) -> None:
    """Refuse a document whose page tree does not match the pages that were read.

    A damaged document can be read partially. The reader repairs what it can
    and hands back the pages that survived, which would turn a corrupt file
    into a document that quietly lost pages. The page tree of a well formed
    document declares how many pages it holds, so the declared count has to
    match the pages that were read. A tree that cannot be read reliably is not
    used to reject the document.
    """

    declared = _declared_page_count(reader)
    if declared is not None and declared != parsed:
        raise ValueError("PDF document does not hold every page it declares")  # noqa: TRY003


def _declared_page_count(reader: Any) -> int | None:
    """Return the number of pages the page tree of a document declares.

    The count is the one the page tree carries, not the number of pages a
    reader is able to produce, so a document that lost one of its pages
    declares more pages than it can show. ``None`` is returned when the tree
    cannot be read, which leaves the document unjudged.
    """

    try:
        pages = reader.root_object["/Pages"]
    except Exception:
        return None
    try:
        count = pages.get("/Count")
    except Exception:
        return None
    if count is None:
        return None
    try:
        return int(count)
    except (TypeError, ValueError):
        return None


class PDFPageSourceAdapter:
    """Resolve and read individual PDF pages."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        source_id = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Project one PDF page into canonical text evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.source_id,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    PDFPageSourceAdapter(),
    version=PDF_PAGE_DEFINITION_VERSION,
    projections=(PDFPageTextEvidenceProjection(),),
)


class PDFPageMemory(Protocol):
    """The scoped Memory surface used to search and cite PDF pages."""

    async def search(self, request: SearchMemoryRequest, /) -> MemorySearchPage: ...

    async def get(self, request: GetMemoryEntryRequest, /) -> MemoryEntryRecord: ...


_CandidateKey = tuple[str, str]


async def search_pdf_pages(
    memory: PDFPageMemory,
    question: str,
    limit: int = 10,
) -> tuple[MemoryCitation, ...]:
    """Return exact Memory citations for the distinct PDF pages that answer a question.

    The pages are retrieved through the public Memory search API and the
    returned citations are the ones the Memory itself reports, so every
    citation resolves back to its stored page. The retrieval probes every term
    of the question to learn which terms the stored pages actually use, widens
    the question into short term pairs that the Memory admission floor
    accepts, merges their candidates, and reranks them offline before the page
    limit is applied. The reranking weighs the line of a page that states its
    subject best, so a page that names the subject of the question outranks a
    page that merely repeats one of its words.
    """

    if limit < 1:
        return ()
    normalized = question.strip()
    if not normalized:
        return ()

    terms = _query_terms(normalized)
    if not terms:
        return ()

    from powercontext.builtin.runtime.models import SearchMemoryRequest

    probes = await _probe_terms(memory, terms, SearchMemoryRequest)
    live = tuple(term for term in terms if probes[term])
    if not live:
        # No term of the question occurs in the stored pages, so the question
        # cannot be answered from them.
        return ()

    weights = _term_weights(probes, live)
    windows = _plan_windows(terms, live)
    window_hits = await _run_queries(memory, [query for query, _members in windows], _WINDOW_LIMIT, SearchMemoryRequest)

    ranked = _rank_candidates(probes, windows, window_hits, weights, live)
    return await _cite_pages(memory, ranked, limit)


def _query_terms(question: str) -> tuple[str, ...]:
    """Return the distinct lexical terms of a question in question order."""

    return tuple(dict.fromkeys(analyze_fts_query(question).split()))


def _plan_windows(terms: Sequence[str], live: Sequence[str]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Build the term-pair queries used to retrieve candidates for one question.

    The whole question is not used as a query: its ranking is dominated by the
    common terms it carries, which buries the page that names the subject of
    the question. Each adjacent pair of terms becomes its own query instead,
    because a two-term query only has to cover one term to be admitted. The
    pairs follow the question order, so a pair like ``cash flow`` keeps the
    phrase the question used.
    """

    live_terms = set(live)
    plan: list[tuple[str, tuple[str, ...]]] = []
    for index in range(len(terms) - 1):
        pair = (terms[index], terms[index + 1])
        members = tuple(term for term in pair if term in live_terms)
        if not members:
            # Neither term of the pair occurs in the stored pages.
            continue
        plan.append((f"{pair[0]} {pair[1]}", members))
    if len(plan) > _MAX_SUB_QUERIES:
        plan = _evenly_spaced(plan, _MAX_SUB_QUERIES)
    return tuple(plan)


def _evenly_spaced(items: Sequence[_T], count: int) -> list[_T]:
    """Select a deterministic, evenly spread subset of a longer item list."""

    if count <= 0:
        return []
    if len(items) <= count:
        return list(items)
    step = len(items) / count
    return [items[min(len(items) - 1, int(index * step))] for index in range(count)]


async def _probe_terms(
    memory: PDFPageMemory,
    terms: Sequence[str],
    request_type: type[SearchMemoryRequest],
) -> dict[str, tuple[MemoryHit, ...]]:
    """Ask the Memory how every term of a question ranks the stored pages.

    A term the Memory cannot match never occurs in the stored pages, and a term
    that matches almost every page carries almost no information. Both facts
    are only visible from the ranking itself, so the ranking is the
    measurement.
    """

    probes: dict[str, tuple[MemoryHit, ...]] = {}
    for term in terms:
        page = await memory.search(request_type(query=term, limit=_PROBE_LIMIT, mode="fts"))
        probes[term] = tuple(page.hits)
    return probes


async def _run_queries(
    memory: PDFPageMemory,
    queries: Sequence[str],
    limit: int,
    request_type: type[SearchMemoryRequest],
) -> tuple[tuple[MemoryHit, ...], ...]:
    """Run every planned sub-query and keep the ranking each one reports."""

    results: list[tuple[MemoryHit, ...]] = []
    for query in queries:
        page = await memory.search(request_type(query=query, limit=limit, mode="fts"))
        results.append(tuple(page.hits))
    return tuple(results)


def _term_weights(probes: dict[str, tuple[MemoryHit, ...]], live: Sequence[str]) -> dict[str, float]:
    """Return the inverse-frequency weight of every term that occurs in the pages.

    The weight is relative to the most common term of the question, so the
    scale does not depend on how many pages the Memory happens to store.
    """

    ceiling = max(len(probes[term]) for term in live) or 1
    return {term: math.log((ceiling + 1) / (len(probes[term]) + 1)) + 1.0 for term in live}


def _normalize(scores: dict[_CandidateKey, float]) -> dict[_CandidateKey, float]:
    """Scale a set of raw fusion scores into the unit interval."""

    if not scores:
        return {}
    low = min(scores.values())
    high = max(scores.values())
    if high - low < 1e-12:
        return dict.fromkeys(scores, 1.0)
    return {key: (value - low) / (high - low) for key, value in scores.items()}


def _page_body(text: str) -> str:
    """Return the page text of a stored PDF page entry.

    A stored page entry starts with the document identifier that the text
    evidence projection puts in front of the page text. That identifier belongs
    to no page, so it is not scored.
    """

    _identifier, separator, body = text.partition("\n")
    return body if separator else ""


def _passage_focus(texts: Mapping[_CandidateKey, str], weights: Mapping[str, float]) -> dict[_CandidateKey, float]:
    """Score every candidate page by the line that states its subject best.

    Each line of the stored page is scored like a small passage. The terms of
    the question that the line carries are weighted by how rare they are in the
    stored pages, the repetition of one term saturates, and the length of the
    line discounts the result, so a heading that names the subject of the
    question outweighs the same words inside a paragraph of running text.
    """

    focus: dict[_CandidateKey, float] = {}
    saturation = _PASSAGE_TERM_SATURATION
    for key, text in texts.items():
        best = 0.0
        for line in _page_body(text).splitlines():
            terms = analyze_text(line).split()
            if len(terms) < _PASSAGE_MINIMUM_TERMS:
                continue
            counts: dict[str, int] = defaultdict(int)
            for term in terms:
                if term in weights:
                    counts[term] += 1
            if not counts:
                continue
            score = 0.0
            for term, count in counts.items():
                score += weights[term] * (count * (saturation + 1)) / (count + saturation)
            score /= 1.0 + _PASSAGE_LENGTH_NORMALIZATION * (len(terms) - 1)
            best = max(best, score)
        focus[key] = best
    return focus


def _rank_candidates(
    probes: dict[str, tuple[MemoryHit, ...]],
    windows: Sequence[tuple[str, tuple[str, ...]]],
    window_hits: Sequence[tuple[MemoryHit, ...]],
    weights: dict[str, float],
    live: Sequence[str],
) -> list[MemoryHit]:
    """Rank the candidate pages against the question with offline signals.

    The shared evidence of the term pairs and the evidence of the single terms
    are fused as weighted reciprocal ranks, coverage weighs a page by the share
    of the question it carries, the lead bonus lifts the page every
    distinctive term points at, and the passage focus weighs the line of the
    page that states its subject best. The five signals are combined with the
    weights declared above.
    """

    hits_by_key: dict[_CandidateKey, MemoryHit] = {}
    texts: dict[_CandidateKey, str] = {}
    window_scores: dict[_CandidateKey, float] = defaultdict(float)
    for (_query, members), hits in zip(windows, window_hits, strict=True):
        weight = sum(weights[term] for term in members) / len(members)
        for rank, hit in enumerate(hits, start=1):
            key = (hit.entry_id, hit.entry_version_id)
            hits_by_key.setdefault(key, hit)
            texts.setdefault(key, hit.text)
            window_scores[key] += weight / (_RANK_CONSTANT + rank)

    term_scores: dict[_CandidateKey, float] = defaultdict(float)
    carriers: dict[_CandidateKey, set[str]] = defaultdict(set)
    for term in live:
        weight = weights[term]
        for rank, hit in enumerate(probes[term], start=1):
            key = (hit.entry_id, hit.entry_version_id)
            hits_by_key.setdefault(key, hit)
            texts.setdefault(key, hit.text)
            term_scores[key] += weight / (_RANK_CONSTANT + rank)
            carriers[key].add(term)

    fused_windows = _normalize(window_scores)
    fused_terms = _normalize(term_scores)
    fused_focus = _normalize(_passage_focus(texts, weights))
    total_weight = sum(weights.values()) or 1.0

    scores: dict[_CandidateKey, float] = {}
    for key in hits_by_key:
        coverage = sum(weights[term] for term in carriers.get(key, ())) / total_weight
        scores[key] = (
            _WINDOW_WEIGHT * fused_windows.get(key, 0.0)
            + _TERM_WEIGHT * fused_terms.get(key, 0.0)
            + _COVERAGE_WEIGHT * coverage
            + _PASSAGE_WEIGHT * fused_focus.get(key, 0.0)
        )

    for term in live:
        if weights[term] < _LEAD_MINIMUM_WEIGHT:
            continue
        hits = probes[term]
        if not hits:
            continue
        head = hits[0]
        key = (head.entry_id, head.entry_version_id)
        if key in scores:
            scores[key] += _LEAD_WEIGHT * weights[term]

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [hits_by_key[key] for key, _score in ranked]


async def _cite_pages(
    memory: PDFPageMemory,
    ranked: Sequence[MemoryHit],
    limit: int,
) -> tuple[MemoryCitation, ...]:
    """Resolve the ranked candidates into distinct page citations.

    Each candidate is read back through the Memory so the citation is the one
    the Memory reports for that entry, and so a page that several entries
    reference is only cited once.
    """

    from powercontext.builtin.artifacts.memory.errors import (
        InvalidMemoryCitationError,
        MemoryEntryInactiveError,
    )
    from powercontext.builtin.runtime.models import GetMemoryEntryRequest

    citations: list[MemoryCitation] = []
    used: set[str] = set()
    for hit in ranked:
        if len(citations) >= limit:
            break
        citation = MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )
        try:
            record = await memory.get(GetMemoryEntryRequest(citation=citation))
        except (InvalidMemoryCitationError, MemoryEntryInactiveError):
            continue
        pages = {
            reference.source_id for reference in record.entry.sources if reference.source_type == PDF_PAGE_SOURCE_NAME
        }
        if not pages or pages & used:
            continue
        used |= pages
        citations.append(record.citation)
    return tuple(citations)
