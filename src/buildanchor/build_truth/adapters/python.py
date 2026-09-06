# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""Python build-system adapter."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..core import manifest_parsing


class PythonAdapter:
    system = "python"

    def collect_facts(self, engine: Any, paths: list[Path], facts: list, evidence: list, dependencies: list[dict[str, Any]]) -> None:
        """Collect runtime facts and declared dependencies from every manifest.

        Every ``pyproject.toml`` is read, not just the first in sort order: in a
        polyglot repository the first is an arbitrary project, and reporting its
        dependencies as the repository's is a confident wrong answer.
        """
        manifests = sorted({path for path in paths if path.name == "pyproject.toml"})
        for path in manifests:
            text = engine._read(path)
            if not text:
                continue
            module = self._module_label(engine, path)
            runtime = manifest_parsing.requires_python(text)
            if runtime:
                engine._fact(
                    "runtime.python", runtime, path, evidence, facts,
                    module=None if module == "." else module,
                )
            for coordinate, scope in manifest_parsing.python_dependencies(text):
                dependencies.append({
                    "coordinate": coordinate,
                    "scope": scope,
                    "module": module,
                    "source": "declared",
                    "status": "unresolved",
                })

    @staticmethod
    def _module_label(engine: Any, path: Path) -> str:
        try:
            relative = path.parent.relative_to(engine.workspace)
        except ValueError:
            return "."
        return str(relative) if str(relative) != "." else "."

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
