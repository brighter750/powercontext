"""Unit tests for StudyFlow's pure domain rules.

These tests cover the parts that decide what a learner has actually mastered, which
is where the product's main promise lives: the system must not conclude that a
learner cannot do something just because they got one question wrong.
"""

from __future__ import annotations

from app.memory_document import (
    KIND_KNOWLEDGE_STATE,
    KIND_LEARNING_RECORD,
    MemoryDocument,
    build_key,
)
from app.services.ai_service import (
    LearningInsights,
    _decode_json_array,
    _decode_json_object,
)
from app.services.learning_service import LearningService, TopicStats


# --- memory envelope -------------------------------------------------------


def test_memory_document_round_trip() -> None:
    """A document survives serialisation and parsing unchanged."""
    document = MemoryDocument(
        kind=KIND_KNOWLEDGE_STATE,
        key=build_key(KIND_KNOWLEDGE_STATE, "BCNF 分解"),
        fields={"topic": "BCNF 分解", "mastery": "0.5129"},
        narrative="累计练习 2 次，答对 1 次。",
    )
    parsed = MemoryDocument.from_text(document.to_text())

    assert parsed.kind == KIND_KNOWLEDGE_STATE
    assert parsed.key == "knowledge_state:BCNF 分解"
    assert parsed.fields["topic"] == "BCNF 分解"
    assert parsed.fields["mastery"] == "0.5129"
    assert parsed.narrative == "累计练习 2 次，答对 1 次。"


def test_memory_document_parses_legacy_plain_text() -> None:
    """A payload without the envelope still parses, keeping the text as narrative."""
    parsed = MemoryDocument.from_text("一段没有结构的旧记忆", default_kind="legacy")

    assert parsed.kind == "legacy"
    assert parsed.narrative == "一段没有结构的旧记忆"


# --- mastery rules ---------------------------------------------------------


def record(
    topic: str,
    *,
    correct: int,
    wrong: int,
    rating: int | None = None,
    at: str = "2026-01-01T00:00:00+00:00",
) -> MemoryDocument:
    """Build one learning-record document for a test."""
    fields = {
        "topic": topic,
        "correct_count": str(correct),
        "wrong_count": str(wrong),
        "practiced_at": at,
    }
    if rating is not None:
        fields["self_rating"] = str(rating)
    return MemoryDocument(
        kind=KIND_LEARNING_RECORD,
        key=build_key(KIND_LEARNING_RECORD, at),
        fields=fields,
    )


def test_single_wrong_answer_is_not_a_high_severity_weakness() -> None:
    """One wrong answer must never condemn a topic; it stays 'medium' at most."""
    service = LearningService.__new__(LearningService)  # no I/O needed for domain rules
    stats = service.compute_topic_stats([record("BCNF 分解", correct=0, wrong=1, rating=2)])

    topic_stats = stats["BCNF 分解"]
    assert topic_stats.attempts == 1
    assert topic_stats.wrong == 1
    assert service._severity(topic_stats) == "medium"

    reason = service._weakness_reason(topic_stats)
    assert "只有 1 次练习记录" in reason


def test_repeated_errors_raise_severity_to_high() -> None:
    """Repeated evidence is what upgrades a topic to 'high'."""
    service = LearningService.__new__(LearningService)
    stats = service.compute_topic_stats(
        [
            record("BCNF 分解", correct=0, wrong=1, rating=2, at="2026-01-01T00:00:00+00:00"),
            record("BCNF 分解", correct=0, wrong=1, rating=2, at="2026-01-02T00:00:00+00:00"),
            record("BCNF 分解", correct=0, wrong=1, rating=2, at="2026-01-03T00:00:00+00:00"),
        ]
    )

    topic_stats = stats["BCNF 分解"]
    assert topic_stats.attempts == 3
    assert topic_stats.wrong == 3
    assert service._severity(topic_stats) == "high"


def test_mastery_rises_when_later_practice_succeeds() -> None:
    """A correct answer after a wrong one lifts the mastery estimate."""
    service = LearningService.__new__(LearningService)
    stats = service.compute_topic_stats(
        [
            record("BCNF 分解", correct=0, wrong=1, rating=2, at="2026-01-01T00:00:00+00:00"),
            record("BCNF 分解", correct=1, wrong=0, rating=4, at="2026-01-02T00:00:00+00:00"),
        ]
    )

    topic_stats = stats["BCNF 分解"]
    assert topic_stats.attempts == 2
    assert topic_stats.correct == 1
    assert topic_stats.wrong == 1
    assert 0.4 < topic_stats.mastery < 0.7
    assert "有提升" in topic_stats.trend


