# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""Node.js and TypeScript build-system adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core import toolchain


class NodeAdapter:
    system = "node"

    def collect_facts(self, engine: Any, paths: list[Path], facts: list, evidence: list, dependencies: list[dict[str, Any]]) -> None:
        """Collect engine facts and declared dependencies from every manifest.

        Reads all ``package.json`` files rather than the first in sort order,
        so one arbitrary package cannot stand in for the whole repository.
        """
        for path in sorted({path for path in paths if path.name == "package.json"}):
            text = engine._read(path)
            if not text:
                continue
            try:
                package = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(package, dict):
                continue
            try:
                relative = str(path.parent.relative_to(engine.workspace))
            except ValueError:
                relative = "."
            module = relative if relative != "." else "."
            for key in ("engines", "packageManager"):
                if key in package:
                    engine._fact(
                        f"node.{key}", package[key], path, evidence, facts,
                        module=None if module == "." else module,
                    )
            for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
                entries = package.get(section)
                if not isinstance(entries, dict):
                    continue
                for name, version in entries.items():
                    dependencies.append({
                        "coordinate": f"{name}@{version}",
                        "scope": section,
                        "module": module,
                        "source": "declared",
                        "status": "unresolved",
                    })

    def validation_commands(self, engine: Any, paths: list[Path]) -> list[dict[str, Any]]:
        path, text = engine._first_text([path for path in paths if path.name == "package.json"])
        if not path:
            return []
        try:
            scripts = json.loads(text).get("scripts", {})
        except json.JSONDecodeError:
            return []
        relative_directory = str(path.parent.relative_to(engine.workspace))
        # Run inside the package directory. `npm --prefix <dir>` leaves the
        # working directory at the root, which breaks any script resolving a
        # relative path — and contradicts the working directory this same
        # report gives for the module.
        runner, _source = toolchain.node_runner(path.parent, engine.workspace)
        if "test" in scripts:
            return [engine._command(
                toolchain.node_script_command(runner, "test"),
                "package.json test script", [path], working_directory=relative_directory,
            )]
        if "build" in scripts:
            return [engine._command(
                toolchain.node_script_command(runner, "build"),
                "package.json build script", [path], working_directory=relative_directory,
            )]
        return []

    def find_package(self, engine: Any, name: str, show_usage: bool) -> list[dict]:
        module_path = engine.workspace / "node_modules" / name
        manifest = module_path / "package.json"
        installed_version, patterns = None, []
        if manifest.is_file():
            try:
                metadata = json.loads(manifest.read_text(encoding="utf-8", errors="replace"))
                installed_version = metadata.get("version")
                if "exports" in metadata: patterns.append(f"import ... from '{name}'")
                if metadata.get("module"): patterns.append(f"import ... from '{name}'  (ESM)")
                if metadata.get("main"): patterns.append(f"const x = require('{name}')")
            except (json.JSONDecodeError, OSError):
                pass
        if installed_version is not None and not patterns:
            patterns.append(f"import ... from '{name}'")
        declared_version, declared_scope = None, None
        root_manifest = engine.workspace / "package.json"
        if root_manifest.is_file():
            try:
                metadata = json.loads(root_manifest.read_text(encoding="utf-8", errors="replace"))
                for scope in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
                    if name in metadata.get(scope, {}):
                        declared_version, declared_scope = metadata[scope][name], scope
                        break
            except (json.JSONDecodeError, OSError):
                pass
        usage = engine._grep_usage(name, {".js", ".ts", ".mjs", ".cjs", ".jsx", ".tsx"}) if show_usage else []
        if installed_version is None and declared_version is None and not usage:
            return []
        return [{"ecosystem": self.system, "package": name, "installed": installed_version is not None, "installed_version": installed_version, "declared_version": declared_version, "declared_scope": declared_scope, "install_path": str(module_path.relative_to(engine.workspace)) if module_path.is_dir() else None, "import_patterns": patterns, "usage": usage[:5]}]
