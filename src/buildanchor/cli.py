# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import json
import sys

from .engine import BuildAnchor, BuildAnchorError
from .transports import MCPServer, serve_http


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="buildanchor",
        description="BuildAnchor: local-first Build Truth for AI coding agents.",
    )
    parser.add_argument(
        "command",
        choices=[
            "llm-prompt", "token-estimate",
            "inspect", "context", "preflight", "plan",
            "change-impact", "validate-change", "repair", "compatibility",
            "explain-dependency", "find", "cmd", "modules", "init",
            "mcp", "serve",
        ],
    )
    parser.add_argument(
        "phase_arg",
        nargs="?",
        default=None,
        help="Optional phase positional argument for cmd command (e.g. 'buildanchor cmd test').",
    )
    # Core arguments
    parser.add_argument("--workspace", default=".")
    parser.add_argument("--allow-root")
    parser.add_argument(
        "--format",
        choices=["json", "text", "markdown", "sarif", "llm"],
        default="text",
        help="Output format. Use --format llm to strip noise for LLM injection.",
    )
    parser.add_argument("--baseline", default="HEAD")
    parser.add_argument("--token-budget", type=int, default=2500)
    parser.add_argument("--dependency", default="")
    parser.add_argument("--objective", default="")
    parser.add_argument("--stdio", action="store_true")
    parser.add_argument("--listen", default="127.0.0.1:8787")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--timeout", type=int, default=300)

    # find command arguments
    parser.add_argument("--package", default="", help="Package name to search for (used with find command).")
    parser.add_argument("--installed-only", action="store_true", help="Only return results if the package is actually installed.")
    parser.add_argument("--no-show-usage", action="store_true", help="Disable source file usage scanning for the find command.")

    # cmd command arguments
    parser.add_argument("--phase", default="test", choices=["test", "build", "lint", "format", "clean"],
                        help="Build phase to resolve (used with cmd command).")
    parser.add_argument("--scope", default=None,
                        help="Target category ('ui', 'backend', 'shared') or specific package name/path in monorepos.")
    parser.add_argument("--changed", action="store_true",
                        help="Target only packages containing modified files according to git diff.")
    parser.add_argument("--list", action="store_true",
                        help="List discovered monorepo modules and available scopes.")

    # Output control flags — works with any AI agent or CI pipeline
    parser.add_argument("--quiet", action="store_true", help="Suppress evidence, digests, and limitations.")
    parser.add_argument("--agent", action="store_true",
                        help="Output only the raw LLM prompt block. Zero noise. Works with Claude, GPT, Gemini, Cursor, Aider, Windsurf, Ollama, or any agent.")
    parser.add_argument("--ci", action="store_true",
                        help="CI mode: quiet + only-errors + exit-on-mismatch + GitHub Actions annotations.")

    # Gate flags
    parser.add_argument("--exit-on-mismatch", action="store_true",
                        help="Exit with code 3 if OBJECTIVE_ECOSYSTEM_MISMATCH is detected.")
    parser.add_argument("--assert-ecosystem", default="",
                        help="Assert that the workspace matches the expected ecosystem. Exit 3 on mismatch.")
    parser.add_argument("--only-errors", action="store_true",
                        help="Remove warnings, show only blocking errors.")
    parser.add_argument("--explain", action="store_true",
                        help="Add a plain-English 'why:' explanation to each finding.")
    parser.add_argument("--staged", action="store_true",
                        help="Only inspect files staged in git (for pre-commit hooks).")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    # --ci implies --quiet --only-errors --exit-on-mismatch
    if args.ci:
        args.quiet = True
        args.only_errors = True
        args.exit_on_mismatch = True

    try:
        if args.command == "mcp":
            MCPServer(args.allow_root or args.workspace).run(sys.stdin, sys.stdout)
            return 0
        if args.command == "serve":
            host, port = args.listen.rsplit(":", 1)
            serve_http(args.allow_root or args.workspace, host, int(port))
            return 0

        engine = BuildAnchor(args.workspace, args.allow_root)

        # --assert-ecosystem: check before doing anything else
        if args.assert_ecosystem:
            report = engine._inspect_cached()
            expected = args.assert_ecosystem.lower()
            detected = [s.lower() for s in report.build_systems]
            if expected not in detected:
                msg = f"assert: FAIL — expected {expected}, got {', '.join(detected) or 'none'}"
                if args.ci:
                    print(f"::error ::{msg}")
                else:
                    print(msg, file=sys.stderr)
                return 3

        # --agent: output just the raw LLM prompt block and exit
        if args.agent:
            block = engine.llm_prompt(args.objective)
            print(block.content)
            return 0

        if args.command == "llm-prompt":
            block = engine.llm_prompt(args.objective)
            if args.format == "json":
                print(json.dumps(block.to_dict(), indent=2, sort_keys=True))
            else:
                print(block.content)
                if args.format == "text" and not args.quiet:
                    print()
                    print("# token_estimate=" + str(block.token_estimate) + "  format=" + block.format)
            return 0

        if args.command == "token-estimate":
            result = engine.token_estimate()
            if args.format == "json":
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                _print_token_estimate(result)
            return 0

        if args.command == "find":
            if not args.package:
                print('{"status":"blocked","error":"--package is required for the find command"}', file=sys.stderr)
                return 4
            result = engine.find_package(
                args.package,
                show_usage=not args.no_show_usage,
                installed_only=args.installed_only,
            )
            if args.format == "json":
                print(json.dumps(result, indent=2, sort_keys=True))
            elif args.format == "llm":
                print(result.get("llm_context", ""))
            else:
                _print_find_result(result, args.explain)
            return 0 if result.get("found") else 1

        if args.command == "cmd":
            if getattr(args, "list", False):
                modules = engine.discover_modules()
                if args.format == "json":
                    print(json.dumps([m.to_dict() for m in modules], indent=2))
                else:
                    if not modules:
                        print("No monorepo modules detected (single-project repository).")
                    else:
                        print(f"Monorepo modules ({len(modules)} found):")
                        for m in modules:
                            cat_upper = m.category.upper()
                            t_cmd = f" | test: {m.test_command}" if m.test_command else ""
                            b_cmd = f" | build: {m.build_command}" if m.build_command else ""
                            print(f"  - {m.name} ({m.path}) [{cat_upper}]{t_cmd}{b_cmd}")
                        print("\nAvailable scopes:")
                        print("  --scope ui        (Frontend/Web/Client packages)")
                        print("  --scope backend   (API/Server/Service packages)")
                        print("  --scope <name>    (Target specific package by name or path)")
                        print("  --changed         (Target packages with git modifications)")
                return 0

            phase = args.phase_arg or args.phase or "test"
            resolved = engine.resolve_command(phase, scope=args.scope, changed=args.changed)
            if args.format == "json":
                print(json.dumps(resolved, indent=2, sort_keys=True))
            else:
                if resolved.get("command"):
                    if args.explain:
                        print(f"command: {resolved['command']}")
                        print(f"phase: {resolved.get('phase')}")
                        if resolved.get("scope"):
                            print(f"scope: {resolved.get('scope')}")
                        if resolved.get("changed"):
                            print("changed: True")
                        if resolved.get("reason"):
                            print(f"reason: {resolved.get('reason')}")
                        if resolved.get("targeted_modules"):
                            targets = ", ".join(f"{m['name']} ({m['category']})" for m in resolved["targeted_modules"])
                            print(f"targeted: {targets}")
                    else:
                        print(resolved["command"])
                else:
                    print(f"# No {phase} command detected", file=sys.stderr)
            return 0 if resolved.get("command") else 2

        if args.command == "modules":
            modules = engine.discover_modules()
            if args.format == "json":
                print(json.dumps([m.to_dict() for m in modules], indent=2))
            else:
                if not modules:
                    print("No monorepo modules detected (single-project repository).")
                else:
                    print(f"Monorepo modules ({len(modules)} found):")
                    for m in modules:
                        cat_upper = m.category.upper()
                        t_cmd = f" | test: {m.test_command}" if m.test_command else ""
                        b_cmd = f" | build: {m.build_command}" if m.build_command else ""
                        print(f"  - {m.name} ({m.path}) [{cat_upper}]{t_cmd}{b_cmd}")
                    print("\nRun targeted tests with:")
                    print("  buildanchor cmd test --scope ui")
                    print("  buildanchor cmd test --scope backend")
                    print("  buildanchor cmd test --scope <name>")
                    print("  buildanchor cmd test --changed")
            return 0

        if args.command == "init":
            report = engine._inspect_cached()
            test_cmd = engine.resolve_command("test").get("command")
            build_cmd = engine.resolve_command("build").get("command")
            lint_cmd = engine.resolve_command("lint").get("command")
            format_cmd = engine.resolve_command("format").get("command")

            # 1. Write .buildanchor.json
            config_path = engine.workspace / ".buildanchor.json"
            config_data = {
                "version": "1.0",
                "build_systems": report.build_systems,
                "languages": report.languages,
                "verified_commands": {
                    "test": test_cmd,
                    "build": build_cmd,
                    "lint": lint_cmd,
                    "format": format_cmd,
                },
                "policy": "allow",
            }
            config_path.write_text(json.dumps(config_data, indent=2) + "\n", encoding="utf-8")

            # 2. Append/create AGENT.md or CLAUDE.md
            rule_block = (
                "\n<!-- BuildAnchor Rules Block -->\n"
                "## Build Truth & Verification (BuildAnchor)\n"
                "Always run buildanchor preflight before modifying configuration or installing dependencies:\n"
                "```bash\n"
                "buildanchor preflight --agent\n"
                "```\n"
                "To search for installed packages and import patterns:\n"
                "```bash\n"
                "buildanchor find --package <package-name>\n"
                "```\n"
                "To run verified tests:\n"
                f"```bash\n"
                f"{test_cmd or 'buildanchor cmd test'}\n"
                "```\n"
                "<!-- End BuildAnchor Rules Block -->\n"
            )

            rules_file = engine.workspace / "CLAUDE.md"
            if not rules_file.is_file():
                rules_file = engine.workspace / "AGENT.md"

            if rules_file.is_file():
                existing = rules_file.read_text(encoding="utf-8", errors="replace")
                if "<!-- BuildAnchor Rules Block -->" not in existing:
                    rules_file.write_text(existing + rule_block, encoding="utf-8")
            else:
                rules_file.write_text("# Agent Guidelines\n" + rule_block, encoding="utf-8")

            badge = "[![BuildAnchor Verified](https://img.shields.io/badge/BuildAnchor-Protected-blue)](https://github.com/tensilestream/buildanchor)"

            if args.format == "json":
                print(json.dumps({
                    "status": "initialized",
                    "workspace": str(engine.workspace),
                    "config_file": str(config_path.relative_to(engine.workspace)),
                    "rules_file": str(rules_file.relative_to(engine.workspace)),
                    "verified_commands": config_data["verified_commands"],
                    "badge": badge,
                }, indent=2))
            else:
                print(f"BuildAnchor initialized for {engine.workspace.name or engine.workspace}")
                print(f"  Config: {config_path.name}")
                print(f"  Rules:  {rules_file.name}")
                if test_cmd:
                    print(f"  Test:   {test_cmd}")
                if build_cmd:
                    print(f"  Build:  {build_cmd}")
                print()
                print("README badge snippet:")
                print(f"  {badge}")
            return 0

        report = engine._inspect_cached()
        if args.command == "inspect":
            result = report.to_dict()
        elif args.command == "context":
            result = engine.context(report, args.token_budget).to_dict()
        elif args.command == "change-impact":
            result = engine.change_impact(args.baseline, report, staged=args.staged).to_dict()
        elif args.command == "validate-change":
            result = engine.validate_change(args.baseline, report, execute=args.execute, timeout_seconds=args.timeout, staged=args.staged)
        elif args.command == "compatibility":
            result = {
                "schema_version": "v1",
                "session_id": report.session_id,
                "status": "invalid" if any(r["severity"] == "error" for r in report.recommendations) else "valid",
                "recommendations": report.recommendations,
            }
        elif args.command == "preflight":
            result = engine.preflight(args.objective, token_budget=args.token_budget, report=report)
        elif args.command == "plan":
            result = engine.plan(args.objective, args.token_budget)
        elif args.command == "explain-dependency":
            matches = [item for item in report.dependencies if args.dependency.lower() in str(item.get("coordinate", "")).lower()]
            result = {"schema_version": "v1", "session_id": report.session_id, "dependency": args.dependency, "matches": matches, "status": "valid" if matches else "unknown"}
        else:
            result = engine.repair_guidance(report=report)

        # --only-errors: filter warnings from recommendations
        if args.only_errors:
            result = _filter_only_errors(result)

        # --exit-on-mismatch: check for OBJECTIVE_ECOSYSTEM_MISMATCH
        if args.exit_on_mismatch:
            if _has_mismatch(result):
                if args.ci:
                    for rec in result.get("recommendations", []) + [s for step in result.get("steps", []) for s in step.get("recommendations", [])]:
                        if "MISMATCH" in rec.get("code", ""):
                            msg = rec.get("message", "Ecosystem mismatch detected")
                            print(f"::error ::{msg}")
                elif not args.quiet:
                    for rec in result.get("recommendations", []) + [s for step in result.get("steps", []) for s in step.get("recommendations", [])]:
                        if "MISMATCH" in rec.get("code", ""):
                            print("[BLOCKED] " + rec.get("code", "") + ": " + rec.get("message", ""), file=sys.stderr)
                return 3

        # --explain: add why: lines to recommendations
        if args.explain:
            result = _add_explanations(result)

        if args.quiet:
            result = _strip_noise(result)

    except BuildAnchorError as exc:
        if args.ci:
            print(f"::error ::BuildAnchor: {exc}")
        else:
            print(json.dumps({"status": "blocked", "error": str(exc)}), file=sys.stderr)
        return 4

    if args.format == "json":
        print(json.dumps(result, indent=2, sort_keys=True))
    elif args.format == "llm":
        print(engine.llm_prompt(getattr(args, "objective", "")).content)
    elif args.format == "markdown":
        _print_markdown(result)
    elif args.format == "sarif":
        print(json.dumps(_to_sarif(result), indent=2, sort_keys=True))
    else:
        _print_text(result)

    # Exit code contract:  0=valid/found  1=invalid/not-found  2=inconclusive  3=blocked/mismatch  4=error
    if args.command in {"validate-change", "compatibility", "preflight", "plan"}:
        return {"valid": 0, "invalid": 1, "inconclusive": 2, "blocked": 3}.get(result.get("status", "unknown"), 2)
    return 0


