# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""Maven and Gradle adapter for JVM workspaces."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


class JvmAdapter:
    def __init__(self, system: str):
        self.system = system

    def collect_facts(self, engine: Any, paths: list[Path], facts: list, evidence: list, dependencies: list[dict[str, Any]]) -> None:
        if self.system == "maven":
            path, text = engine._first_text([path for path in paths if path.name == "pom.xml"])
            if not path: return
            for label, pattern in (("runtime.java", r"<(?:maven.compiler.release|maven.compiler.source|java.version)>([^<]+)"), ("framework.spring_boot", r"<spring-boot.version>([^<]+)")):
                match = re.search(pattern, text)
                if match: engine._fact(label, match.group(1), path, evidence, facts)
            for match in re.finditer(r"<dependency>\s*<groupId>([^<]+)</groupId>\s*<artifactId>([^<]+)</artifactId>(?:\s*<version>([^<]+)</version>)?", text, re.S):
                dependencies.append({"coordinate": f"{match.group(1)}:{match.group(2)}:{match.group(3) or 'managed'}", "source": "declared", "status": "unresolved"})
        else:
            path, text = engine._first_text([path for path in paths if path.name.startswith("build.gradle")])
            if not path: return
            match = re.search(r'(?:sourceCompatibility|JavaVersion\.VERSION_)(?:\s*=|\s*\.equals\()?\s*[\x27"]?(\d+)[\x27"]?', text, re.I)
            if match: engine._fact("runtime.java", match.group(1), path, evidence, facts)
            match = re.search(r'org\.springframework\.boot[^\n]*version[^\x27"]*[\x27"]([^\x27"]+)', text)
            if match: engine._fact("framework.spring_boot", match.group(1), path, evidence, facts)
        if "javax.persistence" in text:
            engine._fact("compatibility.persistence_namespace", "javax.persistence", path, evidence, facts)
        elif "jakarta.persistence" in text:
            engine._fact("compatibility.persistence_namespace", "jakarta.persistence", path, evidence, facts)

    def find_package(self, engine: Any, name: str, show_usage: bool) -> list[dict]:
        group, artifact = name.split(":", 1) if ":" in name else (None, name)
        group, artifact = (group.lower() if group else None), artifact.lower()
        declared_version, declared_file = None, None
        for filename in ("pom.xml", "build.gradle", "build.gradle.kts"):
            path = engine.workspace / filename
            if not path.is_file(): continue
            text = engine._read(path)
            match = re.search(re.escape(artifact) + r'[^\n]*?[>"\']([0-9][^<"\s]+)', text, re.I)
            if match: declared_version, declared_file = match.group(1), filename
            if group and re.search(re.escape(group) + r"[^\n]*" + re.escape(artifact), text, re.I) and not declared_file: declared_file = filename
        installed_version, install_path = None, None
        if group:
            repository = Path.home() / ".m2" / "repository" / group.replace(".", "/") / artifact
            if repository.is_dir():
                versions = sorted(path.name for path in repository.iterdir() if path.is_dir() and not path.name.startswith("."))
                if versions: installed_version, install_path = versions[-1], str(repository / versions[-1])
        prefix = group or artifact.replace("-", ".")
        usage = engine._grep_usage(prefix, {".java", ".kt", ".scala", ".groovy"}) if show_usage else []
        if installed_version is None and declared_version is None and not usage: return []
        return [{"ecosystem": self.system, "package": name, "installed": installed_version is not None, "installed_version": installed_version, "declared_version": declared_version, "declared_file": declared_file, "install_path": install_path, "import_patterns": [f"import {prefix}.*;"], "usage": usage[:5]}]
