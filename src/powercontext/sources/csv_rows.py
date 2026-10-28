"""CSV row Sources with stable identities and text evidence projection."""

from __future__ import annotations

import csv
import io
import json
from urllib.parse import quote

from pydantic import BaseModel, field_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    AdapterSourceDefinition,
    TextEvidence,
)
from powercontext.sources.models import Source, SourceMaterialization


class CSVDocumentInput(BaseModel):
    """A complete offline CSV document."""

    document_id: str
    primary_key: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("document_id must not be empty")  # noqa: TRY003
        if value != value.strip():
            raise ValueError("document_id must not have surrounding whitespace")  # noqa: TRY003
        return value

    @field_validator("primary_key")
    @classmethod
    def validate_primary_key(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("primary_key must not be empty")  # noqa: TRY003
        return value


class CSVRowInput(BaseModel):
    """One parsed CSV row."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class CSVRowSource(Source):
    """A captured Source representing exactly one CSV row."""

    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Return the canonical stable identity for a CSV row."""
    if not document_id or document_id != document_id.strip():
        raise ValueError("document_id must be non-empty without surrounding whitespace")  # noqa: TRY003
    if not primary_key_value or not primary_key_value.strip():
        raise ValueError("primary_key_value must be non-empty and non-blank")  # noqa: TRY003

    source_id = f"csv:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError("CSV row source ID exceeds the maximum length")  # noqa: TRY003
    return source_id


def _read_csv_header(reader: csv.reader, primary_key: str) -> tuple[str, ...]:
    """Read and validate the CSV header."""
    try:
        columns = next(reader)
    except StopIteration:
        raise ValueError("CSV document must contain a header") from None  # noqa: TRY003

    if not columns:
        raise ValueError("CSV header must not be empty")  # noqa: TRY003
    if any(not column or not column.strip() for column in columns):
        raise ValueError("CSV header names must not be empty or whitespace-only")  # noqa: TRY003
    if len(set(columns)) != len(columns):
        raise ValueError("CSV header names must be unique")  # noqa: TRY003
    if primary_key not in columns:
        raise ValueError("primary_key must exactly match a CSV header")  # noqa: TRY003

    return tuple(columns)


def _parse_csv_rows(
    reader: csv.reader,
    value: CSVDocumentInput,
    columns: tuple[str, ...],
) -> tuple[CSVRowInput, ...]:
    """Parse rows and enforce primary-key uniqueness."""
    key_index = columns.index(value.primary_key)
    rows: list[CSVRowInput] = []
    seen_keys: set[str] = set()

    for fields in reader:
        # csv.reader represents a completely blank physical record as [].
        if fields == []:
            continue

        if len(fields) != len(columns):
            raise ValueError("CSV row width must match the header width")  # noqa: TRY003

        key_value = fields[key_index]
        if not key_value or not key_value.strip():
            raise ValueError("CSV primary key values must not be empty")  # noqa: TRY003
        if key_value in seen_keys:
            raise ValueError("CSV primary key values must be unique")  # noqa: TRY003
        seen_keys.add(key_value)

        rows.append(
            CSVRowInput(
                document_id=value.document_id,
                primary_key=value.primary_key,
                primary_key_value=key_value,
                columns=columns,
                values=tuple(fields),
            )
        )

    return tuple(rows)


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Parse and validate a complete CSV document without partial results."""
    try:
        text = value.content.decode("utf-8-sig", errors="strict")
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        columns = _read_csv_header(reader, value.primary_key)
        return _parse_csv_rows(reader, value, columns)
    except (UnicodeDecodeError, csv.Error) as error:
        raise ValueError("Invalid UTF-8 or malformed CSV document") from error  # noqa: TRY003


class CSVRowSourceAdapter:
    """Resolve and read individual CSV rows."""

    input_class = CSVRowInput
    name = "csv-row"
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        source_id = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


class CSVRowTextEvidenceProjection:
    """Project one CSV row into canonical text evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(
            source_type="csv-row",
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


CSV_ROW_SOURCE_DEFINITION = AdapterSourceDefinition(
    CSVRowSourceAdapter(),
    projections=(CSVRowTextEvidenceProjection(),),
)