def _print_token_estimate(result: dict) -> None:
    dig = result.get("workspace_digest", "")[:16]
    print("BuildAnchor token estimates  workspace=" + dig + "...")
    print("  Recommended tool: " + str(result.get("recommended_tool")))
    print("  " + str(result.get("guidance", "")))
    print()
    for name, info in result.get("estimates", {}).items():
        t = info["tokens"]
        tier = "LOW " if t <= 300 else ("MED " if t <= 1000 else "HIGH")
        print("  [" + tier + "] " + name.ljust(35) + " ~" + str(t).rjust(5) + " tokens")
        print("         " + info["description"])


def _strip_noise(result: dict) -> dict:
    """Remove fields that waste tokens when injecting into an LLM context."""
    noise = {"evidence", "evidence_refs", "fact_refs", "workspace_digest", "schema_version", "session_id", "workspace"}
    def _clean(v):
        if isinstance(v, dict):
            return {k: _clean(val) for k, val in v.items() if k not in noise}
        if isinstance(v, list):
            return [_clean(item) for item in v]
        return v
    return _clean(result)


def _print_find_result(result: dict, explain: bool = False) -> None:
    """Human-readable output for the find command."""
    pkg = result.get("package", "?")
    found = result.get("found", False)
    if not found:
        print(f"Package: {pkg}")
        print("Status: NOT FOUND")
        print()
        print(result.get("guidance", ""))
        return

    for r in result.get("results", []):
        print(f"Package: {pkg}")
        eco = r.get("ecosystem", "?")
        installed = r.get("installed")
        print(f"Ecosystem: {eco}")
        if installed:
            print(f"Status: installed")
            print(f"Version (installed): {r.get('installed_version', '?')}")
        else:
            print("Status: declared but NOT installed")
        dv = r.get("declared_version")
        if dv:
            scope = r.get("declared_scope") or r.get("declared_file") or "manifest"
            print(f"Version (declared): {dv}  [{scope}]")
        if r.get("install_path"):
            print(f"Location: {r['install_path']}")
        pats = r.get("import_patterns", [])
        if pats:
            print()
            print("Import patterns:")
            for p in pats:
                print(f"  {p}")
        usage = r.get("usage", [])
        if usage:
            print()
            print(f"Used in {len(usage)} file(s):")
            for u in usage[:5]:
                print(f"  {u['file']}:{u['line']}  {u['text']}")
        print()

    guidance = result.get("guidance", "")
    if guidance:
        print(f"LLM guidance: {guidance}")
    if explain:
        print("  why: BuildAnchor checked installed files, declared versions, and source imports to prevent duplicates and version conflicts.")


