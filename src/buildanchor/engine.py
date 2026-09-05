# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from .compatibility import compatibility_recommendations
from .models import BuildReport, ChangeReport, ContextPack, ModuleInfo, PromptBlock

# Optional tiktoken for accurate token counting; falls back to chars/4
try:
    import tiktoken as _tiktoken
    _ENC = _tiktoken.get_encoding("cl100k_base")
    def _count_tokens(text: str) -> int:
        return len(_ENC.encode(text))
except Exception:
    def _count_tokens(text: str) -> int:  # type: ignore[misc]
        return max(1, len(text) // 4)


def _cost_tier(tokens: int) -> str:
    if tokens <= 300:
        return "low"
    if tokens <= 1000:
        return "medium"
    return "high"


class BuildAnchorError(ValueError):
    pass


_MARKERS: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    ("maven", ("pom.xml",), ("Java", "Kotlin", "Scala")),
    ("gradle", ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "gradlew"), ("Java", "Kotlin", "Groovy", "Scala")),
    ("node", ("package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"), ("JavaScript", "TypeScript")),
    ("python", ("pyproject.toml", "requirements.txt", "requirements-dev.txt", "uv.lock", "poetry.lock", "setup.py"), ("Python",)),
    ("go", ("go.mod", "go.sum"), ("Go",)),
    ("rust", ("Cargo.toml", "Cargo.lock"), ("Rust",)),
    ("dotnet", ("global.json", "packages.lock.json"), ("C#", "F#", "Visual Basic")),
    ("generic", ("Makefile", "CMakeLists.txt", "BUILD", "WORKSPACE", "Package.swift", "composer.json", "Gemfile", "pubspec.yaml", "Dockerfile"), ("C/C++", "Swift", "PHP", "Ruby", "Dart")),
]

# Ecosystem labels for human-readable output
_ECOSYSTEM_LABELS: dict[str, str] = {
    "maven": "Java/Maven",
    "gradle": "Java/Gradle",
    "node": "Node.js",
    "python": "Python",
    "go": "Go",
    "rust": "Rust",
    "dotnet": ".NET",
    "generic": "Generic/Make",
}


