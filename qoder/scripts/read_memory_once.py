"""Read one learner's StudyFlow memory in a fresh process and print it as JSON.

This is the probe used by acceptance scenario E: it runs with no shared state, no
warm connection, and no in-memory cache, so anything it can read must have been
persisted by the PowerContext Server.

Usage::

    python qoder/scripts/read_memory_once.py <user_id> [--powercontext-url URL]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings  # noqa: E402
from app.powercontext.client import PowerContextClient  # noqa: E402
from app.powercontext.repository import MemoryRepository  # noqa: E402
from app.services.ai_service import AiService  # noqa: E402
from app.services.learning_service import LearningService  # noqa: E402


async def read(user_id: str, base_url: str | None, token: str | None) -> dict[str, object]:
    """Read one learner's memory and return a JSON-friendly summary."""
    settings = get_settings()
    client = PowerContextClient(
        base_url or settings.powercontext_base_url,
        token=token if token is not None else settings.powercontext_token,
        timeout=settings.powercontext_timeout,
    )
    try:
        repository = MemoryRepository(client)
        service = LearningService(repository, AiService(settings, provider=None), settings)
        records = await service.list_records(user_id)
        center = await service.get_memory_center(user_id)
        return {
            "user_id": user_id,
            "scope_id": center.scope_id,
            "scope_title": center.scope_title,
            "record_count": len(records),
            "entry_count": center.entry_count,
            "records": [
                {
                    "topic": record.topic,
                    "notes": record.notes,
                    "artifact_id": record.artifact_id,
                    "revision": record.revision,
                    "practiced_at": record.practiced_at,
                }
                for record in records
            ],
            "knowledge_states": [
                {"topic": state.topic, "mastery": state.mastery, "attempts": state.attempts}
                for state in center.knowledge_states
            ],
        }
    finally:
        await client.aclose()


def main() -> int:
    """Parse arguments, read the memory, and print the result."""
    parser = argparse.ArgumentParser(description="Read one learner's memory in a fresh process.")
    parser.add_argument("user_id")
    parser.add_argument("--powercontext-url", default=None)
    parser.add_argument("--powercontext-token", default=None)
    args = parser.parse_args()

    payload = asyncio.run(read(args.user_id, args.powercontext_url, args.powercontext_token))
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