def _resolve_command(phase: str, report, engine) -> dict:
    """Resolve the verified shell command for a build phase via engine."""
    return engine.resolve_command(phase)


def _filter_only_errors(result: dict) -> dict:
    """Remove warnings from recommendations, keeping only severity=error items."""
    result = dict(result)
    if "recommendations" in result:
        result["recommendations"] = [r for r in result["recommendations"] if r.get("severity") == "error"]
    if "steps" in result:
        steps = []
        for step in result["steps"]:
            step = dict(step)
            if "recommendations" in step:
                step["recommendations"] = [r for r in step["recommendations"] if r.get("severity") == "error"]
            steps.append(step)
        result["steps"] = steps
    # Update status if all errors are removed
    if "recommendations" in result and not result["recommendations"]:
        if result.get("status") == "invalid":
            result["status"] = "valid"
    return result


def _has_mismatch(result: dict) -> bool:
    """Check if any recommendation code contains MISMATCH."""
    for rec in result.get("recommendations", []):
        if "MISMATCH" in rec.get("code", ""):
            return True
    for step in result.get("steps", []):
        for rec in step.get("recommendations", []):
            if "MISMATCH" in rec.get("code", ""):
                return True
    return False


# Explanation lookup — maps rule codes to plain-English reasons
_EXPLANATIONS = {
    "JAKARTA_NAMESPACE": "why: Spring Boot 3+ uses jakarta.* instead of javax.* due to the Jakarta EE migration. Using javax.* will cause ClassNotFoundException at runtime.",
    "SPRING_BOOT_JAVA_VERSION": "why: Spring Boot 3.x requires Java 17+. Older Java versions are not supported and will fail at compile time.",
    "PYTHON_PACKAGING_MODERN": "why: Modern Python packaging uses pyproject.toml instead of setup.py/setup.cfg for better dependency resolution and build reproducibility.",
    "NODE_MISSING_EXPORTS_FIELD": "why: The 'exports' field in package.json controls what downstream ESM consumers can import. Without it, deep imports may break.",
    "NODE_ESM_REQUIRE": "why: ES Modules cannot use require(). Use import/export syntax instead, or set \"type\": \"module\" in package.json.",
    "GO_MODULE_VERSION": "why: Go module versioning follows semantic import versioning. Major version changes require path suffixes (v2, v3).",
    "RUST_EDITION": "why: Rust editions (2015, 2018, 2021) determine which language features are available. Mismatched edition can cause compile errors.",
    "OBJECTIVE_ECOSYSTEM_MISMATCH": "why: The requested action targets a different technology stack than what this repository uses. This would add incompatible dependencies.",
}


