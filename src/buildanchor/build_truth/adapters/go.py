# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""Go module adapter."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


class GoAdapter:
    system = "go"

    def collect_facts(self, engine: Any, paths: list[Path], facts: list, evidence: list, dependencies: list[dict[str, Any]]) -> None:
        path, text = engine._first_text([path for path in paths if path.name == "go.mod"])
        if not path: return
        match = re.search(r"^go\s+([0-9.]+)", text, re.M)
        if match: engine._fact("runtime.go", match.group(1), path, evidence, facts)
        for module, version in re.findall(r"^\s*(?:require\s+)?([\w./-]+)\s+v([0-9][^\s]+)", text, re.M):
            dependencies.append({"coordinate": f"{module}:v{version}", "source": "declared", "status": "unresolved"})

    def find_package(self, engine: Any, name: str, show_usage: bool) -> list[dict]:
        declared_version = None
        manifest = engine.workspace / "go.mod"
        if manifest.is_file():
            match = re.search(r"\b" + re.escape(name) + r"\s+v([\d.]+)", engine._read(manifest))
            if match: declared_version = match.group(1)
        vendor = engine.workspace / "vendor" / name
        installed = vendor.is_dir()
        usage = engine._grep_usage(name, {".go"}) if show_usage else []
        if not installed and declared_version is None and not usage: return []
        return [{"ecosystem": self.system, "package": name, "installed": installed, "installed_version": declared_version, "declared_version": declared_version, "install_path": vendor.relative_to(engine.workspace).as_posix() if installed else None, "import_patterns": [f'import "{name}"'], "usage": usage[:5]}]
