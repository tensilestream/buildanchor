# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from ... import schema as schema_module
from ...compatibility import compatibility_recommendations
from ...compatibility import stale_rules as compatibility_stale_rules
from ...models import BuildReport, ModuleInfo
from ..adapters import adapter_for
from ..core import languages as languages_module
from ..core import manifest_parsing, repository_shape, toolchain, vocabulary
from ..core.build_systems import MARKERS
from ..core.errors import BuildAnchorError


class InspectionMixin:
    def inspect(self, session_id: str | None = None) -> BuildReport:
        session_id = session_id or str(uuid.uuid4())
        evidence: list = []
        facts: list = []
        systems: list[str] = []
        modules: list[str] = []
        limitations: list[str] = []
        dependencies: list[dict[str, Any]] = []
        commands: list[dict[str, Any]] = []

        files, _digest = self._scan()
        for system, markers, _system_languages in MARKERS:
            matches = [path for path in files if path.name in markers]
            if system == "dotnet":
                matches += [path for path in files if path.suffix.lower() in {".csproj", ".fsproj", ".vbproj", ".sln"}]
            if not matches:
                continue
            systems.append(system)
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
                # Resolve through the shared toolchain rather than a hardcoded
                # convention: emitting `python -m unittest discover` for a
                # pytest project made this list contradict `get_test_command`,
                # and an agent that catches two BuildAnchor answers disagreeing
                # has no reason to believe either.
                root_manifest = self.workspace / "pyproject.toml"
                if root_manifest.is_file() and "[tool.pytest" in self._read(root_manifest):
                    py_cmd, py_reason = toolchain.python_test_command(self.workspace)
                    py_reason = f"pyproject.toml [tool.pytest] ({py_reason})"
                elif (self.workspace / "tests").is_dir():
                    py_cmd, py_reason = ["python", "-m", "unittest", "discover", "-s", "tests", "-v"], "Python test convention"
                else:
                    py_cmd, py_reason = toolchain.python_test_command(self.workspace)
                commands.append(self._command(py_cmd, py_reason, matches))
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
        # A manifest that cannot be parsed is the loudest thing in a repository
        # and was previously the quietest: the ecosystem was detected, no module
        # was produced, and the report said "valid". Silence there is how a typo
        # becomes a confident empty answer.
        unreadable = self._unreadable_manifests(files)
        for path in unreadable:
            limitations.append(
                f"'{path}' could not be parsed, so anything it declares is missing from this "
                "report. Fix the syntax and re-run."
            )

        stale = compatibility_stale_rules()
        if stale:
            codes = ", ".join(rule["code"] for rule in stale[:4])
            limitations.append(
                f"{len(stale)} compatibility rule(s) are past their review horizon and may be "
                f"outdated: {codes}. Their advice is unchanged but unconfirmed."
            )
        if not systems:
            limitations.append("No supported build marker was detected.")
        if any(s in systems for s in {"maven", "gradle", "node", "python", "go", "rust", "dotnet"}):
            limitations.append("Static mode does not claim that dependencies resolved or tests passed.")
        if any(r["severity"] == "error" for r in recommendations):
            status = "invalid"
        elif unreadable:
            # Something is declared here that BuildAnchor could not read, so it
            # cannot claim the picture is complete.
            status = "inconclusive"
        elif systems:
            status = "valid"
        else:
            status = "inconclusive"

        # Languages are evidence-backed: a language appears only when source
        # files or an unambiguous marker demonstrate it. See core/languages.py.
        language_details = languages_module.detect(files, self.workspace)

        discovered_modules = self._apply_verification_cache(self._discover_modules(files))
        repository = repository_shape.detect(self.workspace, discovered_modules, self._read)

        # Lead with the command for the project at the root. An agent reads the
        # first command in the list; in a root-plus-satellites repository that
        # should be the root project's, not whichever ecosystem sorted first.
        # Depth of the shallowest supporting evidence is the ranking signal:
        # `mvn test -f sdk/java/pom.xml` runs from the root but is a satellite's
        # command, and its evidence says so.
        def command_depth(item: dict[str, Any]) -> tuple[int, int]:
            evidence_paths = item.get("evidence") or []
            depth = min((path.count("/") for path in evidence_paths), default=99)
            return depth, 0 if item.get("working_directory", ".") == "." else 1

        commands.sort(key=command_depth)
        for dm in discovered_modules:
            modules.append(dm["name"])
            if dm["path"] != ".":
                modules.append(dm["path"])

        # Invariant: every discovery marker in `evidence` resolves either to a
        # module or to a stated reason for exclusion. The Node/Python discovery
        # asymmetry that hid whole projects from `module_details` while listing
        # their markers in `evidence` is exactly what this catches.
        unresolved = self._unresolved_markers(evidence, discovered_modules)
        for note in unresolved:
            limitations.append(note)

        report = BuildReport(
            schema_version=schema_module.CURRENT_SCHEMA,
            session_id=session_id,
            workspace=str(self.workspace),
            workspace_digest=_digest,
            status=status,
            build_systems=systems,
            languages=languages_module.names(language_details),
            language_details=language_details,
            modules=sorted(set(modules)),
            module_details=discovered_modules,
            repository=repository,
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

    @staticmethod
    def _module_info(d: dict[str, Any]) -> ModuleInfo:
        """Convert one ``module_details`` entry to a :class:`ModuleInfo`."""
        return ModuleInfo(
            name=d.get("name", ""),
            path=d.get("path", ""),
            ecosystem=d.get("ecosystem", "generic"),
            category=d.get("category", "unknown"),
            category_confidence=d.get("category_confidence", "none"),
            test_command=d.get("test_command"),
            build_command=d.get("build_command"),
            working_directory=d.get("working_directory", "."),
            test_command_shell=d.get("test_command_shell"),
            build_command_shell=d.get("build_command_shell"),
            test_command_status=d.get("test_command_status", "declared"),
            test_command_outcome=d.get("test_command_outcome", "declared"),
            test_command_duration_ms=d.get("test_command_duration_ms"),
            verified_at=d.get("verified_at"),
        )

    def discover_modules(self) -> list[ModuleInfo]:
        """Return discovered monorepo modules as ModuleInfo objects."""
        report = self._inspect_cached()
        return [
            ModuleInfo(
                name=d.get("name", ""),
                path=d.get("path", ""),
                ecosystem=d.get("ecosystem", "generic"),
                category=d.get("category", "unknown"),
                category_confidence=d.get("category_confidence", "none"),
                test_command=d.get("test_command"),
                build_command=d.get("build_command"),
                working_directory=d.get("working_directory", "."),
                test_command_shell=d.get("test_command_shell"),
                build_command_shell=d.get("build_command_shell"),
                test_command_status=d.get("test_command_status", "declared"),
                test_command_outcome=d.get("test_command_outcome", "declared"),
                test_command_duration_ms=d.get("test_command_duration_ms"),
                verified_at=d.get("verified_at"),
            )
            for d in report.module_details
        ]

    def _scan(self) -> tuple[list[Path], str]:
        """Return ``(files, digest)``, revalidating at most once per window.

        Establishing that the cache is still valid costs a walk of the
        repository, and one request can ask several times — the transport, the
        command resolver, the usage index. Within a short window the answer
        cannot meaningfully have changed, so it is computed once. The window is
        set by ``BUILDANCHOR_REVALIDATE_MS`` (default 250); ``0`` disables it and
        revalidates on every call.
        """
        window_ms = self._revalidate_window_ms()
        now = time.monotonic()
        cached = getattr(self, "_scan_cache", None)
        if cached is not None and window_ms > 0 and (now - cached[0]) * 1000 < window_ms:
            return cached[1], cached[2]
        files = self._files()
        digest = self._workspace_digest(files)
        self._scan_cache = (now, files, digest)
        return files, digest

    @staticmethod
    def _revalidate_window_ms() -> float:
        raw = os.environ.get("BUILDANCHOR_REVALIDATE_MS")
        if raw is None:
            return 250.0
        try:
            return max(0.0, float(raw))
        except ValueError:
            return 250.0

    def _invalidate_scan(self) -> None:
        """Drop the revalidation window — used when a caller demands freshness."""
        self._scan_cache = None

    def _inspect_cached(self, session_id: str | None = None) -> BuildReport:
        """Return a cached report if workspace has not changed, else re-inspect."""
        _files, digest = self._scan()
        if digest in self._report_cache:
            return self._report_cache[digest]
        return self.inspect(session_id)

    #: Directories that never contain project source and are not descended into.
    IGNORED_DIRS = frozenset({
        "node_modules", ".venv", "venv", "target", "build", "dist", "__pycache__",
        ".pytest_cache", ".ruff_cache", ".mypy_cache", ".buildanchor", ".git",
        "fixtures", "test-fixtures",
    })

    def _git_tracked_files(self) -> list[Path] | None:
        """Ask git for the files that matter, or ``None`` if it cannot answer.

        ``--cached --others --exclude-standard`` is exactly the right set:
        tracked files plus untracked ones that are not ignored. It delegates
        gitignore semantics — negations, nested files, ``**`` — to the only
        implementation guaranteed to agree with the repository, and it skips a
        large ignored tree without ever walking into it.
        """
        try:
            result = subprocess.run(
                ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                cwd=self.workspace, capture_output=True, timeout=30, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None

        files: list[Path] = []
        for raw in result.stdout.split(b"\0"):
            if not raw:
                continue
            try:
                relative = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            if self._is_ignored_relpath(relative):
                continue
            full = self.workspace / relative
            # git's answer is trusted for ordinary files — stat-ing all of them
            # costs more than the traversal it replaces. Only the files whose
            # bytes get read are checked, because `--cached` also lists a file
            # that is tracked but deleted on disk, and a submodule is a
            # directory rather than a file.
            if self._is_significant(full) and not full.is_file():
                continue
            files.append(full)
        return files

    @classmethod
    def _is_ignored_relpath(cls, relative: str) -> bool:
        parts = relative.split("/")
        if parts[-1] == ".DS_Store" or parts[-1].endswith((".pyc", ".pyo")):
            return True
        return any(
            part in cls.IGNORED_DIRS or part.endswith(".egg-info")
            for part in parts[:-1]
        )

    def _files(self) -> list[Path]:
        """List the workspace's files, pruning ignored trees during the walk.

        In a git repository the list comes from git, so whatever the repository
        already ignores costs nothing — a hardcoded list of eleven directory
        names cannot know about a project's own generated trees.

        Outside git, the fallback walk prunes ignored directories before
        descending, so a 400 MB `node_modules` is never traversed rather than
        traversed and then discarded, and symlinks are not followed — which makes
        an escape from the workspace impossible by construction and removes a
        `realpath` call per file.
        """
        tracked = self._git_tracked_files()
        if tracked is not None:
            return tracked

        files: list[Path] = []
        workspace = str(self.workspace)
        for directory, subdirectories, filenames in os.walk(workspace, followlinks=False):
            subdirectories[:] = [
                name for name in subdirectories
                if name not in self.IGNORED_DIRS
                and not name.endswith(".egg-info")
                and not os.path.islink(os.path.join(directory, name))
            ]
            for filename in filenames:
                if filename == ".DS_Store" or filename.endswith((".pyc", ".pyo")):
                    continue
                full = os.path.join(directory, filename)
                if os.path.islink(full):
                    # A symlinked file could point anywhere; excluding it keeps
                    # the workspace boundary a property of the walk itself.
                    continue
                files.append(Path(full))
        return files

    def _relative(self, path: Path) -> str:
        """A repository-relative path, always with forward slashes.

        These strings are data: they appear in reports, in the committed
        verification cache's keys, and in evidence entries. Windows would render
        them ``sdk\\node`` and every other platform ``sdk/node``, so the same
        repository would produce two different reports and a committed cache
        that churns whenever the platform changes. Git speaks forward slashes;
        so does this.
        """
        try:
            return path.relative_to(self.workspace).as_posix()
        except ValueError:
            return path.as_posix()

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
        relative = self._relative(path)
        digest = hashlib.sha256(data).hexdigest()
        return Evidence(f"ev_{digest[:12]}", kind, relative, f"sha256:{digest}", detail)

    def _workspace_digest(self, files: list[Path]) -> str:
        """Digest the workspace for cache identity.

        Cache identity is the set of paths plus the contents of the files a
        conclusion is actually drawn from — manifests, lockfiles, build
        descriptors. Nothing in the report depends on the contents of a source
        file: languages come from which extensions are present, modules and
        dependencies from manifests. So editing a function body correctly does
        not invalidate the report, and validating the cache costs no `stat` at
        all rather than one per file.

        Evidence entries are unaffected: each still carries a true SHA-256 of
        the file's contents.
        """
        digest = hashlib.sha256()
        for path in sorted(files):
            digest.update(self._relative(path).encode())
            if self._is_significant(path):
                digest.update(path.read_bytes())
        return f"sha256:{digest.hexdigest()}"

    #: Files whose contents can change a report, and so are hashed in full.
    SIGNIFICANT_SUFFIXES = frozenset({".csproj", ".fsproj", ".vbproj", ".sln"})

    @classmethod
    def _is_significant(cls, path: Path) -> bool:
        if path.name in cls._significant_names():
            return True
        return path.suffix.lower() in cls.SIGNIFICANT_SUFFIXES

    @classmethod
    def _significant_names(cls) -> frozenset[str]:
        cached = getattr(cls, "_significant_names_cache", None)
        if cached is None:
            names = {name for _system, markers, _langs in MARKERS for name in markers}
            names.update(toolchain.MANIFEST_FILES)
            names.update(languages_module.MARKER_LANGUAGES)
            names.update({"turbo.json", "nx.json", "lerna.json", "pnpm-workspace.yaml", "go.work", "mvnw", "gradlew"})
            cached = frozenset(names)
            cls._significant_names_cache = cached
        return cached

    def _fact(self, key: str, value: Any, path: Path, evidence: list, facts: list,
              detail: str = "", module: str | None = None) -> None:
        from ...models import Fact
        item = self._evidence(path, "file", detail or f"source for {key}")
        if item.id not in {e.id for e in evidence}:
            evidence.append(item)
        facts.append(Fact(key, value, "proven", (item.id,), detail, module))

    def _first_text(self, paths: list[Path]) -> tuple:
        for path in sorted(set(paths)):
            text = self._read(path)
            if text:
                return path, text
        return None, ""

    #: Files that show what a project *is*, independent of what it is called.
    UI_FILE_EVIDENCE: tuple[str, ...] = (
        "index.html", "vite.config.ts", "vite.config.js", "next.config.js", "next.config.mjs",
        "nuxt.config.ts", "svelte.config.js", "angular.json", "tailwind.config.js",
        "tailwind.config.ts", "astro.config.mjs",
    )
    UI_SOURCE_SUFFIXES: frozenset[str] = frozenset({".jsx", ".tsx", ".vue", ".svelte", ".astro"})
    BACKEND_FILE_EVIDENCE: tuple[str, ...] = (
        "Dockerfile", "docker-compose.yml", "Procfile", "alembic.ini", "manage.py",
        "wsgi.py", "asgi.py", "gunicorn.conf.py", "nest-cli.json",
    )

    def _categorize_module(
        self, name: str, rel_path: str, dependencies: list[str], directory: Path | None = None
    ) -> str:
        """Return ``(category, confidence)`` for a module.

        Three signals of different kinds are weighed — what the project is
        called, what it depends on, and what files it contains — so a name alone
        no longer decides on its own. When two or more agree the category is
        ``high`` confidence; when only one is available it is ``low``; when
        nothing points anywhere the answer is ``unknown``.
        """
        text = f"{name} {rel_path}".lower()
        tokens = set(re.split(r"[/\\_\-.\s@]+", text))
        lower_deps = {d.lower() for d in dependencies}

        ui_tokens = {"ui", "web", "frontend", "front-end", "client", "desktop", "mobile", "app", "view", "page", "components"}
        ui_deps = {"react", "react-dom", "vue", "svelte", "@sveltejs/kit", "next", "nuxt", "vite", "angular", "@angular/core", "astro", "solid-js", "remix", "electron", "react-native", "tailwindcss"}

        # "core" is deliberately absent: it names a shared core library at least
        # as often as a backend service.
        be_tokens = {"api", "backend", "back-end", "server", "service", "services", "db", "database", "worker", "job", "graphql", "rest", "gateway"}
        be_deps = {"express", "fastify", "koa", "hono", "nest", "@nestjs/core", "flask", "django", "fastapi", "spring", "spring-boot", "actix-web", "axum", "gin", "echo", "fiber", "prisma", "typeorm", "mongoose", "sqlalchemy", "uvicorn", "gunicorn", "celery"}

        shared_tokens = {"common", "shared", "util", "utils", "lib", "library", "sdk", "types", "proto", "config", "core", "internal"}

        ui_signals = {
            "name": bool(tokens & ui_tokens),
            "dependencies": any(d in lower_deps for d in ui_deps),
            "files": self._has_ui_evidence(directory),
        }
        backend_signals = {
            "name": bool(tokens & be_tokens),
            "dependencies": any(d in lower_deps for d in be_deps),
            "files": self._has_backend_evidence(directory),
        }
        ui_score = sum(ui_signals.values())
        backend_score = sum(backend_signals.values())

        if ui_score and ui_score > backend_score:
            return "ui", self._confidence(ui_score)
        if backend_score and backend_score > ui_score:
            # A shared-library name carrying a backend dependency is still a
            # library: `lib-a-core` depending on sqlalchemy is not a service.
            if tokens & shared_tokens and not (tokens & {"api", "server", "service", "gateway"}):
                return "shared", self._confidence(backend_score)
            return "backend", self._confidence(backend_score)
        if tokens & shared_tokens:
            return "shared", "low"
        return "unknown", "none"

    @staticmethod
    def _confidence(score: int) -> str:
        """How much evidence stands behind a category.

        ``high`` means at least two signals of different kinds agreed — what the
        project is called, what it depends on, what files it holds. ``low`` means
        one did, which for a Maven or Gradle module is often all there is, since
        those ecosystems expose no dependency or file evidence to weigh against
        the name. Reporting the support is more useful than refusing to answer.
        """
        return "high" if score >= 2 else "low"

    @classmethod
    def _has_ui_evidence(cls, directory: Path | None) -> bool:
        """Whether the directory contains something only a UI project has."""
        if directory is None or not directory.is_dir():
            return False
        if any((directory / name).is_file() for name in cls.UI_FILE_EVIDENCE):
            return True
        return cls._contains_suffix(directory, cls.UI_SOURCE_SUFFIXES)

    @classmethod
    def _has_backend_evidence(cls, directory: Path | None) -> bool:
        if directory is None or not directory.is_dir():
            return False
        return any((directory / name).is_file() for name in cls.BACKEND_FILE_EVIDENCE)

    @staticmethod
    def _contains_suffix(directory: Path, suffixes: frozenset[str], max_entries: int = 400) -> bool:
        """Look for a suffix without walking a whole tree."""
        for seen, path in enumerate(directory.rglob("*"), 1):
            if seen > max_entries:
                return False
            if path.suffix.lower() in suffixes and path.is_file():
                return True
        return False

    #: Marker filenames that identify a project root, and so are expected to
    #: resolve to a module in a multi-project repository. Shared, because a
    #: marker that `doctor` explains but the invariant ignores is a hole in the
    #: guarantee that nothing is excluded silently.
    PROJECT_MARKERS = vocabulary.PROJECT_MARKER_NAMES

    #: Manifests whose contents are parsed, and the parser that must accept them.
    PARSEABLE_MANIFESTS: tuple[str, ...] = ("package.json", "pyproject.toml")

    def _unreadable_manifests(self, files: list[Path]) -> list[str]:
        """Return manifests that exist but cannot be parsed."""
        broken: list[str] = []
        for path in files:
            if path.name not in self.PARSEABLE_MANIFESTS:
                continue
            text = self._read(path)
            if not text.strip():
                continue
            if path.name == "package.json":
                try:
                    json.loads(text)
                except (json.JSONDecodeError, ValueError):
                    broken.append(self._relative(path))
            elif not manifest_parsing.is_parseable(text):
                broken.append(self._relative(path))
        return sorted(broken)

    def _unresolved_markers(self, evidence: list, modules: list[dict[str, Any]]) -> list[str]:
        """Return a stated reason for every project marker that is not a module.

        Silence here is what made the original defect invisible: the report
        listed `web-ui/package.json` as evidence and omitted `web-ui` from
        `module_details`, with nothing to connect the two.
        """
        module_dirs = {module.get("path", ".") for module in modules}
        module_dirs.add(".")
        notes: list[str] = []
        for item in evidence:
            path = Path(getattr(item, "path", ""))
            if path.name not in self.PROJECT_MARKERS:
                continue
            directory = str(path.parent) or "."
            if directory in module_dirs:
                continue
            notes.append(
                f"Marker '{path}' was detected but '{directory}' is not reported as a module: "
                "it declares no test or build entry point that BuildAnchor recognises."
            )
        return sorted(set(notes))

    def _module_entry(
        self,
        name: str,
        rel_path: str,
        ecosystem: str,
        category: str,
        test_argv: list[str] | None,
        build_argv: list[str] | None,
        working_directory: str = ".",
        command_source: str = "",
        category_confidence: str = "none",
    ) -> dict[str, Any]:
        """Build one ``module_details`` entry with an unambiguous working directory.

        ``test_argv`` and ``build_argv`` are expressed relative to
        ``working_directory``. The ``*_shell`` variants prepend the ``cd`` so a
        caller can paste them from the workspace root without changing meaning.

        The shell variants are POSIX ``sh`` syntax. ``working_directory`` plus
        the bare command is the portable form, and is what a caller that does
        not know the target shell should use.
        """
        def render(argv: list[str] | None) -> tuple[str | None, str | None]:
            if not argv:
                return None, None
            command = " ".join(argv)
            if working_directory in ("", "."):
                return command, command
            return command, f"cd {working_directory} && {command}"

        test_command, test_shell = render(test_argv)
        build_command, build_shell = render(build_argv)
        return {
            "name": name,
            "path": rel_path,
            "ecosystem": ecosystem,
            "category": category,
            "category_confidence": category_confidence,
            "working_directory": working_directory or ".",
            "test_command": test_command,
            "build_command": build_command,
            "test_command_shell": test_shell,
            "build_command_shell": build_shell,
            "test_command_status": "declared",
            "test_command_outcome": "declared",
            "test_command_duration_ms": None,
            "test_command_source": command_source,
            "verified_at": None,
        }

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
            rel_dir = self._relative(pkg_file.parent)
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

            declared = (
                is_matched
                or (self.workspace / "turbo.json").is_file()
                or (self.workspace / "nx.json").is_file()
                or pnpm_ws.is_file()
            )
            if not declared:
                # Last resort, mirroring the rule that already makes the Python
                # side work: a package.json that declares a test or build script
                # is a project wherever it sits. Without this, a repository of
                # sibling projects at the root — no workspace file, no
                # apps/packages convention — reports its Python modules and
                # silently omits its Node ones.
                try:
                    scripts = json.loads(self._read(pkg_file)).get("scripts", {})
                except (json.JSONDecodeError, AttributeError):
                    scripts = {}
                declared = isinstance(scripts, dict) and bool({"test", "build"} & set(scripts))

            if declared:
                try:
                    pkg_data = json.loads(self._read(pkg_file))
                    name = pkg_data.get("name") or Path(rel_dir).name
                    scripts = pkg_data.get("scripts", {})
                    deps = list(pkg_data.get("dependencies", {}).keys()) + list(pkg_data.get("devDependencies", {}).keys())
                    cat, cat_confidence = self._categorize_module(name, rel_dir, deps, pkg_file.parent)

                    # Run inside the package directory rather than via
                    # `npm --prefix`, which leaves the working directory at the
                    # root and breaks any script that resolves relative paths.
                    runner, runner_source = toolchain.node_runner(pkg_file.parent, self.workspace)
                    test_argv = toolchain.node_script_command(runner, "test") if "test" in scripts else None
                    build_argv = toolchain.node_script_command(runner, "build") if "build" in scripts else None

                    discovered[rel_dir] = self._module_entry(
                        name, rel_dir, "node", cat, test_argv, build_argv,
                        working_directory=rel_dir, command_source=runner_source, category_confidence=cat_confidence,
                    )
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
                    cat, cat_confidence = self._categorize_module(mod_name, mod_rel, deps, self.workspace / mod_rel)
                    wrapper = "./mvnw" if (self.workspace / "mvnw").is_file() else "mvn"
                    # The reactor is driven from the root, so the root is the
                    # correct working directory for a `-pl` invocation.
                    discovered[mod_rel] = self._module_entry(
                        mod_name, mod_rel, "maven", cat,
                        [wrapper, "test", "-pl", mod_rel],
                        [wrapper, "package", "-pl", mod_rel],
                        working_directory=".", command_source="pom.xml [modules]", category_confidence=cat_confidence,
                    )

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
                        cat, cat_confidence = self._categorize_module(mod_name, rel_dir, [], self.workspace / rel_dir)
                        wrapper = "./gradlew" if (self.workspace / "gradlew").is_file() else "gradle"
                        proj_arg = f":{item}" if not item.startswith(":") else item
                        discovered[rel_dir] = self._module_entry(
                            mod_name, rel_dir, "gradle", cat,
                            [wrapper, f"{proj_arg}:test"],
                            [wrapper, f"{proj_arg}:build"],
                            working_directory=".", command_source=s_name, category_confidence=cat_confidence,
                        )

        # 4. Rust Cargo Workspaces
        root_cargo = self.workspace / "Cargo.toml"
        if root_cargo.is_file():
            cargo_text = self._read(root_cargo)
            if "[workspace]" in cargo_text:
                cargo_sub_files = [p for p in files if p.name == "Cargo.toml" and p.parent != self.workspace]
                for c_file in cargo_sub_files:
                    rel_dir = self._relative(c_file.parent)
                    sub_cargo_text = self._read(c_file)
                    name_m = re.search(r'\[package\]\s*name\s*=\s*[\x27"]([^\x27"]+)[\x27"]', sub_cargo_text)
                    crate_name = name_m.group(1) if name_m else Path(rel_dir).name
                    cat, cat_confidence = self._categorize_module(crate_name, rel_dir, [], c_file.parent)
                    discovered[rel_dir] = self._module_entry(
                        crate_name, rel_dir, "rust", cat,
                        ["cargo", "test", "-p", crate_name],
                        ["cargo", "build", "-p", crate_name],
                        working_directory=".", command_source="Cargo.toml [workspace]", category_confidence=cat_confidence,
                    )

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
                    cat, cat_confidence = self._categorize_module(mod_name, rel_dir, [], self.workspace / rel_dir)
                    discovered[rel_dir] = self._module_entry(
                        mod_name, rel_dir, "go", cat,
                        ["go", "test", "./..."],
                        ["go", "build", "./..."],
                        working_directory=rel_dir, command_source="go.work", category_confidence=cat_confidence,
                    )

        # 6. Python Workspaces / Polyglot Subprojects
        py_sub_files = [p for p in files if p.name in ("pyproject.toml", "setup.py") and p.parent != self.workspace]
        for py_file in py_sub_files:
            rel_dir = self._relative(py_file.parent)
            first_part = Path(rel_dir).parts[0] if Path(rel_dir).parts else ""
            if first_part in standard_roots or len(Path(rel_dir).parts) <= 2:
                py_text = self._read(py_file)
                name_m = re.search(r'name\s*=\s*[\x27"]([^\x27"]+)[\x27"]', py_text)
                pkg_name = name_m.group(1) if name_m else Path(rel_dir).name
                declared = [
                    coordinate.split(";")[0].strip()
                    for coordinate, _scope in manifest_parsing.python_dependencies(py_text)
                ]
                cat, cat_confidence = self._categorize_module(
                    pkg_name, rel_dir,
                    [re.split(r"[<>=!~\[ ]", d)[0] for d in declared],
                    py_file.parent,
                )
                # Each project owns its interpreter and its pytest rootdir, so
                # the command must run inside it and use its declared
                # environment. `python -m pytest <path>` from the root fails to
                # import the package under test.
                test_argv, env_source = toolchain.python_test_command(py_file.parent)
                runner_prefix, _ = toolchain.python_runner(py_file.parent)
                build_argv = [*runner_prefix, "build"] if runner_prefix[:2] in (["uv", "run"], ["poetry", "run"]) else [*runner_prefix, "-m", "build"]
                discovered[rel_dir] = self._module_entry(
                    pkg_name, rel_dir, "python", cat, test_argv, build_argv,
                    working_directory=rel_dir, command_source=env_source, category_confidence=cat_confidence,
                )

        return [discovered[k] for k in sorted(discovered.keys())]

    def _maven_modules(self, paths: list[Path]) -> list[str]:
        return [self._relative(path.parent) or "." for path in paths if path.name == "pom.xml"]

    def _gradle_modules(self, paths: list[Path]) -> list[str]:
        return [self._relative(path.parent) or "." for path in paths if path.name.startswith("settings.gradle")]

    def _command(self, command: list[str], reason: str, paths: list[Path],
                 working_directory: str = ".") -> dict[str, Any]:
        """Describe a validation command, including where it must run.

        ``status`` reports the verification rung the command has reached, so
        this list cannot advertise something the rest of the report calls
        unproven. It stays ``candidate`` until `buildanchor verify` says more.
        """
        proven = self._verified_status_for(working_directory, "test", " ".join(command))
        return {
            "command": command,
            "working_directory": working_directory,
            "command_shell": " ".join(command) if working_directory == "." else f"cd {working_directory} && {' '.join(command)}",
            "status": "candidate" if proven == "declared" else proven,
            "reason": reason,
            "evidence": [self._relative(path) for path in paths],
        }

    def _git_info(self) -> dict[str, Any]:
        try:
            root = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=self.workspace, capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return {"detected": False, "baseline_capable": False}
        if root.returncode != 0:
            return {"detected": False, "baseline_capable": False}
        head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=self.workspace, capture_output=True, text=True, timeout=10, check=False)
        return {"detected": True, "root": root.stdout.strip(), "head": head.stdout.strip() if head.returncode == 0 else None, "baseline_capable": head.returncode == 0}
