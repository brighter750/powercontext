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

"""Select literal evidence spans out of recalled Memory entries.

Retrieval decides *which* Memory entries are relevant; this module decides *which parts*
of those entries actually carry the evidence for the caller's query. A selected span is
always a contiguous slice of the original entry text, so a caller can quote it verbatim
and cite it back to the exact entry version it came from.

The analysis is offline, deterministic, and free of model calls: regions are ranked
lexically against the query, and the byte budget is charged against the rendered
output - trust policy, envelope, JSON, citations, and text together - rather than
against the extracted text alone.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from itertools import pairwise

from powercontext.artifacts import ArtifactAddress

# The smallest useful slice of an entry. A candidate that cannot be given at least this
# many content bytes is not worth a citation slot, because the citation itself costs
# more than the evidence it would carry.
_MIN_CONTENT_BYTES = 192

# How sharply the content budget follows the query relevance of the candidates. The
# budget is a scarce resource and only one candidate usually carries the evidence, so
# the strongest candidate is meant to take nearly all of it. Candidates that answer the
# query about as well keep their own share.
_ALLOCATION_EXPONENT = 3.0

# How the relevance of a region is built from the three lexical signals it carries:
# how much of the query's weight it covers, how much of the query it repeats word for
# word, and how much of the region the query actually occupies.
_COVERAGE_WEIGHT = 0.60
_PHRASE_WEIGHT = 0.10
_DENSITY_WEIGHT = 0.30

# The widest region of consecutive sentences one candidate may anchor on.
_MAX_SPAN_SENTENCES = 3

# How many times the leftover budget is redistributed over the selected regions.
_GROW_ROUNDS = 3


@dataclass(frozen=True)
class MemoryEntryAddress:
    """Identify one exact Memory entry version across Scope boundaries."""

    memory: ArtifactAddress
    entry_id: str
    entry_version_id: str


@dataclass(frozen=True)
class PreparedEvidenceFragment:
    """One literal span of one Memory entry, addressable back to its source."""

    address: MemoryEntryAddress
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class EvidenceCandidate:
    """One recalled Memory entry that may contribute evidence."""

    address: MemoryEntryAddress
    text: str


@dataclass(frozen=True)
class _QueryIndex:
    """A weighted view of one query against the recalled candidate set.

    Terms that only a few candidates carry weigh more than terms every candidate
    repeats, so a region that mentions a discriminative term outranks a region that
    merely repeats the query's common words.
    """

    weights: dict[str, float]
    total_weight: float
    bigrams: frozenset[tuple[str, str]]

    def coverage(self, terms: Sequence[str]) -> float:
        """Share of the query's weight the given terms carry."""

        if not self.total_weight:
            return 0.0
        matched = {term for term in terms if term in self.weights}
        return sum(self.weights[term] for term in matched) / self.total_weight

    def density(self, terms: Sequence[str]) -> float:
        """Share of the region's own terms that the query asked for.

        Two regions can carry the same query words, but the one that spends fewer of
        its own words on the query is the more direct answer.
        """

        if not terms:
            return 0.0
        distinct = set(terms)
        matched = distinct & self.weights.keys()
        return len(matched) / len(distinct)

    def phrase(self, terms: Sequence[str]) -> float:
        """Share of the query's word pairs that appear consecutively in the region."""

        if not self.bigrams:
            return 0.0
        pairs = set(pairwise(terms))
        return len(self.bigrams & pairs) / len(self.bigrams)


@dataclass(frozen=True)
class _EvidenceSpan:
    """One candidate region of an entry, with the lexical score that selected it."""

    first: int
    last: int
    start: int
    end: int
    score: float
    coverage: float


@dataclass(frozen=True)
class _CandidatePlan:
    """The ranked evidence regions of one recalled entry."""

    candidate: EvidenceCandidate
    sentences: tuple[tuple[int, int], ...]
    spans: tuple[_EvidenceSpan, ...]

    @property
    def weight(self) -> float:
        """How strongly this entry answers the query."""

        return self.spans[0].score if self.spans else 0.0


