"""StudyFlow's learning domain logic.

This module owns the rules that decide what a learner has actually mastered, what
counts as a weakness, and what should be reviewed next. It reads and writes only
through :class:`~app.powercontext.repository.MemoryRepository`, so every rule below is
exercised against real PowerContext state.

Design rule: **learning records are authoritative, knowledge state is a derived
projection.** Mastery is always recomputed from the stored records; the projection
stored in PowerContext keeps the identity of the records it was derived from, so a
rebuild never silently rewrites history.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from app.config import Settings
from app.memory_document import (
    KIND_KNOWLEDGE_STATE,
    KIND_LEARNING_RECORD,
    KIND_PREFERENCE,
    KIND_PROFILE,
    KIND_REVIEW_PLAN,
    KIND_WEAKNESS,
    MemoryDocument,
    build_key,
    field_bool,
    field_number,
    field_text,
)
from app.powercontext.repository import MemoryRepository, UpsertResult
from app.schemas import (
    DashboardSummary,
    KnowledgeState,
    LearnerProfile,
    LearningRecordInput,
    LearningRecordView,
    MemoryCenter,
    MemoryEntryView,
    ProfileUpdate,
    RecordCreated,
    ReviewPlan,
    ReviewPlanItem,
    TutorRequest,
    TutorResponse,
    WeaknessItem,
    WriteResult,
)
from app.services.ai_service import AiService, LearningInsights

LOGGER = logging.getLogger("studyflow.learning")

# Smoothing constants for the mastery estimate.
PRIOR_MASTERY = 0.5
PRIOR_WEIGHT = 2.0
RECENCY_DECAY = 0.9
PRACTICE_WEIGHT = 0.7
SELF_RATING_WEIGHT = 0.3

SELF_REPORTED_MASTERY = {
    "weak": 0.25,
    "unknown": 0.3,
    "familiar": 0.6,
    "mastered": 0.85,
}

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def utc_now() -> str:
    """Return the current UTC timestamp in ISO-8601 form."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(slots=True)
class TopicStats:
    """Derived mastery state for one knowledge point."""

    topic: str
    course: str = ""
    mastery: float = 0.0
    attempts: int = 0
    correct: int = 0
    wrong: int = 0
    self_rating: float | None = None
    last_practiced_at: str = ""
    record_ids: list[str] = field(default_factory=list)
    source: str = "practice"
    note: str = ""
    trend: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Serialise the stats for storage inside a memory document."""
        return {
            "topic": self.topic,
            "course": self.course,
            "mastery": f"{self.mastery:.4f}",
            "attempts": str(self.attempts),
            "correct": str(self.correct),
            "wrong": str(self.wrong),
            "self_rating": "" if self.self_rating is None else f"{self.self_rating:.2f}",
            "last_practiced_at": self.last_practiced_at,
            "record_ids": ",".join(self.record_ids),
            "source": self.source,
            "note": self.note,
        }


@dataclass(slots=True)
class MemorySnapshot:
    """All memory documents currently stored for one learner."""

    scope_id: str = ""
    scope_title: str = ""
    documents: list[MemoryDocument] = field(default_factory=list)
    entries: list[Any] = field(default_factory=list)

    def by_kind(self, kind: str) -> list[MemoryDocument]:
        """All documents of one kind."""
        return [document for document in self.documents if document.kind == kind]

    @property
    def profile(self) -> MemoryDocument | None:
        """The learner's profile document, if one exists."""
        profiles = self.by_kind(KIND_PROFILE)
        return profiles[0] if profiles else None

    @property
    def records(self) -> list[MemoryDocument]:
        """The authoritative learning-record event log, oldest first."""
        records = self.by_kind(KIND_LEARNING_RECORD)
        return sorted(records, key=lambda document: field_text(document, "practiced_at"))


