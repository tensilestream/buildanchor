# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""Cargo/Rust adapter."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


class RustAdapter:
    system = "rust"

    def collect_facts(self, engine: Any, paths: list[Path], facts: list, evidence: list, dependencies: list[dict[str, Any]]) -> None:
        path, text = engine._first_text([path for path in paths if path.name == "Cargo.toml"])
        if not path: return
        match = re.search(r'edition\s*=\s*[\x27"]([ ^\x27"]+)', text)
        if match: engine._fact("runtime.rust_edition", match.group(1), path, evidence, facts)
        if "[dependencies]" in text:
            for name, value in re.findall(r"^([\w-]+)\s*=\s*(.+)$", text.split("[dependencies]", 1)[1].split("[", 1)[0], re.M):
                dependencies.append({"coordinate": f"{name}:{value.strip()}", "source": "declared", "status": "unresolved"})

    def find_package(self, engine: Any, name: str, show_usage: bool) -> list[dict]:
        manifest, lockfile = engine.workspace / "Cargo.toml", engine.workspace / "Cargo.lock"
        declared_version, installed_version = None, None
        if manifest.is_file():
            match = re.search(r'^' + re.escape(name) + r'\s*=\s*["\']?([\d.^~*]+)', engine._read(manifest), re.M)
            if match: declared_version = match.group(1)
        if lockfile.is_file():
            match = re.search(r'\[\[package\]\][^[]*?name = "' + re.escape(name) + r'"[^[]*?version = "([^"]+)"', engine._read(lockfile), re.S)
            if match: installed_version = match.group(1)
        usage = engine._grep_usage(name, {".rs"}) if show_usage else []
        if installed_version is None and declared_version is None and not usage: return []
        normalized = name.replace("-", "_")
        return [{"ecosystem": self.system, "package": name, "installed": installed_version is not None, "installed_version": installed_version, "declared_version": declared_version, "install_path": None, "import_patterns": [f"use {normalized}::", f"extern crate {normalized};"], "usage": usage[:5]}]
