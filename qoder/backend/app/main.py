"""StudyFlow backend application entrypoint.

Run with::

    uvicorn app.main:app --host 127.0.0.1 --port 8080

The backend owns every credential. The browser never sees a PowerContext token or an
AI model key; it only talks to the JSON API below.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import QODER_ROOT, get_settings
from app.powercontext.client import (
    PowerContextAuthError,
    PowerContextClient,
    PowerContextError,
    PowerContextRequestError,
    PowerContextTimeout,
    PowerContextUnavailable,
)
from app.powercontext.repository import MemoryRepository
from app.services.ai_service import AiNotConfiguredError, AiProviderError, AiService
from app.services.learning_service import LearningService

LOGGER = logging.getLogger("studyflow")

FRONTEND_DIR = QODER_ROOT / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the service graph on startup and release it on shutdown."""
    settings = get_settings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    client = PowerContextClient(
        settings.powercontext_base_url,
        token=settings.powercontext_token,
        timeout=settings.powercontext_timeout,
    )
    repository = MemoryRepository(client)
    ai_service = AiService(settings)
    app.state.settings = settings
    app.state.powercontext_client = client
    app.state.repository = repository
    app.state.ai_service = ai_service
    app.state.service = LearningService(repository, ai_service, settings)
    LOGGER.info(
        "studyflow started powercontext=%s ai_configured=%s",
        settings.powercontext_base_url,
        settings.ai_configured,
    )
    try:
        yield
    finally:
        await client.aclose()


app = FastAPI(
    title="StudyFlow API",
    description="StudyFlow —— 具有长期记忆的 AI 学习教练（PowerContext 驱动）",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(PowerContextTimeout)
async def _powercontext_timeout(request: Request, error: PowerContextTimeout) -> JSONResponse:
    """Map a PowerContext timeout onto a gateway timeout."""
    LOGGER.warning("powercontext timeout on %s: %s", request.url.path, error)
    return JSONResponse(
        status_code=504,
        content={"code": "powercontext_timeout", "message": str(error)},
    )


@app.exception_handler(PowerContextAuthError)
async def _powercontext_auth(request: Request, error: PowerContextAuthError) -> JSONResponse:
    """Map a rejected PowerContext credential onto a forbidden response."""
    LOGGER.warning("powercontext auth failure on %s: %s", request.url.path, error)
    return JSONResponse(
        status_code=403,
        content={"code": "powercontext_forbidden", "message": str(error)},
    )


@app.exception_handler(PowerContextUnavailable)
async def _powercontext_unavailable(
    request: Request, error: PowerContextUnavailable
) -> JSONResponse:
    """Map an unreachable PowerContext Server onto a service-unavailable response."""
    LOGGER.warning("powercontext unavailable on %s: %s", request.url.path, error)
    return JSONResponse(
        status_code=503,
        content={"code": "powercontext_unavailable", "message": str(error)},
    )


@app.exception_handler(PowerContextRequestError)
async def _powercontext_request(request: Request, error: PowerContextRequestError) -> JSONResponse:
    """Map a rejected PowerContext request onto a bad-request response."""
    LOGGER.warning("powercontext request rejected on %s: %s", request.url.path, error)
    return JSONResponse(
        status_code=400,
        content={"code": "powercontext_bad_request", "message": str(error)},
    )


@app.exception_handler(PowerContextError)
async def _powercontext_other(request: Request, error: PowerContextError) -> JSONResponse:
    """Map any other PowerContext failure onto a service-unavailable response."""
    return JSONResponse(
        status_code=503,
        content={"code": "powercontext_error", "message": str(error)},
    )


@app.exception_handler(AiNotConfiguredError)
async def _ai_not_configured(request: Request, error: AiNotConfiguredError) -> JSONResponse:
    """Report an unconfigured model service honestly instead of faking a reply."""
    return JSONResponse(
        status_code=503,
        content={"code": "ai_not_configured", "message": str(error)},
    )


@app.exception_handler(AiProviderError)
async def _ai_provider_error(request: Request, error: AiProviderError) -> JSONResponse:
    """Report a model service failure honestly."""
    LOGGER.warning("ai provider failure on %s: %s", request.url.path, error)
    return JSONResponse(
        status_code=502,
        content={"code": "ai_provider_error", "message": str(error)},
    )


app.include_router(router)


@app.get("/api/health")
async def health() -> dict[str, str]:
    """A lightweight liveness probe for the backend itself."""
    return {"status": "ok", "service": "studyflow"}


if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


__all__ = ["app"]
