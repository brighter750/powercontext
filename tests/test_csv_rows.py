from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    SourceCatalog,
    SourceDefinitionRegistry,
    SourceMaterialization,
    project_source_for_transport,
)
from powercontext.sources.csv_rows import (
    CSV_ROW_SOURCE_DEFINITION,
    CSVDocumentInput,
    CSVRowInput,
    CSVRowSource,
    CSVRowSourceAdapter,
    CSVRowTextEvidenceProjection,
    csv_row_source_id,
    parse_csv_document,
)


def make_document(
    content: bytes,
    *,
    document_id: str = "airports",
    primary_key: str = "id",
) -> CSVDocumentInput:
    return CSVDocumentInput(
        document_id=document_id,
        primary_key=primary_key,
        content=content,
    )


class EmptySourceBackend:
    async def list(self):
        return ()

    async def get(self, source, /):
        raise AssertionError(source)


def test_parse_csv_preserves_column_order_and_original_values() -> None:
    rows = parse_csv_document(make_document(b"id,name,country\n001,Changi,Singapore\n002,Test,Malaysia\n"))

    assert len(rows) == 2
    assert rows[0].columns == ("id", "name", "country")
    assert rows[0].values == ("001", "Changi", "Singapore")
    assert rows[0].primary_key_value == "001"
    assert rows[1].values == ("002", "Test", "Malaysia")


def test_parse_csv_accepts_utf8_bom() -> None:
    rows = parse_csv_document(make_document(b"\xef\xbb\xbfid,name\n1,Changi\n"))

    assert rows[0].columns == ("id", "name")


def test_parse_csv_preserves_whitespace_in_values_and_headers() -> None:
    rows = parse_csv_document(make_document(b" id ,name\n 1 , Changi \n", primary_key=" id "))

    assert rows[0].columns == (" id ", "name")
    assert rows[0].values == (" 1 ", " Changi ")
    assert rows[0].primary_key_value == " 1 "


def test_parse_csv_ignores_completely_blank_records() -> None:
    rows = parse_csv_document(make_document(b"id,name\n1,Changi\n\n"))

    assert len(rows) == 1


def test_parse_csv_header_only_returns_empty_tuple() -> None:
    assert parse_csv_document(make_document(b"id,name\n")) == ()


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"\n",
        b"id,,name\n1,2,3\n",
        b"id,   ,name\n1,2,3\n",
        b"id,name,id\n1,2,3\n",
        b"id,name\n1,A\n2\n",
        b"id,name\n1,A,B\n",
        b"id,name\n,A\n",
        b"id,name\n   ,A\n",
        b'id,name\n1,"unterminated\n',
        b"id,name\n1,\xff\n",
    ],
)
def test_parse_csv_rejects_invalid_documents(content: bytes) -> None:
    with pytest.raises(ValueError):
        parse_csv_document(make_document(content))


def test_parse_csv_requires_exact_primary_key_header() -> None:
    with pytest.raises(ValueError):
        parse_csv_document(make_document(b"id,name\n1,Changi\n", primary_key="ID"))


def test_parse_csv_rejects_duplicate_primary_key_values() -> None:
    with pytest.raises(ValueError):
        parse_csv_document(make_document(b"id,name\n1,A\n1,B\n"))


@pytest.mark.parametrize(
    "document_id",
    ["", " ", " airports", "airports "],
)
def test_document_id_rejects_empty_or_surrounding_whitespace(
    document_id: str,
) -> None:
    with pytest.raises(ValidationError):
        make_document(b"id,name\n1,A\n", document_id=document_id)


@pytest.mark.parametrize("primary_key", ["", " "])
def test_primary_key_rejects_empty_or_whitespace(primary_key: str) -> None:
    with pytest.raises(ValidationError):
        make_document(b"id,name\n1,A\n", primary_key=primary_key)


def test_csv_row_source_id_is_escaped_and_stable() -> None:
    source_id = csv_row_source_id("airports/doc", "001 A")

    assert source_id == "csv:airports%2Fdoc:001%20A"
    assert len(source_id) <= 256