@dataclass(frozen=True)
class _Selection:
    """One admitted candidate together with the region it contributes."""

    plan: _CandidatePlan
    fragment: PreparedEvidenceFragment


def select_evidence_fragments(
    query: str,
    candidates: Sequence[EvidenceCandidate],
    *,
    max_bytes: int,
    measure: Callable[[Sequence[PreparedEvidenceFragment]], int],
) -> tuple[PreparedEvidenceFragment, ...]:
    """Choose literal evidence spans for one query inside the rendered byte budget.

    ``measure`` reports the rendered byte size of a candidate fragment list. The budget
    is therefore charged against the delivered output - trust policy, envelope, JSON,
    citations, and text together - instead of the extracted text alone.
    """

    plans = _plan_candidates(query, candidates)
    ranked = sorted(plans, key=lambda plan: -plan.weight)
    active = [plan for plan in ranked if plan.weight > 0]
    if not active:
        # Nothing in the recalled set answers the query on its own terms. The best
        # candidate still holds quotable text, so it is offered rather than nothing.
        active = [plan for plan in ranked if plan.spans][:1]
    if not active:
        return ()
    base = measure(())
    available = max_bytes - base
    if available <= 0:
        return ()

    overheads = [_item_overhead(plan.candidate, measure, base) for plan in active]
    admitted = _admit(active, overheads, available)
    if not admitted:
        return ()
    admitted_plans = [active[index] for index in admitted]
    admitted_overheads = [overheads[index] for index in admitted]
    content_pool = available - sum(admitted_overheads)
    allocations = _allocate_content(content_pool, [plan.weight for plan in admitted_plans])
    selections: list[_Selection] = []
    kept_allocations: list[int] = []
    for plan, allocation in zip(admitted_plans, allocations, strict=True):
        fragment = _select_fragment(plan, allocation, measure)
        if fragment is not None:
            selections.append(_Selection(plan=plan, fragment=fragment))
            kept_allocations.append(allocation)
    if not selections:
        return ()

    selections = _add_regions(selections, kept_allocations, max_bytes, measure)
    selections = _grow_selections(selections, max_bytes, measure)
    selections = _fit_budget(selections, max_bytes, measure)
    return tuple(selection.fragment for selection in selections)


def _plan_candidates(query: str, candidates: Sequence[EvidenceCandidate]) -> tuple[_CandidatePlan, ...]:
    """Rank the evidence regions of every candidate against the query."""

    if not candidates:
        return ()
    documents = [_terms(candidate.text) for candidate in candidates]
    index = _build_index(_terms(query), documents)
    plans: list[_CandidatePlan] = []
    for candidate in candidates:
        sentences = _sentence_spans(candidate.text)
        spans = _rank_spans(candidate.text, sentences, index)
        plans.append(_CandidatePlan(candidate=candidate, sentences=sentences, spans=spans))
    return tuple(plans)


def _build_index(query_terms: Sequence[str], documents: Sequence[Sequence[str]]) -> _QueryIndex:
    """Weight the query terms by how rare they are across the recalled entries."""

    total = len(documents)
    frequency: Counter[str] = Counter()
    for document in documents:
        frequency.update(set(document))
    weights = {term: math.log((total + 1) / (frequency.get(term, 0) + 1)) + 1.0 for term in dict.fromkeys(query_terms)}
    return _QueryIndex(
        weights=weights,
        total_weight=sum(weights.values()),
        bigrams=frozenset(pairwise(query_terms)),
    )


def _rank_spans(
    text: str,
    sentences: Sequence[tuple[int, int]],
    index: _QueryIndex,
) -> tuple[_EvidenceSpan, ...]:
    """Rank every candidate region of one entry by how much query evidence it carries."""

    if not sentences:
        return ()
    sentence_terms = [_terms(text[start:end]) for start, end in sentences]
    spans: list[_EvidenceSpan] = []
    for position in range(len(sentences)):
        for size in range(1, _MAX_SPAN_SENTENCES + 1):
            stop = position + size
            if stop > len(sentences):
                break
            window = _join_terms(sentence_terms[position:stop])
            coverage = index.coverage(window)
            score = (
                _COVERAGE_WEIGHT * coverage
                + _PHRASE_WEIGHT * index.phrase(window)
                + _DENSITY_WEIGHT * index.density(window)
            )
            spans.append(
                _EvidenceSpan(
                    first=position,
                    last=stop - 1,
                    start=sentences[position][0],
                    end=sentences[stop - 1][1],
                    score=score,
                    coverage=coverage,
                )
            )
    spans.sort(key=lambda span: (-span.score, -span.coverage, span.start))
    return tuple(spans)


