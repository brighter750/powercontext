"""Mock-level tests for StudyFlow's orchestration.

These tests drive the real service and repository code against
:class:`FakePowerContextClient`. They verify StudyFlow's own behaviour — scope
lifecycle, create-or-revise semantics, profile and record handling, and user
isolation — without needing a running Server.

They are **not** a substitute for the integration tests: only the integration tests
prove that the real PowerContext Server behaves as StudyFlow expects.
"""

from __future__ import annotations

import pytest

from app.powercontext.client import (
    PowerContextAuthError,
    PowerContextError,
    PowerContextTimeout,
    PowerContextUnavailable,
)
from app.schemas import LearningRecordInput, ProfileUpdate, TutorRequest
from fakes import FakePowerContextClient


# --- profile ---------------------------------------------------------------


async def test_profile_is_created_and_read_back(mock_service, unique_user_id: str) -> None:
    """A saved profile is readable again from the same learner id."""
    profile, writes = await mock_service.update_profile(
        unique_user_id,
        ProfileUpdate(
            nickname="小舟",
            course="数据库系统",
            goal="通过期末规范化部分",
            preferred_style="先给例子再讲定义",
            focus_points=["BCNF 分解"],
        ),
    )

    assert profile.nickname == "小舟"
    assert profile.course == "数据库系统"
    assert profile.focus_points == ["BCNF 分解"]
    assert writes and writes[0].action == "created"

    reloaded = await mock_service.get_profile(unique_user_id)
    assert reloaded.nickname == "小舟"
    assert reloaded.source == "powercontext"
    assert reloaded.scope_id == profile.scope_id


async def test_profile_update_revises_the_same_entry(
    mock_service, unique_user_id: str
) -> None:
    """Updating a profile revises the existing artifact instead of duplicating it."""
    await mock_service.update_profile(unique_user_id, ProfileUpdate(nickname="小舟"))
    _, writes = await mock_service.update_profile(unique_user_id, ProfileUpdate(nickname="小舟2"))

    assert writes[0].action == "revised"

    snapshot = await mock_service.load_snapshot(unique_user_id)
    profiles = snapshot.by_kind("learner_profile")
    assert len(profiles) == 1
    assert profiles[0].fields["nickname"] == "小舟2"


# --- learning records ------------------------------------------------------


async def test_record_creates_authoritative_event_and_projection(
    mock_service, unique_user_id: str
) -> None:
    """Saving a record writes one learning_record and one knowledge_state."""
    result = await mock_service.create_record(
        unique_user_id,
        LearningRecordInput(
            course="数据库系统",
            topic="BCNF 分解",
            question="求候选码",
            user_answer="AC",
            correct_answer="AD",
            is_correct=False,
            self_rating=2,
        ),
    )

    assert result.record.topic == "BCNF 分解"
    assert result.record.artifact_id
    assert result.knowledge_state is not None
    assert result.knowledge_state.attempts == 1
    assert result.knowledge_state.wrong == 1

    snapshot = await mock_service.load_snapshot(unique_user_id)
    assert len(snapshot.by_kind("learning_record")) == 1
    assert len(snapshot.by_kind("knowledge_state")) == 1


async def test_second_record_revises_the_knowledge_state(
    mock_service, unique_user_id: str
) -> None:
    """A later practice revises the knowledge_state rather than adding a duplicate."""
    await mock_service.create_record(
        unique_user_id,
        LearningRecordInput(topic="BCNF 分解", is_correct=False, self_rating=2),
    )
    await mock_service.create_record(
        unique_user_id,
        LearningRecordInput(topic="BCNF 分解", is_correct=True, self_rating=4),
    )

    snapshot = await mock_service.load_snapshot(unique_user_id)
    states = snapshot.by_kind("knowledge_state")
    assert len(states) == 1, "knowledge_state 必须原地更新，不能出现两份互相矛盾的记录"
    assert states[0].fields["attempts"] == "2"

    records = snapshot.by_kind("learning_record")
    assert len(records) == 2, "learning_record 是权威事件日志，应当逐条追加"


async def test_records_are_returned_newest_first(
    mock_service, unique_user_id: str
) -> None:
    """The stored log is ordered for display."""
    await mock_service.create_record(
        unique_user_id,
        LearningRecordInput(topic="A", practiced_at="2026-01-01T00:00:00+00:00"),
    )
    await mock_service.create_record(
        unique_user_id,
        LearningRecordInput(topic="B", practiced_at="2026-02-01T00:00:00+00:00"),
    )

    records = await mock_service.list_records(unique_user_id)
    assert [record.topic for record in records] == ["B", "A"]


# --- weaknesses and review plan -------------------------------------------


async def test_weaknesses_reflect_real_practice(mock_service, unique_user_id: str) -> None:
    """A topic with repeated errors shows up as a weakness with cited evidence."""
    for index in range(3):
        await mock_service.create_record(
            unique_user_id,
            LearningRecordInput(
                topic="BCNF 分解",
                is_correct=False,
                self_rating=2,
                practiced_at=f"2026-01-0{index + 1}T00:00:00+00:00",
            ),
        )

    weaknesses = await mock_service.get_weaknesses(unique_user_id)
    assert len(weaknesses) == 1
    assert weaknesses[0].topic == "BCNF 分解"
    assert weaknesses[0].severity == "high"
    assert "累计练习 3 次" in weaknesses[0].reason


