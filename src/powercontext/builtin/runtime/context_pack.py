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

"""Transfer prepared context as a verifiable pack and reuse its prefix over deltas.

A *Context Pack* carries prepared context as bounded, individually addressed chunks.
The first transfer sends the whole pack; later transfers send a *delta* that names how
many leading chunks the receiver already holds and carries only the remaining suffix.
Both forms are self-describing JSON documents with a SHA-256 digest, so a receiver can
rebuild the exact target content from serialized bytes alone.

The chunking is line oriented on purpose. A prepared context is a sequence of lines,
and a line boundary is a stable place to cut: when one line changes, the lines after it
keep their identity, so the delta can still name them as an unchanged prefix. A fixed
capacity cut would shift every later boundary and lose the whole tail.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Final

from powercontext.errors import PowerContextError

CONTEXT_PACK_SCHEMA: Final = "powercontext.context-pack.v1"
CONTEXT_PACK_DELTA_SCHEMA: Final = "powercontext.context-pack-delta.v1"
CONTEXT_PACK_DEFAULT_CHUNK_BYTES: Final = 512

_DIGEST_PREFIX: Final = "sha256:"
_DIGEST_PATTERN: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
_PACK_FIELDS: Final = frozenset({"schema", "items", "digest", "byte_count"})
_DELTA_FIELDS: Final = frozenset({"schema", "base_digest", "target_digest", "prefix_items", "appended"})


class ContextPackError(PowerContextError, ValueError):
    """Raised when a context pack or delta violates the transfer protocol."""

    def __init__(self, code: str) -> None:
        self.code = code
        messages = {
            "items-type": "context pack items must be a sequence of byte chunks",
            "item-type": "every context pack item must be bytes",
            "chunk-size": "context pack chunk size must be a positive integer",
            "chunk-size-character": "context pack chunk size cannot hold one character",
            "content-encoding": "context pack content must be valid UTF-8",
            "schema": "unsupported context pack schema",
            "fields": "context pack document has the wrong fields",
            "json": "context pack document is not valid JSON",
            "object": "context pack document must be a JSON object",
            "digest": "context pack digest does not match its content",
            "digest-format": "context pack digest must be a sha256 digest",
            "byte-count": "context pack byte count does not match its serialized size",
            "byte-count-converge": "context pack byte count did not converge",
            "prefix-type": "context pack delta prefix must be a non-negative integer",
            "prefix-range": "context pack delta prefix exceeds the base pack",
            "base-digest": "context pack delta does not belong to this base pack",
            "target-digest": "context pack delta does not rebuild the target pack",
            "items-list": "context pack chunks must be a list of Base64 strings",
            "item-base64": "every context pack chunk must be a Base64 string",
            "item-base64-invalid": "a context pack chunk is not valid Base64",
        }
        super().__init__(messages.get(code, f"invalid context pack: {code}"))


@dataclass(frozen=True, slots=True)
class ContextPack:
    """One complete prepared context, addressed as bounded byte chunks.

    ``items`` is the chunk list in content order. Concatenating them reproduces the
    exact bytes of the prepared context, so the pack is a lossless carrier rather than
    a re-encoding of the text.
    """

    items: tuple[bytes, ...]

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        """Build a pack from already chunked content."""
        if isinstance(items, (bytes, bytearray, str)) or not isinstance(items, (tuple, list)):
            raise ContextPackError("items-type")
        chunks = tuple(items)
        for chunk in chunks:
            if not isinstance(chunk, (bytes, bytearray)):
                raise ContextPackError("item-type")
        return cls(items=tuple(bytes(chunk) for chunk in chunks))

    @classmethod
    def from_content(cls, content: str | bytes, *, chunk_bytes: int = CONTEXT_PACK_DEFAULT_CHUNK_BYTES) -> ContextPack:
        """Chunk content into bounded pieces that each stay valid UTF-8.

        The cut points follow the content's own line boundaries, so an unchanged line
        keeps its chunk identity even when the lines around it change. A line longer
        than ``chunk_bytes`` is split at character boundaries instead.
        """
        limit = _validate_chunk_bytes(chunk_bytes)
        if isinstance(content, str):
            data = content.encode("utf-8")
        else:
            data = bytes(content)
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ContextPackError("content-encoding") from error
        return cls(items=tuple(_chunk_content(data, limit)))

    @property
    def digest(self) -> str:
        """The SHA-256 digest of the pack's schema and chunks."""
        return _digest(_canonical({"schema": CONTEXT_PACK_SCHEMA, "items": _encode_items(self.items)}))

    @property
    def byte_count(self) -> int:
        """The exact number of bytes this pack occupies on the wire."""
        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        """Serialize the pack into its canonical wire form."""
        payload: dict[str, object] = {
            "schema": CONTEXT_PACK_SCHEMA,
            "items": _encode_items(self.items),
            "digest": self.digest,
            "byte_count": 0,
        }
        return _seal(payload)

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPack:
        """Decode and verify a pack received over the wire."""
        payload = _decode_payload(data, _PACK_FIELDS, "context pack")
        if payload["schema"] != CONTEXT_PACK_SCHEMA:
            raise ContextPackError("schema")
        pack = cls(items=_decode_items(payload["items"]))
        if payload["digest"] != pack.digest:
            raise ContextPackError("digest")
        byte_count = payload["byte_count"]
        if type(byte_count) is not int or byte_count != pack.byte_count:
            raise ContextPackError("byte-count")
        return pack


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """One incremental transfer that reuses the leading chunks of a base pack."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        """Describe how to turn ``base`` into ``target`` by reusing its prefix."""
        limit = min(len(base.items), len(target.items))
        prefix = 0
        while prefix < limit and base.items[prefix] == target.items[prefix]:
            prefix += 1
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix,
            appended=target.items[prefix:],
        )

    @property
    def byte_count(self) -> int:
        """The exact number of bytes this delta occupies on the wire."""
        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        """Serialize the delta into its canonical wire form."""
        return _canonical({
            "schema": CONTEXT_PACK_DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": _encode_items(self.appended),
        })

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPackDelta:
        """Decode and verify a delta received over the wire."""
        payload = _decode_payload(data, _DELTA_FIELDS, "context pack delta")
        if payload["schema"] != CONTEXT_PACK_DELTA_SCHEMA:
            raise ContextPackError("schema")
        prefix_items = payload["prefix_items"]
        if type(prefix_items) is not int or prefix_items < 0:
            raise ContextPackError("prefix-type")
        base_digest = payload["base_digest"]
        target_digest = payload["target_digest"]
        if not isinstance(base_digest, str) or not _is_digest(base_digest):
            raise ContextPackError("digest-format")
        if not isinstance(target_digest, str) or not _is_digest(target_digest):
            raise ContextPackError("digest-format")
        return cls(
            base_digest=base_digest,
            target_digest=target_digest,
            prefix_items=prefix_items,
            appended=_decode_items(payload["appended"]),
        )


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Rebuild the target pack from a base pack and a delta, verifying both digests."""
    if base.digest != delta.base_digest:
        raise ContextPackError("base-digest")
    if delta.prefix_items > len(base.items):
        raise ContextPackError("prefix-range")
    target = ContextPack(items=base.items[: delta.prefix_items] + delta.appended)
    if target.digest != delta.target_digest:
        raise ContextPackError("target-digest")
    return target


