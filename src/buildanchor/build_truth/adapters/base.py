# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""Contracts shared by ecosystem-specific Build Truth adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class BuildSystemAdapter(Protocol):
    """Extract ecosystem facts and optional package intelligence."""

    system: str

    def collect_facts(
        self, engine: Any, paths: list[Path], facts: list, evidence: list,
        dependencies: list[dict[str, Any]],
    ) -> None: ...

    def find_package(self, engine: Any, name: str, show_usage: bool) -> list[dict]: ...
