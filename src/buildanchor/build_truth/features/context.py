# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from typing import Any

from ...compatibility import compatibility_recommendations
from ...models import BuildReport, ContextPack, PromptBlock
from ..core.build_systems import ECOSYSTEM_LABELS
from ..core.token_estimation import cost_tier, count_tokens
from ..core.errors import BuildAnchorError


class ContextMixin:
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
            eco = " | ".join(ECOSYSTEM_LABELS.get(s, s) for s in report.build_systems)
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
            from ...compatibility import compatibility_recommendations as _compat
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
        tokens = count_tokens(content)
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
                "tokens": count_tokens(prompt_content),
                "description": "Compact ready-to-inject system prompt block. Use this first.",
                "recommended_for": "System prompt injection before any build-affecting action.",
            },
            "build.context": {
                "tokens": count_tokens(ctx_json),
                "description": "Compact context pack with constraints and validation commands.",
                "recommended_for": "Fetching build constraints before editing files.",
            },
            "build.preflight": {
                "tokens": count_tokens(preflight_json),
                "description": "Pre-change check with blocking compatibility errors.",
                "recommended_for": "Mandatory gate before making any dependency or build file change.",
            },
            "build.inspect": {
                "tokens": count_tokens(report_json),
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
            ECOSYSTEM_LABELS.get(s, s) for s in report.build_systems
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
            cost_tier=cost_tier(tokens),
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
            from ...compatibility import compatibility_recommendations as _compat
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
        from ...compatibility import compatibility_recommendations as _compat
        files = self._files()
        obj_recs = _compat(
            self.workspace, files, report.build_systems, report.facts,
            report.dependencies, lambda _: type("E", (), {"id": "x"})(),
            objective=objective,
        )
        all_recs = recommendations + [r for r in obj_recs if r not in recommendations]

        # Build a human-readable plan summary
        eco_names = [ECOSYSTEM_LABELS.get(s, s) for s in report.build_systems] or ["unknown"]
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
