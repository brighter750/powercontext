"""Parse Pi v3 session history into stable, projectable source events."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.connectors import ConnectorRunCompletion, ConnectorRunStatus
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence


class PiSessionFormatError(ValueError):
    """Raised when a Pi session archive is invalid."""

    MESSAGES: ClassVar[dict[str, str]] = {
        "archive encoding": "Invalid Pi session archive encoding",
        "empty archive": "Empty Pi session archive",
        "invalid JSONL record": "Invalid JSONL record",
        "invalid session header": "Invalid Pi v3 session header",
        "invalid event object": "Each event must be a JSON object",
        "invalid event ID": "Missing or duplicate event ID",
        "invalid parent": "Parent must refer to a previously seen event",
        "no events": "Session has no events",
        "missing leaf": "Selected leaf does not exist",
        "ancestry cycle": "Cycle detected in session ancestry",
        "unreadable archive": "Invalid or unreadable Pi session archive",
        "invalid checkpoint": "Invalid Pi session checkpoint",
        "identity changed": "Pi session identity changed",
        "event changed": "Previously observed Pi event was changed",
    }

    @classmethod
    def archive_encoding(cls) -> PiSessionFormatError:
        return cls("archive encoding")

    @classmethod
    def empty_archive(cls) -> PiSessionFormatError:
        return cls("empty archive")

    @classmethod
    def invalid_jsonl_record(cls) -> PiSessionFormatError:
        return cls("invalid JSONL record")

    @classmethod
    def invalid_session_header(cls) -> PiSessionFormatError:
        return cls("invalid session header")

    @classmethod
    def invalid_event_object(cls) -> PiSessionFormatError:
        return cls("invalid event object")

    @classmethod
    def invalid_event_id(cls) -> PiSessionFormatError:
        return cls("invalid event ID")

    @classmethod
    def invalid_parent(cls) -> PiSessionFormatError:
        return cls("invalid parent")

    @classmethod
    def no_events(cls) -> PiSessionFormatError:
        return cls("no events")

    @classmethod
    def missing_leaf(cls) -> PiSessionFormatError:
        return cls("missing leaf")

    @classmethod
    def ancestry_cycle(cls) -> PiSessionFormatError:
        return cls("ancestry cycle")

    @classmethod
    def unreadable_archive(cls) -> PiSessionFormatError:
        return cls("unreadable archive")

    @classmethod
    def invalid_checkpoint(cls) -> PiSessionFormatError:
        return cls("invalid checkpoint")

    @classmethod
    def identity_changed(cls) -> PiSessionFormatError:
        return cls("identity changed")

    @classmethod
    def event_changed(cls, event_id: str) -> PiSessionFormatError:
        return cls("event changed", event_id)

    def __init__(self, issue: str, detail: str = "") -> None:
        self.issue = issue
        self.detail = detail
        message = self.MESSAGES.get(issue, issue)
        if detail:
            message = f"{message}: {detail}"
        super().__init__(message)


class PiSessionInput(BaseModel):
    """Raw Pi session archive and optional selected leaf."""

    model_config = ConfigDict(frozen=True)

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible event from the selected ancestry path."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    event_id: str
    parent_id: str | None = None
    event_type: str
    role: str
    content: str
    record: dict[str, Any]


class PiEventSource(Source):
    """Stable source identity for one Pi event."""

    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None = None
    event_type: str
    role: str
    content: str
    record: dict[str, Any]


def _text_content(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            item["text"]
            for item in value
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
        )
    return ""


def _message_body(
    message: dict[str, Any],
    content: object,
) -> tuple[str, str] | None:
    role = message.get("role")
    if not isinstance(role, str):
        role = ""

    if role in {"thinking", "image"}:
        return None

    if role == "toolResult":
        body = _text_content(content)
        tool_name = message.get("toolName", "")
        call_id = message.get("toolCallId", "")
        return role, f"[tool_result {tool_name} {call_id}] {body}"

    if role == "bashExecution":
        command = message.get("command", "")
        output = message.get("output", "")
        return role, f"$ {command}\n{output}"

    if isinstance(content, list):
        calls = [
            item
            for item in content
            if isinstance(item, dict) and item.get("type") == "toolCall"
        ]
        if calls:
            rendered = []
            for item in calls:
                arguments = item.get("arguments", {})
                rendered.append(
                    f"[tool_call {item.get('name', '')} {item.get('id', '')}] "
                    f"{json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
                )
            return role or "assistant", "\n".join(rendered)

    body = _text_content(content)
    if body:
        return role or "assistant", body
    return None


def _summary_body(record: Mapping[str, Any]) -> tuple[str, str] | None:
    event_type = record.get("type")
    body = record.get("summary", record.get("content", ""))
    if not isinstance(body, str):
        body = _text_content(body)
    return event_type, body


def _custom_message_body(
    content: object,
    record: Mapping[str, Any],
) -> tuple[str, str] | None:
    value = content if content is not None else record.get("content")
    body = _text_content(value)
    if body:
        return "custom", body
    return None


def _event_body(record: Mapping[str, Any]) -> tuple[str, str] | None:
    event_type = record.get("type")
    message = record.get("message")
    if not isinstance(message, dict):
        message = {}

    content = message.get("content", record.get("content"))

    if event_type == "message":
        if "toolName" not in message and "toolName" in record:
            message = {**message, "toolName": record["toolName"]}
        if "toolCallId" not in message and "toolCallId" in record:
            message = {**message, "toolCallId": record["toolCallId"]}
        if "command" not in message and "command" in record:
            message = {**message, "command": record["command"]}
        if "output" not in message and "output" in record:
            message = {**message, "output": record["output"]}
        return _message_body(message, content)

    if event_type in {"compaction", "branch_summary"}:
        return _summary_body(record)

    if event_type == "custom_message":
        return _custom_message_body(content, record)

    return None


def _decode_pi_records(content: bytes) -> list[dict[str, Any] | object]:
    try:
        text = content.decode("utf-8-sig")
    except (AttributeError, UnicodeDecodeError) as error:
        raise PiSessionFormatError.archive_encoding() from error

    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise PiSessionFormatError.empty_archive()

    try:
        return [json.loads(line) for line in lines]
    except (json.JSONDecodeError, TypeError) as error:
        raise PiSessionFormatError.invalid_jsonl_record() from error


def _validate_pi_header(records: list[dict[str, Any] | object]) -> str:
    header = records[0]
    if (
        not isinstance(header, dict)
        or header.get("type") != "session"
        or header.get("version") != 3
        or not isinstance(header.get("id"), str)
        or not header["id"].strip()
    ):
        raise PiSessionFormatError.invalid_session_header()
    return header["id"]


def _collect_pi_nodes(
    records: list[dict[str, Any] | object],
) -> tuple[dict[str, dict[str, Any]], dict[str, str | None], list[str]]:
    nodes: dict[str, dict[str, Any]] = {}
    parents: dict[str, str | None] = {}
    order: list[str] = []

    for record in records[1:]:
        if not isinstance(record, dict):
            raise PiSessionFormatError.invalid_event_object()

        event_id = record.get("id")
        if not isinstance(event_id, str) or not event_id or event_id in nodes:
            raise PiSessionFormatError.invalid_event_id()

        parent_id = record.get("parentId")
        if parent_id in ("", None):
            parent_id = None

        if parent_id is not None and (
            not isinstance(parent_id, str) or parent_id not in nodes
        ):
            raise PiSessionFormatError.invalid_parent()

        nodes[event_id] = record
        parents[event_id] = parent_id
        order.append(event_id)

    if not order:
        raise PiSessionFormatError.no_events()

    return nodes, parents, order


def _select_pi_ancestry(
    nodes: dict[str, dict[str, Any]],
    parents: dict[str, str | None],
    order: list[str],
    leaf_id: str | None,
) -> list[str]:
    selected_leaf = leaf_id if leaf_id is not None else order[-1]
    if selected_leaf not in nodes:
        raise PiSessionFormatError.missing_leaf()

    ancestry: list[str] = []
    seen: set[str] = set()
    current: str | None = selected_leaf

    while current is not None:
        if current in seen:
            raise PiSessionFormatError.ancestry_cycle()
        seen.add(current)
        ancestry.append(current)
        current = parents[current]

    ancestry.reverse()
    return ancestry


def _build_pi_events(
    session_id: str,
    ancestry: list[str],
    nodes: dict[str, dict[str, Any]],
    parents: dict[str, str | None],
) -> tuple[PiEventInput, ...]:
    events: list[PiEventInput] = []

    for event_id in ancestry:
        record = nodes[event_id]
        rendered = _event_body(record)
        if rendered is None:
            continue

        role, body = rendered
        events.append(
            PiEventInput(
                session_id=session_id,
                event_id=event_id,
                parent_id=parents[event_id],
                event_type=str(record.get("type", "")),
                role=role,
                content=f"{role}: {body}",
                record=record,
            )
        )

    return tuple(events)


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Validate a Pi v3 JSONL archive and return visible selected-branch events."""
    records = _decode_pi_records(value.content)
    session_id = _validate_pi_header(records)
    nodes, parents, order = _collect_pi_nodes(records)
    ancestry = _select_pi_ancestry(nodes, parents, order, value.leaf_id)
    return _build_pi_events(session_id, ancestry, nodes, parents)


