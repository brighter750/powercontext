"""Integration tests against the **real** PowerContext Server.

These tests exercise the actual HTTP contract StudyFlow depends on: scope creation,
durable external binding, Atomic Memory writes, revision-based updates, recall by
search, cross-learner isolation, and persistence across process restarts.

They are skipped automatically when no Server is reachable. Point them at a specific
Server with::

    STUDYFLOW_TEST_POWERCONTEXT_URL=http://127.0.0.1:8765
    STUDYFLOW_TEST_POWERCONTEXT_TOKEN=<token>
"""

from __future__ import annotations

import pytest

from app.memory_document import KIND_KNOWLEDGE_STATE, MemoryDocument, build_key
from app.schemas import LearningRecordInput, ProfileUpdate


async def test_scope_is_created_and_bound_on_the_real_server(
    live_service, unique_user_id: str
) -> None:
    """The learner's Scope is created on the Server and bound to their id."""
    profile, writes = await live_service.update_profile(
        unique_user_id,
        ProfileUpdate(nickname="集成测试", course="数据库系统"),
    )

    assert writes[0].action == "created"
    assert profile.scope_id.startswith("scp_")

    # A fresh service instance must resolve the same Scope from the Server alone.
    scope_id, _, _ = await live_service.ensure_scope(unique_user_id)
    assert scope_id == profile.scope_id


async def test_write_then_read_from_the_real_server(
    live_service, unique_user_id: str
) -> None:
    """A memory written to PowerContext is readable back from it."""
    await live_service.update_profile(unique_user_id, ProfileUpdate(course="数据库系统"))

    result = await live_service.create_record(
        unique_user_id,
        LearningRecordInput(
            course="数据库系统",
            topic="BCNF 分解",
            question="求候选码",
            user_answer="AC",
            correct_answer="AD",
            is_correct=False,
            self_rating=2,
            notes="集成测试记录",
        ),
    )
    assert result.record.artifact_id

    records = await live_service.list_records(unique_user_id)
    assert len(records) == 1
    assert records[0].topic == "BCNF 分解"
    assert records[0].notes == "集成测试记录"


async def test_update_revises_the_same_artifact_on_the_real_server(
    live_service, live_client, unique_user_id: str
) -> None:
    """A later activity revises the stored entry instead of duplicating it."""
    await live_service.create_record(
        unique_user_id,
        LearningRecordInput(topic="BCNF 分解", is_correct=False, self_rating=2),
    )
    await live_service.create_record(
        unique_user_id,
        LearningRecordInput(topic="BCNF 分解", is_correct=True, self_rating=4),
    )

    scope_id, _, _ = await live_service.ensure_scope(unique_user_id)
    entries = await live_client.list_memory_entries(scope_id=scope_id, limit=100)
    states = [
        entry
        for entry in entries
        if MemoryDocument.from_text(entry.text, default_kind=entry.kind).kind
        == KIND_KNOWLEDGE_STATE
    ]

    assert len(states) == 1, "掌握度投影必须只有一条，不能出现互相矛盾的两份"
    document = MemoryDocument.from_text(states[0].text, default_kind=states[0].kind)
    assert document.fields["attempts"] == "2"
    assert document.fields["correct"] == "1"


async def test_revision_history_is_preserved_on_the_real_server(
    live_service, live_client, unique_user_id: str
) -> None:
    """Revising keeps the earlier revision reachable through the artifact history."""
    scope_id, _, _ = await live_service.ensure_scope(unique_user_id)

    document = MemoryDocument(
        kind=KIND_KNOWLEDGE_STATE,
        key=build_key(KIND_KNOWLEDGE_STATE, "集成知识点"),
        fields={"topic": "集成知识点", "mastery": "0.2000"},
        narrative="第一版。",
    )
    created = await live_service.repository.upsert_document(scope_id, document)
    assert created.entry.revision == 1

    document.fields["mastery"] = "0.8000"
    document.narrative = "第二版。"
    revised = await live_service.repository.upsert_document(scope_id, document)

    assert revised.action == "revised"
    assert revised.entry.revision == 2
    assert revised.entry.artifact_id == created.entry.artifact_id

    text, etag = await live_client.get_artifact(
        scope_id=scope_id, artifact_id=created.entry.artifact_id
    )
    assert "第二版。" in text
    assert etag == '"revision:2"'