def _admit(
    plans: Sequence[_CandidatePlan],
    overheads: Sequence[int],
    available: int,
) -> list[int]:
    """Choose the candidates that earn a citation slot and a useful slice.

    A candidate is admitted when the share the budget would give it can carry a
    citation and a slice of evidence worth reading. A candidate that answers the query
    far worse than the best one cannot pay for its own citation, so it is left out
    instead of taking budget away from the evidence.
    """

    pool = max(0, available - sum(overheads))
    powered = [plan.weight**_ALLOCATION_EXPONENT for plan in plans]
    total = sum(powered)
    admitted: list[int] = []
    reserved = 0
    for index, _plan in enumerate(plans):
        share = powered[index] / total if total else 1 / len(plans)
        fair = pool * share
        if fair < _MIN_CONTENT_BYTES and admitted:
            break
        if reserved + overheads[index] + _MIN_CONTENT_BYTES > available:
            break
        admitted.append(index)
        reserved += overheads[index] + _MIN_CONTENT_BYTES
    return admitted


def _allocate_content(content_pool: int, weights: Sequence[float]) -> list[int]:
    """Split the content budget over the admitted candidates by query relevance."""

    count = len(weights)
    if count == 0:
        return []
    powered = [weight**_ALLOCATION_EXPONENT for weight in weights]
    total = sum(powered)
    if total <= 0:
        share = content_pool / count
        return [int(share)] * count
    return [int(content_pool * value / total) for value in powered]


def _item_overhead(
    candidate: EvidenceCandidate,
    measure: Callable[[Sequence[PreparedEvidenceFragment]], int],
    base: int,
) -> int:
    """Measure what one item costs before its content is added."""

    probe = PreparedEvidenceFragment(address=candidate.address, start=0, end=0, text="")
    return measure((probe,)) - base


def _select_fragment(
    plan: _CandidatePlan,
    allocation: int,
    measure: Callable[[Sequence[PreparedEvidenceFragment]], int],
) -> PreparedEvidenceFragment | None:
    """Pick the region of one entry that answers the query within its share.

    The best scoring region anchors the choice. The region is then widened to the
    widest window that still holds the anchor and fits the share, because a wider
    window can only cover more of the evidence, and every selected character is a
    literal quote of the entry.
    """

    if not plan.spans:
        return None
    anchor = plan.spans[0]
    chosen = _widest_containing(plan, anchor, allocation, measure)
    if chosen is None:
        # No window around the anchor fits. A single sentence of the anchor is the
        # next best answer, and it keeps the delivered text on a sentence boundary.
        chosen = _narrowest_inside(plan, anchor, allocation, measure)
    if chosen is None:
        trimmed = _trim_span(plan.candidate.text, anchor.start, anchor.end, allocation)
        if trimmed[1] <= trimmed[0]:
            return None
        chosen = trimmed
    start, end = chosen
    return PreparedEvidenceFragment(
        address=plan.candidate.address,
        start=start,
        end=end,
        text=plan.candidate.text[start:end],
    )


def _widest_containing(
    plan: _CandidatePlan,
    anchor: _EvidenceSpan,
    allocation: int,
    measure: Callable[[Sequence[PreparedEvidenceFragment]], int],
) -> tuple[int, int] | None:
    """Find the widest window around the anchor that fits the share."""

    chosen: tuple[int, int] | None = None
    for span in plan.spans:
        if span.first > anchor.first or span.last < anchor.last:
            continue
        candidate = (span.start, span.end)
        if _content_cost(plan.candidate, candidate, measure) > allocation:
            continue
        if chosen is None or _span_bytes(plan.candidate, candidate) > _span_bytes(plan.candidate, chosen):
            chosen = candidate
    return chosen