async def test_review_plan_cites_stored_evidence(
    mock_service, unique_user_id: str
) -> None:
    """The plan explains itself using the learner's real history."""
    await mock_service.create_record(
        unique_user_id,
        LearningRecordInput(
            topic="BCNF 分解",
            is_correct=False,
            self_rating=2,
            practiced_at="2026-01-01T00:00:00+00:00",
        ),
    )
    await mock_service.create_record(
        unique_user_id,
        LearningRecordInput(
            topic="BCNF 分解",
            is_correct=False,
            self_rating=2,
            practiced_at="2026-01-02T00:00:00+00:00",
        ),
    )

    plan = await mock_service.build_review_plan(unique_user_id, generate_questions=False)
    assert plan.items
    assert plan.items[0].topic == "BCNF 分解"
    assert "累计练习 2 次" in plan.items[0].reason
    assert "答错 2 次" in plan.items[0].reason


async def test_empty_plan_is_friendly(mock_service, unique_user_id: str) -> None:
    """A learner with no history gets guidance instead of an empty box."""
    plan = await mock_service.build_review_plan(unique_user_id, generate_questions=False)
    assert plan.items == []
    assert "还没有足够的学习记录" in plan.summary


# --- scope lifecycle and user isolation ------------------------------------


async def test_same_learner_reuses_one_scope(
    mock_service, unique_user_id: str
) -> None:
    """Repeated use of a learner id maps to the same PowerContext Scope."""
    await mock_service.update_profile(unique_user_id, ProfileUpdate(nickname="小舟"))
    first = await mock_service.load_snapshot(unique_user_id)
    await mock_service.create_record(unique_user_id, LearningRecordInput(topic="A"))
    second = await mock_service.load_snapshot(unique_user_id)

    assert first.scope_id == second.scope_id


async def test_different_learners_are_isolated(mock_service) -> None:
    """One learner's memory is never visible to another learner."""
    await mock_service.create_record(
        "student_iso_a",
        LearningRecordInput(topic="数据库规范化", notes="A 的私有记录"),
    )

    other = await mock_service.load_snapshot("student_iso_b")
    assert other.documents == []
    assert other.scope_id != (await mock_service.load_snapshot("student_iso_a")).scope_id

    other_records = await mock_service.list_records("student_iso_b")
    assert other_records == []


async def test_scope_is_bound_to_the_learner_id(
    mock_service, fake_client: FakePowerContextClient, unique_user_id: str
) -> None:
    """The learner id is bound to the Scope on the Server, not only in the browser."""
    await mock_service.update_profile(unique_user_id, ProfileUpdate(nickname="小舟"))

    binding = ("studyflow", "learner", unique_user_id)
    assert binding in fake_client.bindings
    scope_id = fake_client.bindings[binding]
    references = fake_client.scopes[scope_id]["external_references"]
    assert {"kind": "studyflow.learner_id", "value": unique_user_id} in references


# --- error handling --------------------------------------------------------


async def test_tutor_reports_missing_ai_instead_of_faking_an_answer(
    mock_service, unique_user_id: str
) -> None:
    """With no model configured the tutor says so plainly."""
    response = await mock_service.tutor(
        TutorRequest(user_id=unique_user_id, message="我今天应该复习什么？")
    )

    assert response.ai_configured is False
    assert "尚未配置" in response.answer
    assert "伪造" not in response.answer


async def test_powercontext_failure_surfaces_to_the_caller(
    mock_service, monkeypatch, unique_user_id: str
) -> None:
    """A Server outage is reported as a PowerContext error, not swallowed."""

    async def boom(*args, **kwargs):
        raise PowerContextUnavailable("无法连接 PowerContext Server")

    monkeypatch.setattr(mock_service.repository.client, "list_memory_entries", boom)

    with pytest.raises(PowerContextUnavailable):
        await mock_service.list_records(unique_user_id)


async def test_powercontext_timeout_is_distinguishable(
    mock_service, monkeypatch, unique_user_id: str
) -> None:
    """A timeout is a distinct error type from an outage."""

    async def boom(*args, **kwargs):
        raise PowerContextTimeout("PowerContext 请求超时")

    monkeypatch.setattr(mock_service.repository.client, "list_memory_entries", boom)

    with pytest.raises(PowerContextTimeout):
        await mock_service.list_records(unique_user_id)


async def test_powercontext_auth_error_is_distinguishable(
    mock_service, monkeypatch, unique_user_id: str
) -> None:
    """A rejected credential is a distinct error type."""

    async def boom(*args, **kwargs):
        raise PowerContextAuthError("PowerContext 拒绝了当前凭据")

    monkeypatch.setattr(mock_service.repository.client, "list_memory_entries", boom)

    with pytest.raises(PowerContextAuthError):
        await mock_service.list_records(unique_user_id)


def test_powercontext_errors_share_a_base_class() -> None:
    """Every transport failure can be caught with one handler."""
    for error in (
        PowerContextUnavailable("x"),
        PowerContextTimeout("x"),
        PowerContextAuthError("x"),
    ):
        assert isinstance(error, PowerContextError)
