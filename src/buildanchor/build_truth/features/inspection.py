# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

from ...compatibility import compatibility_recommendations
from ...models import BuildReport, ModuleInfo
from ..adapters import adapter_for
from ..core.build_systems import MARKERS
from ..core.errors import BuildAnchorError


class InspectionMixin:
    def inspect(self, session_id: str | None = None) -> BuildReport:
        session_id = session_id or str(uuid.uuid4())
        evidence: list = []
        facts: list = []
        systems: list[str] = []
        languages: set[str] = set()
        modules: list[str] = []
        limitations: list[str] = []
        dependencies: list[dict[str, Any]] = []
        commands: list[dict[str, Any]] = []

        files = self._files()
        for system, markers, system_languages in MARKERS:
            matches = [path for path in files if path.name in markers]
            if system == "dotnet":
                matches += [path for path in files if path.suffix.lower() in {".csproj", ".fsproj", ".vbproj", ".sln"}]
            if not matches:
                continue
            systems.append(system)
            languages.update(system_languages)
            for path in sorted(set(matches)):
                evidence.append(self._evidence(path, "file", f"detected {system} marker"))
            if system == "maven":
                modules.extend(self._maven_modules(matches))
                adapter_for(system).collect_facts(self, matches, facts, evidence, dependencies)
                mvn_bin = "./mvnw" if (self.workspace / "mvnw").is_file() else "mvn"
                if (self.workspace / "pom.xml").is_file():
                    maven_cmd = [mvn_bin, "test"]
                elif matches:
                    rel_pom = str(matches[0].relative_to(self.workspace)) if matches[0].is_absolute() else str(matches[0])
                    maven_cmd = [mvn_bin, "test", "-f", rel_pom]
                else:
                    maven_cmd = [mvn_bin, "test"]
                commands.append(self._command(maven_cmd, "Maven test command", matches))
            elif system == "gradle":
                modules.extend(self._gradle_modules(matches))
                adapter_for(system).collect_facts(self, matches, facts, evidence, dependencies)
                gradle_cmd = ["./gradlew", "test"] if (self.workspace / "gradlew").is_file() else ["gradle", "test"]
                commands.append(self._command(gradle_cmd, "Gradle test command", matches))
            elif system == "node":
                adapter = adapter_for(system)
                adapter.collect_facts(self, matches, facts, evidence, dependencies)
                commands.extend(adapter.validation_commands(self, matches))
            elif system == "python":
                adapter_for(system).collect_facts(self, matches, facts, evidence, dependencies)
                py_cmd = ["python", "-m", "unittest", "discover", "-s", "tests", "-v"] if (self.workspace / "tests").is_dir() else ["python", "-m", "pytest"]
                commands.append(self._command(py_cmd, "Python test convention", matches))
            elif system == "go":
                adapter_for(system).collect_facts(self, matches, facts, evidence, dependencies)
                commands.append(self._command(["go", "test", "./..."], "Go module test command", matches))
            elif system == "rust":
                adapter_for(system).collect_facts(self, matches, facts, evidence, dependencies)
                commands.append(self._command(["cargo", "test"], "Cargo test command", matches))
            elif system == "dotnet":
                adapter_for(system).collect_facts(self, matches, facts, evidence, dependencies)
                commands.append(self._command(["dotnet", "test"], ".NET test command", matches))
            elif system == "generic":
                limitations.append("Generic build detection found markers; command execution is not inferred in static mode.")

        def compatibility_evidence(path: Path):
            item = self._evidence(path, "file", "compatibility source")
            if item.id not in {e.id for e in evidence}:
                evidence.append(item)
            return item

        recommendations = compatibility_recommendations(
            self.workspace, files, systems, facts, dependencies, compatibility_evidence
        )
        if not systems:
            limitations.append("No supported build marker was detected.")
        if any(s in systems for s in {"maven", "gradle", "node", "python", "go", "rust", "dotnet"}):
            limitations.append("Static mode does not claim that dependencies resolved or tests passed.")
        status = "invalid" if any(r["severity"] == "error" for r in recommendations) else ("valid" if systems else "inconclusive")

        discovered_modules = self._discover_modules(files)
        for dm in discovered_modules:
            modules.append(dm["name"])
            if dm["path"] != ".":
                modules.append(dm["path"])

        report = BuildReport(
            schema_version="v1",
            session_id=session_id,
            workspace=str(self.workspace),
            workspace_digest=self._workspace_digest(files),
            status=status,
            build_systems=systems,
            languages=sorted(languages),
            modules=sorted(set(modules)),
            module_details=discovered_modules,
            facts=facts,
            dependencies=dependencies,
            recommendations=recommendations,
            validation_commands=commands,
            evidence=evidence,
            limitations=limitations,
            git=self._git_info(),
        )
        self._report_cache[report.workspace_digest] = report
        return report

    def discover_modules(self) -> list[ModuleInfo]:
        """Return discovered monorepo modules as ModuleInfo objects."""
        report = self._inspect_cached()
        return [
            ModuleInfo(
                name=d.get("name", ""),
                path=d.get("path", ""),
                ecosystem=d.get("ecosystem", "generic"),
                category=d.get("category", "shared"),
                test_command=d.get("test_command"),
                build_command=d.get("build_command"),
            )
            for d in report.module_details
        ]

    def _inspect_cached(self, session_id: str | None = None) -> BuildReport:
        """Return a cached report if workspace has not changed, else re-inspect."""
        files = self._files()
        digest = self._workspace_digest(files)
        if digest in self._report_cache:
            return self._report_cache[digest]
        return self.inspect(session_id)

    def _files(self) -> list[Path]:
        files = []
        ignored_dirs = {"node_modules", ".venv", "venv", "target", "build", "dist", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
        for path in self.workspace.rglob("*"):
            if not path.is_file() or ".git" in path.parts:
                continue
            rel_parts = path.relative_to(self.workspace).parts[:-1]
            if any(part in ignored_dirs or part.endswith(".egg-info") or part in {"fixtures", "test-fixtures"} for part in rel_parts):
                continue
            if path.name == ".DS_Store" or path.suffix in {".pyc", ".pyo"}:
                continue
            self._assert_inside(path.resolve())
            files.append(path)
        return files

    def _assert_inside(self, path: Path) -> None:
        try:
            path.relative_to(self.allow_root)
        except ValueError as exc:
            raise BuildAnchorError(f"path is outside allowed root: {path}") from exc

    def _read(self, path: Path, limit: int = 256_000) -> str:
        self._assert_inside(path)
        try:
            return path.read_text(encoding="utf-8", errors="replace")[:limit]
        except OSError:
            return ""

    def _evidence(self, path: Path, kind: str, detail: str):
        from ...models import Evidence
        data = path.read_bytes()
        relative = str(path.relative_to(self.workspace))
        digest = hashlib.sha256(data).hexdigest()
        return Evidence(f"ev_{digest[:12]}", kind, relative, f"sha256:{digest}", detail)

    def _workspace_digest(self, files: list[Path]) -> str:
        digest = hashlib.sha256()
        for path in sorted(files):
            digest.update(str(path.relative_to(self.workspace)).encode())
            digest.update(path.read_bytes())
        return f"sha256:{digest.hexdigest()}"

    def _fact(self, key: str, value: Any, path: Path, evidence: list, facts: list, detail: str = "") -> None:
        from ...models import Fact
        item = self._evidence(path, "file", detail or f"source for {key}")
        if item.id not in {e.id for e in evidence}:
            evidence.append(item)
        facts.append(Fact(key, value, "proven", (item.id,), detail))

    def _first_text(self, paths: list[Path]) -> tuple:
        for path in sorted(set(paths)):
            text = self._read(path)
            if text:
                return path, text
        return None, ""

    def _categorize_module(self, name: str, rel_path: str, dependencies: list[str]) -> str:
        text = f"{name} {rel_path}".lower()
        tokens = set(re.split(r"[/\\_\-.\s@]+", text))
        lower_deps = {d.lower() for d in dependencies}

        ui_tokens = {"ui", "web", "frontend", "front-end", "client", "desktop", "mobile", "app", "view", "page", "components"}
        ui_deps = {"react", "react-dom", "vue", "svelte", "@sveltejs/kit", "next", "nuxt", "vite", "angular", "@angular/core", "astro", "solid-js", "remix", "electron", "react-native", "tailwindcss"}

        be_tokens = {"api", "backend", "back-end", "server", "service", "services", "db", "database", "worker", "job", "core", "graphql", "rest", "gateway"}
        be_deps = {"express", "fastify", "koa", "hono", "nest", "@nestjs/core", "flask", "django", "fastapi", "spring", "spring-boot", "actix-web", "axum", "gin", "echo", "fiber", "prisma", "typeorm", "mongoose"}

        has_ui = bool(tokens & ui_tokens) or any(d in lower_deps for d in ui_deps)
        has_be = bool(tokens & be_tokens) or any(d in lower_deps for d in be_deps)

        if has_ui and not has_be:
            return "ui"
        if has_be and not has_ui:
            return "backend"
        if has_ui and has_be:
            if any(t in tokens for t in {"ui", "web", "frontend", "client"}):
                return "ui"
            return "backend"

        if any(t in tokens for t in {"common", "shared", "util", "utils", "lib", "library", "sdk", "types", "proto", "config"}):
            return "shared"

        return "shared"

    def _discover_modules(self, files: list[Path]) -> list[dict[str, Any]]:
        discovered: dict[str, dict[str, Any]] = {}

        # 1. Node.js Monorepos (pnpm workspaces, npm/yarn workspaces, turbo, nx, lerna)
        workspace_globs: list[str] = []
        root_pkg_path = self.workspace / "package.json"
        if root_pkg_path.is_file():
            try:
                root_pkg = json.loads(self._read(root_pkg_path))
                ws = root_pkg.get("workspaces")
                if isinstance(ws, list):
                    workspace_globs.extend(ws)
                elif isinstance(ws, dict) and isinstance(ws.get("packages"), list):
                    workspace_globs.extend(ws["packages"])
            except Exception:
                pass

        pnpm_ws = self.workspace / "pnpm-workspace.yaml"
        if pnpm_ws.is_file():
            text = self._read(pnpm_ws)
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("-"):
                    glob_pat = stripped.lstrip("-").strip().strip("'\"")
                    if glob_pat:
                        workspace_globs.append(glob_pat)

        standard_roots = {"apps", "packages", "services", "libs", "modules", "frontend", "backend", "client", "server", "web", "api"}
        node_pkgs = [p for p in files if p.name == "package.json" and p.parent != self.workspace]

        for pkg_file in node_pkgs:
            rel_dir = str(pkg_file.parent.relative_to(self.workspace))
            is_matched = False
            if workspace_globs:
                for pat in workspace_globs:
                    clean_pat = pat.rstrip("/")
                    if clean_pat.endswith("/*"):
                        prefix = clean_pat[:-2]
                        if rel_dir.startswith(prefix + "/") or rel_dir == prefix:
                            is_matched = True
                            break
                    elif clean_pat == rel_dir or clean_pat == "*":
                        is_matched = True
                        break
            if not is_matched:
                first_part = Path(rel_dir).parts[0] if Path(rel_dir).parts else ""
                if first_part in standard_roots:
                    is_matched = True

            if is_matched or (self.workspace / "turbo.json").is_file() or (self.workspace / "nx.json").is_file() or pnpm_ws.is_file():
                try:
                    pkg_data = json.loads(self._read(pkg_file))
                    name = pkg_data.get("name") or Path(rel_dir).name
                    scripts = pkg_data.get("scripts", {})
                    deps = list(pkg_data.get("dependencies", {}).keys()) + list(pkg_data.get("devDependencies", {}).keys())
                    cat = self._categorize_module(name, rel_dir, deps)

                    test_cmd = f"npm --prefix {rel_dir} test" if "test" in scripts else None
                    build_cmd = f"npm --prefix {rel_dir} run build" if "build" in scripts else None

                    discovered[rel_dir] = {
                        "name": name,
                        "path": rel_dir,
                        "ecosystem": "node",
                        "category": cat,
                        "test_command": test_cmd,
                        "build_command": build_cmd,
                    }
                except Exception:
                    pass

        # 2. Maven Multi-Module
        root_pom = self.workspace / "pom.xml"
        if root_pom.is_file():
            pom_text = self._read(root_pom)
            m_sec = re.search(r"<modules>(.*?)</modules>", pom_text, re.DOTALL)
            if m_sec:
                for mod_m in re.finditer(r"<module>([^<]+)</module>", m_sec.group(1)):
                    mod_rel = mod_m.group(1).strip()
                    mod_pom = self.workspace / mod_rel / "pom.xml"
                    mod_name = Path(mod_rel).name
                    deps = []
                    if mod_pom.is_file():
                        sub_text = self._read(mod_pom)
                        art_m = re.search(r"<artifactId>([^<]+)</artifactId>", sub_text)
                        if art_m:
                            mod_name = art_m.group(1).strip()
                        deps = [d.group(1) for d in re.finditer(r"<artifactId>([^<]+)</artifactId>", sub_text)]
                    cat = self._categorize_module(mod_name, mod_rel, deps)
                    wrapper = "./mvnw" if (self.workspace / "mvnw").is_file() else "mvn"
                    discovered[mod_rel] = {
                        "name": mod_name,
                        "path": mod_rel,
                        "ecosystem": "maven",
                        "category": cat,
                        "test_command": f"{wrapper} test -pl {mod_rel}",
                        "build_command": f"{wrapper} package -pl {mod_rel}",
                    }

        # 3. Gradle Multi-Project
        for s_name in ("settings.gradle", "settings.gradle.kts"):
            s_path = self.workspace / s_name
            if s_path.is_file():
                s_text = self._read(s_path)
                for inc_m in re.finditer(r'include(?:\s+|\()([^\n;)]+)', s_text):
                    for str_m in re.finditer(r'[\x27"]([^\x27"]+)[\x27"]', inc_m.group(1)):
                        item = str_m.group(1).strip().lstrip(":")
                        if not item:
                            continue
                        rel_dir = item.replace(":", "/")
                        mod_name = item.split(":")[-1]
                        cat = self._categorize_module(mod_name, rel_dir, [])
                        wrapper = "./gradlew" if (self.workspace / "gradlew").is_file() else "gradle"
                        proj_arg = f":{item}" if not item.startswith(":") else item
                        discovered[rel_dir] = {
                            "name": mod_name,
                            "path": rel_dir,
                            "ecosystem": "gradle",
                            "category": cat,
                            "test_command": f"{wrapper} {proj_arg}:test",
                            "build_command": f"{wrapper} {proj_arg}:build",
                        }

        # 4. Rust Cargo Workspaces
        root_cargo = self.workspace / "Cargo.toml"
        if root_cargo.is_file():
            cargo_text = self._read(root_cargo)
            if "[workspace]" in cargo_text:
                cargo_sub_files = [p for p in files if p.name == "Cargo.toml" and p.parent != self.workspace]
                for c_file in cargo_sub_files:
                    rel_dir = str(c_file.parent.relative_to(self.workspace))
                    sub_cargo_text = self._read(c_file)
                    name_m = re.search(r'\[package\]\s*name\s*=\s*[\x27"]([^\x27"]+)[\x27"]', sub_cargo_text)
                    crate_name = name_m.group(1) if name_m else Path(rel_dir).name
                    cat = self._categorize_module(crate_name, rel_dir, [])
                    discovered[rel_dir] = {
                        "name": crate_name,
                        "path": rel_dir,
                        "ecosystem": "rust",
                        "category": cat,
                        "test_command": f"cargo test -p {crate_name}",
                        "build_command": f"cargo build -p {crate_name}",
                    }

        # 5. Go Workspaces
        go_work = self.workspace / "go.work"
        if go_work.is_file():
            work_text = self._read(go_work)
            for use_m in re.finditer(r'use\s+(?:\((.*?)\)|([^\s\n]+))', work_text, re.DOTALL):
                content = use_m.group(1) or use_m.group(2) or ""
                for line in content.splitlines():
                    rel_dir = line.strip().strip("'\"").lstrip("./")
                    if not rel_dir:
                        continue
                    mod_file = self.workspace / rel_dir / "go.mod"
                    mod_name = Path(rel_dir).name
                    if mod_file.is_file():
                        m_m = re.search(r'module\s+([^\s\n]+)', self._read(mod_file))
                        if m_m:
                            mod_name = m_m.group(1).split("/")[-1]
                    cat = self._categorize_module(mod_name, rel_dir, [])
                    discovered[rel_dir] = {
                        "name": mod_name,
                        "path": rel_dir,
                        "ecosystem": "go",
                        "category": cat,
                        "test_command": f"go test ./{rel_dir}/...",
                        "build_command": f"go build ./{rel_dir}/...",
                    }

        # 6. Python Workspaces / Polyglot Subprojects
        py_sub_files = [p for p in files if p.name in ("pyproject.toml", "setup.py") and p.parent != self.workspace]
        for py_file in py_sub_files:
            rel_dir = str(py_file.parent.relative_to(self.workspace))
            first_part = Path(rel_dir).parts[0] if Path(rel_dir).parts else ""
            if first_part in standard_roots or len(Path(rel_dir).parts) <= 2:
                py_text = self._read(py_file)
                name_m = re.search(r'name\s*=\s*[\x27"]([^\x27"]+)[\x27"]', py_text)
                pkg_name = name_m.group(1) if name_m else Path(rel_dir).name
                cat = self._categorize_module(pkg_name, rel_dir, [])
                discovered[rel_dir] = {
                    "name": pkg_name,
                    "path": rel_dir,
                    "ecosystem": "python",
                    "category": cat,
                    "test_command": f"python -m pytest {rel_dir}",
                    "build_command": f"python -m build {rel_dir}",
                }

        return [discovered[k] for k in sorted(discovered.keys())]

    def _maven_modules(self, paths: list[Path]) -> list[str]:
        return [str(path.parent.relative_to(self.workspace)) or "." for path in paths if path.name == "pom.xml"]

    def _gradle_modules(self, paths: list[Path]) -> list[str]:
        return [str(path.parent.relative_to(self.workspace)) or "." for path in paths if path.name.startswith("settings.gradle")]

    def _command(self, command: list[str], reason: str, paths: list[Path]) -> dict[str, Any]:
        return {"command": command, "status": "candidate", "reason": reason, "evidence": [str(path.relative_to(self.workspace)) for path in paths]}

    def _git_info(self) -> dict[str, Any]:
        try:
            root = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=self.workspace, capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return {"detected": False, "baseline_capable": False}
        if root.returncode != 0:
            return {"detected": False, "baseline_capable": False}
        head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=self.workspace, capture_output=True, text=True, timeout=10, check=False)
        return {"detected": True, "root": root.stdout.strip(), "head": head.stdout.strip() if head.returncode == 0 else None, "baseline_capable": head.returncode == 0}