def _narrowest_inside(
    plan: _CandidatePlan,
    anchor: _EvidenceSpan,
    allocation: int,
    measure: Callable[[Sequence[PreparedEvidenceFragment]], int],
) -> tuple[int, int] | None:
    """Find a sentence of the anchor that fits the share on its own."""

    for span in plan.spans:
        if span.first < anchor.first or span.last > anchor.last:
            continue
        candidate = (span.start, span.end)
        if _content_cost(plan.candidate, candidate, measure) > allocation:
            continue
        return candidate
    return None


def _add_regions(
    selections: Sequence[_Selection],
    allocations: Sequence[int],
    max_bytes: int,
    measure: Callable[[Sequence[PreparedEvidenceFragment]], int],
) -> list[_Selection]:
    """Give an entry a second, disjoint region when the budget still allows one.

    One entry often holds two separate passages that answer the query. A second region
    only pays off when the entry is too large to be covered as a whole, because a
    shorter entry is better served by one wide region around its best passage.
    """

    result = list(selections)
    for index, selection in enumerate(selections):
        plan = selection.plan
        anchor = selection.fragment
        text_bytes = len(plan.candidate.text.encode("utf-8"))
        if text_bytes <= allocations[index]:
            continue
        taken = [(entry.fragment.start, entry.fragment.end) for entry in result if entry.plan is plan]
        ceiling = len(anchor.text.encode("utf-8"))
        for span in plan.spans:
            if any(span.start < end and start < span.end for start, end in taken):
                continue
            if len(plan.candidate.text[span.start : span.end].encode("utf-8")) > ceiling:
                break
            probe = PreparedEvidenceFragment(
                address=anchor.address,
                start=span.start,
                end=span.end,
                text=plan.candidate.text[span.start : span.end],
            )
            if measure([entry.fragment for entry in result] + [probe]) <= max_bytes:
                result.append(_Selection(plan=plan, fragment=probe))
            break
    return result


def _grow_selections(
    selections: Sequence[_Selection],
    max_bytes: int,
    measure: Callable[[Sequence[PreparedEvidenceFragment]], int],
) -> list[_Selection]:
    """Spend the leftover budget on the regions that answer the query best."""

    grown = list(selections)
    for _ in range(_GROW_ROUNDS):
        leftover = max_bytes - measure([selection.fragment for selection in grown])
        if leftover <= 0:
            break
        growable = [
            index
            for index, selection in enumerate(grown)
            if _room(selection.plan.candidate.text, selection.fragment) > 0
        ]
        if not growable:
            break
        share = leftover // len(growable)
        for index in growable:
            grown[index] = _grow_selection(grown, index, share, max_bytes, measure)
    return grown


def _grow_selection(
    selections: Sequence[_Selection],
    index: int,
    ceiling: int,
    max_bytes: int,
    measure: Callable[[Sequence[PreparedEvidenceFragment]], int],
) -> _Selection:
    """Widen one region as far as the remaining budget allows."""

    selection = selections[index]
    text = selection.plan.candidate.text
    others = [entry.fragment for position, entry in enumerate(selections) if position != index]
    best = selection.fragment
    lower, upper = 0, ceiling
    while lower <= upper:
        middle = (lower + upper) // 2
        start, end = _extend_span(text, best.start, best.end, middle)
        if start == best.start and end == best.end:
            lower = middle + 1
            continue
        probe = PreparedEvidenceFragment(address=best.address, start=start, end=end, text=text[start:end])
        if measure([*others, probe]) <= max_bytes:
            best = probe
            lower = middle + 1
        else:
            upper = middle - 1
    return _Selection(plan=selection.plan, fragment=best)


def _fit_budget(
    selections: Sequence[_Selection],
    max_bytes: int,
    measure: Callable[[Sequence[PreparedEvidenceFragment]], int],
) -> list[_Selection]:
    """Bring the selected regions inside the budget, weakest region first."""

    fitted = list(selections)
    while fitted and measure([selection.fragment for selection in fitted]) > max_bytes:
        if len(fitted) > 1:
            weakest = min(range(len(fitted)), key=lambda position: fitted[position].plan.weight)
            fitted.pop(weakest)
            continue
        trimmed = _trim_fragment(fitted[0], max_bytes, measure)
        if trimmed is None:
            return []
        fitted[0] = trimmed
    return fitted


