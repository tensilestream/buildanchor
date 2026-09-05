# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""Node.js and TypeScript build-system adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class NodeAdapter:
    system = "node"

    def collect_facts(self, engine: Any, paths: list[Path], facts: list, evidence: list, dependencies: list[dict[str, Any]]) -> None:
        path, text = engine._first_text([path for path in paths if path.name == "package.json"])
        if not path:
            return
        try:
            package = json.loads(text)
        except json.JSONDecodeError:
            return
        for key in ("engines", "packageManager"):
            if key in package:
                engine._fact(f"node.{key}", package[key], path, evidence, facts)
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            for name, version in package.get(section, {}).items():
                dependencies.append({"coordinate": f"{name}@{version}", "scope": section, "source": "declared", "status": "unresolved"})

    def validation_commands(self, engine: Any, paths: list[Path]) -> list[dict[str, Any]]:
        path, text = engine._first_text([path for path in paths if path.name == "package.json"])
        if not path:
            return []
        try:
            scripts = json.loads(text).get("scripts", {})
        except json.JSONDecodeError:
            return []
        relative_directory = path.parent.relative_to(engine.workspace)
        prefix = [] if relative_directory == Path(".") else ["--prefix", str(relative_directory)]
        if "test" in scripts:
            return [engine._command(["npm", *prefix, "test"], "package.json test script", [path])]
        return [engine._command(["npm", *prefix, "run", "build"], "package.json build script", [path])] if "build" in scripts else []

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
