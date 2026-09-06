# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from ...models import BuildReport, ChangeReport


class ValidationMixin:
    def change_impact(self, baseline: str = "HEAD", report: BuildReport | None = None, staged: bool = False) -> dict:

        report = report or self._inspect_cached()
        changed, baseline_error = self._git_changed_files(baseline, staged=staged)
        affected: list[str] = []
        guidance: list[str] = []
        changed_paths = [item["path"] for item in changed]
        if any(Path(path).name in {"pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"} for path in changed_paths):
            affected.extend(["build_system", "dependencies", "runtime", "validation_command"])
            guidance.append("Re-inspect dependency and runtime facts before continuing.")
        if any(Path(path).name in {"package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "pyproject.toml", "uv.lock", "go.mod", "Cargo.toml", "global.json"} for path in changed_paths):
            affected.extend(["dependencies", "runtime"])
            guidance.append("Resolve dependencies using the approved policy mode, then validate again.")
        if any(path.startswith(".github/") or path.startswith(".gitlab/") for path in changed_paths):
            affected.append("validation_command")
            guidance.append("Review CI toolchain versions and validation commands.")
        if baseline_error:
            status = "inconclusive"
            guidance.append(baseline_error)
        elif not changed:
            status = "inconclusive"
            guidance.append("The baseline is valid, but no tracked or untracked change was detected." if not staged else "No staged changes detected in git index.")
        else:
            status = "inconclusive" if affected else "valid"
            if not affected:
                guidance.append("No build-affecting files were detected in the diff.")
        return ChangeReport(
            schema_version="v1", session_id=report.session_id, baseline=baseline,
            changed_files=changed, affected_facts=sorted(set(affected)), status=status,
            guidance=guidance, baseline_resolved=baseline_error is None, change_detected=bool(changed),
        )

    def repair_guidance(self, report: BuildReport | None = None, change=None) -> dict[str, Any]:
        report = report or self._inspect_cached()
        change = change or self.change_impact(report=report)
        issues = []
        if not report.build_systems:
            issues.append({"code": "BUILD_SYSTEM_UNKNOWN", "message": "No supported build system was detected.", "next_action": "Add a supported build marker or provide an explicit adapter."})
        for rec in report.recommendations:
            if rec.get("severity") == "error":
                msg = rec.get("message") or f"Use {rec['recommended']} instead of {rec['requested']}."
                issues.append({"code": rec["code"], "message": msg, "affected_files": rec.get("affected_files", []), "next_action": rec.get("repair", "Apply the compatibility recommendation and validate again.")})
        if change.status in {"inconclusive", "invalid"}:
            issues.extend({"code": "CHANGE_REQUIRES_VALIDATION", "message": msg, "next_action": "Run the recommended approved validation probes."} for msg in change.guidance)
        return {"schema_version": "v1", "session_id": report.session_id, "status": "inconclusive" if issues else "valid", "issues": issues}

    def validate_change(self, baseline: str = "HEAD", report: BuildReport | None = None, execute: bool = False, timeout_seconds: int = 300, staged: bool = False) -> dict[str, Any]:
        report = report or self._inspect_cached()
        change = self.change_impact(baseline, report, staged=staged)
        execution = self._execute_validation(report.validation_commands, timeout_seconds) if execute else {
            "mode": "static", "commands_executed": [], "results": [], "network_used": False,
            "limitations": ["Static validation does not execute project commands or claim that tests passed."],
        }
        if report.status == "invalid":
            status = "invalid"
        elif not change.baseline_resolved or not change.change_detected or not execute:
            status = "inconclusive"
        else:
            results = execution["results"]
            status = "valid" if results and all(r["status"] == "passed" for r in results) else "invalid"
            if not results or any(r["status"] == "unavailable" for r in results):
                status = "inconclusive"
        repair = self.repair_guidance(report, change)
        if status == "valid":
            repair = {"schema_version": "v1", "session_id": report.session_id, "status": "valid", "issues": []}
        else:
            for result in execution["results"]:
                if result["status"] in {"failed", "timed_out"}:
                    repair["issues"].append({
                        "code": "VALIDATION_PROBE_FAILED",
                        "message": f"Validation probe {'timed out' if result['status'] == 'timed_out' else 'failed'}: {' '.join(result['command'])}",
                        "next_action": "Review the captured probe output, repair the change, and run validation again.",
                    })
            if repair["issues"]:
                repair["status"] = "invalid" if status == "invalid" else "inconclusive"
        return {
            "schema_version": "v1", "session_id": report.session_id, "status": status,
            "report": report.to_dict(), "change": change.to_dict(), "repair": repair, "execution": execution,
        }

    # ------------------------------------------------------------------
    def _git_changed_files(self, baseline: str, staged: bool = False) -> tuple[list[dict[str, str]], str | None]:
        try:
            repo = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=self.workspace, capture_output=True, text=True, timeout=10, check=False)
            if repo.returncode != 0:
                return [], "No Git repository was detected. Initialize Git and create a baseline commit, or provide a Git workspace."
            if staged:
                result = subprocess.run(["git", "diff", "--cached", "--name-status"], cwd=self.workspace, capture_output=True, text=True, timeout=10, check=False)
                changed = []
                for line in result.stdout.splitlines():
                    parts = line.split("\t", 1)
                    if len(parts) == 2:
                        changed.append({"status": parts[0], "path": parts[1]})
                return changed, None
            resolved = subprocess.run(["git", "rev-parse", "--verify", f"{baseline}^{{commit}}"], cwd=self.workspace, capture_output=True, text=True, timeout=10, check=False)
            if resolved.returncode != 0:
                return [], f"Git baseline '{baseline}' could not be resolved. Create a baseline commit or pass an existing commit, branch, or tag."
            result = subprocess.run(["git", "diff", "--name-status", baseline, "--"], cwd=self.workspace, capture_output=True, text=True, timeout=10, check=False)
            status_result = subprocess.run(["git", "status", "--porcelain=v1", "--untracked-files=all"], cwd=self.workspace, capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return [], "Git inspection failed or timed out while resolving the requested baseline."
        changed = []
        for line in result.stdout.splitlines():
            parts = line.split("	", 1)
            if len(parts) == 2:
                changed.append({"status": parts[0], "path": parts[1]})
        known = {item["path"] for item in changed}
        for line in status_result.stdout.splitlines():
            if len(line) < 4:
                continue
            path = line[3:]
            if " -> " in path:
                path = path.rsplit(" -> ", 1)[-1]
            if path not in known:
                changed.append({"status": line[:2].strip() or "??", "path": path})
        return changed, None

    def _execute_validation(self, commands: list[dict[str, Any]], timeout_seconds: int) -> dict[str, Any]:
        timeout_seconds = max(1, min(timeout_seconds, 900))
        results: list[dict[str, Any]] = []
        for spec in commands:
            command = [str(item) for item in spec.get("command", [])]
            if not command:
                continue
            # Run where the command says it must run. Executing every probe at
            # the repository root is the same fault the commands themselves had.
            working_directory = str(spec.get("working_directory", ".") or ".")
            directory = (self.workspace / working_directory).resolve()
            if not directory.is_dir():
                results.append({"command": command, "working_directory": working_directory, "status": "unavailable", "exit_code": None, "duration_ms": 0, "stdout": "", "stderr": f"working directory does not exist: {working_directory}"})
                continue
            self._assert_inside(directory)
            if not self._command_available(command, directory):
                results.append({"command": command, "working_directory": working_directory, "status": "unavailable", "exit_code": None, "duration_ms": 0, "stdout": "", "stderr": "executable or wrapper is not available in the workspace"})
                continue
            started = time.monotonic()
            try:
                completed = subprocess.run(command, cwd=directory, capture_output=True, text=True, timeout=timeout_seconds, check=False, shell=False)
                results.append({"command": command, "working_directory": working_directory, "status": "passed" if completed.returncode == 0 else "failed", "exit_code": completed.returncode, "duration_ms": int((time.monotonic() - started) * 1000), "stdout": completed.stdout[-12000:], "stderr": completed.stderr[-12000:]})
            except subprocess.TimeoutExpired as exc:
                results.append({"command": command, "working_directory": working_directory, "status": "timed_out", "exit_code": None, "duration_ms": int((time.monotonic() - started) * 1000), "stdout": str(exc.stdout or "")[-12000:], "stderr": str(exc.stderr or "")[-12000:]})
            except OSError as exc:
                results.append({"command": command, "working_directory": working_directory, "status": "failed", "exit_code": None, "duration_ms": int((time.monotonic() - started) * 1000), "stdout": "", "stderr": str(exc)})
        return {"mode": "probe", "commands_executed": [r["command"] for r in results if r["status"] != "unavailable"], "results": results, "network_used": "unknown", "limitations": ["Probe commands use shell=False, but selected build tools may resolve dependencies or run project-defined test code."]}


    # ------------------------------------------------------------------
    def _command_available(self, command: list[str], directory: Path | None = None) -> bool:
        """Whether the command's entrypoint resolves, relative to its own directory."""
        base = directory or self.workspace
        executable = command[0]
        if "/" in executable or "\\" in executable:
            path = (base / executable).resolve()
            try:
                path.relative_to(self.workspace)
            except ValueError:
                return False
            return path.is_file() and os.access(path, os.X_OK)
        return shutil.which(executable) is not None