def _trim_fragment(
    selection: _Selection,
    max_bytes: int,
    measure: Callable[[Sequence[PreparedEvidenceFragment]], int],
) -> _Selection | None:
    """Shorten one region until its rendered form fits the budget."""

    fragment = selection.fragment
    text = selection.plan.candidate.text
    empty = PreparedEvidenceFragment(address=fragment.address, start=0, end=0, text="")
    allowed = max_bytes - measure((empty,))
    lower, upper = 0, fragment.end - fragment.start
    best = 0
    while lower <= upper:
        middle = (lower + upper) // 2
        probe = PreparedEvidenceFragment(
            address=fragment.address,
            start=fragment.start,
            end=fragment.start + middle,
            text=text[fragment.start : fragment.start + middle],
        )
        if measure((probe,)) <= allowed:
            best = middle
            lower = middle + 1
        else:
            upper = middle - 1
    if best == 0:
        return None
    return _Selection(
        plan=selection.plan,
        fragment=PreparedEvidenceFragment(
            address=fragment.address,
            start=fragment.start,
            end=fragment.start + best,
            text=text[fragment.start : fragment.start + best],
        ),
    )


def _room(text: str, fragment: PreparedEvidenceFragment) -> int:
    """Report how many bytes may still be added around one fragment."""

    return len(text[: fragment.start].encode("utf-8")) + len(text[fragment.end :].encode("utf-8"))


def _content_cost(
    candidate: EvidenceCandidate,
    span: tuple[int, int],
    measure: Callable[[Sequence[PreparedEvidenceFragment]], int],
) -> int:
    """Measure the rendered cost of one region's text."""

    start, end = span
    probe = PreparedEvidenceFragment(
        address=candidate.address,
        start=start,
        end=end,
        text=candidate.text[start:end],
    )
    empty = PreparedEvidenceFragment(address=candidate.address, start=0, end=0, text="")
    return measure((probe,)) - measure((empty,))


def _span_bytes(candidate: EvidenceCandidate, span: tuple[int, int]) -> int:
    """Report the byte size of one region's text."""

    start, end = span
    return len(candidate.text[start:end].encode("utf-8"))


def _trim_span(text: str, start: int, end: int, byte_budget: int) -> tuple[int, int]:
    """Cut a region down to its budget, keeping its beginning."""

    if byte_budget <= 0:
        return start, start
    encoded = text[start:end].encode("utf-8")
    if len(encoded) <= byte_budget:
        return start, end
    trimmed = encoded[:byte_budget].decode("utf-8", errors="ignore")
    return start, start + len(trimmed)


def _extend_span(text: str, start: int, end: int, byte_budget: int) -> tuple[int, int]:
    """Grow a region around its centre, keeping it inside the original text."""

    if byte_budget <= 0:
        return start, end
    left_room = len(text[:start].encode("utf-8"))
    right_room = len(text[end:].encode("utf-8"))
    if left_room + right_room <= byte_budget:
        return 0, len(text)
    take_left = byte_budget * left_room // max(1, left_room + right_room)
    take_right = byte_budget - take_left
    return _shrink_left(text, start, take_left), _grow_right(text, end, take_right)


def _shrink_left(text: str, start: int, byte_budget: int) -> int:
    """Move a region's start left by at most the given bytes."""

    if byte_budget <= 0:
        return start
    prefix = text[:start].encode("utf-8")
    if len(prefix) <= byte_budget:
        return 0
    trimmed = prefix[len(prefix) - byte_budget :].decode("utf-8", errors="ignore")
    return start - len(trimmed)


def _grow_right(text: str, end: int, byte_budget: int) -> int:
    """Move a region's end right by at most the given bytes."""

    if byte_budget <= 0:
        return end
    suffix = text[end:].encode("utf-8")
    if len(suffix) <= byte_budget:
        return len(text)
    trimmed = suffix[:byte_budget].decode("utf-8", errors="ignore")
    return end + len(trimmed)


