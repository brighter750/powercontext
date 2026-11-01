"""HTTP routes for the StudyFlow API."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request

from app.config import Settings
from app.schemas import (
    DashboardSummary,
    LearnerProfile,
    LearningRecordInput,
    LearningRecordView,
    MemoryCenter,
    ProfileUpdate,
    RecordCreated,
    ReviewPlan,
    SystemStatus,
    TutorRequest,
    TutorResponse,
    WeaknessItem,
)
from app.services.learning_service import LearningService

LOGGER = logging.getLogger("studyflow.api")

router = APIRouter(prefix="/api", tags=["studyflow"])


def get_service(request: Request) -> LearningService:
    """Return the application service bound to the running app."""
    return request.app.state.service


def get_app_settings(request: Request) -> Settings:
    """Return the running app's settings."""
    return request.app.state.settings


@router.get("/system/status", response_model=SystemStatus)
async def system_status(
    request: Request,
    service: LearningService = Depends(get_service),
    settings: Settings = Depends(get_app_settings),
) -> SystemStatus:
    """Report whether the AI model service and PowerContext are usable."""
    powercontext_ok = False
    detail = ""
    server_info: dict[str, object] = {}
    try:
        server_info = await service.repository.client.server_info()
        powercontext_ok = True
        detail = "PowerContext Server 可用"
    except Exception as error:  # noqa: BLE001 - status must never fail the request
        detail = f"PowerContext 不可用：{error}"
        LOGGER.warning("powercontext status check failed: %s", error)

    return SystemStatus(
        ai_configured=settings.ai_configured,
        ai_api_base=settings.ai_api_base,
        ai_model=settings.ai_model,
        ai_api_style=settings.ai_api_style,
        ai_api_key_present=bool(settings.ai_api_key),
        powercontext_ok=powercontext_ok,
        powercontext_base_url=settings.powercontext_base_url,
        powercontext_token_present=bool(settings.powercontext_token),
        powercontext_detail=detail,
        powercontext_server_info=server_info,
    )


@router.get("/dashboard", response_model=DashboardSummary)
async def dashboard(
    user_id: str = Query(min_length=1, max_length=128),
    service: LearningService = Depends(get_service),
) -> DashboardSummary:
    """Return the learning overview for one learner."""
    return await service.get_dashboard(user_id)


@router.get("/profile", response_model=LearnerProfile)
async def get_profile(
    user_id: str = Query(min_length=1, max_length=128),
    service: LearningService = Depends(get_service),
) -> LearnerProfile:
    """Read the learner's profile from PowerContext."""
    return await service.get_profile(user_id)


@router.put("/profile", response_model=LearnerProfile)
async def update_profile(
    user_id: str = Query(min_length=1, max_length=128),
    payload: ProfileUpdate = ...,
    service: LearningService = Depends(get_service),
) -> LearnerProfile:
    """Update the learner's profile and persist it to PowerContext."""
    profile, _ = await service.update_profile(user_id, payload)
    return profile


@router.get("/records", response_model=list[LearningRecordView])
async def list_records(
    user_id: str = Query(min_length=1, max_length=128),
    service: LearningService = Depends(get_service),
) -> list[LearningRecordView]:
    """List the learner's stored learning activities."""
    return await service.list_records(user_id)


@router.post("/records", response_model=RecordCreated)
async def create_record(
    payload: LearningRecordInput,
    user_id: str = Query(min_length=1, max_length=128),
    service: LearningService = Depends(get_service),
) -> RecordCreated:
    """Store a learning activity and refresh the derived mastery projection."""
    return await service.create_record(user_id, payload)


@router.get("/weaknesses", response_model=list[WeaknessItem])
async def list_weaknesses(
    user_id: str = Query(min_length=1, max_length=128),
    service: LearningService = Depends(get_service),
) -> list[WeaknessItem]:
    """List the learner's weak knowledge points with their evidence."""
    return await service.get_weaknesses(user_id)


@router.get("/review-plan", response_model=ReviewPlan)
async def review_plan(
    user_id: str = Query(min_length=1, max_length=128),
    service: LearningService = Depends(get_service),
) -> ReviewPlan:
    """Build today's personalised review plan."""
    return await service.build_review_plan(user_id)


@router.post("/tutor", response_model=TutorResponse)
async def tutor(
    payload: TutorRequest,
    service: LearningService = Depends(get_service),
) -> TutorResponse:
    """Ask the AI tutor, grounded in the learner's recalled long-term memory."""
    return await service.tutor(payload)


@router.get("/memory", response_model=MemoryCenter)
async def memory_center(
    user_id: str = Query(min_length=1, max_length=128),
    service: LearningService = Depends(get_service),
) -> MemoryCenter:
    """Show everything StudyFlow stored in PowerContext for one learner."""
    return await service.get_memory_center(user_id)


__all__ = ["router"]