class LearningService:
    """Application service tying PowerContext memory to the learning domain."""

    def __init__(
        self,
        repository: MemoryRepository,
        ai_service: AiService,
        settings: Settings,
    ) -> None:
        self._repository = repository
        self._ai = ai_service
        self._settings = settings

    # --- infrastructure --------------------------------------------------

    @property
    def repository(self) -> MemoryRepository:
        """The PowerContext repository."""
        return self._repository

    @property
    def ai(self) -> AiService:
        """The AI model service."""
        return self._ai

    async def ensure_scope(self, user_id: str, nickname: str = "") -> tuple[str, str, bool]:
        """Resolve or create the learner's Scope and return ``(scope_id, title, created)``."""
        scope, created = await self._repository.ensure_scope(
            user_id=user_id,
            nickname=nickname,
            scope_kind=self._settings.powercontext_scope_kind,
            integration=self._settings.powercontext_integration,
            binding_kind=self._settings.powercontext_binding_kind,
        )
        return scope.scope_id, scope.title, created

    async def load_snapshot(self, user_id: str, nickname: str = "") -> MemorySnapshot:
        """Load every memory document stored for one learner."""
        scope_id, scope_title, _ = await self.ensure_scope(user_id, nickname)
        entries = await self._repository.read_entries(scope_id)
        documents = [
            MemoryDocument.from_text(entry.text, default_kind=entry.kind) for entry in entries
        ]
        return MemorySnapshot(scope_id=scope_id, scope_title=scope_title, documents=documents, entries=entries)

    # --- profile ---------------------------------------------------------

    async def get_profile(self, user_id: str) -> LearnerProfile:
        """Read the learner's profile out of PowerContext."""
        snapshot = await self.load_snapshot(user_id)
        document = snapshot.profile
        if document is None:
            return LearnerProfile(user_id=user_id, scope_id=snapshot.scope_id, source="empty")
        return LearnerProfile(
            user_id=user_id,
            nickname=field_text(document, "nickname"),
            course=field_text(document, "course"),
            goal=field_text(document, "goal"),
            preferred_style=field_text(document, "preferred_style"),
            focus_points=_split_list(field_text(document, "focus_points")),
            created_at=field_text(document, "created_at"),
            updated_at=field_text(document, "updated_at"),
            scope_id=snapshot.scope_id,
            source="powercontext",
        )

    async def update_profile(
        self,
        user_id: str,
        update: ProfileUpdate,
    ) -> tuple[LearnerProfile, list[WriteResult]]:
        """Update the learner's profile and persist it to PowerContext."""
        snapshot = await self.load_snapshot(user_id)
        existing = snapshot.profile
        document = existing or MemoryDocument(
            kind=KIND_PROFILE,
            key=build_key(KIND_PROFILE),
            fields={"created_at": utc_now()},
        )
        fields = dict(document.fields)
        if update.nickname is not None:
            fields["nickname"] = update.nickname
        if update.course is not None:
            fields["course"] = update.course
        if update.goal is not None:
            fields["goal"] = update.goal
        if update.preferred_style is not None:
            fields["preferred_style"] = update.preferred_style
        if update.focus_points is not None:
            fields["focus_points"] = ",".join(update.focus_points)
        fields.setdefault("nickname", user_id)
        fields["updated_at"] = utc_now()
        document.fields = fields
        if not document.narrative:
            document.narrative = _profile_narrative(fields)
        result = await self._repository.upsert_document(snapshot.scope_id, document)
        profile = await self.get_profile(user_id)
        return profile, [_to_write_result(result)]

    # --- learning records ------------------------------------------------

    async def list_records(self, user_id: str) -> list[LearningRecordView]:
        """Read the authoritative learning-record log."""
        snapshot = await self.load_snapshot(user_id)
        views: list[LearningRecordView] = []
        for document in snapshot.records:
            views.append(
                LearningRecordView(
                    record_id=field_text(document, "record_id"),
                    course=field_text(document, "course"),
                    topic=field_text(document, "topic"),
                    notes=field_text(document, "notes"),
                    question=field_text(document, "question"),
                    user_answer=field_text(document, "user_answer"),
                    correct_answer=field_text(document, "correct_answer"),
                    is_correct=_optional_bool(field_text(document, "is_correct")),
                    self_rating=_optional_int(field_text(document, "self_rating")),
                    problems=field_text(document, "problems"),
                    practiced_at=field_text(document, "practiced_at"),
                    artifact_id=field_text(document, "artifact_id"),
                    revision=int(field_number(document, "revision", 0)),
                )
            )
        views.sort(key=lambda item: item.practiced_at, reverse=True)
        return views

    async def create_record(
        self,
        user_id: str,
        payload: LearningRecordInput,
    ) -> RecordCreated:
        """Store a learning activity and refresh the derived mastery projection."""
        snapshot = await self.load_snapshot(user_id)
        profile = await self.get_profile(user_id)
        course = payload.course or profile.course
        practiced_at = payload.practiced_at or utc_now()
        record_id = f"rec_{uuid.uuid4().hex[:12]}"
        is_correct = "" if payload.is_correct is None else str(payload.is_correct).lower()
        correct_count = 1 if payload.is_correct is True else 0
        wrong_count = 1 if payload.is_correct is False else 0

        document = MemoryDocument(
            kind=KIND_LEARNING_RECORD,
            key=build_key(KIND_LEARNING_RECORD, f"{practiced_at}_{record_id}"),
            fields={
                "record_id": record_id,
                "course": course,
                "topic": payload.topic,
                "notes": payload.notes,
                "question": payload.question,
                "user_answer": payload.user_answer,
                "correct_answer": payload.correct_answer,
                "is_correct": is_correct,
                "correct_count": str(correct_count),
                "wrong_count": str(wrong_count),
                "self_rating": "" if payload.self_rating is None else str(payload.self_rating),
                "problems": payload.problems,
                "practiced_at": practiced_at,
            },
            narrative=(
                f"在「{payload.topic}」上完成了一次学习活动。"
                + (
                    f"本次练习结果正确。"
                    if payload.is_correct is True
                    else ("本次练习结果错误。" if payload.is_correct is False else "")
                )
                + (f"自评掌握程度 {payload.self_rating}/5。" if payload.self_rating else "")
            ),
        )

        writes: list[WriteResult] = []
        record_result = await self._repository.upsert_document(
            snapshot.scope_id,
            document,
            reason="learning-record",
        )
        writes.append(_to_write_result(record_result))
        document.fields["artifact_id"] = record_result.entry.artifact_id
        document.fields["revision"] = str(record_result.entry.revision)
        await self._repository.upsert_document(
            snapshot.scope_id,
            document,
            reason="learning-record-identity",
        )

        state = await self._rebuild_projection(snapshot.scope_id, user_id)
        knowledge_state = state.get(payload.topic)
        return RecordCreated(
            record=LearningRecordView(
                record_id=record_id,
                course=course,
                topic=payload.topic,
                notes=payload.notes,
                question=payload.question,
                user_answer=payload.user_answer,
                correct_answer=payload.correct_answer,
                is_correct=payload.is_correct,
                self_rating=payload.self_rating,
                problems=payload.problems,
                practiced_at=practiced_at,
                artifact_id=record_result.entry.artifact_id,
                revision=record_result.entry.revision,
            ),
            writes=writes,
            knowledge_state=_to_knowledge_state(payload.topic, knowledge_state),
            notice="学习记录已保存到 PowerContext，并已更新该知识点的掌握度。",
        )

    async def _rebuild_projection(
        self,
        scope_id: str,
        user_id: str,
    ) -> dict[str, TopicStats]:
        """Recompute the mastery projection from the authoritative records.

        Only the topics whose evidence changed are rewritten; every other projection is
        left untouched so its identity and history are preserved.
        """
        snapshot = await self.load_snapshot(user_id)
        stats = self.compute_topic_stats(snapshot.records)
        for topic, topic_stats in stats.items():
            document = MemoryDocument(
                kind=KIND_KNOWLEDGE_STATE,
                key=build_key(KIND_KNOWLEDGE_STATE, topic),
                fields=topic_stats.as_dict(),
                narrative=_knowledge_narrative(topic_stats),
            )
            await self._repository.upsert_document(scope_id, document, reason="mastery-projection")
        return stats

    # --- mastery rules ---------------------------------------------------

    def compute_topic_stats(self, records: Iterable[MemoryDocument]) -> dict[str, TopicStats]:
        """Compute mastery per knowledge point from the authoritative record log."""
        grouped: dict[str, list[MemoryDocument]] = {}
        for document in records:
            topic = field_text(document, "topic")
            if not topic:
                continue
            grouped.setdefault(topic, []).append(document)

        stats: dict[str, TopicStats] = {}
        for topic, documents in grouped.items():
            documents.sort(key=lambda document: field_text(document, "practiced_at"))
            attempts = 0
            correct = 0
            wrong = 0
            weighted_correct = 0.0
            weighted_total = 0.0
            ratings: list[tuple[float, float]] = []
            record_ids: list[str] = []
            course = ""
            last_practiced_at = ""

            total = len(documents)
            for index, document in enumerate(documents):
                weight = RECENCY_DECAY ** (total - 1 - index)
                record_correct, record_wrong = _record_outcome(document)
                weighted_correct += weight * record_correct
                weighted_total += weight * (record_correct + record_wrong)
                attempts += 1
                correct += record_correct
                wrong += record_wrong
                record_ids.append(field_text(document, "record_id"))
                course = course or field_text(document, "course")
                last_practiced_at = field_text(document, "practiced_at") or last_practiced_at
                rating = field_number(document, "self_rating", 0.0)
                if rating:
                    ratings.append((rating, weight))

            practice_mastery = (
                weighted_correct + PRIOR_WEIGHT * PRIOR_MASTERY
            ) / (weighted_total + PRIOR_WEIGHT)
            self_rating = None
            if ratings:
                rating_weight = sum(weight for _, weight in ratings)
                self_rating = sum(rating * weight for rating, weight in ratings) / rating_weight
                self_rating = max(1.0, min(5.0, self_rating))

            mastery = self._blend_mastery(practice_mastery, self_rating)
            stats[topic] = TopicStats(
                topic=topic,
                course=course,
                mastery=mastery,
                attempts=attempts,
                correct=correct,
                wrong=wrong,
                self_rating=self_rating,
                last_practiced_at=last_practiced_at,
                record_ids=record_ids,
                source="practice",
                trend=_trend(documents),
            )
        return stats

    @staticmethod
    def _blend_mastery(practice_mastery: float, self_rating: float | None) -> float:
        """Blend practice evidence with the learner's own rating."""
        if self_rating is None:
            return max(0.0, min(1.0, practice_mastery))
        self_mastery = (self_rating - 1.0) / 4.0
        blended = PRACTICE_WEIGHT * practice_mastery + SELF_RATING_WEIGHT * self_mastery
        return max(0.0, min(1.0, blended))

    # --- weaknesses ------------------------------------------------------

    async def get_knowledge_states(self, user_id: str) -> list[KnowledgeState]:
        """Read the derived mastery states."""
        snapshot = await self.load_snapshot(user_id)
        stats = self.compute_topic_stats(snapshot.records)
        states = [self._to_state(topic, topic_stats) for topic, topic_stats in stats.items()]
        states.sort(key=lambda state: state.mastery)
        return states

    def _to_state(self, topic: str, stats: TopicStats) -> KnowledgeState:
        """Convert derived stats into the API-facing knowledge state."""
        return KnowledgeState(
            topic=topic,
            course=stats.course,
            mastery=round(stats.mastery, 4),
            attempts=stats.attempts,
            correct=stats.correct,
            wrong=stats.wrong,
            self_rating=stats.self_rating,
            last_practiced_at=stats.last_practiced_at,
            evidence=_evidence_text(stats),
            updated_at=utc_now(),
        )

    async def get_weaknesses(self, user_id: str) -> list[WeaknessItem]:
        """Identify the knowledge points that need attention, with their evidence."""
        snapshot = await self.load_snapshot(user_id)
        stats = self.compute_topic_stats(snapshot.records)
        return self._build_weaknesses(snapshot, stats)

    def _build_weaknesses(
        self,
        snapshot: MemorySnapshot,
        stats: dict[str, TopicStats],
    ) -> list[WeaknessItem]:
        """Derive the weakness list from practice evidence and self-reported focus."""
        items: list[WeaknessItem] = []
        for topic, topic_stats in stats.items():
            if topic_stats.attempts == 0:
                continue
            severity = self._severity(topic_stats)
            if severity is None:
                continue
            reason = self._weakness_reason(topic_stats)
            items.append(
                WeaknessItem(
                    topic=topic,
                    course=topic_stats.course,
                    mastery=round(topic_stats.mastery, 4),
                    attempts=topic_stats.attempts,
                    wrong=topic_stats.wrong,
                    severity=severity,
                    evidence=_evidence_text(topic_stats),
                    reason=reason,
                    last_practiced_at=topic_stats.last_practiced_at,
                    recommendation=_recommendation(topic_stats),
                )
            )
        for document in snapshot.by_kind(KIND_WEAKNESS):
            topic = field_text(document, "topic")
            if not topic or topic in stats:
                continue
            items.append(
                WeaknessItem(
                    topic=topic,
                    course=field_text(document, "course"),
                    mastery=round(field_number(document, "mastery", 0.25), 4),
                    attempts=0,
                    wrong=0,
                    severity="medium",
                    evidence="学习者自述",
                    reason=field_text(document, "reason") or "学习者自述在该知识点上反复出错。",
                    last_practiced_at="",
                    recommendation=_recommendation(None),
                )
            )
        items.sort(key=lambda item: (SEVERITY_ORDER[item.severity], item.mastery))
        return items

    @staticmethod
    def _severity(stats: TopicStats) -> str | None:
        """Classify how much attention a topic needs.

        A topic needs *negative* evidence to be called a weakness. A single wrong
        answer never condemns a topic — it stays at most ``medium`` — and a topic the
        learner has answered correctly with a good self-rating is not a weakness at
        all, however few attempts there are.
        """
        if stats.attempts == 0:
            return None

        has_negative_evidence = stats.wrong > 0 or (
            stats.self_rating is not None and stats.self_rating <= 2.0
        )
        if not has_negative_evidence:
            # No error and a decent self-rating: only worth flagging when repeated
            # practice still leaves the mastery estimate low.
            if stats.attempts >= 2 and stats.mastery < 0.55:
                return "medium"
            return None

        if stats.attempts >= 2 and stats.mastery < 0.5:
            return "high"
        if stats.wrong >= 3 and stats.mastery < 0.55:
            return "high"
        if stats.mastery < 0.62:
            return "medium"
        if stats.mastery < 0.78:
            return "low"
        return None

    def _weakness_reason(self, stats: TopicStats) -> str:
        """Explain why a topic is weak, citing only stored evidence."""
        accuracy = (stats.correct / stats.attempts) if stats.attempts else 0.0
        parts = [
            f"在「{stats.topic}」上累计练习 {stats.attempts} 次、答错 {stats.wrong} 次，"
            f"答题正确率约 {accuracy * 100:.0f}%"
        ]
        if stats.self_rating is not None:
            parts.append(f"自评掌握程度约 {stats.self_rating:.1f}/5")
        parts.append(f"综合掌握度约 {stats.mastery * 100:.0f}%")
        trend = stats.trend
        if trend:
            parts.append(trend)
        if stats.attempts == 1:
            parts.append("目前只有 1 次练习记录，证据还比较少，先做一组针对性练习再判断")
        return "，".join(parts) + "。"

    # --- review plan -----------------------------------------------------

    async def build_review_plan(
        self,
        user_id: str,
        *,
        generate_questions: bool = True,
    ) -> ReviewPlan:
        """Build today's review plan from the learner's stored memory."""
        snapshot = await self.load_snapshot(user_id)
        stats = self.compute_topic_stats(snapshot.records)
        weaknesses = self._build_weaknesses(snapshot, stats)
        items: list[ReviewPlanItem] = []
        for priority, weakness in enumerate(weaknesses[:5], start=1):
            items.append(
                ReviewPlanItem(
                    topic=weakness.topic,
                    priority=priority,
                    reason=weakness.reason,
                    action=(
                        f"先复习「{weakness.topic}」的核心概念与典型题型，再做 {3 + priority} 道针对性练习"
                    ),
                    mastery=weakness.mastery,
                    severity=weakness.severity,
                    evidence=weakness.evidence,
                )
            )

        questions: list[dict[str, Any]] = []
        if generate_questions and items:
            questions = await self._generate_questions(user_id, snapshot, items[0].topic)

        summary = self._plan_summary(items, stats)
        plan_document = MemoryDocument(
            kind=KIND_REVIEW_PLAN,
            key=build_key(KIND_REVIEW_PLAN, utc_now()[:10]),
            fields={
                "user_id": user_id,
                "date": utc_now()[:10],
                "topics": ",".join(item.topic for item in items),
                "generated_at": utc_now(),
            },
            narrative=summary,
        )
        await self._repository.upsert_document(snapshot.scope_id, plan_document, reason="review-plan")

        return ReviewPlan(
            user_id=user_id,
            generated_at=utc_now(),
            items=items,
            summary=summary,
            practice_questions=questions,
        )

    def _plan_summary(self, items: list[ReviewPlanItem], stats: dict[str, TopicStats]) -> str:
        """Summarise the plan in plain language."""
        if not items:
            if stats:
                return "目前没有明显的薄弱知识点，建议保持当前学习节奏，继续巩固已掌握的知识点。"
            return "还没有足够的学习记录，先记录几次练习，系统就能给出针对性的复习建议。"
        top = items[0]
        return (
            f"今天建议优先复习 {len(items)} 个知识点，其中「{top.topic}」最需要关注：{top.reason}"
        )

    async def _generate_questions(
        self,
        user_id: str,
        snapshot: MemorySnapshot,
        topic: str,
    ) -> list[dict[str, Any]]:
        """Generate practice questions for the top review topic."""
        if not self._ai.is_configured:
            return []
        context = self.build_memory_context(snapshot)
        try:
            questions = await self._ai.generate_practice(topic=topic, count=5, memory_context=context)
        except Exception as error:  # noqa: BLE001 - a provider failure must not break the plan
            LOGGER.warning("practice generation failed topic=%s error=%s", topic, error)
            return []
        return [
            {"question": item.question, "answer": item.answer, "hint": item.hint}
            for item in questions
        ]

    # --- memory context --------------------------------------------------

    def build_memory_context(self, snapshot: MemorySnapshot) -> str:
        """Render the learner's recalled memory as text for the AI tutor."""
        lines: list[str] = []
        profile = snapshot.profile
        if profile is not None:
            lines.append("[学习档案]")
            for name, label in (
                ("nickname", "昵称"),
                ("course", "当前课程"),
                ("goal", "学习目标"),
                ("preferred_style", "偏好的讲解方式"),
                ("focus_points", "重点关注的知识点"),
            ):
                value = field_text(profile, name)
                if value:
                    lines.append(f"- {label}：{value}")

        stats = self.compute_topic_stats(snapshot.records)
        if stats:
            lines.append("[知识点掌握情况]")
            for topic, topic_stats in sorted(stats.items(), key=lambda pair: pair[1].mastery):
                lines.append(
                    f"- {topic}：掌握度 {topic_stats.mastery * 100:.0f}%，"
                    f"练习 {topic_stats.attempts} 次，答对 {topic_stats.correct} 次，答错 {topic_stats.wrong} 次"
                )

        records = snapshot.records
        if records:
            lines.append("[最近的学习记录]")
            for document in records[-5:]:
                lines.append(
                    f"- {field_text(document, 'practiced_at')} 「{field_text(document, 'topic')}」"
                    f" {field_text(document, 'narrative_summary') or field_text(document, 'notes')}"
                )

        for document in snapshot.by_kind(KIND_WEAKNESS):
            topic = field_text(document, "topic")
            if topic:
                lines.append(f"[薄弱知识点] {topic}：{field_text(document, 'reason')}")

        return "\n".join(lines)

    # --- AI tutor --------------------------------------------------------

    async def tutor(self, request: TutorRequest) -> TutorResponse:
        """Answer a learner question using recalled long-term memory."""
        snapshot = await self.load_snapshot(request.user_id)
        context = self.build_memory_context(snapshot)
        memory_hits: list[dict[str, Any]] = []
        try:
            hits = await self._repository.search_documents(
                snapshot.scope_id,
                request.message,
                limit=8,
            )
            memory_hits = [
                {
                    "kind": document.kind,
                    "key": document.key,
                    "score": round(score, 4),
                    "narrative": document.narrative,
                }
                for document, score in hits
            ]
        except Exception as error:  # noqa: BLE001 - recall failure must not break the tutor
            LOGGER.warning("memory search failed user=%s error=%s", request.user_id, error)

        used_memory = bool(context.strip()) and bool(memory_hits)
        if not self._ai.is_configured:
            return TutorResponse(
                answer=(
                    "AI 模型服务尚未配置，无法生成回答。"
                    "你可以在「记忆中心」查看已经保存到 PowerContext 的学习记忆，"
                    "或配置 STUDYFLOW_AI_API_BASE / STUDYFLOW_AI_API_KEY / STUDYFLOW_AI_MODEL 后重试。"
                ),
                user_id=request.user_id,
                scope_id=snapshot.scope_id,
                used_memory=False,
                memory_hits=memory_hits,
                memory_context=context,
                saved=False,
                ai_configured=False,
                notice="AI 未配置",
            )
        answer = await self._ai.answer(request.message, memory_context=context)

        saved_actions: list[dict[str, Any]] = []
        if request.save_to_memory:
            saved_actions = await self._apply_insights(request.user_id, snapshot, request.message, answer)

        return TutorResponse(
            answer=answer,
            user_id=request.user_id,
            scope_id=snapshot.scope_id,
            used_memory=used_memory,
            memory_hits=memory_hits,
            memory_context=context,
            saved=bool(saved_actions),
            saved_actions=saved_actions,
            ai_configured=True,
            notice="已写入 PowerContext" if saved_actions else "",
        )

    async def _apply_insights(
        self,
        user_id: str,
        snapshot: MemorySnapshot,
        message: str,
        answer: str,
    ) -> list[dict[str, Any]]:
        """Turn a learner turn into updated long-term memory."""
        try:
            insights = await self._ai.extract_insights(
                message, context=self.build_memory_context(snapshot)
            )
        except Exception as error:  # noqa: BLE001 - a provider failure must not lose the turn
            LOGGER.warning("insight extraction failed user=%s error=%s", user_id, error)
            return []
        actions: list[dict[str, Any]] = []
        writes: list[UpsertResult] = []

        profile_document = snapshot.profile or MemoryDocument(
            kind=KIND_PROFILE,
            key=build_key(KIND_PROFILE),
            fields={"created_at": utc_now(), "nickname": user_id},
        )
        profile_fields = dict(profile_document.fields)
        changed = False
        if insights.course and insights.course != profile_fields.get("course"):
            profile_fields["course"] = insights.course
            changed = True
        if insights.goal and insights.goal != profile_fields.get("goal"):
            profile_fields["goal"] = insights.goal
            changed = True
        if insights.preferred_style:
            profile_fields["preferred_style"] = insights.preferred_style
            changed = True
        weak_topics = [topic.topic for topic in insights.topics if topic.status == "weak"]
        if weak_topics:
            existing_focus = _split_list(profile_fields.get("focus_points", ""))
            merged = existing_focus + [topic for topic in weak_topics if topic not in existing_focus]
            profile_fields["focus_points"] = ",".join(merged)
            changed = True
        if changed:
            profile_fields["updated_at"] = utc_now()
            profile_document.fields = profile_fields
            profile_document.narrative = _profile_narrative(profile_fields)
            writes.append(
                await self._repository.upsert_document(
                    snapshot.scope_id, profile_document, reason="profile-update"
                )
            )

        for topic_insight in insights.topics:
            topic = topic_insight.topic
            knowledge_document = MemoryDocument(
                kind=KIND_KNOWLEDGE_STATE,
                key=build_key(KIND_KNOWLEDGE_STATE, topic),
                fields={
                    "topic": topic,
                    "course": insights.course or profile_fields.get("course", ""),
                    "mastery": f"{SELF_REPORTED_MASTERY.get(topic_insight.status, 0.3):.4f}",
                    "attempts": "0",
                    "correct": "0",
                    "wrong": "0",
                    "self_rating": "",
                    "last_practiced_at": "",
                    "record_ids": "",
                    "source": "self_report",
                    "note": topic_insight.note,
                },
                narrative=topic_insight.note or f"学习者自述在「{topic}」上的掌握情况：{topic_insight.status}。",
            )
            writes.append(
                await self._repository.upsert_document(
                    snapshot.scope_id, knowledge_document, reason="self-reported-state"
                )
            )
            if topic_insight.status == "weak":
                weakness_document = MemoryDocument(
                    kind=KIND_WEAKNESS,
                    key=build_key(KIND_WEAKNESS, topic),
                    fields={
                        "topic": topic,
                        "course": insights.course or profile_fields.get("course", ""),
                        "mastery": f"{SELF_REPORTED_MASTERY['weak']:.4f}",
                        "reason": topic_insight.note
                        or f"学习者自述在「{topic}」上反复出错，需要重点练习。",
                    },
                    narrative=topic_insight.note,
                )
                writes.append(
                    await self._repository.upsert_document(
                        snapshot.scope_id, weakness_document, reason="self-reported-weakness"
                    )
                )

        if insights.is_learning_activity and insights.practice_total:
            self._record_practice_from_insights(snapshot.scope_id, insights, writes)

        for write in writes:
            actions.append(
                {
                    "action": write.action,
                    "kind": write.document.kind,
                    "key": write.document.key,
                    "artifact_id": write.entry.artifact_id,
                    "revision": write.entry.revision,
                }
            )
        LOGGER.info("tutor memory writes user=%s count=%s", user_id, len(actions))
        return actions

    async def _record_practice_from_insights(
        self,
        scope_id: str,
        insights: LearningInsights,
        writes: list[UpsertResult],
    ) -> None:
        """Persist a practice activity that the learner described in free text."""
        practiced_at = utc_now()
        record_id = f"rec_{uuid.uuid4().hex[:12]}"
        topic = insights.topics[0].topic if insights.topics else "综合练习"
        correct = insights.practice_correct or 0
        wrong = insights.practice_wrong or 0
        document = MemoryDocument(
            kind=KIND_LEARNING_RECORD,
            key=build_key(KIND_LEARNING_RECORD, f"{practiced_at}_{record_id}"),
            fields={
                "record_id": record_id,
                "course": insights.course,
                "topic": topic,
                "notes": insights.notes,
                "question": "",
                "user_answer": "",
                "correct_answer": "",
                "is_correct": "",
                "correct_count": str(correct),
                "wrong_count": str(wrong),
                "self_rating": "" if insights.self_rating is None else str(insights.self_rating),
                "problems": "",
                "practiced_at": practiced_at,
                "source": "conversation",
            },
            narrative=(
                f"完成 {correct + wrong} 道练习，答对 {correct} 道，答错 {wrong} 道。"
                + (f"自评掌握程度 {insights.self_rating}/5。" if insights.self_rating else "")
            ),
        )
        writes.append(
            await self._repository.upsert_document(scope_id, document, reason="conversation-record")
        )

    # --- read models -----------------------------------------------------

    async def get_memory_center(self, user_id: str) -> MemoryCenter:
        """Collect everything the Memory Center page shows."""
        snapshot = await self.load_snapshot(user_id)
        profile = await self.get_profile(user_id)
        states = await self.get_knowledge_states(user_id)
        weaknesses = await self.get_weaknesses(user_id)
        records = await self.list_records(user_id)

        entries: list[MemoryEntryView] = []
        last_update = ""
        for entry, document in zip(snapshot.entries, snapshot.documents):
            entries.append(
                MemoryEntryView(
                    kind=entry.kind,
                    key=document.key,
                    text=entry.text,
                    narrative=document.narrative,
                    artifact_id=entry.artifact_id,
                    revision=entry.revision,
                    state=entry.state,
                )
            )
        if snapshot.entries:
            last_update = _last_update(snapshot)

        return MemoryCenter(
            user_id=user_id,
            scope_id=snapshot.scope_id,
            scope_title=snapshot.scope_title,
            profile=profile,
            knowledge_states=states,
            weaknesses=weaknesses,
            recent_records=records[:10],
            entries=entries,
            last_memory_update=last_update,
            entry_count=len(entries),
            powercontext={
                "base_url": self._settings.powercontext_base_url,
                "token_present": bool(self._settings.powercontext_token),
                "scope_kind": self._settings.powercontext_scope_kind,
            },
            read_ok=True,
            write_ok=True,
            notice="记忆来自 PowerContext Server，可通过 Memory Center 核对每条记忆。",
        )

    async def get_dashboard(self, user_id: str) -> DashboardSummary:
        """Collect the Dashboard payload."""
        profile = await self.get_profile(user_id)
        records = await self.list_records(user_id)
        weaknesses = await self.get_weaknesses(user_id)
        plan = await self.build_review_plan(user_id, generate_questions=False)
        snapshot = await self.load_snapshot(user_id)
        progress = [
            {
                "topic": record.topic,
                "practiced_at": record.practiced_at,
                "is_correct": record.is_correct,
                "notes": record.notes,
            }
            for record in records[:5]
        ]
        return DashboardSummary(
            user_id=user_id,
            profile=profile,
            activity_count=len(records),
            knowledge_point_count=len({record.topic for record in records}),
            weaknesses=weaknesses[:5],
            recent_progress=progress,
            today_review=plan.items,
            review_summary=plan.summary,
            last_memory_update=_last_update(snapshot),
            powercontext_ok=True,
            ai_configured=self._ai.is_configured,
            notice="",
        )