class PiEventSourceAdapter:
    """Resolve and read durable Pi event sources."""

    input_class = PiEventInput
    source_class = PiEventSource
    name = "pi-event"
    version = "1"

    async def resolve(self, value: PiEventInput) -> PiEventSource:
        identity = json.dumps(
            [value.session_id, value.event_id],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        source_id = f"pi:{identity}"
        if len(source_id) > MAX_SOURCE_ID_LENGTH:
            digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            source_id = f"pi:sha256:{digest}"
        return PiEventSource(
            name=source_id,
            definition_version=self.version,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class PiEventTextEvidenceProjection:
    """Expose a Pi event as searchable text evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class = TextEvidence

    def project(self, source: PiEventSource) -> TextEvidence:
        return TextEvidence(
            source_type="pi-event",
            source_id=source.name,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    PiEventSourceAdapter(),
    projections=(PiEventTextEvidenceProjection(),),
)


async def _read_pi_archive(path: str, leaf_id: str | None) -> tuple[bytes, tuple[PiEventInput, ...], list[dict[str, Any] | object]]:
    try:
        raw = await asyncio.to_thread(Path(path).read_bytes)
        events = parse_pi_session(PiSessionInput(content=raw, leaf_id=leaf_id))
        records = _decode_pi_records(raw)
    except (OSError, PiSessionFormatError) as error:
        raise PiSessionFormatError.unreadable_archive() from error

    return raw, events, records


def _hash_pi_records(records: list[dict[str, Any] | object]) -> tuple[str, dict[str, str]]:
    header = records[0]
    if not isinstance(header, dict):
        raise PiSessionFormatError.invalid_session_header()

    session_id = header["id"]
    current_hashes: dict[str, str] = {}

    for record in records[1:]:
        if not isinstance(record, dict):
            raise PiSessionFormatError.invalid_event_object()

        event_id = record["id"]
        canonical = json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        current_hashes[event_id] = hashlib.sha256(canonical).hexdigest()

    return session_id, current_hashes


def _load_pi_checkpoint(
    previous: object,
    session_id: str,
    current_hashes: dict[str, str],
) -> tuple[dict[str, str], list[str]]:
    if previous is None:
        return {}, []

    if not isinstance(previous, dict):
        raise PiSessionFormatError.invalid_checkpoint()

    if previous.get("session_id") != session_id:
        raise PiSessionFormatError.identity_changed()

    hashes = previous.get("record_hashes")
    ids = previous.get("imported_event_ids")

    if not isinstance(hashes, dict) or not isinstance(ids, list):
        raise PiSessionFormatError.invalid_checkpoint()

    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in hashes.items()):
        raise PiSessionFormatError.invalid_checkpoint()

    if any(not isinstance(item, str) for item in ids):
        raise PiSessionFormatError.invalid_checkpoint()

    known_hashes = dict(hashes)
    imported = list(ids)

    for event_id, old_hash in known_hashes.items():
        if current_hashes.get(event_id) != old_hash:
            raise PiSessionFormatError.event_changed(event_id)

    return known_hashes, imported


async def _submit_pi_events(
    session: Any,
    events: tuple[PiEventInput, ...],
    imported: list[str],
    previous: object,
) -> tuple[list[str], ConnectorRunCompletion | None]:
    imported_set = set(imported)

    for event in events:
        if event.event_id in imported_set:
            continue

        outcome = await session.submit(event.event_id, "pi-event", event)
        status = getattr(outcome, "status", None)

        if status is None or str(status).lower().split(".")[-1] != "accepted":
            return imported, ConnectorRunCompletion(
                status=ConnectorRunStatus.INCOMPLETE,
                checkpoint=previous,
            )

        imported.append(event.event_id)
        imported_set.add(event.event_id)

    return imported, None


class PiSessionConnector:
    """Incrementally import visible events from a Pi session archive."""

    name = "pi-session-history"
    version = "1"
    source_definitions = frozenset({"pi-event"})

    def __init__(self, path: str, leaf_id: str | None = None) -> None:
        self.path = path
        self.leaf_id = leaf_id

    async def run(self, session: Any) -> ConnectorRunCompletion:
        """Incrementally import visible Pi events with mutation detection."""
        _, events, records = await _read_pi_archive(self.path, self.leaf_id)
        session_id, current_hashes = _hash_pi_records(records)

        previous = session.checkpoint
        known_hashes, imported = _load_pi_checkpoint(
            previous,
            session_id,
            current_hashes,
        )

        imported, incomplete = await _submit_pi_events(
            session,
            events,
            imported,
            previous,
        )
        if incomplete is not None:
            return incomplete

        merged_hashes = dict(known_hashes)
        merged_hashes.update(current_hashes)

        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint={
                "session_id": session_id,
                "record_hashes": merged_hashes,
                "imported_event_ids": imported,
            },
        )
