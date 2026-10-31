"""MiniMax 接入子包。"""

from __future__ import annotations

from sddctx.llm.coder import CodeModifier, PatchResult, apply_diff, build_prompt, extract_diff
from sddctx.llm.minimax_client import LlmCall, MiniMaxClient, MiniMaxError

__all__ = [
    "CodeModifier",
    "LlmCall",
    "MiniMaxClient",
    "MiniMaxError",
    "PatchResult",
    "apply_diff",
    "build_prompt",
    "extract_diff",
]