def _join_terms(parts: Sequence[Sequence[str]]) -> tuple[str, ...]:
    """Concatenate the terms of consecutive sentences."""

    joined: list[str] = []
    for part in parts:
        joined.extend(part)
    return tuple(joined)


def _sentence_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Split text into sentence spans, keeping exact character offsets."""

    spans: list[tuple[int, int]] = []
    start = 0
    for match in _SENTENCE_BOUNDARY.finditer(text):
        end = match.start(1) + len(match.group(1))
        if _ends_with_abbreviation(text, start, end):
            continue
        spans.append((start, end))
        start = match.end()
    if start < len(text):
        spans.append((start, len(text)))
    trimmed: list[tuple[int, int]] = []
    for span_start, span_end in spans:
        while span_end > span_start and text[span_end - 1].isspace():
            span_end -= 1
        if span_end > span_start:
            trimmed.append((span_start, span_end))
    return tuple(trimmed)


def _ends_with_abbreviation(text: str, start: int, end: int) -> bool:
    """Report whether a span ends in an abbreviation that must not end a sentence."""

    match = _TRAILING_WORD.search(text[start:end])
    if match is None:
        return False
    return match.group().lower().rstrip(".") in _ABBREVIATIONS


def _terms(text: str) -> tuple[str, ...]:
    """Normalize text into comparable terms, preserving document order.

    ASCII words and numbers are reduced to a light stem so that a query and an entry
    still match when they inflect differently. CJK runs are expanded into character
    bigrams, the smallest unit that keeps word order usable.
    """

    terms: list[str] = []
    for run in _WORD_RUN.finditer(text.lower()):
        terms.extend(_run_terms(run.group()))
    return tuple(terms)


def _run_terms(run: str) -> list[str]:
    """Reduce one word-like run to its comparable terms."""

    if _ASCII_ONLY.match(run):
        return [_stem(run)]
    terms: list[str] = []
    index = 0
    for cjk in _CJK_RUN.finditer(run):
        if cjk.start() > index:
            terms.append(run[index : cjk.start()])
        chunk = cjk.group()
        if len(chunk) == 1:
            terms.append(chunk)
        else:
            terms.extend(chunk[offset : offset + 2] for offset in range(len(chunk) - 1))
        index = cjk.end()
    if index < len(run):
        terms.append(run[index:])
    return terms


def _stem(word: str) -> str:
    """Reduce a lowercase ASCII word to a comparable stem."""

    if len(word) > 4 and word.endswith("ies"):
        return f"{word[:-3]}y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    if len(word) > 5 and word.endswith("ing"):
        return _undouble(word[:-3])
    if len(word) > 4 and word.endswith("ed"):
        return _undouble(word[:-2])
    return word


def _undouble(stem: str) -> str:
    """Drop the doubled consonant a stripped suffix leaves behind."""

    if len(stem) > 2 and stem[-1] == stem[-2]:
        return stem[:-1]
    return stem


_ASCII_ONLY = re.compile(r"\A[a-z0-9]+\Z")
_WORD_RUN = re.compile(r"[a-z0-9]+|[^\W\d_]+", re.UNICODE)
_CJK_RUN = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]+")
_SENTENCE_BOUNDARY = re.compile(r"([.!?]+[\"')\]]*)(\s+)")
_TRAILING_WORD = re.compile(r"[A-Za-z.]+\Z")

# Abbreviations that must not end a sentence, so that "et al. showed" stays one region.
_ABBREVIATIONS = frozenset({
    "al",
    "approx",
    "ca",
    "cf",
    "co",
    "dept",
    "dr",
    "e.g",
    "ed",
    "eq",
    "etc",
    "et",
    "fig",
    "figs",
    "i.e",
    "inc",
    "jr",
    "ltd",
    "mr",
    "mrs",
    "ms",
    "no",
    "ph",
    "prof",
    "ref",
    "sr",
    "st",
    "univ",
    "vol",
    "vs",
})
