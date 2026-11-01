"""Populate a demo learner so the app can be explored with realistic data.

The seed uses the backend's public HTTP API, so every row it creates goes through the
same code path a real user drives — and lands in PowerContext as real memory.

Usage::

    python qoder/scripts/seed_demo.py
    python qoder/scripts/seed_demo.py --api-url http://127.0.0.1:8080 --user-id student_demo_001
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

sys.stdout.reconfigure(encoding="utf-8")

RECORDS = [
    {
        "course": "数据库系统",
        "topic": "函数依赖",
        "question": "R(A,B,C)，F={A→B, A→C}，判断是否属于 3NF",
        "user_answer": "属于 3NF",
        "correct_answer": "属于 3NF",
        "is_correct": True,
        "self_rating": 4,
        "notes": "主属性判断已经掌握",
        "problems": "",
        "practiced_at": "2026-10-20T09:00:00+00:00",
    },
    {
        "course": "数据库系统",
        "topic": "候选码",
        "question": "R(A,B,C)，F={A→B, B→C}，求候选码",
        "user_answer": "候选码是 A",
        "correct_answer": "候选码是 A",
        "is_correct": True,
        "self_rating": 4,
        "notes": "会算闭包了",
        "problems": "",
        "practiced_at": "2026-10-22T09:00:00+00:00",
    },
    {
        "course": "数据库系统",
        "topic": "BCNF 分解",
        "question": "R(A,B,C,D)，F={A→B, C→D, B→C}，分解到 BCNF",
        "user_answer": "候选码是 AC",
        "correct_answer": "候选码是 AD",
        "is_correct": False,
        "self_rating": 2,
        "notes": "第一次做 BCNF 分解",
        "problems": "总是漏掉求闭包这一步",
        "practiced_at": "2026-10-25T09:00:00+00:00",
    },
    {
        "course": "数据库系统",
        "topic": "BCNF 分解",
        "question": "R(A,B,C,D)，F={A→B, C→D, B→C}，分解到 BCNF",
        "user_answer": "候选码是 AD",
        "correct_answer": "候选码是 AD",
        "is_correct": False,
        "self_rating": 2,
        "notes": "第二次，还是错在分解步骤",
        "problems": "分解后没有检查是否还是 BCNF",
        "practiced_at": "2026-10-28T09:00:00+00:00",
    },
    {
        "course": "数据库系统",
        "topic": "BCNF 分解",
        "question": "同上，求 BCNF 分解",
        "user_answer": "候选码是 AD，分解得到 R1(A,B,C)、R2(C,D)",
        "correct_answer": "候选码是 AD，分解得到 R1(A,B,C)、R2(C,D)",
        "is_correct": True,
        "self_rating": 4,
        "notes": "第三次做对了，比之前熟悉",
        "problems": "",
        "practiced_at": "2026-10-31T09:00:00+00:00",
    },
    {
        "course": "数据库系统",
        "topic": "第三范式",
        "question": "判断 R(A,B,C,D) 属于 2NF 但不满足 3NF 的例子",
        "user_answer": "存在传递依赖 A→B→C",
        "correct_answer": "存在传递依赖 A→B→C",
        "is_correct": True,
        "self_rating": 3,
        "notes": "3NF 的判定还需要多练",
        "problems": "传递依赖和函数依赖的关系还不太确定",
        "practiced_at": "2026-11-01T09:00:00+00:00",
    },
]


def main() -> int:
    """Create the demo learner through the backend API."""
    parser = argparse.ArgumentParser(description="Seed a demo learner for StudyFlow.")
    parser.add_argument(
        "--api-url",
        default=os.environ.get("STUDYFLOW_TEST_API_URL", "http://127.0.0.1:8080"),
    )
    parser.add_argument("--user-id", default="student_demo_001")
    args = parser.parse_args()

    with httpx.Client(base_url=args.api_url, timeout=120.0) as client:
        status = client.get("/api/system/status").json()
        if not status.get("powercontext_ok"):
            print("PowerContext 不可用，无法写入演示数据。请先启动 PowerContext Server。")
            return 2

        profile = client.put(
            "/api/profile",
            params={"user_id": args.user_id},
            json={
                "nickname": "小舟",
                "course": "数据库系统",
                "goal": "通过期末的规范化部分，并搞懂 BCNF 分解",
                "preferred_style": "先给一个具体例子，再讲定义和解题步骤",
                "focus_points": ["BCNF 分解", "第三范式"],
            },
        )
        print(f"档案写入: HTTP {profile.status_code}")

        for record in RECORDS:
            response = client.post("/api/records", params={"user_id": args.user_id}, json=record)
            body = response.json()
            state = body.get("knowledge_state") or {}
            print(
                f"  {record['topic']:<10} {record['practiced_at'][:10]}  "
                f"HTTP {response.status_code}  掌握度 {round(state.get('mastery', 0) * 100)}%"
            )

        weaknesses = client.get("/api/weaknesses", params={"user_id": args.user_id}).json()
        print("\n当前薄弱知识点：")
        for item in weaknesses:
            print(f"  - {item['topic']}  掌握度 {round(item['mastery'] * 100)}%  [{item['severity']}]")
            print(f"    {item['reason']}")

        plan = client.get("/api/review-plan", params={"user_id": args.user_id}).json()
        print(f"\n今日复习计划：{plan['summary']}")
        print(json.dumps([item["topic"] for item in plan["items"]], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
