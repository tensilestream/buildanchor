# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""Token-estimation policy for BuildAnchor's LLM-facing responses."""

from __future__ import annotations

try:
    import tiktoken as _tiktoken

    _ENCODING = _tiktoken.get_encoding("cl100k_base")

    def count_tokens(text: str) -> int:
        return len(_ENCODING.encode(text))
except Exception:
    def count_tokens(text: str) -> int:  # type: ignore[misc]
        return max(1, len(text) // 4)


def cost_tier(tokens: int) -> str:
    if tokens <= 300:
        return "low"
    if tokens <= 1000:
        return "medium"
    return "high"
