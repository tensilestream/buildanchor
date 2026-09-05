# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

""".NET project adapter."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


class DotnetAdapter:
    system = "dotnet"

    def collect_facts(self, engine: Any, paths: list[Path], facts: list, evidence: list, dependencies: list[dict[str, Any]]) -> None:
        path, text = engine._first_text([path for path in paths if path.suffix.lower() in {".csproj", ".fsproj", ".vbproj"}])
        if not path:
            return
        match = re.search(r"<TargetFrameworks?>([^<]+)", text)
        if match:
            engine._fact("runtime.dotnet", match.group(1), path, evidence, facts)

    def find_package(self, engine: Any, name: str, show_usage: bool) -> list[dict]:
        return []