# --- helpers --------------------------------------------------------------


def _to_write_result(result: UpsertResult) -> WriteResult:
    """Convert an upsert outcome into the API write record."""
    return WriteResult(
        action=result.action,
        kind=result.document.kind,
        key=result.document.key,
        artifact_id=result.entry.artifact_id,
        revision=result.entry.revision,
    )


def _to_knowledge_state(topic: str, stats: TopicStats | None) -> KnowledgeState | None:
    """Convert derived stats into a knowledge state, or ``None``."""
    if stats is None:
        return None
    return KnowledgeState(
        topic=topic,
        course=stats.course,
        mastery=round(stats.mastery, 4),
        attempts=stats.attempts,
        correct=stats.correct,
        wrong=stats.wrong,
        self_rating=stats.self_rating,
        last_practiced_at=stats.last_practiced_at,
        evidence=_evidence_text(stats),
    )


def _evidence_text(stats: TopicStats) -> str:
    """Describe how much evidence supports the mastery estimate."""
    if stats.attempts == 0:
        return "来自学习者自述，尚无练习记录"
    if stats.attempts == 1:
        return "基于 1 次练习，证据较少"
    return f"基于 {stats.attempts} 次练习"


def _recommendation(stats: TopicStats | None) -> str:
    """Suggest a concrete next action for a weak topic."""
    if stats is None:
        return "先做一组基础题确认当前水平，再决定加强方向。"
    if stats.attempts == 1:
        return "先补一组同类型练习，再根据正确率判断是否需要系统性加强。"
    if stats.mastery < 0.5:
        return "回到概念定义与典型例题，先讲清解题步骤再刷题。"
    if stats.mastery < 0.62:
        return "重点练习容易混淆的题型，做完后复盘错题原因。"
    return "保持练习频率，定期回顾即可。"


