"""配置：全部来自环境变量或 ``.env`` 文件，不硬编码任何凭据。

约定：

* 所有配置项使用 ``SDDCTX_`` 前缀；
* 凭据（MiniMax API Key、OceanBase 口令、PowerContext Token）只从环境变量读取，
  不写入仓库、不写入日志。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

DEFAULT_MINIMAX_BASE_URL = "https://api.minimaxi.com/anthropic"
DEFAULT_MINIMAX_MODEL = "MiniMax-M2.7"
DEFAULT_POWERCONTEXT_URL = "http://127.0.0.1:8000"


def load_dotenv(path: Path) -> dict[str, str]:
    """读取简单的 ``KEY=VALUE`` 形式 ``.env`` 文件（不覆盖已有环境变量）。"""

    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            values[key] = value
            os.environ[key] = value
    return values


@dataclass(slots=True)
class SddConfig:
    """上下文管理模块的运行配置。"""

    # --- 业务工程 ---
    project_root: Path
    project_name: str = "business-project"

    # --- 资料库 ---
    db_backend: Literal["seekdb", "oceanbase"] = "seekdb"
    seekdb_path: Path = field(default_factory=lambda: Path(".sddctx-data/seekdb"))
    oceanbase_dsn: str = ""
    oceanbase_table_prefix: str = "sdd"

    # --- PowerContext ---
    powercontext_url: str = DEFAULT_POWERCONTEXT_URL
    powercontext_scope: str = ""
    powercontext_scope_key: str = "sddctx-minimax-oceanbase-scope"
    powercontext_token: str = ""

    # --- MiniMax ---
    minimax_api_key: str = ""
    minimax_base_url: str = DEFAULT_MINIMAX_BASE_URL
    minimax_model: str = DEFAULT_MINIMAX_MODEL
    minimax_protocol: Literal["anthropic", "openai"] = "anthropic"
    minimax_max_tokens: int = 8192
    minimax_timeout: float = 300.0

    # --- 运行目录 ---
    work_dir: Path = field(default_factory=lambda: Path(".sddctx-run"))
    evidence_dir_override: Path | None = None

    @property
    def evidence_dir(self) -> Path:
        if self.evidence_dir_override is not None:
            return self.evidence_dir_override
        return self.work_dir / "evidence"

    @property
    def state_dir(self) -> Path:
        return self.work_dir / "state"

    @classmethod
    def from_env(cls, env_file: Path | None = None) -> SddConfig:
        """从环境变量构造配置。"""

        if env_file is not None:
            load_dotenv(env_file)

        project_root = Path(os.environ.get("SDDCTX_PROJECT_ROOT", ".")).resolve()
        work_dir = Path(os.environ.get("SDDCTX_WORK_DIR", str(project_root / ".sddctx-run"))).resolve()

        backend = os.environ.get("SDDCTX_DB_BACKEND", "seekdb").strip().lower()
        if backend not in {"seekdb", "oceanbase"}:
            raise ValueError(f"SDDCTX_DB_BACKEND must be 'seekdb' or 'oceanbase', got {backend!r}")

        protocol = os.environ.get("SDDCTX_MINIMAX_PROTOCOL", "anthropic").strip().lower()
        if protocol not in {"anthropic", "openai"}:
            raise ValueError(f"SDDCTX_MINIMAX_PROTOCOL must be 'anthropic' or 'openai', got {protocol!r}")

        return cls(
            project_root=project_root,
            project_name=os.environ.get("SDDCTX_PROJECT_NAME", project_root.name),
            db_backend=backend,  # type: ignore[arg-type]
            seekdb_path=Path(os.environ.get("SDDCTX_SEEKDB_PATH", str(work_dir / "seekdb"))).resolve(),
            oceanbase_dsn=os.environ.get("SDDCTX_OCEANBASE_DSN", ""),
            oceanbase_table_prefix=os.environ.get("SDDCTX_OCEANBASE_TABLE_PREFIX", "sdd"),
            powercontext_url=os.environ.get("SDDCTX_POWERCONTEXT_URL", DEFAULT_POWERCONTEXT_URL).rstrip("/"),
            powercontext_scope=os.environ.get("SDDCTX_POWERCONTEXT_SCOPE", ""),
            powercontext_scope_key=os.environ.get(
                "SDDCTX_POWERCONTEXT_SCOPE_KEY", "sddctx-minimax-oceanbase-scope"
            ),
            powercontext_token=os.environ.get("SDDCTX_POWERCONTEXT_TOKEN", ""),
            minimax_api_key=os.environ.get("SDDCTX_MINIMAX_API_KEY", ""),
            minimax_base_url=os.environ.get("SDDCTX_MINIMAX_BASE_URL", DEFAULT_MINIMAX_BASE_URL).rstrip("/"),
            minimax_model=os.environ.get("SDDCTX_MINIMAX_MODEL", DEFAULT_MINIMAX_MODEL),
            minimax_protocol=protocol,  # type: ignore[arg-type]
            minimax_max_tokens=int(os.environ.get("SDDCTX_MINIMAX_MAX_TOKENS", "8192")),
            minimax_timeout=float(os.environ.get("SDDCTX_MINIMAX_TIMEOUT", "300")),
            work_dir=work_dir,
            evidence_dir_override=(
                Path(os.environ["SDDCTX_EVIDENCE_DIR"]).resolve()
                if os.environ.get("SDDCTX_EVIDENCE_DIR")
                else None
            ),
        )

    def require_minimax(self) -> None:
        if not self.minimax_api_key:
            raise RuntimeError(
                "缺少 MiniMax API Key：请设置环境变量 SDDCTX_MINIMAX_API_KEY"
                "（或 MINIMAX_API_KEY）。示例见 minimax/.env.example。"
            )

    def require_powercontext(self) -> None:
        if not self.powercontext_scope:
            raise RuntimeError(
                "缺少 PowerContext Scope：请设置环境变量 SDDCTX_POWERCONTEXT_SCOPE"
                "（可用 `python -m sddctx state init-scope` 自动创建并打印）。"
            )


__all__ = ["DEFAULT_MINIMAX_BASE_URL", "DEFAULT_MINIMAX_MODEL", "SddConfig", "load_dotenv"]
