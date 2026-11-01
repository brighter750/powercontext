"""Request and response models for the StudyFlow HTTP API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class LearnerProfile(BaseModel):
    """The learner's study profile."""

    user_id: str = Field(min_length=1, max_length=128)
    nickname: str = ""
    course: str = ""
    goal: str = ""
    preferred_style: str = ""
    focus_points: list[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    scope_id: str = ""
    source: Literal["powercontext", "empty"] = "empty"


class ProfileUpdate(BaseModel):
    """Fields a learner may edit on their profile."""

    nickname: str | None = None
    course: str | None = None
    goal: str | None = None
    preferred_style: str | None = None
    focus_points: list[str] | None = None


class KnowledgeState(BaseModel):
    """Mastery state of one knowledge point."""

    topic: str
    course: str = ""
    mastery: float = 0.0
    attempts: int = 0
    correct: int = 0
    wrong: int = 0
    self_rating: float | None = None
    last_practiced_at: str = ""
    evidence: str = ""
    updated_at: str = ""


class LearningRecordInput(BaseModel):
    """A learning activity submitted through the Learning Records page."""

    course: str = ""
    topic: str = Field(min_length=1, max_length=200)
    notes: str = ""
    question: str = ""
    user_answer: str = ""
    correct_answer: str = ""
    is_correct: bool | None = None
    self_rating: int | None = Field(default=None, ge=1, le=5)
    problems: str = ""
    practiced_at: str = ""


class LearningRecordView(BaseModel):
    """A stored learning activity."""

    record_id: str = ""
    course: str = ""
    topic: str = ""
    notes: str = ""
    question: str = ""
    user_answer: str = ""
    correct_answer: str = ""
    is_correct: bool | None = None
    self_rating: int | None = None
    problems: str = ""
    practiced_at: str = ""
    artifact_id: str = ""
    revision: int = 0


class WeaknessItem(BaseModel):
    """One knowledge point that needs attention."""

    topic: str
    course: str = ""
    mastery: float = 0.0
    attempts: int = 0
    wrong: int = 0
    severity: Literal["high", "medium", "low"] = "medium"
    evidence: str = ""
    reason: str = ""
    last_practiced_at: str = ""
    recommendation: str = ""


class ReviewPlanItem(BaseModel):
    """One recommended review action."""

    topic: str
    priority: int = 0
    reason: str = ""
    action: str = ""
    mastery: float = 0.0
    severity: Literal["high", "medium", "low"] = "medium"
    evidence: str = ""


class ReviewPlan(BaseModel):
    """Today's review plan."""

    user_id: str = ""
    generated_at: str = ""
    items: list[ReviewPlanItem] = Field(default_factory=list)
    summary: str = ""
    practice_questions: list[dict[str, Any]] = Field(default_factory=list)


class TutorRequest(BaseModel):
    """A question sent to the AI tutor."""

    user_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1)
    save_to_memory: bool = True


class TutorResponse(BaseModel):
    """The tutor's answer plus the memory evidence it was based on."""

    answer: str = ""
    user_id: str = ""
    scope_id: str = ""
    used_memory: bool = False
    memory_hits: list[dict[str, Any]] = Field(default_factory=list)
    memory_context: str = ""
    saved: bool = False
    saved_actions: list[dict[str, Any]] = Field(default_factory=list)
    ai_configured: bool = True
    notice: str = ""


class MemoryEntryView(BaseModel):
    """One raw PowerContext memory entry, shown in the Memory Center."""

    kind: str = ""
    key: str = ""
    text: str = ""
    narrative: str = ""
    artifact_id: str = ""
    revision: int = 0
    state: str = ""


class MemoryCenter(BaseModel):
    """Everything the Memory Center page shows."""

    user_id: str = ""
    scope_id: str = ""
    scope_title: str = ""
    profile: LearnerProfile = Field(default_factory=LearnerProfile)
    knowledge_states: list[KnowledgeState] = Field(default_factory=list)
    weaknesses: list[WeaknessItem] = Field(default_factory=list)
    recent_records: list[LearningRecordView] = Field(default_factory=list)
    entries: list[MemoryEntryView] = Field(default_factory=list)
    last_memory_update: str = ""
    entry_count: int = 0
    powercontext: dict[str, Any] = Field(default_factory=dict)
    read_ok: bool = False
    write_ok: bool = False
    notice: str = ""


class DashboardSummary(BaseModel):
    """The Dashboard payload."""

    user_id: str = ""
    profile: LearnerProfile = Field(default_factory=LearnerProfile)
    activity_count: int = 0
    knowledge_point_count: int = 0
    weaknesses: list[WeaknessItem] = Field(default_factory=list)
    recent_progress: list[dict[str, Any]] = Field(default_factory=list)
    today_review: list[ReviewPlanItem] = Field(default_factory=list)
    review_summary: str = ""
    last_memory_update: str = ""
    powercontext_ok: bool = False
    ai_configured: bool = True
    notice: str = ""


class SystemStatus(BaseModel):
    """Runtime status of the two external services StudyFlow depends on."""

    ai_configured: bool = False
    ai_api_base: str | None = None
    ai_model: str | None = None
    ai_api_style: str = "openai"
    ai_api_key_present: bool = False
    powercontext_ok: bool = False
    powercontext_base_url: str = ""
    powercontext_token_present: bool = False
    powercontext_detail: str = ""
    powercontext_server_info: dict[str, Any] = Field(default_factory=dict)


class WriteResult(BaseModel):
    """The outcome of writing one memory document."""

    action: Literal["created", "revised"]
    kind: str = ""
    key: str = ""
    artifact_id: str = ""
    revision: int = 0


class RecordCreated(BaseModel):
    """Response of a learning-record submission."""

    record: LearningRecordView
    writes: list[WriteResult] = Field(default_factory=list)
    knowledge_state: KnowledgeState | None = None
    notice: str = ""


class ApiError(BaseModel):
    """A structured error payload."""

    code: str
    message: str
    detail: Any = None


__all__ = [
    "ApiError",
    "DashboardSummary",
    "KnowledgeState",
    "LearnerProfile",
    "LearningRecordInput",
    "LearningRecordView",
    "MemoryCenter",
    "MemoryEntryView",
    "ProfileUpdate",
    "RecordCreated",
    "ReviewPlan",
    "ReviewPlanItem",
    "SystemStatus",
    "TutorRequest",
    "TutorResponse",
    "WeaknessItem",
    "WriteResult",
]