def align_context_pack_to_base(base: ContextPack, content: str) -> ContextPack | None:
    """Cut ``content`` so that it starts with the chunks ``base`` already holds.

    A base pack that was cut differently than this build cuts content shares no chunk
    with it, but its leading chunks still spell out the bytes the receiver keeps. Cutting
    the content to those same boundaries restores a usable prefix. ``None`` means the base
    holds no leading bytes of this content, so nothing can be reused.
    """
    data = content.encode("utf-8")
    offset = 0
    reused = 0
    for item in base.items:
        if data[offset : offset + len(item)] != item:
            break
        offset += len(item)
        reused += 1
    if reused == 0:
        return None
    items = base.items[:reused] + tuple(_chunk_content(data[offset:], CONTEXT_PACK_DEFAULT_CHUNK_BYTES))
    return ContextPack(items=items)


def _validate_chunk_bytes(chunk_bytes: int) -> int:
    if not isinstance(chunk_bytes, int) or isinstance(chunk_bytes, bool) or chunk_bytes < 1:
        raise ContextPackError("chunk-size")
    return chunk_bytes


def _chunk_content(data: bytes, limit: int) -> list[bytes]:
    """Cut content into bounded chunks that never split a UTF-8 character."""
    chunks: list[bytes] = []
    start = 0
    while start < len(data):
        newline = data.find(b"\n", start)
        end = len(data) if newline == -1 else newline + 1
        chunks.extend(_split_line(data[start:end], limit))
        start = end
    return chunks


def _split_line(line: bytes, limit: int) -> list[bytes]:
    """Split one line into pieces of at most ``limit`` bytes on character boundaries."""
    pieces: list[bytes] = []
    start = 0
    length = len(line)
    while start < length:
        end = min(start + limit, length)
        # A UTF-8 character continues while it sees 10xxxxxx bytes, so a cut may not land
        # inside one. Backing up to the character's first byte keeps every piece valid.
        while end > start and end < length and (line[end] & 0xC0) == 0x80:
            end -= 1
        if end == start:
            raise ContextPackError("chunk-size-character")
        pieces.append(line[start:end])
        start = end
    return pieces


def _canonical(payload: dict[str, object]) -> bytes:
    """Encode one payload as the canonical wire form."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(canonical: bytes) -> str:
    return f"{_DIGEST_PREFIX}{hashlib.sha256(canonical).hexdigest()}"


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and _DIGEST_PATTERN.match(value) is not None


def _encode_items(items: tuple[bytes, ...]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


def _decode_items(value: object) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError("items-list")
    decoded: list[bytes] = []
    for item in value:
        if not isinstance(item, str):
            raise ContextPackError("item-base64")
        try:
            decoded.append(base64.b64decode(item, validate=True))
        except (binascii.Error, ValueError) as error:
            raise ContextPackError("item-base64-invalid") from error
    return tuple(decoded)


def _decode_payload(data: bytes, expected: frozenset[str], label: str) -> dict[str, object]:
    """Decode one wire document and require exactly the expected fields."""
    if not isinstance(data, (bytes, bytearray)):
        raise ContextPackError("object")
    try:
        payload = json.loads(bytes(data))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContextPackError("json") from error
    if not isinstance(payload, dict):
        raise ContextPackError("object")
    if set(payload) != expected:
        raise ContextPackError("fields")
    return payload


def _seal(payload: dict[str, object]) -> bytes:
    """Finalize a payload whose ``byte_count`` field states its own serialized size."""
    count = 0
    for _ in range(8):
        payload["byte_count"] = count
        actual = len(_canonical(payload))
        if actual == count:
            return _canonical(payload)
        count = actual
    raise ContextPackError("byte-count-converge")


__all__ = [
    "CONTEXT_PACK_DEFAULT_CHUNK_BYTES",
    "CONTEXT_PACK_DELTA_SCHEMA",
    "CONTEXT_PACK_SCHEMA",
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "align_context_pack_to_base",
    "apply_context_pack_delta",
]
