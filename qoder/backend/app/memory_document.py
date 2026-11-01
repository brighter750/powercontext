"""The on-disk (in-memory-service) representation of one StudyFlow memory record.

StudyFlow stores its long-term memory as PowerContext Atomic Memory entries. Each
entry's ``text`` uses a small, parseable envelope so the backend can read state back
deterministically, while the narrative block stays natural language that the AI tutor
can quote.

Envelope layout::

    key: knowledge_state:BCNF 分解
    course: 数据库系统
    topic: BCNF 分解
    mastery: 0.42
    ---
    学习者在 BCNF 分解上累计练习 5 题、答错 3 题，需要继续巩固。

The header block is ``key: value`` lines. Everything after the ``---`` separator is the
narrative. ``key`` identifies the entry so a later activity can *revise* the same
PowerContext artifact instead of appending a duplicate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

SEPARATOR = "---"

# Field names are plain snake_case identifiers; anything else is prose.
_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

# Memory entry kinds used by StudyFlow.
KIND_PROFILE = "learner_profile"
KIND_KNOWLEDGE_STATE = "knowledge_state"
KIND_LEARNING_RECORD = "learning_record"
KIND_WEAKNESS = "weakness"
KIND_REVIEW_PLAN = "review_plan"
KIND_PREFERENCE = "preference"

ALL_KINDS = (
    KIND_PROFILE,
    KIND_KNOWLEDGE_STATE,
    KIND_LEARNING_RECORD,
    KIND_WEAKNESS,
    KIND_REVIEW_PLAN,
    KIND_PREFERENCE,
)


@dataclass(slots=True)
class MemoryDocument:
    """One StudyFlow memory entry, decomposed into machine fields plus a narrative."""

    kind: str
    key: str
    fields: dict[str, str] = field(default_factory=dict)
    narrative: str = ""

    def to_text(self) -> str:
        """Render the document into the Atomic Memory ``text`` payload."""
        lines = [f"key: {self.key}"]
        for name, value in self.fields.items():
            if value is None:
                continue
            lines.append(f"{name}: {value}")
        lines.append(SEPARATOR)
        narrative = self.narrative.strip()
        if narrative:
            lines.append(narrative)
        return "\n".join(lines)

    @classmethod
    def from_text(cls, text: str, *, default_kind: str = "") -> MemoryDocument:
        """Parse an Atomic Memory ``text`` payload back into a document.

        Tolerant by design: a payload that predates the envelope still parses, with the
        whole text kept as the narrative. A line is only read as a field when its name
        is a plain snake_case identifier, so prose that happens to contain a colon is
        not mistaken for structured data.
        """
        raw = text or ""
        header, separator, narrative = raw.partition(f"\n{SEPARATOR}")

        fields: dict[str, str] = {}
        kind = default_kind
        key = ""
        leftover: list[str] = []
        for line in header.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            name, colon, value = line.partition(":")
            if not colon or not _FIELD_NAME.match(name.strip()):
                leftover.append(stripped)
                continue
            name = name.strip()
            value = value.strip()
            if name == "key":
                key = value
            else:
                fields[name] = value
        if key:
            kind = key.split(":", 1)[0]
        if not separator:
            # No envelope separator: whatever is not a field line is the narrative.
            narrative = "\n".join(leftover)
        return cls(kind=kind, key=key or kind, fields=fields, narrative=narrative.strip())


def field_number(document: MemoryDocument, name: str, default: float = 0.0) -> float:
    """Read a numeric field, tolerating malformed values."""
    raw = document.fields.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def field_bool(document: MemoryDocument, name: str, default: bool = False) -> bool:
    """Read a boolean field written as ``true``/``false``/``1``/``0``."""
    raw = document.fields.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"true", "1", "yes", "y"}


def field_text(document: MemoryDocument, name: str, default: str = "") -> str:
    """Read a textual field."""
    return document.fields.get(name, default)


def build_key(kind: str, discriminator: str = "") -> str:
    """Build the stable memory key for a kind, optionally scoped by a discriminator."""
    if not discriminator:
        return kind
    return f"{kind}:{discriminator}"


__all__ = [
    "ALL_KINDS",
    "KIND_KNOWLEDGE_STATE",
    "KIND_LEARNING_RECORD",
    "KIND_PREFERENCE",
    "KIND_PROFILE",
    "KIND_REVIEW_PLAN",
    "KIND_WEAKNESS",
    "MemoryDocument",
    "SEPARATOR",
    "build_key",
    "field_bool",
    "field_number",
    "field_text",
]