def _profile_narrative(fields: dict[str, str]) -> str:
    """Render a readable profile summary."""
    parts: list[str] = []
    if fields.get("course"):
        parts.append(f"当前课程：{fields['course']}")
    if fields.get("goal"):
        parts.append(f"学习目标：{fields['goal']}")
    if fields.get("preferred_style"):
        parts.append(f"偏好的讲解方式：{fields['preferred_style']}")
    focus = _split_list(fields.get("focus_points", ""))
    if focus:
        parts.append(f"重点关注：{'、'.join(focus)}")
    return "；".join(parts) + ("。" if parts else "")


def _knowledge_narrative(stats: TopicStats) -> str:
    """Render a readable mastery summary for a knowledge point."""
    if stats.attempts == 0:
        return f"学习者自述在「{stats.topic}」上的掌握情况尚未通过练习验证。"
    accuracy = (stats.correct / stats.attempts) if stats.attempts else 0.0
    return (
        f"在「{stats.topic}」上累计练习 {stats.attempts} 次，答对 {stats.correct} 次、"
        f"答错 {stats.wrong} 次，正确率约 {accuracy * 100:.0f}%，综合掌握度约 {stats.mastery * 100:.0f}%。"
    )


def _split_list(value: str) -> list[str]:
    """Split a comma-separated field into a clean list."""
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _record_outcome(document: MemoryDocument) -> tuple[int, int]:
    """Read the (correct, wrong) counts of one learning record.

    Records created from the Learning Records page carry explicit counts; records
    created from a conversation carry a single ``is_correct`` flag.
    """
    correct = int(field_number(document, "correct_count", 0.0))
    wrong = int(field_number(document, "wrong_count", 0.0))
    if correct or wrong:
        return correct, wrong
    flag = field_text(document, "is_correct")
    if flag == "true":
        return 1, 0
    if flag == "false":
        return 0, 1
    return 0, 0