def _add_explanations(result: dict) -> dict:
    """Add a 'why' field to each recommendation with a plain-English explanation."""
    result = dict(result)
    if "recommendations" in result:
        recs = []
        for rec in result["recommendations"]:
            rec = dict(rec)
            code = rec.get("code", "")
            rec["why"] = _EXPLANATIONS.get(code, f"why: BuildAnchor detected this based on static analysis of your project configuration.")
            recs.append(rec)
        result["recommendations"] = recs
    if "steps" in result:
        steps = []
        for step in result["steps"]:
            step = dict(step)
            if "recommendations" in step:
                recs = []
                for rec in step["recommendations"]:
                    rec = dict(rec)
                    code = rec.get("code", "")
                    rec["why"] = _EXPLANATIONS.get(code, f"why: BuildAnchor detected this based on static analysis of your project configuration.")
                    recs.append(rec)
                step["recommendations"] = recs
            steps.append(step)
        result["steps"] = steps
    return result


def _print_text(result: dict) -> None:
    if "plan_summary" in result:
        print("plan: " + result.get("plan_id", ""))
        print("status: " + result.get("status", "unknown"))
        print(result.get("plan_summary", ""))
        if result.get("llm_prompt"):
            print()
            print(result["llm_prompt"])
        print()
        print("steps:")
        for step in result.get("steps", []):
            marker = chr(10003) if step.get("status") == "complete" else ("!" if step.get("status") == "blocked" else chr(8594))
            print("  " + marker + " " + step["id"] + ": " + step["action"] + " [" + step.get("status", "unknown") + "]")
            for rec in step.get("recommendations", []):
                msg = rec.get("message") or ("Use " + str(rec.get("recommended")) + " instead of " + str(rec.get("requested")))
                sev = rec.get("severity", "?").upper()
                print("      [" + sev + "] " + rec["code"] + ": " + msg)
        return
    if "change" in result:
        change = result["change"]
        print("status: " + result.get("status", "unknown"))
        print("baseline: " + change.get("baseline", "unknown"))
        print("changed files: " + str(len(change.get("changed_files", []))))
        for item in change.get("changed_files", []):
            print("- " + item.get("status", "?") + " " + item.get("path", ""))
        for message in change.get("guidance", []):
            print("guidance: " + message)
        execution = result.get("execution", {})
        if execution:
            print("validation mode: " + execution.get("mode", "unknown"))
            for probe in execution.get("results", []):
                print("probe: " + " ".join(probe.get("command", [])) + " [" + probe.get("status", "unknown") + "] (" + str(probe.get("duration_ms", 0)) + " ms)")
        for issue in result.get("repair", {}).get("issues", []):
            print("repair: " + issue.get("message", ""))
        return
    if "summary" in result:
        print(result["summary"])
        ctx = result.get("llm_context") or result.get("agent_context", {}).get("llm_context", "")
        if ctx:
            print()
            print(ctx)
        else:
            for item in result.get("constraints", []):
                print("- " + item)
        print()
        print("Validation:")
        for command in result.get("validation_commands", []):
            if isinstance(command, list):
                print("- " + " ".join(command))
        return
    print("status: " + result.get("status", "unknown"))
    if "build_systems" in result:
        print("build: " + (", ".join(result["build_systems"]) or "unknown"))
        print("languages: " + (", ".join(result.get("languages", [])) or "unknown"))
        for fact in result.get("facts", []):
            if isinstance(fact, dict):
                print("  " + str(fact.get("key")) + ": " + str(fact.get("value")))
        for command in result.get("validation_commands", []):
            if isinstance(command, dict):
                print("candidate: " + " ".join(command["command"]))
    for item in result.get("recommendations", []):
        msg = item.get("message") or ("Use " + str(item.get("recommended")) + " instead of " + str(item.get("requested")))
        print("[" + item.get("severity", "warn").upper() + "] " + item["code"] + ": " + msg)
    for item in result.get("guidance", []):
        print("guidance: " + item)
    for issue in result.get("issues", []):
        print("issue: " + str(issue.get("message")))