class BuildAnchor:
    """Safe static Build Truth inspection for a bounded workspace."""

    def __init__(self, workspace: str | Path = ".", allow_root: str | Path | None = None):
        raw = Path(workspace).expanduser().resolve()
        if not raw.is_dir():
            tip = " (tip: use '.' for the current directory, or pass an absolute path)"
            if str(workspace) != str(raw):
                raise BuildAnchorError(
                    f"workspace is not a directory: '{workspace}' → resolved to '{raw}'{tip}"
                )
            raise BuildAnchorError(f"workspace is not a directory: '{workspace}'{tip}")
        self.workspace = raw
        self.allow_root = Path(allow_root or raw).expanduser().resolve()
        self._assert_inside(self.workspace)
        # In-process cache: keyed by workspace_digest to avoid re-scanning
        self._report_cache: dict[str, BuildReport] = {}

    # ------------------------------------------------------------------
    # Core inspection
    # ------------------------------------------------------------------

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
        for system, markers, system_languages in _MARKERS:
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
                self._maven_facts(matches, facts, evidence, dependencies)
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
                self._gradle_facts(matches, facts, evidence)
                gradle_cmd = ["./gradlew", "test"] if (self.workspace / "gradlew").is_file() else ["gradle", "test"]
                commands.append(self._command(gradle_cmd, "Gradle test command", matches))
            elif system == "node":
                self._node_facts(matches, facts, evidence, dependencies)
                commands.extend(self._node_commands(matches))
            elif system == "python":
                self._python_facts(matches, facts, evidence, dependencies)
                py_cmd = ["python", "-m", "unittest", "discover", "-s", "tests", "-v"] if (self.workspace / "tests").is_dir() else ["python", "-m", "pytest"]
                commands.append(self._command(py_cmd, "Python test convention", matches))
            elif system == "go":
                self._go_facts(matches, facts, evidence, dependencies)
                commands.append(self._command(["go", "test", "./..."], "Go module test command", matches))
            elif system == "rust":
                self._rust_facts(matches, facts, evidence, dependencies)
                commands.append(self._command(["cargo", "test"], "Cargo test command", matches))
            elif system == "dotnet":
                self._dotnet_facts(matches, facts, evidence)
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

    # ------------------------------------------------------------------
    # LLM context & token cost methods  (the primary cost-saving surface)
    # ------------------------------------------------------------------

    def llm_prompt(self, objective: str = "", fmt: str = "inline") -> PromptBlock:
        """Return a compact, ready-to-inject system prompt block.

        This is the CHEAPEST way to give an LLM accurate build context.
        Inject the returned ``content`` into the system prompt BEFORE the
        agent edits any files.  It replaces the need to read pyproject.toml,
        package.json, pom.xml, go.mod, Cargo.toml, etc. at inference time.

        Token cost: typically 80-250 tokens depending on repo complexity.
        """
        report = self._inspect_cached()
        lines: list[str] = [
            "# BuildAnchor Build Truth (authoritative — do not contradict without evidence)",
        ]

        # Ecosystem + languages
        if report.build_systems:
            eco = " | ".join(_ECOSYSTEM_LABELS.get(s, s) for s in report.build_systems)
            lines.append(f"Ecosystem: {eco}")
        if report.languages:
            lines.append(f"Languages: {', '.join(report.languages)}")

        # Key runtime facts (versions, editions, frameworks)
        proven_facts = [f for f in report.facts if f.status == "proven"]
        if proven_facts:
            lines.append("Runtime facts:")
            for fact in proven_facts[:8]:  # cap at 8 to stay concise
                lines.append(f"  {fact.key} = {fact.value}")

        # Validation commands (most useful for agent after acting)
        test_cmds = [" ".join(item["command"]) for item in report.validation_commands[:3]]
        if test_cmds:
            lines.append(f"Validate with: {' ; '.join(test_cmds)}")

        # Compatibility constraints (critical — prevents wrong package choices)
        errors = [r for r in report.recommendations if r.get("severity") == "error"]
        warnings = [r for r in report.recommendations if r.get("severity") == "warning"]
        if errors:
            lines.append("COMPATIBILITY ERRORS (fix before acting):")
            for r in errors:
                msg = r.get("message") or f"Use {r.get('recommended', '?')} instead of {r.get('requested', '?')}"
                lines.append(f"  [ERROR] {r['code']}: {msg}")
                if r.get("repair"):
                    lines.append(f"    Repair: {r['repair']}")
        if warnings:
            lines.append("Compatibility warnings:")
            for r in warnings[:4]:  # cap to stay lean
                msg = r.get("message") or r.get("recommendation", "")
                lines.append(f"  [WARN] {r['code']}: {msg}")

        # Objective-specific mismatch warning
        if objective:
            from .compatibility import compatibility_recommendations as _compat
            files = self._files()
            obj_recs = _compat(
                self.workspace, files, report.build_systems, report.facts,
                report.dependencies, lambda _: type("E", (), {"id": "x"})(),
                objective=objective,
            )
            obj_warnings = [r for r in obj_recs if r.get("code") == "OBJECTIVE_ECOSYSTEM_MISMATCH"]
            if obj_warnings:
                lines.append("OBJECTIVE MISMATCH WARNING:")
                for w in obj_warnings:
                    lines.append(f"  {w['message']}")
                    lines.append(f"  Advice: {w['recommendation']}")
            lines.append(f"Objective: {objective}")

        # Monorepo topology
        if report.module_details:
            lines.append(f"Monorepo: {len(report.module_details)} module(s) detected")
            ui_mods = [m["name"] for m in report.module_details if m.get("category") == "ui"]
            be_mods = [m["name"] for m in report.module_details if m.get("category") == "backend"]
            if ui_mods:
                lines.append(f"  UI modules: {', '.join(ui_mods[:4])} (use: buildanchor cmd test --scope ui)")
            if be_mods:
                lines.append(f"  Backend modules: {', '.join(be_mods[:4])} (use: buildanchor cmd test --scope backend)")
            lines.append("  Target changed packages: buildanchor cmd test --changed")

        # Git baseline info
        if report.git.get("baseline_capable"):
            lines.append(f"Git HEAD: {report.git.get('head', 'unknown')[:12]}")

        content = "\n".join(lines)
        tokens = _count_tokens(content)
        return PromptBlock(
            role="system",
            content=content,
            token_estimate=tokens,
            format=fmt,
            workspace_digest=report.workspace_digest,
            session_id=report.session_id,
        )

    def token_estimate(self) -> dict[str, Any]:
        """Return estimated token cost for each BuildAnchor tool call.

        Call this FIRST so the agent can choose the cheapest tool that
        meets its needs — instead of always calling the most expensive one.
        """
        report = self._inspect_cached()
        report_json = json.dumps(report.to_dict())
        ctx = self.context(report)
        ctx_json = json.dumps(ctx.to_dict())
        prompt_content = self.llm_prompt().content
        preflight = self.preflight(report=report)
        preflight_json = json.dumps(preflight)

        estimates = {
            "build.llm_prompt": {
                "tokens": _count_tokens(prompt_content),
                "description": "Compact ready-to-inject system prompt block. Use this first.",
                "recommended_for": "System prompt injection before any build-affecting action.",
            },
            "build.context": {
                "tokens": _count_tokens(ctx_json),
                "description": "Compact context pack with constraints and validation commands.",
                "recommended_for": "Fetching build constraints before editing files.",
            },
            "build.preflight": {
                "tokens": _count_tokens(preflight_json),
                "description": "Pre-change check with blocking compatibility errors.",
                "recommended_for": "Mandatory gate before making any dependency or build file change.",
            },
            "build.inspect": {
                "tokens": _count_tokens(report_json),
                "description": "Full Build Truth report including all evidence and dependencies.",
                "recommended_for": "Deep investigation only; avoid injecting into LLM context.",
            },
        }
        recommended = min(estimates, key=lambda k: estimates[k]["tokens"])
        return {
            "schema_version": "v1",
            "session_id": report.session_id,
            "workspace_digest": report.workspace_digest,
            "recommended_tool": recommended,
            "estimates": estimates,
            "guidance": (
                "Inject build.llm_prompt into the system prompt. "
                "Call build.preflight before changing build/dependency files. "
                "Avoid passing build.inspect output directly to the LLM."
            ),
        }

    def context(self, report: BuildReport | None = None, token_budget: int = 2500) -> ContextPack:
        report = report or self._inspect_cached()
        fact_lines = [f"{fact.key}={fact.value}" for fact in report.facts if fact.status == "proven"]
        summary = ", ".join(
            _ECOSYSTEM_LABELS.get(s, s) for s in report.build_systems
        ) or "unknown build system"
        if report.languages:
            summary += f"; languages: {', '.join(report.languages)}"
        constraints = [line for line in fact_lines if any(
            term in line.lower() for term in ("runtime", "framework", "namespace", "edition", "target")
        )]
        # Add compatibility constraint summaries
        for item in report.recommendations:
            if item.get("message"):
                constraints.append(f"[{item.get('severity','warn').upper()}] {item['code']}: {item['message']}")
            elif item.get("requested"):
                constraints.append(f"use {item['recommended']} instead of {item['requested']} ({item['code']})")
        if report.module_details:
            constraints.append(f"Monorepo: {len(report.module_details)} modules. Use 'buildanchor cmd test --scope ui|backend' or '--changed'.")
        commands = [item["command"] for item in report.validation_commands[:4]]
        # Trim to budget
        while len(json.dumps({"summary": summary, "constraints": constraints, "commands": commands})) > max(600, token_budget * 4) and constraints:
            constraints.pop()

        # Build the llm_context string
        prompt_block = self.llm_prompt()
        tokens = prompt_block.token_estimate

        return ContextPack(
            schema_version=report.schema_version,
            session_id=report.session_id,
            summary=summary,
            constraints=constraints,
            validation_commands=commands,
            fact_refs=[fact.key for fact in report.facts],
            evidence_refs=[item.id for item in report.evidence],
            limitations=report.limitations,
            llm_context=prompt_block.content,
            token_estimate=tokens,
            cost_tier=_cost_tier(tokens),
        )

    def preflight(self, objective: str = "", token_budget: int = 2500, report: BuildReport | None = None) -> dict[str, Any]:
        """Create the authoritative context an agent should receive before acting."""
        report = report or self._inspect_cached()
        context = self.context(report, token_budget)
        blocking = [item for item in report.recommendations if item.get("severity") == "error"]
        instructions = [
            "Treat this context as authoritative for build, runtime, dependency, and compatibility decisions.",
            "Do not add or replace packages until compatibility recommendations have been considered.",
            "After changing build, dependency, runtime, or framework files, call build.validate_change.",
        ]
        for item in report.recommendations:
            if item.get("repair"):
                label = item.get("message") or f"Use {item.get('recommended')} instead of {item.get('requested')}"
                instructions.append(f"{label}: {item['repair']}")

        # Mismatch check for objective
        if objective:
            from .compatibility import compatibility_recommendations as _compat
            files = self._files()
            obj_recs = _compat(
                self.workspace, files, report.build_systems, report.facts,
                report.dependencies, lambda _: type("E", (), {"id": "x"})(),
                objective=objective,
            )
            mismatch = [r for r in obj_recs if r.get("code") == "OBJECTIVE_ECOSYSTEM_MISMATCH"]
            if mismatch:
                for m in mismatch:
                    instructions.append(f"WARNING: {m['message']} {m['recommendation']}")

        return {
            "schema_version": "v1",
            "session_id": report.session_id,
            "phase": "preflight",
            "objective": objective,
            "ready_to_act": report.status == "valid" and not blocking,
            "status": "blocked" if blocking else report.status,
            "agent_context": context.to_dict(),
            "llm_prompt": self.llm_prompt(objective).content,
            "instructions": instructions,
            "compatibility": report.recommendations,
            "evidence": report.to_dict()["evidence"],
        }

    def plan(self, objective: str, token_budget: int = 2500) -> dict[str, Any]:
        """Create a build-aware execution plan for an external coding agent."""
        if not objective.strip():
            raise BuildAnchorError("plan objective must not be empty")
        # Use one shared report — no double-inspect
        report = self._inspect_cached()
        preflight = self.preflight(objective, token_budget, report=report)
        context = preflight["agent_context"]
        recommendations = preflight["compatibility"]
        blocked = not preflight["ready_to_act"]

        # Objective-aware compatibility check (includes mismatch detection)
        from .compatibility import compatibility_recommendations as _compat
        files = self._files()
        obj_recs = _compat(
            self.workspace, files, report.build_systems, report.facts,
            report.dependencies, lambda _: type("E", (), {"id": "x"})(),
            objective=objective,
        )
        all_recs = recommendations + [r for r in obj_recs if r not in recommendations]

        # Build a human-readable plan summary
        eco_names = [_ECOSYSTEM_LABELS.get(s, s) for s in report.build_systems] or ["unknown"]
        plan_summary_lines = [
            f"Objective: {objective}",
            f"Workspace: {', '.join(eco_names)}",
        ]
        if report.facts:
            for f in report.facts[:4]:
                plan_summary_lines.append(f"  {f.key} = {f.value}")
        mismatch_recs = [r for r in obj_recs if r.get("code") == "OBJECTIVE_ECOSYSTEM_MISMATCH"]
        if mismatch_recs:
            plan_summary_lines.append("MISMATCH DETECTED:")
            for m in mismatch_recs:
                plan_summary_lines.append(f"  {m['message']}")
                plan_summary_lines.append(f"  Action: {m['recommendation']}")

        steps = [
            {"id": "inspect", "action": "Review the authoritative Build Context Pack (llm_prompt injected above).", "status": "complete", "gate": True},
            {"id": "compatibility", "action": "Confirm package, runtime, and API compatibility before editing.", "status": "blocked" if blocked else "ready", "gate": True, "recommendations": all_recs},
            {"id": "act", "action": "Apply the requested change using the approved plan.", "status": "blocked" if blocked else "ready", "gate": False},
            {"id": "validate", "action": "Call build.validate_change against this plan baseline.", "status": "blocked" if blocked else "pending", "gate": True},
            {"id": "repair", "action": "If validation is invalid or inconclusive, follow build.repair_guidance and repeat validation.", "status": "pending", "gate": True},
        ]
        return {
            "schema_version": "v1",
            "plan_id": f"plan_{report.session_id}",
            "session_id": report.session_id,
            "objective": objective,
            "plan_summary": "\n".join(plan_summary_lines),
            "llm_prompt": preflight["llm_prompt"],
            "baseline": {"workspace_digest": report.workspace_digest},
            "status": "blocked" if blocked else "ready",
            "agent_context": context,
            "steps": steps,
            "validation_gates": ["compatibility", "validate"],
            "instructions": preflight["instructions"],
            "compatibility": all_recs,
            "evidence": preflight["evidence"],
        }

    def change_impact(self, baseline: str = "HEAD", report: BuildReport | None = None, staged: bool = False) -> dict:
        from .models import ChangeReport
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
        elif not change.baseline_resolved or not change.change_detected:
            status = "inconclusive"
        elif not execute:
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
    # Internal helpers (unchanged from original, except _inspect_cached)
    # ------------------------------------------------------------------

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
        from .models import Evidence
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
        from .models import Fact
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

    def _maven_facts(self, paths: list[Path], facts: list, evidence: list, dependencies: list) -> None:
        path, text = self._first_text([p for p in paths if p.name == "pom.xml"])
        if not path:
            return
        for label, pattern in (
            ("runtime.java", r"<(?:maven.compiler.release|maven.compiler.source|java.version)>([^<]+)"),
            ("framework.spring_boot", r"<spring-boot.version>([^<]+)"),
        ):
            m = re.search(pattern, text)
            if m:
                self._fact(label, m.group(1), path, evidence, facts)
        for m in re.finditer(r"<dependency>\s*<groupId>([^<]+)</groupId>\s*<artifactId>([^<]+)</artifactId>(?:\s*<version>([^<]+)</version>)?", text, re.S):
            version = m.group(3) or "managed"
            dependencies.append({"coordinate": f"{m.group(1)}:{m.group(2)}:{version}", "source": "declared", "status": "unresolved"})
        if re.search(r"javax\.persistence", text):
            self._fact("compatibility.persistence_namespace", "javax.persistence", path, evidence, facts)
        elif re.search(r"jakarta\.persistence", text):
            self._fact("compatibility.persistence_namespace", "jakarta.persistence", path, evidence, facts)

    def _gradle_modules(self, paths: list[Path]) -> list[str]:
        return [str(path.parent.relative_to(self.workspace)) or "." for path in paths if path.name.startswith("settings.gradle")]

    def _gradle_facts(self, paths, facts, evidence) -> None:
        path, text = self._first_text([p for p in paths if p.name.startswith("build.gradle")])
        if not path:
            return
        m = re.search(
            r'(?:sourceCompatibility|JavaVersion\.VERSION_)(?:\s*=|\s*\.equals\()?\s*[\x27"]?(\d+)[\x27"]?',
            text, re.I
        )
        if m:
            self._fact("runtime.java", m.group(1), path, evidence, facts)
        m = re.search(r'org\.springframework\.boot[^\n]*version[^\x27"]*[\x27"]([^\x27"]+)', text)
        if m:
            self._fact("framework.spring_boot", m.group(1), path, evidence, facts)
        if "javax.persistence" in text:
            self._fact("compatibility.persistence_namespace", "javax.persistence", path, evidence, facts)
        elif "jakarta.persistence" in text:
            self._fact("compatibility.persistence_namespace", "jakarta.persistence", path, evidence, facts)

    def _node_facts(self, paths: list[Path], facts: list, evidence: list, dependencies: list) -> None:
        path, text = self._first_text([p for p in paths if p.name == "package.json"])
        if not path:
            return
        try:
            package = json.loads(text)
        except json.JSONDecodeError:
            return
        for key in ("engines", "packageManager"):
            if key in package:
                self._fact(f"node.{key}", package[key], path, evidence, facts)
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            for name, version in package.get(section, {}).items():
                dependencies.append({"coordinate": f"{name}@{version}", "scope": section, "source": "declared", "status": "unresolved"})

    def _node_commands(self, paths: list[Path]) -> list[dict[str, Any]]:
        path, text = self._first_text([p for p in paths if p.name == "package.json"])
        if not path:
            return []
        try:
            scripts = json.loads(text).get("scripts", {})
        except json.JSONDecodeError:
            return []
        if "test" in scripts:
            return [self._command(["npm", "test"], "package.json test script", [path])]
        return [self._command(["npm", "run", "build"], "package.json build script", [path])] if "build" in scripts else []

    def _python_facts(self, paths: list[Path], facts: list, evidence: list, dependencies: list) -> None:
        path, text = self._first_text([p for p in paths if p.name == "pyproject.toml"])
        if not path:
            return
        m = re.search(r"requires-python\s*=\s*[\"']([^\"']+)", text)
        if m:
            self._fact("runtime.python", m.group(1), path, evidence, facts)
        for dep in re.findall(r"[\"']([A-Za-z0-9_.-]+(?:[<>=!~].*)?)[\"']", text):
            if any(op in dep for op in (">", "<", "=", "~")):
                dependencies.append({"coordinate": dep, "source": "declared", "status": "unresolved"})

    def _go_facts(self, paths: list[Path], facts: list, evidence: list, dependencies: list) -> None:
        path, text = self._first_text([p for p in paths if p.name == "go.mod"])
        if not path:
            return
        m = re.search(r"^go\s+([0-9.]+)", text, re.M)
        if m:
            self._fact("runtime.go", m.group(1), path, evidence, facts)
        for module, version in re.findall(r"^\s*(?:require\s+)?([\w./-]+)\s+v([0-9][^\s]+)", text, re.M):
            dependencies.append({"coordinate": f"{module}:v{version}", "source": "declared", "status": "unresolved"})

    def _rust_facts(self, paths: list[Path], facts: list, evidence: list, dependencies: list) -> None:
        path, text = self._first_text([p for p in paths if p.name == "Cargo.toml"])
        if not path:
            return
        m = re.search(r'edition\s*=\s*[\x27"]([ ^\x27"]+)', text)
        if m:
            self._fact("runtime.rust_edition", m.group(1), path, evidence, facts)
        if "[dependencies]" in text:
            section = text.split("[dependencies]", 1)[1].split("[", 1)[0]
            for name, value in re.findall(r"^([\w-]+)\s*=\s*(.+)$", section, re.M):
                dependencies.append({"coordinate": f"{name}:{value.strip()}", "source": "declared", "status": "unresolved"})

    def _dotnet_facts(self, paths: list[Path], facts: list, evidence: list) -> None:
        path, text = self._first_text([p for p in paths if p.suffix.lower() in {".csproj", ".fsproj", ".vbproj"}])
        if not path:
            return
        m = re.search(r"<TargetFrameworks?>([^<]+)", text)
        if m:
            self._fact("runtime.dotnet", m.group(1), path, evidence, facts)

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
            if not self._command_available(command):
                results.append({"command": command, "status": "unavailable", "exit_code": None, "duration_ms": 0, "stdout": "", "stderr": "executable or wrapper is not available in the workspace"})
                continue
            started = time.monotonic()
            try:
                completed = subprocess.run(command, cwd=self.workspace, capture_output=True, text=True, timeout=timeout_seconds, check=False, shell=False)
                results.append({"command": command, "status": "passed" if completed.returncode == 0 else "failed", "exit_code": completed.returncode, "duration_ms": int((time.monotonic() - started) * 1000), "stdout": completed.stdout[-12000:], "stderr": completed.stderr[-12000:]})
            except subprocess.TimeoutExpired as exc:
                results.append({"command": command, "status": "timed_out", "exit_code": None, "duration_ms": int((time.monotonic() - started) * 1000), "stdout": str(exc.stdout or "")[-12000:], "stderr": str(exc.stderr or "")[-12000:]})
            except OSError as exc:
                results.append({"command": command, "status": "failed", "exit_code": None, "duration_ms": int((time.monotonic() - started) * 1000), "stdout": "", "stderr": str(exc)})
        return {"mode": "probe", "commands_executed": [r["command"] for r in results if r["status"] != "unavailable"], "results": results, "network_used": "unknown", "limitations": ["Probe commands use shell=False, but selected build tools may resolve dependencies or run project-defined test code."]}


    # ------------------------------------------------------------------
    # find_package — installed + declared + usage intelligence
    # ------------------------------------------------------------------

    def find_package(
        self,
        package: str,
        show_usage: bool = True,
        installed_only: bool = False,
    ) -> dict:
        """Search for a package across all ecosystems in the workspace.

        Returns authoritative LLM guidance: installed version, declared
        version, import pattern used in this project, and a single
        guidance sentence telling the agent what to do next.
        """
        if not package or not package.strip():
            raise BuildAnchorError("package name must not be empty")
        name = package.strip()
        report = self._inspect_cached()
        results = []

        for system in report.build_systems:
            if system in {"node"}:
                results.extend(self._find_node(name, show_usage))
            elif system in {"python"}:
                results.extend(self._find_python(name, show_usage))
            elif system in {"maven", "gradle"}:
                results.extend(self._find_jvm(name, system, show_usage))
            elif system == "go":
                results.extend(self._find_go(name, show_usage))
            elif system == "rust":
                results.extend(self._find_rust(name, show_usage))

        if installed_only:
            results = [r for r in results if r.get("installed")]

        found = bool(results)
        guidance = self._package_guidance(name, results, report)
        llm_block = self._package_llm_block(name, results, guidance, report)
        tokens = _count_tokens(llm_block)

        return {
            "schema_version": "v1",
            "session_id": report.session_id,
            "package": name,
            "found": found,
            "results": results,
            "guidance": guidance,
            "llm_context": llm_block,
            "token_estimate": tokens,
        }

    def resolve_command(
        self,
        phase: str = "test",
        scope: str | None = None,
        changed: bool = False,
    ) -> dict[str, Any]:
        """Resolve the verified shell command for a build phase (test, build, lint, format, clean).

        Inspects package.json scripts, pyproject.toml, pom.xml, build.gradle, go.mod, Cargo.toml,
        and Makefile to determine the canonical command for the requested phase.

        Supports monorepo scoping via `scope` ('ui', 'backend', package name/path)
        and git change-impact targeting via `changed=True`.
        """
        workspace = self.workspace
        modules = self.discover_modules()
        is_monorepo = bool(
            len(modules) > 1
            or (len(modules) == 1 and modules[0].path != ".")
            or (workspace / "turbo.json").is_file()
            or (workspace / "nx.json").is_file()
            or (workspace / "pnpm-workspace.yaml").is_file()
        )

        phase_aliases = {
            "test": ["test", "test:unit", "test:all", "tests", "check"],
            "build": ["build", "compile", "dist", "bundle"],
            "lint": ["lint", "lint:fix", "eslint", "check", "typecheck"],
            "format": ["format", "fmt", "prettier", "prettier:write"],
            "clean": ["clean", "reset"],
        }
        aliases = phase_aliases.get(phase, [phase])

        targeted_modules: list[ModuleInfo] = []
        reason: str | None = None

        if changed:
            changed_files, _ = self._git_changed_files("HEAD")
            if changed_files:
                changed_paths = [item["path"] for item in changed_files]
                matched: list[ModuleInfo] = []
                for mod in modules:
                    mod_p = mod.path
                    if mod_p == ".":
                        continue
                    if any(cp == mod_p or cp.startswith(mod_p + "/") for cp in changed_paths):
                        if mod not in matched:
                            matched.append(mod)
                if matched:
                    targeted_modules = matched
                    reason = f"Targeted {len(targeted_modules)} module(s) affected by git changes: {', '.join(m.name for m in targeted_modules)}"
                else:
                    reason = "No changed files detected inside sub-modules; falling back to root command."
            else:
                reason = "No git modifications detected compared to baseline; falling back to root command."

        elif scope:
            scope_clean = scope.strip().lower()
            cat_map = {
                "ui": "ui", "frontend": "ui", "front-end": "ui", "web": "ui", "client": "ui",
                "backend": "backend", "be": "backend", "api": "backend", "server": "backend", "service": "backend",
                "shared": "shared", "common": "shared", "lib": "shared", "utils": "shared",
            }
            target_cat = cat_map.get(scope_clean)
            if target_cat:
                matched = [m for m in modules if m.category == target_cat]
                if matched:
                    targeted_modules = matched
                    reason = f"Targeted {len(targeted_modules)} module(s) matching '{scope}' ({target_cat}) category"

            if not targeted_modules:
                matched = [m for m in modules if m.name.lower() == scope_clean or m.path.lower() == scope_clean]
                if matched:
                    targeted_modules = matched
                    reason = f"Targeted module '{targeted_modules[0].name}' matching exact scope '{scope}'"

            if not targeted_modules:
                matched = [m for m in modules if scope_clean in m.name.lower() or scope_clean in m.path.lower()]
                if matched:
                    targeted_modules = matched
                    reason = f"Targeted {len(targeted_modules)} module(s) fuzzy-matching scope '{scope}'"

            if not targeted_modules:
                reason = f"No module matched scope '{scope}'; falling back to root command."

        def _wrap(res: dict[str, Any], mods: list[ModuleInfo] | None = None) -> dict[str, Any]:
            used_mods = mods if mods is not None else targeted_modules
            res["scope"] = scope
            res["changed"] = changed
            res["is_monorepo"] = is_monorepo
            res["targeted_modules"] = [m.to_dict() for m in used_mods]
            res["reason"] = reason or ("Targeted module command" if used_mods else ("Root workspace command" if is_monorepo else "Single-project command"))
            return res

        # If we have targeted modules, construct the scoped command
        if targeted_modules:
            # Check Turborepo
            if (workspace / "turbo.json").is_file():
                filters = " ".join(f"--filter={m.name}" for m in targeted_modules)
                runner = "turbo run"
                if (workspace / "pnpm-lock.yaml").is_file():
                    runner = "pnpm turbo run"
                elif (workspace / "bun.lockb").is_file() or (workspace / "bun.lock").is_file():
                    runner = "bun x turbo run"
                return _wrap({
                    "phase": phase,
                    "command": f"{runner} {phase} {filters}",
                    "source": "turbo.json",
                    "ecosystem": "node",
                })

            # Check Nx
            if (workspace / "nx.json").is_file():
                proj_list = ",".join(m.name for m in targeted_modules)
                cmd = f"npx nx run-many --target={phase} --projects={proj_list}" if len(targeted_modules) > 1 else f"npx nx {phase} {targeted_modules[0].name}"
                return _wrap({
                    "phase": phase,
                    "command": cmd,
                    "source": "nx.json",
                    "ecosystem": "node",
                })

            # Check pnpm workspace
            if (workspace / "pnpm-workspace.yaml").is_file() or (workspace / "pnpm-lock.yaml").is_file():
                filters = " ".join(f"--filter {m.name}" for m in targeted_modules)
                pnpm_action = "test" if phase == "test" else f"run {phase}"
                return _wrap({
                    "phase": phase,
                    "command": f"pnpm {filters} {pnpm_action}",
                    "source": "pnpm-workspace.yaml" if (workspace / "pnpm-workspace.yaml").is_file() else "pnpm-lock.yaml",
                    "ecosystem": "node",
                })

            # Check npm workspace
            root_pkg = workspace / "package.json"
            if root_pkg.is_file():
                try:
                    r_data = json.loads(self._read(root_pkg))
                    if r_data.get("workspaces"):
                        ws_flags = " ".join(f"--workspace {m.name}" for m in targeted_modules)
                        npm_action = "test" if phase == "test" else f"run {phase}"
                        return _wrap({
                            "phase": phase,
                            "command": f"npm {npm_action} {ws_flags}",
                            "source": "package.json [workspaces]",
                            "ecosystem": "node",
                        })
                except Exception:
                    pass

            # Check Cargo workspace
            if (workspace / "Cargo.toml").is_file() and all(m.ecosystem == "rust" for m in targeted_modules):
                p_flags = " ".join(f"-p {m.name}" for m in targeted_modules)
                cargo_phase = "test" if phase == "test" else ("build" if phase == "build" else "clippy" if phase == "lint" else "fmt")
                return _wrap({
                    "phase": phase,
                    "command": f"cargo {cargo_phase} {p_flags}",
                    "source": "Cargo.toml [workspace]",
                    "ecosystem": "rust",
                })

            # Check Maven multi-module
            if (workspace / "pom.xml").is_file() and all(m.ecosystem == "maven" for m in targeted_modules):
                wrapper = "./mvnw" if (workspace / "mvnw").is_file() else "mvn"
                mvn_phase = "test" if phase == "test" else ("package" if phase == "build" else "clean")
                pl_arg = ",".join(m.path for m in targeted_modules)
                return _wrap({
                    "phase": phase,
                    "command": f"{wrapper} {mvn_phase} -pl {pl_arg}",
                    "source": "pom.xml [modules]",
                    "ecosystem": "maven",
                })

            # Check Gradle multi-project
            if any((workspace / g).is_file() for g in ("settings.gradle", "settings.gradle.kts")) and all(m.ecosystem == "gradle" for m in targeted_modules):
                wrapper = "./gradlew" if (workspace / "gradlew").is_file() else "gradle"
                tasks = " ".join(f":{m.name.replace('/', ':')}:{phase}" for m in targeted_modules)
                return _wrap({
                    "phase": phase,
                    "command": f"{wrapper} {tasks}",
                    "source": "settings.gradle",
                    "ecosystem": "gradle",
                })

            # Check Go workspace
            if (workspace / "go.work").is_file() and all(m.ecosystem == "go" for m in targeted_modules):
                cmds = [f"go {phase} ./{m.path}/..." for m in targeted_modules]
                return _wrap({
                    "phase": phase,
                    "command": " && ".join(cmds),
                    "source": "go.work",
                    "ecosystem": "go",
                })

            # Fallback / Polyglot targeted execution per module
            cmds = []
            for m in targeted_modules:
                if m.ecosystem == "node":
                    if phase == "test":
                        cmds.append(f"npm --prefix {m.path} test")
                    else:
                        cmds.append(f"npm --prefix {m.path} run {phase}")
                elif m.ecosystem == "python":
                    if phase == "test":
                        cmds.append(f"python -m pytest {m.path}")
                    else:
                        cmds.append(f"python -m build {m.path}")
                elif m.ecosystem == "maven":
                    wrapper = "./mvnw" if (workspace / "mvnw").is_file() else "mvn"
                    if (workspace / "pom.xml").is_file():
                        cmds.append(f"{wrapper} {phase} -pl {m.path}")
                    else:
                        cmds.append(f"{wrapper} {phase} -f {m.path}/pom.xml")
                elif m.ecosystem == "gradle":
                    wrapper = "./gradlew" if (workspace / "gradlew").is_file() else "gradle"
                    cmds.append(f"{wrapper} :{m.name}:{phase}")
                elif m.ecosystem == "rust":
                    cmds.append(f"cargo {phase} -p {m.name}")
                elif m.ecosystem == "go":
                    cmds.append(f"go {phase} ./{m.path}/...")
                elif m.test_command and phase == "test":
                    cmds.append(m.test_command)
                else:
                    cmds.append(f"cd {m.path} && npm test")

            return _wrap({
                "phase": phase,
                "command": " && ".join(cmds),
                "source": "monorepo module convention",
                "ecosystem": targeted_modules[0].ecosystem if len(targeted_modules) == 1 else "polyglot",
            })

        # Fallback to root workspace resolution
        result = {"phase": phase, "command": None, "source": None, "ecosystem": None}

        # Node.js: check package.json scripts
        pkg_json = workspace / "package.json"
        if pkg_json.is_file():
            try:
                pkg = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
                scripts = pkg.get("scripts", {})
                runner = "npm run"
                if (workspace / "pnpm-lock.yaml").is_file():
                    runner = "pnpm run"
                elif (workspace / "bun.lockb").is_file() or (workspace / "bun.lock").is_file():
                    runner = "bun run"
                elif (workspace / "yarn.lock").is_file():
                    runner = "yarn"
                for alias in aliases:
                    if alias in scripts:
                        result["command"] = f"{runner} {alias}"
                        result["source"] = "package.json"
                        result["ecosystem"] = "node"
                        return _wrap(result, [])
            except (json.JSONDecodeError, OSError):
                pass

        # Python: check pyproject.toml for test/build commands
        pyproject = workspace / "pyproject.toml"
        if pyproject.is_file():
            text = pyproject.read_text(encoding="utf-8", errors="replace")
            if phase == "test":
                if "[tool.pytest" in text:
                    runner = "python -m pytest"
                    if (workspace / ".venv").is_dir():
                        runner = ".venv/bin/python -m pytest"
                    elif (workspace / "venv").is_dir():
                        runner = "venv/bin/python -m pytest"
                    result["command"] = runner
                    result["source"] = "pyproject.toml [tool.pytest]"
                    result["ecosystem"] = "python"
                    return _wrap(result, [])
            if phase == "build":
                result["command"] = "python -m build"
                result["source"] = "pyproject.toml"
                result["ecosystem"] = "python"
                return _wrap(result, [])
            if phase == "lint":
                if "ruff" in text:
                    result["command"] = "ruff check ."
                    result["source"] = "pyproject.toml [ruff]"
                    result["ecosystem"] = "python"
                    return _wrap(result, [])
            if phase == "format":
                if "ruff" in text:
                    result["command"] = "ruff format ."
                    result["source"] = "pyproject.toml [ruff]"
                    result["ecosystem"] = "python"
                    return _wrap(result, [])

        # Java/Maven
        pom = workspace / "pom.xml"
        if pom.is_file():
            maven_phases = {"test": "mvn test", "build": "mvn package", "clean": "mvn clean", "lint": "mvn checkstyle:check"}
            if phase in maven_phases:
                result["command"] = maven_phases[phase]
                result["source"] = "pom.xml"
                result["ecosystem"] = "maven"
                return _wrap(result, [])

        # Java/Gradle
        for gradle_file in ("build.gradle", "build.gradle.kts"):
            if (workspace / gradle_file).is_file():
                wrapper = "./gradlew" if (workspace / "gradlew").is_file() else "gradle"
                gradle_phases = {"test": f"{wrapper} test", "build": f"{wrapper} build", "clean": f"{wrapper} clean", "lint": f"{wrapper} check"}
                if phase in gradle_phases:
                    result["command"] = gradle_phases[phase]
                    result["source"] = gradle_file
                    result["ecosystem"] = "gradle"
                    return _wrap(result, [])

        # Go
        go_mod = workspace / "go.mod"
        if go_mod.is_file():
            go_phases = {"test": "go test ./...", "build": "go build ./...", "lint": "golangci-lint run", "format": "gofmt -w .", "clean": "go clean"}
            if phase in go_phases:
                result["command"] = go_phases[phase]
                result["source"] = "go.mod"
                result["ecosystem"] = "go"
                return _wrap(result, [])

        # Rust
        cargo_toml = workspace / "Cargo.toml"
        if cargo_toml.is_file():
            cargo_phases = {"test": "cargo test", "build": "cargo build", "lint": "cargo clippy", "format": "cargo fmt", "clean": "cargo clean"}
            if phase in cargo_phases:
                result["command"] = cargo_phases[phase]
                result["source"] = "Cargo.toml"
                result["ecosystem"] = "rust"
                return _wrap(result, [])

        # Makefile fallback
        makefile = workspace / "Makefile"
        if makefile.is_file():
            text = makefile.read_text(encoding="utf-8", errors="replace")
            for alias in aliases:
                if f"\n{alias}:" in text or text.startswith(f"{alias}:"):
                    result["command"] = f"make {alias}"
                    result["source"] = "Makefile"
                    result["ecosystem"] = "make"
                    return _wrap(result, [])

        # Root fallbacks for monorepo orchestrators if no explicit script matched
        if (workspace / "turbo.json").is_file():
            result["command"] = f"turbo run {phase}"
            result["source"] = "turbo.json"
            result["ecosystem"] = "node"
        elif (workspace / "pnpm-workspace.yaml").is_file():
            result["command"] = f"pnpm -r run {phase}" if phase != "test" else "pnpm -r test"
            result["source"] = "pnpm-workspace.yaml"
            result["ecosystem"] = "node"

        return _wrap(result, [])

    def _find_node(self, name: str, show_usage: bool) -> list[dict]:
        nm = self.workspace / "node_modules" / name
        pkg_json = nm / "package.json"
        installed_version = None
        exports_hint: list[str] = []
        if pkg_json.is_file():
            try:
                meta = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
                installed_version = meta.get("version")
                # Derive import hints from exports/main/module fields
                if "exports" in meta:
                    exports_hint.append(f"import ... from '{name}'")
                if meta.get("module"):
                    exports_hint.append(f"import ... from '{name}'  (ESM)")
                if meta.get("main"):
                    exports_hint.append(f"const x = require('{name}')")
                if not exports_hint:
                    exports_hint.append(f"import ... from '{name}'")
            except (json.JSONDecodeError, OSError):
                pass

        # Declared version from package.json
        declared_version = None
        declared_scope = None
        root_pkg = self.workspace / "package.json"
        if root_pkg.is_file():
            try:
                root = json.loads(root_pkg.read_text(encoding="utf-8", errors="replace"))
                for scope in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
                    if name in root.get(scope, {}):
                        declared_version = root[scope][name]
                        declared_scope = scope
                        break
            except (json.JSONDecodeError, OSError):
                pass

        usage = self._grep_usage(name, {".js", ".ts", ".mjs", ".cjs", ".jsx", ".tsx"}) if show_usage else []

        if installed_version is None and declared_version is None and not usage:
            return []

        return [{
            "ecosystem": "node",
            "package": name,
            "installed": installed_version is not None,
            "installed_version": installed_version,
            "declared_version": declared_version,
            "declared_scope": declared_scope,
            "install_path": str(nm.relative_to(self.workspace)) if nm.is_dir() else None,
            "import_patterns": exports_hint,
            "usage": usage[:5],
        }]

    def _find_python(self, name: str, show_usage: bool) -> list[dict]:
        # Normalise: pip normalises hyphens/underscores/dots
        norm = re.sub(r"[-_.]+", "-", name).lower()
        norm_under = norm.replace("-", "_")

        installed_version = None
        install_path = None

        # Search common venv locations
        for venv_dir in ("venv", ".venv", "env", ".env"):
            candidate_roots = [
                self.workspace / venv_dir / "lib",
            ]
            for lib_root in candidate_roots:
                if not lib_root.is_dir():
                    continue
                for py_dir in lib_root.iterdir():
                    site = py_dir / "site-packages"
                    if not site.is_dir():
                        continue
                    for entry in site.iterdir():
                        if entry.suffix == ".dist-info" and re.sub(r"[-_.]+", "-", entry.stem.split("-")[0]).lower() == norm:
                            meta_file = entry / "METADATA"
                            if meta_file.is_file():
                                for line in meta_file.read_text(encoding="utf-8", errors="replace").splitlines()[:20]:
                                    if line.startswith("Version:"):
                                        installed_version = line.split(":", 1)[1].strip()
                                install_path = str(site.relative_to(self.workspace))
                            break

        # Declared version from pyproject.toml / requirements.txt
        declared_version = None
        declared_file = None
        for req_file in ("pyproject.toml", "requirements.txt", "requirements-dev.txt"):
            path = self.workspace / req_file
            if not path.is_file():
                continue
            text = self._read(path)
            match = re.search(
                r'["\']?' + re.escape(norm) + r'["\']?\s*([><=!~^][^\s,;\]]+)?',
                text, re.I
            )
            if match:
                declared_version = match.group(1) or "any"
                declared_file = req_file
                break

        import_name = norm_under
        usage = self._grep_usage(import_name, {".py"}) if show_usage else []
        import_patterns = [
            f"import {import_name}",
            f"from {import_name} import ...",
        ]

        if installed_version is None and declared_version is None and not usage:
            return []

        return [{
            "ecosystem": "python",
            "package": name,
            "installed": installed_version is not None,
            "installed_version": installed_version,
            "declared_version": declared_version,
            "declared_file": declared_file,
            "install_path": install_path,
            "import_patterns": import_patterns,
            "usage": usage[:5],
        }]

    def _find_jvm(self, name: str, system: str, show_usage: bool) -> list[dict]:
        # name can be "groupId:artifactId" or just "artifactId"
        parts = name.split(":")
        artifact_id = parts[-1].lower()
        group_id = parts[0].lower() if len(parts) > 1 else None

        declared_version = None
        declared_file = None
        installed_version = None
        install_path = None

        # Check pom.xml / build.gradle for declared version
        for marker in ("pom.xml", "build.gradle", "build.gradle.kts"):
            path = self.workspace / marker
            if not path.is_file():
                continue
            text = self._read(path)
            match = re.search(re.escape(artifact_id) + r'[^\n]*?[>"\']([0-9][^<"\s]+)', text, re.I)
            if match:
                declared_version = match.group(1)
                declared_file = marker
            # Also check for group:artifact pattern
            if group_id:
                gid_match = re.search(
                    re.escape(group_id) + r"[^\n]*" + re.escape(artifact_id),
                    text, re.I
                )
                if gid_match and not declared_file:
                    declared_file = marker

        # Check ~/.m2/repository
        m2 = Path.home() / ".m2" / "repository"
        if m2.is_dir() and group_id:
            group_path = m2 / group_id.replace(".", "/")
            artifact_path = group_path / artifact_id
            if artifact_path.is_dir():
                versions = [d.name for d in artifact_path.iterdir() if d.is_dir() and not d.name.startswith(".")]
                if versions:
                    installed_version = sorted(versions)[-1]
                    install_path = str(artifact_path / installed_version)

        import_prefix = group_id or artifact_id.replace("-", ".")
        usage = self._grep_usage(import_prefix, {".java", ".kt", ".scala", ".groovy"}) if show_usage else []
        import_patterns = [f"import {import_prefix}.*;"]

        if installed_version is None and declared_version is None and not usage:
            return []

        return [{
            "ecosystem": system,
            "package": name,
            "installed": installed_version is not None,
            "installed_version": installed_version,
            "declared_version": declared_version,
            "declared_file": declared_file,
            "install_path": install_path,
            "import_patterns": import_patterns,
            "usage": usage[:5],
        }]

    def _find_go(self, name: str, show_usage: bool) -> list[dict]:
        declared_version = None
        go_mod = self.workspace / "go.mod"
        if go_mod.is_file():
            text = self._read(go_mod)
            match = re.search(r"\b" + re.escape(name) + r"\s+v([\d.]+)", text)
            if match:
                declared_version = match.group(1)

        vendor = self.workspace / "vendor" / name
        installed = vendor.is_dir()
        usage = self._grep_usage(name, {".go"}) if show_usage else []

        if not installed and declared_version is None and not usage:
            return []

        return [{
            "ecosystem": "go",
            "package": name,
            "installed": installed,
            "installed_version": declared_version,
            "declared_version": declared_version,
            "install_path": str(vendor.relative_to(self.workspace)) if installed else None,
            "import_patterns": [f"import \"{name}\""],
            "usage": usage[:5],
        }]

    def _find_rust(self, name: str, show_usage: bool) -> list[dict]:
        declared_version = None
        cargo_toml = self.workspace / "Cargo.toml"
        if cargo_toml.is_file():
            text = self._read(cargo_toml)
            match = re.search(r'^' + re.escape(name) + r'\s*=\s*["\']?([\d.^~*]+)', text, re.M)
            if match:
                declared_version = match.group(1)

        # Check Cargo.lock for resolved version
        installed_version = None
        cargo_lock = self.workspace / "Cargo.lock"
        if cargo_lock.is_file():
            text = self._read(cargo_lock)
            match = re.search(
                r'\[\[package\]\][^[]*?name = "' + re.escape(name) + r'"[^[]*?version = "([^"]+)"',
                text, re.S
            )
            if match:
                installed_version = match.group(1)

        usage = self._grep_usage(name, {".rs"}) if show_usage else []
        norm = name.replace("-", "_")
        import_patterns = [f"use {norm}::", f"extern crate {norm};"]

        if installed_version is None and declared_version is None and not usage:
            return []

        return [{
            "ecosystem": "rust",
            "package": name,
            "installed": installed_version is not None,
            "installed_version": installed_version,
            "declared_version": declared_version,
            "install_path": None,
            "import_patterns": import_patterns,
            "usage": usage[:5],
        }]

    def _grep_usage(self, name: str, extensions: set[str]) -> list[dict]:
        hits = []
        ignored = {"node_modules", ".venv", "venv", "target", "build", "dist", "__pycache__"}
        pattern = re.compile(re.escape(name), re.I)
        for path in self.workspace.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in extensions:
                continue
            if any(p in ignored for p in path.parts):
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                if pattern.search(line) and ("import" in line or "require" in line or "use " in line):
                    hits.append({
                        "file": str(path.relative_to(self.workspace)),
                        "line": i,
                        "text": line.strip()[:120],
                    })
                    if len(hits) >= 10:
                        return hits
        return hits

    def _package_guidance(self, name: str, results: list[dict], report) -> str:
        if not results:
            systems = ", ".join(report.build_systems) or "unknown"
            return f"{name} was not found in any detected build system ({systems}). Add it as a dependency before using it."
        r = results[0]
        eco = r.get("ecosystem", "")
        installed = r.get("installed")
        declared = r.get("declared_version")
        iv = r.get("installed_version")
        usage = r.get("usage", [])

        if installed and declared:
            pat = r.get("import_patterns", [])
            pattern_hint = f" Use: {pat[0]}" if pat else ""
            usage_hint = f" Already imported in {len(usage)} file(s) — match the existing pattern." if usage else ""
            return f"{name} is installed (v{iv}) and declared ({declared}).{pattern_hint}{usage_hint} Do not add it again."
        if declared and not installed:
            return f"{name} is declared ({declared}) but not installed. Run the install command for this ecosystem before using it."
        if installed and not declared:
            return f"{name} v{iv} is present in {eco} but not declared in the manifest. Add it as an explicit dependency."
        return f"{name} is not installed or declared. Add it as a dependency first."

    def _package_llm_block(self, name: str, results: list[dict], guidance: str, report) -> str:
        lines = [f"# BuildAnchor package: {name}"]
        if not results:
            lines.append(f"Status: NOT FOUND in {', '.join(report.build_systems) or 'unknown'}")
            lines.append(f"Action: {guidance}")
            return "\n".join(lines)
        for r in results:
            eco = r.get("ecosystem", "?")
            lines.append(f"Ecosystem: {eco}")
            if r.get("installed"):
                lines.append(f"Status: installed  version={r.get('installed_version')}")
            else:
                lines.append("Status: NOT installed")
            if r.get("declared_version"):
                scope = r.get("declared_scope") or r.get("declared_file") or "manifest"
                lines.append(f"Declared: {r['declared_version']} [{scope}]")
            if r.get("install_path"):
                lines.append(f"Location: {r['install_path']}")
            pats = r.get("import_patterns", [])
            if pats:
                lines.append("Import pattern:")
                for p in pats[:2]:
                    lines.append(f"  {p}")
            usage = r.get("usage", [])
            if usage:
                lines.append(f"Used in {len(usage)} file(s):")
                for u in usage[:3]:
                    lines.append(f"  {u['file']}:{u['line']}  {u['text']}")
        lines.append(f"LLM guidance: {guidance}")
        return "\n".join(lines)

    def _command_available(self, command: list[str]) -> bool:
        executable = command[0]
        if executable.startswith("./"):
            path = (self.workspace / executable[2:]).resolve()
            try:
                path.relative_to(self.workspace)
            except ValueError:
                return False
            return path.is_file() and os.access(path, os.X_OK)
        return shutil.which(executable) is not None
