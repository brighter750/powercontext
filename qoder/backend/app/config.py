"""Application settings for StudyFlow.

Every credential is read from the environment (or a local ``.env`` file) and is never
exposed to the browser. The frontend only ever talks to this backend.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

QODER_ROOT = Path(__file__).resolve().parents[2]

AiApiStyle = Literal["openai", "anthropic"]


class Settings(BaseSettings):
    """Runtime configuration, resolved from ``STUDYFLOW_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="STUDYFLOW_",
        env_file=(QODER_ROOT / ".env", Path(".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- PowerContext ---------------------------------------------------
    powercontext_base_url: str = "http://127.0.0.1:8765"
    powercontext_token: str | None = None
    powercontext_timeout: float = 30.0
    powercontext_scope_kind: str = "studyflow.learner_id"
    powercontext_integration: str = "studyflow"
    powercontext_binding_kind: str = "learner"

    # --- AI model service ----------------------------------------------
    ai_api_base: str | None = None
    ai_api_key: str | None = None
    ai_model: str | None = None
    ai_api_style: AiApiStyle = "openai"
    ai_timeout: float = 120.0
    ai_max_tokens: int = 2048
    ai_temperature: float = 0.4

    # --- Application ----------------------------------------------------
    default_user_id: str = "student_demo_001"
    app_name: str = "StudyFlow"
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])

    @property
    def ai_configured(self) -> bool:
        """True when both an endpoint and a credential are present."""
        return bool(self.ai_api_base and self.ai_api_key and self.ai_model)

    def public_status(self) -> dict[str, object]:
        """Configuration summary that is safe to expose to the browser.

        Secrets are reported only as present/absent, never as values.
        """
        return {
            "ai_configured": self.ai_configured,
            "ai_api_base": self.ai_api_base,
            "ai_model": self.ai_model,
            "ai_api_style": self.ai_api_style,
            "ai_api_key_present": bool(self.ai_api_key),
            "powercontext_base_url": self.powercontext_base_url,
            "powercontext_token_present": bool(self.powercontext_token),
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()


__all__ = ["AiApiStyle", "QODER_ROOT", "Settings", "get_settings"]