def _print_markdown(result: dict) -> None:
    status = result.get("status", "unknown")
    print("# BuildAnchor Build Truth\n\n**Status:** `" + status + "`")
    if result.get("llm_prompt"):
        print("\n## LLM Context\n\n```\n" + result["llm_prompt"] + "\n```")
    if "plan_summary" in result:
        print("\n## Plan\n\n```\n" + result["plan_summary"] + "\n```")
    if "change" in result:
        change = result["change"]
        print("\n**Baseline:** `" + change.get("baseline", "unknown") + "`")
        print("\n**Changed files:** " + str(len(change.get("changed_files", []))))
        if change.get("changed_files"):
            print()
            for item in change["changed_files"]:
                print("- `" + item.get("status", "?") + "` `" + item.get("path", "") + "`")
        if change.get("guidance"):
            print("\n## Guidance\n")
            for m in change["guidance"]:
                print("- " + m)
        execution = result.get("execution", {})
        if execution.get("results"):
            print("\n## Validation probes\n")
            for probe in execution["results"]:
                print("- `" + " ".join(probe.get("command", [])) + "` — **" + probe.get("status", "unknown") + "**")
        if result.get("repair", {}).get("issues"):
            print("\n## Repair guidance\n")
            for issue in result["repair"]["issues"]:
                print("- " + issue.get("message", ""))
        return
    if result.get("build_systems"):
        print("\n**Build systems:** " + ", ".join(result["build_systems"]))
        print("\n**Languages:** " + (", ".join(result.get("languages", [])) or "unknown"))
    if result.get("recommendations"):
        print("\n## Compatibility")
        for rec in result["recommendations"]:
            msg = rec.get("message") or ("Use `" + str(rec.get("recommended")) + "` instead of `" + str(rec.get("requested")) + "`")
            print("- **[" + rec.get("severity", "?").upper() + "]** `" + rec["code"] + "`: " + msg)
    for key in ("limitations", "guidance"):
        values = result.get(key, [])
        if values:
            print("\n## " + key.title() + "\n")
            for value in values:
                print("- " + value)


def _to_sarif(result: dict) -> dict:
    issues = result.get("repair", {}).get("issues", []) if "repair" in result else result.get("issues", [])
    return {
        "version": "2.1.0",
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "runs": [{
            "tool": {"driver": {"name": "BuildAnchor", "version": "0.2.0"}},
            "results": [{
                "ruleId": issue.get("code", "BUILDANCHOR"),
                "level": "error" if issue.get("severity") == "error" else "warning",
                "message": {"text": issue.get("message", "Build Truth requires attention.")},
            } for issue in issues],
        }],
    }