def test_mastery_is_bounded_between_zero_and_one() -> None:
    """Perfect practice never reports more than full mastery."""
    service = LearningService.__new__(LearningService)
    stats = service.compute_topic_stats(
        [record("主题", correct=10, wrong=0, rating=5, at=f"2026-01-0{i}T00:00:00+00:00") for i in range(1, 4)]
    )
    assert 0.0 <= stats["主题"].mastery <= 1.0

    stats = service.compute_topic_stats(
        [record("主题", correct=0, wrong=10, rating=1, at=f"2026-01-0{i}T00:00:00+00:00") for i in range(1, 4)]
    )
    assert 0.0 <= stats["主题"].mastery <= 1.0


def test_recent_practice_weighs_more_than_old_practice() -> None:
    """Recency decay lets a recent correct answer outweigh an old wrong one."""
    service = LearningService.__new__(LearningService)
    stats = service.compute_topic_stats(
        [
            record("主题", correct=0, wrong=1, rating=2, at="2026-01-01T00:00:00+00:00"),
            record("主题", correct=3, wrong=0, rating=4, at="2026-02-01T00:00:00+00:00"),
        ]
    )
    # The recent correct answer must dominate the older mistake.
    assert stats["主题"].mastery > 0.5


def test_topic_without_practice_has_no_severity() -> None:
    """A topic with no practice evidence is never reported as a weakness."""
    service = LearningService.__new__(LearningService)
    stats = service.compute_topic_stats([])
    assert stats == {}
    assert service._severity(TopicStats(topic="主题")) is None


def test_single_correct_answer_is_not_a_weakness() -> None:
    """One correct answer with a good self-rating is not evidence of a weakness."""
    service = LearningService.__new__(LearningService)
    stats = service.compute_topic_stats(
        [record("函数依赖", correct=1, wrong=0, rating=4)]
    )

    topic_stats = stats["函数依赖"]
    assert topic_stats.attempts == 1
    assert topic_stats.wrong == 0
    assert service._severity(topic_stats) is None


def test_weaknesses_are_ordered_by_severity_then_mastery() -> None:
    """The most serious weakness comes first, so the review plan starts there."""
    service = LearningService.__new__(LearningService)
    high = TopicStats(topic="严重", mastery=0.3, attempts=4, wrong=4, self_rating=1.0)
    low = TopicStats(topic="轻微", mastery=0.7, attempts=2, wrong=1, self_rating=3.0)
    medium = TopicStats(topic="一般", mastery=0.5, attempts=2, wrong=1, self_rating=2.0)

    assert service._severity(high) == "high"
    assert service._severity(medium) == "medium"
    assert service._severity(low) == "low"


# --- AI payload decoding ---------------------------------------------------


def test_decode_json_object_strips_code_fence() -> None:
    """A fenced JSON object is still decoded."""
    payload = _decode_json_object('```json\n{"course": "数据库系统"}\n```')
    assert payload == {"course": "数据库系统"}


def test_decode_json_object_returns_none_for_prose() -> None:
    """Prose without JSON decodes to None instead of raising."""
    assert _decode_json_object("这不是 JSON") is None


def test_decode_json_array_extracts_array_from_prose() -> None:
    """An array embedded in surrounding text is still decoded."""
    payload = _decode_json_array('好的：[{"question": "什么是 BCNF？"}] 以上。')
    assert payload == [{"question": "什么是 BCNF？"}]


def test_learning_insights_from_payload() -> None:
    """A model payload maps onto the structured insights object."""
    insights = LearningInsights.from_payload(
        {
            "is_learning_activity": True,
            "course": "数据库系统",
            "topics": [
                {"topic": "BCNF 分解", "status": "weak", "note": "经常出错"},
                {"topic": "候选码", "status": "familiar"},
            ],
            "practice_count": 5,
            "practice_correct": 4,
            "practice_wrong": 1,
            "self_rating": 4,
        }
    )

    assert insights.course == "数据库系统"
    assert [topic.topic for topic in insights.topics] == ["BCNF 分解", "候选码"]
    assert insights.topics[0].status == "weak"
    assert insights.practice_total == 5
    assert insights.self_rating == 4