def test_csv_row_source_id_rejects_excessive_length() -> None:
    with pytest.raises(ValueError):
        csv_row_source_id("doc", "x" * 300)


def test_source_definition_has_expected_identity_and_projection() -> None:
    definition = CSV_ROW_SOURCE_DEFINITION

    assert definition.name == "csv-row"
    assert definition.version == "1"
    assert definition.input_class is CSVRowInput
    assert definition.source_class is CSVRowSource
    assert len(definition.projections) == 1

    projection = definition.projections[0]
    assert isinstance(projection, CSVRowTextEvidenceProjection)


def test_adapter_resolves_and_reads_captured_row() -> None:
    row = CSVRowInput(
        document_id="airports",
        primary_key="id",
        primary_key_value="001",
        columns=("id", "name"),
        values=("001", "Changi"),
    )
    adapter = CSVRowSourceAdapter()

    async def scenario() -> None:
        source = await adapter.resolve(row)

        assert source.name == source.source_id
        assert source.source_id == "csv:airports:001"
        assert source.materialization is SourceMaterialization.CAPTURED
        assert await adapter.read(source) == row

    asyncio.run(scenario())


def test_text_evidence_projection_has_exact_content_and_metadata() -> None:
    source = CSVRowSource(
        name="csv:airports:001",
        source_id="csv:airports:001",
        materialization=SourceMaterialization.CAPTURED,
        document_id="airports",
        primary_key="id",
        primary_key_value="001",
        columns=("id", "name"),
        values=("001", "樟宜机场"),
    )

    evidence = CSVRowTextEvidenceProjection().project(source)

    assert evidence.source_type == "csv-row"
    assert evidence.source_id == source.source_id
    assert evidence.content == '{"id":"001","name":"樟宜机场"}'
    assert evidence.metadata == {
        "document_id": "airports",
        "primary_key": "id",
        "primary_key_value": "001",
        "columns": ["id", "name"],
        "row": {"id": "001", "name": "樟宜机场"},
    }
    assert len(evidence.metadata) == 5
    assert json.loads(evidence.content) == evidence.metadata["row"]


def test_registry_resolves_reads_projects_and_transports_csv_row() -> None:
    async def scenario() -> None:
        registry = SourceDefinitionRegistry((CSV_ROW_SOURCE_DEFINITION,))
        rows = parse_csv_document(make_document(b"id,name,country\n001,Changi,Singapore\n"))
        assert len(rows) == 1

        source = await registry.resolve(rows[0])

        assert isinstance(source, CSVRowSource)
        assert source.name == source.source_id
        assert source.materialization is SourceMaterialization.CAPTURED
        assert await registry.read(source) == rows[0]

        assert registry.projection_keys(source) == (TEXT_EVIDENCE_PROJECTION_KEY,)

        evidence = registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY)
        assert evidence == {
            "source_type": "csv-row",
            "source_id": "csv:airports:001",
            "content": '{"id":"001","name":"Changi","country":"Singapore"}',
            "metadata": {
                "document_id": "airports",
                "primary_key": "id",
                "primary_key_value": "001",
                "columns": ["id", "name", "country"],
                "row": {
                    "id": "001",
                    "name": "Changi",
                    "country": "Singapore",
                },
            },
        }

        observation = project_source_for_transport(registry, source)

        assert observation.source_type == "csv-row"
        assert observation.name == source.source_id
        assert observation.materialization is SourceMaterialization.CAPTURED

        catalog = SourceCatalog(backend=EmptySourceBackend())

        transported = await catalog.read(observation)
        assert transported == observation.payload

        transported_evidence = catalog.project(observation, TEXT_EVIDENCE_PROJECTION_KEY)
        assert transported_evidence == evidence

        assert registry.project(observation, TEXT_EVIDENCE_PROJECTION_KEY) == evidence

    asyncio.run(scenario())
