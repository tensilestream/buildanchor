# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""Python build-system adapter."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


class PythonAdapter:
    system = "python"

    def collect_facts(self, engine: Any, paths: list[Path], facts: list, evidence: list, dependencies: list[dict[str, Any]]) -> None:
        path, text = engine._first_text([path for path in paths if path.name == "pyproject.toml"])
        if not path:
            return
        runtime = re.search(r"requires-python\s*=\s*[\"']([^\"']+)", text)
        if runtime:
            engine._fact("runtime.python", runtime.group(1), path, evidence, facts)
        for dependency in re.findall(r"[\"']([A-Za-z0-9_.-]+(?:[<>=!~].*)?)[\"']", text):
            if any(operator in dependency for operator in (">", "<", "=", "~")):
                dependencies.append({"coordinate": dependency, "source": "declared", "status": "unresolved"})

    def find_package(self, engine: Any, name: str, show_usage: bool) -> list[dict]:
        normalized = re.sub(r"[-_.]+", "-", name).lower()
        import_name = normalized.replace("-", "_")
        installed_version, install_path = None, None
        for environment in ("venv", ".venv", "env", ".env"):
            library = engine.workspace / environment / "lib"
            if not library.is_dir():
                continue
            for python_dir in library.iterdir():
                site_packages = python_dir / "site-packages"
                if not site_packages.is_dir():
                    continue
                for entry in site_packages.iterdir():
                    if entry.suffix == ".dist-info" and re.sub(r"[-_.]+", "-", entry.stem.split("-")[0]).lower() == normalized:
                        metadata = entry / "METADATA"
                        if metadata.is_file():
                            for line in metadata.read_text(encoding="utf-8", errors="replace").splitlines()[:20]:
                                if line.startswith("Version:"): installed_version = line.split(":", 1)[1].strip()
                            install_path = str(site_packages.relative_to(engine.workspace))
        declared_version, declared_file = None, None
        for filename in ("pyproject.toml", "requirements.txt", "requirements-dev.txt"):
            path = engine.workspace / filename
            if not path.is_file(): continue
            match = re.search(r'["\']?' + re.escape(normalized) + r'["\']?\s*([><=!~^][^\s,;\]]+)?', engine._read(path), re.I)
            if match:
                declared_version, declared_file = match.group(1) or "any", filename
                break
        usage = engine._grep_usage(import_name, {".py"}) if show_usage else []
        if installed_version is None and declared_version is None and not usage: return []
        return [{"ecosystem": self.system, "package": name, "installed": installed_version is not None, "installed_version": installed_version, "declared_version": declared_version, "declared_file": declared_file, "install_path": install_path, "import_patterns": [f"import {import_name}", f"from {import_name} import ..."], "usage": usage[:5]}]
