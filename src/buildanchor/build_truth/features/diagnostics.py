# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""Explaining what BuildAnchor did and did not find.

The question people actually ask is "why isn't my project showing up?", and
until now the answer lived in a limitations string, if it was reachable at all.
Every rule that admits or rejects a directory is knowable, so this reports which
one applied, what evidence it saw, and what would have to change.

Nothing here re-derives discovery. It reads the same report every other command
reads, so a diagnosis can never disagree with the thing it is diagnosing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core import conventions
from ..core.errors import BuildAnchorError
from ..core.vocabulary import PROJECT_MARKERS


class DiagnosticsMixin:
    def diagnose(self, target: str | None = None) -> dict[str, Any]:
        """Explain the repository, or one directory within it."""
        report = self._inspect_cached()
        if target:
            return self._diagnose_path(target, report)
        return self._diagnose_repository(report)

    # -- whole repository -------------------------------------------------

    def _diagnose_repository(self, report: Any) -> dict[str, Any]:
        modules = report.module_details
        unverified = [m for m in modules if m.get("test_command_status", "declared") == "declared"]
        failing = [m for m in modules if m.get("test_command_outcome") == "failed"]
        commandless = [m for m in modules if not m.get("test_command")]

        findings: list[dict[str, str]] = []
        for note in report.limitations:
            if "could not be parsed" in note:
                # The loudest thing in a repository should not be the quietest
                # line in the diagnosis.
                findings.append({"severity": "error", "detail": note})
            elif "is not reported as a module" in note:
                findings.append({"severity": "info", "detail": note})
            elif "review horizon" in note:
                findings.append({"severity": "warning", "detail": note})
        if failing:
            names = ", ".join(m["path"] for m in failing[:5])
            findings.append({
                "severity": "error",
                "detail": f"{len(failing)} module(s) have a test command that does not run: {names}. "
                          "Run 'buildanchor verify' for the failure output.",
            })
        if commandless:
            names = ", ".join(m["path"] for m in commandless[:5])
            findings.append({
                "severity": "warning",
                "detail": f"{len(commandless)} module(s) have no test command: {names}.",
            })
        if unverified and not failing:
            findings.append({
                "severity": "info",
                "detail": f"{len(unverified)} of {len(modules)} module command(s) are declared but "
                          "unproven. Run 'buildanchor verify' to execute a discovery probe.",
            })
        if not report.build_systems:
            findings.append({
                "severity": "error",
                "detail": "No supported build marker was found. BuildAnchor has nothing to work from.",
            })

        # The commands are the point of the tool, and this is the first command
        # most people run. Showing them here is the difference between "what
        # does this do?" and an answer.
        commands = {}
        for phase in ("test", "build"):
            resolved = self.resolve_command(phase, report=report)
            if resolved.get("command"):
                commands[phase] = {
                    "command": resolved["command"],
                    "working_directory": resolved.get("working_directory", "."),
                    "source": resolved.get("source"),
                    "status": resolved.get("command_status", "declared"),
                }

        return {
            "schema_version": "v1",
            "workspace": str(self.workspace),
            "repository": report.repository,
            "build_systems": report.build_systems,
            "languages": report.languages,
            "commands": commands,
            "declared_runners": conventions.declared_runners(self.workspace),
            "modules": [
                {
                    "name": m.get("name"),
                    "path": m.get("path"),
                    "ecosystem": m.get("ecosystem"),
                    "working_directory": m.get("working_directory", "."),
                    "test_command": m.get("test_command"),
                    "status": m.get("test_command_status", "declared"),
                    "outcome": m.get("test_command_outcome", "declared"),
                }
                for m in modules
            ],
            "findings": findings,
            "status": "invalid" if any(f["severity"] == "error" for f in findings)
                      else ("inconclusive" if findings else "valid"),
        }

    # -- one directory ----------------------------------------------------

    def _diagnose_path(self, target: str, report: Any) -> dict[str, Any]:
        directory = (self.workspace / target).resolve()
        try:
            relative = str(directory.relative_to(self.workspace))
        except ValueError as exc:
            raise BuildAnchorError(f"path is outside the workspace: {target}") from exc
        relative = "." if relative == "." else relative

        result: dict[str, Any] = {
            "schema_version": "v1",
            "workspace": str(self.workspace),
            "path": relative,
            "exists": directory.is_dir(),
        }
        if not directory.is_dir():
            result["status"] = "invalid"
            result["reason"] = "no such directory"
            result["suggestions"] = ["Check the path; it is resolved relative to the workspace root."]
            return result

        markers = sorted(name for name in PROJECT_MARKERS if (directory / name).is_file())
        result["markers"] = markers
        result["considered"] = self._path_is_considered(directory)

        module = next((m for m in report.module_details if m.get("path") == relative), None)
        if module:
            result["status"] = "valid"
            result["is_module"] = True
            result["module"] = module
            result["reason"] = f"discovered as a {module.get('ecosystem')} module"
            result["suggestions"] = self._module_suggestions(module)
            return result

        result["is_module"] = False
        reason, suggestions = self._why_not_a_module(directory, relative, markers, report)
        result["status"] = "inconclusive"
        result["reason"] = reason
        result["suggestions"] = suggestions
        return result

    def _path_is_considered(self, directory: Path) -> dict[str, Any]:
        """Whether the directory's files reach discovery at all, and why not."""
        try:
            relative_parts = directory.relative_to(self.workspace).parts
        except ValueError:
            return {"included": False, "reason": "outside the workspace"}
        for part in relative_parts:
            if part in self.IGNORED_DIRS:
                return {"included": False, "reason": f"'{part}' is never traversed"}
            if part.endswith(".egg-info"):
                return {"included": False, "reason": f"'{part}' is never traversed"}

        tracked = self._git_tracked_files()
        if tracked is not None:
            prefix = str(directory.relative_to(self.workspace))
            inside = any(
                str(path.relative_to(self.workspace)).startswith("" if prefix == "." else prefix + "/")
                for path in tracked
            )
            if not inside:
                return {
                    "included": False,
                    "reason": "git lists no files here — it is empty, or ignored by .gitignore",
                }
        return {"included": True, "reason": "files here are visible to discovery"}

    def _why_not_a_module(
        self, directory: Path, relative: str, markers: list[str], report: Any
    ) -> tuple[str, list[str]]:
        if relative == ".":
            shape = (report.repository or {}).get("shape", "unknown")
            if shape in ("single-project", "root-plus-satellites"):
                return (
                    "the repository root is the project itself, and module_details lists "
                    "sub-projects only",
                    ["Use 'buildanchor cmd test' for the root project's command."],
                )
            return ("the repository root declares no project of its own", [])

        considered = self._path_is_considered(directory)
        if not considered["included"]:
            return (
                f"not considered: {considered['reason']}",
                ["Remove it from .gitignore, or move the project outside the ignored path."],
            )

        if not markers:
            names = ", ".join(sorted(PROJECT_MARKERS))
            return (
                "no project marker was found here",
                [f"A project root needs one of: {names}."],
            )

        for marker in markers:
            ecosystem = PROJECT_MARKERS[marker]
            if ecosystem == "node":
                return self._why_not_a_node_module(directory, marker)
            if ecosystem == "python":
                return self._why_not_a_python_module(relative, marker)
            return (
                f"'{marker}' was found, but {ecosystem} projects are discovered through the "
                "workspace their root declares",
                [f"Add this directory to the {ecosystem} workspace declaration at the repository root."],
            )
        return ("no rule admitted this directory", [])

    def _why_not_a_node_module(self, directory: Path, marker: str) -> tuple[str, list[str]]:
        try:
            package = json.loads(self._read(directory / marker))
        except (json.JSONDecodeError, OSError):
            return (
                f"'{marker}' is not valid JSON, so it could not be read",
                ["Fix the JSON syntax; BuildAnchor will not guess at a malformed manifest."],
            )
        scripts = package.get("scripts") if isinstance(package, dict) else None
        if not isinstance(scripts, dict) or not ({"test", "build"} & set(scripts)):
            return (
                f"'{marker}' declares no 'test' or 'build' script, so there is no entry point "
                "to report",
                [
                    'Add a "test" or "build" script to package.json, or',
                    "list this package in the workspace declaration at the repository root "
                    "(package.json workspaces, pnpm-workspace.yaml, turbo.json or nx.json).",
                ],
            )
        return ("it should have been discovered — this is a bug worth reporting", [])

    def _why_not_a_python_module(self, relative: str, marker: str) -> tuple[str, list[str]]:
        depth = len(Path(relative).parts)
        if depth > 2:
            return (
                f"'{marker}' is {depth} directories deep; Python projects are discovered at most "
                "two levels down, or anywhere under a conventional root",
                [
                    "Move the project nearer the repository root, or",
                    "place it under one of: apps, packages, services, libs, modules, frontend, "
                    "backend, client, server, web, api.",
                ],
            )
        return ("it should have been discovered — this is a bug worth reporting", [])

    @staticmethod
    def _module_suggestions(module: dict[str, Any]) -> list[str]:
        status = module.get("test_command_status", "declared")
        outcome = module.get("test_command_outcome", status)
        path = module.get("path", ".")
        if outcome == "failed":
            return [f"Its test command does not run. See: buildanchor verify --scope {path}"]
        if status == "declared":
            return [f"Its command is unproven. Prove it with: buildanchor verify --scope {path}"]
        if outcome == "skipped":
            return ["BuildAnchor has no discovery-only probe for this runner, so it stopped at "
                    f"'{status}'. Run the suite to go further: buildanchor verify "
                    f"--verify-level passes --scope {path}"]
        return []
