"""Integration tests for the StudyFlow HTTP API.

These tests drive the running backend over HTTP and therefore exercise the whole
stack: the API layer, the service layer, and the real PowerContext Server.

The backend must already be running. Point the tests at a specific instance with::

    STUDYFLOW_TEST_API_URL=http://127.0.0.1:8080
"""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")


async def test_health(api_client: httpx.AsyncClient) -> None:
    """The backend reports it is alive."""
    response = await api_client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_system_status_reports_both_services(api_client: httpx.AsyncClient) -> None:
    """The status endpoint reports PowerContext and the AI model service."""
    response = await api_client.get("/api/system/status")
    assert response.status_code == 200

    payload = response.json()
    assert payload["powercontext_ok"] is True
    assert "powercontext_base_url" in payload
    assert "ai_configured" in payload


async def test_profile_create_and_read(api_client: httpx.AsyncClient, unique_user_id: str) -> None:
    """A profile written through the API is read back through the API."""
    response = await api_client.put(
        "/api/profile",
        params={"user_id": unique_user_id},
        json={
            "nickname": "接口测试",
            "course": "数据库系统",
            "goal": "掌握规范化",
            "preferred_style": "举例优先",
            "focus_points": ["BCNF 分解"],
        },
    )
    assert response.status_code == 200

    profile = (await api_client.get("/api/profile", params={"user_id": unique_user_id})).json()
    assert profile["nickname"] == "接口测试"
    assert profile["source"] == "powercontext"
    assert profile["scope_id"]


async def test_record_create_and_weakness_update(
    api_client: httpx.AsyncClient, unique_user_id: str
) -> None:
    """Records drive the weakness analysis end to end."""
    for index in range(2):
        response = await api_client.post(
            "/api/records",
            params={"user_id": unique_user_id},
            json={
                "course": "数据库系统",
                "topic": "BCNF 分解",
                "question": f"第 {index + 1} 题",
                "is_correct": False,
                "self_rating": 2,
                "notes": "接口测试记录",
                "practiced_at": f"2026-01-0{index + 1}T00:00:00+00:00",
            },
        )
        assert response.status_code == 200
        assert response.json()["writes"][0]["action"] == "created"

    weaknesses = (await api_client.get("/api/weaknesses", params={"user_id": unique_user_id})).json()
    assert weaknesses
    assert weaknesses[0]["topic"] == "BCNF 分解"
    assert "累计练习 2 次" in weaknesses[0]["reason"]


async def test_review_plan_and_dashboard(api_client: httpx.AsyncClient, unique_user_id: str) -> None:
    """The plan and the dashboard are derived from the stored records."""
    await api_client.post(
        "/api/records",
        params={"user_id": unique_user_id},
        json={"topic": "函数依赖", "is_correct": True, "self_rating": 4},
    )

    plan = (await api_client.get("/api/review-plan", params={"user_id": unique_user_id})).json()
    assert plan["user_id"] == unique_user_id
    assert isinstance(plan["items"], list)

    dashboard = (await api_client.get("/api/dashboard", params={"user_id": unique_user_id})).json()
    assert dashboard["activity_count"] == 1
    assert dashboard["powercontext_ok"] is True


async def test_memory_center_lists_real_entries(
    api_client: httpx.AsyncClient, unique_user_id: str
) -> None:
    """The Memory Center shows the real artifact ids written to PowerContext."""
    await api_client.post(
        "/api/records",
        params={"user_id": unique_user_id},
        json={"topic": "记忆中心", "notes": "证据", "is_correct": True},
    )

    center = (await api_client.get("/api/memory", params={"user_id": unique_user_id})).json()
    assert center["entry_count"] >= 1
    assert center["scope_id"]
    assert all(entry["artifact_id"].startswith("art_") for entry in center["entries"])
    assert center["read_ok"] is True


async def test_user_isolation_through_the_api(
    api_client: httpx.AsyncClient, unique_user_id: str
) -> None:
    """Two learner ids never see each other's memory."""
    await api_client.post(
        "/api/records",
        params={"user_id": unique_user_id},
        json={"topic": "私有主题", "notes": "只属于一个学习者"},
    )

    other = (await api_client.get("/api/memory", params={"user_id": "student_isolation_control"})).json()
    assert other["entry_count"] == 0
    assert other["scope_id"] != ""

    other_records = (
        await api_client.get("/api/records", params={"user_id": "student_isolation_control"})
    ).json()
    assert other_records == []


async def test_tutor_without_ai_is_honest(api_client: httpx.AsyncClient, unique_user_id: str) -> None:
    """Without a model configured the tutor explains itself instead of inventing."""
    response = await api_client.post(
        "/api/tutor",
        json={"user_id": unique_user_id, "message": "我今天应该复习什么？"},
    )
    assert response.status_code == 200

    payload = response.json()
    if payload["ai_configured"]:
        assert payload["answer"]
    else:
        assert "尚未配置" in payload["answer"]


async def test_frontend_is_served(api_client: httpx.AsyncClient) -> None:
    """The backend also serves the single-page frontend."""
    response = await api_client.get("/")
    assert response.status_code == 200
    assert "StudyFlow" in response.text
