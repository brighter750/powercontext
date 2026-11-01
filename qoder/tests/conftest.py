"""Shared pytest fixtures for the StudyFlow test suite.

The suite is split into three clearly separated layers:

``unit``
    Pure domain rules — mastery maths, the memory envelope, JSON decoding.

``mock``
    StudyFlow's own orchestration, against :class:`FakePowerContextClient`.

``integration``
    The real PowerContext Server. These tests talk to a live Server and are skipped
    when none is reachable, so a reviewer can run ``pytest`` without a Server and
    still get a meaningful signal.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest

from app.config import Settings, get_settings
from app.powercontext.client import PowerContextClient
from app.powercontext.repository import MemoryRepository
from app.services.ai_service import AiService
from app.services.learning_service import LearningService
from fakes import FakePowerContextClient


@pytest.fixture(scope="session")
def settings() -> Settings:
    """Return the configured settings, with a deterministic default user."""
    return get_settings()


@pytest.fixture
def unique_user_id() -> str:
    """Return a learner id that no other test uses."""
    return f"student_test_{uuid.uuid4().hex[:10]}"


@pytest.fixture
def fake_client() -> FakePowerContextClient:
    """Return the in-memory PowerContext double."""
    return FakePowerContextClient()


@pytest.fixture
def mock_service(
    fake_client: FakePowerContextClient,
    settings: Settings,
) -> LearningService:
    """Return a service backed by the fake client and an unconfigured AI."""
    ai_service = AiService(settings, provider=None)
    return LearningService(MemoryRepository(fake_client), ai_service, settings)


@pytest.fixture
async def live_client(settings: Settings) -> AsyncIterator[PowerContextClient]:
    """Return a client for the real PowerContext Server, or skip the test."""
    base_url = os.environ.get("STUDYFLOW_TEST_POWERCONTEXT_URL", settings.powercontext_base_url)
    token = os.environ.get("STUDYFLOW_TEST_POWERCONTEXT_TOKEN", settings.powercontext_token)
    client = PowerContextClient(base_url, token=token, timeout=settings.powercontext_timeout)
    try:
        await client.health()
    except Exception as error:  # noqa: BLE001 - any failure means "no live Server"
        await client.aclose()
        pytest.skip(f"real PowerContext Server is not reachable at {base_url}: {error}")
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def live_service(live_client: PowerContextClient, settings: Settings) -> LearningService:
    """Return a service bound to the real PowerContext Server."""
    ai_service = AiService(settings, provider=None)
    return LearningService(MemoryRepository(live_client), ai_service, settings)


@pytest.fixture
def api_client() -> httpx.AsyncClient:
    """Return an HTTP client aimed at the StudyFlow backend API."""
    base_url = os.environ.get("STUDYFLOW_TEST_API_URL", "http://127.0.0.1:8080")
    return httpx.AsyncClient(base_url=base_url, timeout=60.0)