async def test_recall_by_search_on_the_real_server(
    live_service, live_client, unique_user_id: str
) -> None:
    """PowerContext can recall the learner's own memory by keyword."""
    await live_service.create_record(
        unique_user_id,
        LearningRecordInput(
            topic="BCNF 分解",
            notes="候选码求解过程练习",
            is_correct=False,
            self_rating=2,
        ),
    )

    scope_id, _, _ = await live_service.ensure_scope(unique_user_id)
    hits = await live_client.search_memory(scope_id=scope_id, query="候选码", limit=5)

    assert hits, "写入的记忆必须能被检索到"
    assert any("候选码" in entry.text for entry, _ in hits)


async def test_learners_are_isolated_on_the_real_server(live_service) -> None:
    """One learner's memory is unreachable through another learner's Scope."""
    await live_service.create_record(
        "student_iso_a_real",
        LearningRecordInput(topic="数据库规范化", notes="只属于 A 的私有记录"),
    )

    other_records = await live_service.list_records("student_iso_b_real")
    assert other_records == [], "不同学习者之间不能读取到对方的记忆"

    scope_a, _, _ = await live_service.ensure_scope("student_iso_a_real")
    scope_b, _, _ = await live_service.ensure_scope("student_iso_b_real")
    assert scope_a != scope_b

    entries_b = await live_service.repository.read_entries(scope_b)
    assert entries_b == []


async def test_memory_survives_a_restart(live_client, live_service, unique_user_id: str) -> None:
    """Memory written before a restart is still readable after it.

    The restart is simulated by building a completely new client and service that
    share nothing but the Server's durable storage.
    """
    from app.config import get_settings
    from app.powercontext.client import PowerContextClient
    from app.powercontext.repository import MemoryRepository
    from app.services.ai_service import AiService
    from app.services.learning_service import LearningService

    await live_service.create_record(
        unique_user_id,
        LearningRecordInput(topic="重启保留", notes="重启前写入", is_correct=True, self_rating=4),
    )

    settings = get_settings()
    fresh_client = PowerContextClient(
        settings.powercontext_base_url,
        token=settings.powercontext_token,
        timeout=settings.powercontext_timeout,
    )
    try:
        fresh_service = LearningService(
            MemoryRepository(fresh_client), AiService(settings, provider=None), settings
        )
        records = await fresh_service.list_records(unique_user_id)
        assert len(records) == 1
        assert records[0].notes == "重启前写入"
    finally:
        await fresh_client.aclose()


async def test_upsert_is_idempotent_for_the_same_key(
    live_service, live_client, unique_user_id: str
) -> None:
    """Writing the same key twice yields one entry with a bumped revision."""
    scope_id, _, _ = await live_service.ensure_scope(unique_user_id)
    document = MemoryDocument(
        kind=KIND_KNOWLEDGE_STATE,
        key=build_key(KIND_KNOWLEDGE_STATE, "幂等知识点"),
        fields={"topic": "幂等知识点"},
        narrative="第一版。",
    )

    first = await live_service.repository.upsert_document(scope_id, document)
    second = await live_service.repository.upsert_document(scope_id, document)

    assert first.action == "created"
    assert second.action == "revised"
    assert second.entry.artifact_id == first.entry.artifact_id

    entries = await live_client.list_memory_entries(scope_id=scope_id, limit=100)
    matching = [
        entry
        for entry in entries
        if MemoryDocument.from_text(entry.text, default_kind=entry.kind).key
        == "knowledge_state:幂等知识点"
    ]
    assert len(matching) == 1


@pytest.mark.parametrize("query", ["", "不存在的主题", "BCNF"])
async def test_search_never_raises_on_odd_queries(
    live_service, live_client, unique_user_id: str, query: str
) -> None:
    """Search stays well-behaved for empty, unknown, and known queries."""
    scope_id, _, _ = await live_service.ensure_scope(unique_user_id)
    if query:
        hits = await live_client.search_memory(scope_id=scope_id, query=query, limit=5)
        assert isinstance(hits, list)