def _trend(documents: list[MemoryDocument]) -> str:
    """Compare older and newer accuracy for one topic.

    Returns a short Chinese phrase describing the direction, or an empty string when
    there is not enough history to say anything.
    """
    if len(documents) < 2:
        return ""
    outcomes = [_record_outcome(document) for document in documents]
    midpoint = len(outcomes) // 2
    older = outcomes[:midpoint]
    newer = outcomes[midpoint:]

    def accuracy(items: list[tuple[int, int]]) -> float | None:
        total = sum(correct + wrong for correct, wrong in items)
        if total == 0:
            return None
        return sum(correct for correct, _ in items) / total

    older_accuracy = accuracy(older)
    newer_accuracy = accuracy(newer)
    if older_accuracy is None or newer_accuracy is None:
        return ""
    delta = newer_accuracy - older_accuracy
    if delta >= 0.15:
        return "最近几次的正确率比之前有提升"
    if delta <= -0.15:
        return "最近几次的正确率比之前有所下滑"
    return "最近几次的正确率和之前基本持平"


def _optional_bool(value: str) -> bool | None:
    """Parse an optional boolean field."""
    if value == "":
        return None
    return value.strip().lower() in {"true", "1", "yes", "y"}


def _optional_int(value: str) -> int | None:
    """Parse an optional integer field."""
    if value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _last_update(snapshot: MemorySnapshot) -> str:
    """Return the most recent ``updated_at`` across the stored documents."""
    timestamps = [
        field_text(document, "updated_at") or field_text(document, "practiced_at")
        for document in snapshot.documents
    ]
    timestamps = [value for value in timestamps if value]
    return max(timestamps) if timestamps else ""


__all__ = ["LearningService", "MemorySnapshot", "TopicStats", "utc_now"]
