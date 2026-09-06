# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, TextIO

from . import schema as schema_module
from .engine import BuildAnchor, BuildAnchorError
from .transports import MCPServer, advertised_tools, serve_http


@dataclass(frozen=True)
class _CLIIdentity:
    """Terminal brand assets owned exclusively by the command-line interface."""

    name: str
    tagline: str
    wordmark: str


_CLI_IDENTITY = _CLIIdentity(
    name="BuildAnchor",
    tagline="Build Truth for AI coding agents",
    wordmark=(
        " ____        _ _     _    _             _\n"
        "| __ ) _   _(_) | __| |  / \\   _ __   ___| |__   ___  _ __\n"
        "|  _ \\| | | | | |/ _` | / _ \\ | '_ \\ / __| '_ \\ / _ \\| '__|\n"
        "| |_) | |_| | | | (_| |/ ___ \\| | | | (__| | | | (_) | |\n"
        "|____/ \\__,_|_|_|\\__,_/_/   \\_\\_| |_|\\___|_| |_|\\___/|_|"
    ),
)


def _buildanchor_version() -> str:
    """Read the installed distribution version without duplicating release metadata."""
    try:
        return version("buildanchor")
    except PackageNotFoundError:
        return "development"


def _should_render_cli_banner(args: argparse.Namespace) -> bool:
    """Keep human-facing branding out of protocols and machine-readable output."""
    return (
        sys.stdout.isatty()
        and args.command not in {"mcp", "serve"}
        and args.format == "text"
        and not args.agent
        and not args.ci
    )


#: Fragments of the guidance block that carry verification state rather than
#: build truth. They move whenever somebody runs `verify`, a manifest changes,
#: or a fresh clone has no cache yet — none of which mean the guidance has
#: stopped describing the repository.
_VERIFICATION_STATE = (
    re.compile(r"(?m)^This command is \*\*[a-z]+\*\* — read from a manifest.*$"),
    re.compile(r"(?m)^Verified: this command reached \*\*[a-z]+\*\*.*$"),
    re.compile(r"(?m)^`declared` means read from a manifest and not executed\..*$"),
)

_TABLE_STATUS = re.compile(r"(?m)^(\|(?:[^|\n]*\|){3})[^|\n]*\|$")


def _without_verification_state(block: str) -> str:
    """The block with proof status removed, for staleness comparison.

    `--check` exists to catch guidance that has stopped describing the
    repository — a command that changed, a module that appeared. It must not
    fire because somebody ran `verify`, or because a fresh checkout has no cache
    yet. Those move the proof status without changing a single instruction, and
    a gate that fails for that is a gate people learn to ignore.
    """
    normalised = block
    for pattern in _VERIFICATION_STATE:
        normalised = pattern.sub("<verification state>", normalised)
    normalised = _TABLE_STATUS.sub(r"\1 <verification state> |", normalised)
    return "\n".join(line.rstrip() for line in normalised.splitlines() if line.strip())


def _proven_label(module: dict) -> str:
    """Describe how far a module's test command is proven, without overstating.

    Reaching a rung is not the same as a clean result: a command whose discovery
    probe failed reached ``resolvable``, and printing only that would read as an
    endorsement.
    """
    status = module.get("test_command_status", "declared")
    outcome = module.get("test_command_outcome", status)
    if outcome == "failed":
        return f"{status}, then FAILED"
    if outcome == "skipped":
        return f"{status} (no probe available)"
    return status


#: Guidance files that coding agents read without being asked. Different tools
#: read different names, and a repository often carries more than one.
AGENT_RULES_FILENAMES: tuple[str, ...] = ("CLAUDE.md", "AGENTS.md", "AGENT.md", "GEMINI.md")

#: Written when a repository has none of the above. AGENTS.md is the
#: cross-agent convention, so it reaches the most tools with one file.
DEFAULT_AGENT_RULES_FILENAME = "AGENTS.md"

RULES_MARKER = "<!-- BuildAnchor Rules Block -->"
RULES_END_MARKER = "<!-- End BuildAnchor Rules Block -->"


COMMAND_HELP = """\
start here
  init                  Write the build commands into this repo's agent guidance
                        file (CLAUDE.md / AGENTS.md). Add --verify to prove them.
  cmd test              Print the test command. Add --explain for the working
                        directory and how far it is proven.
  verify                Execute a discovery-only probe per module and record
                        which commands genuinely run.
  doctor [PATH]         Explain what was found, or why a directory is not
                        reported as a module.

day to day
  modules               List every project, its working directory and command.
  find --package NAME   Is this package already installed, declared, imported?
  inspect               The full report: modules, dependencies, evidence.
  context               A compact block to inject into an agent's prompt.

change validation
  change-impact         What changed against a git baseline.
  validate-change       Validate a change; --execute runs the probes.
  compatibility         Ecosystem rules that catch incompatible edits.
  repair                Guidance for a failed validation.

running as a server
  mcp --stdio           Model Context Protocol server for agents.
  serve                 HTTP server.
  setup-mcp             Register with Claude Code, Cursor, Copilot, Codex.

examples
  buildanchor init --verify
  buildanchor cmd test --scope ui --explain
  buildanchor verify --verify-level passes --jobs 8
  buildanchor doctor packages/web
  buildanchor init --check          # exit 1 if the guidance has gone stale
"""


def _modules_envelope(engine: BuildAnchor, modules: list) -> dict:
    """The one module-listing contract, shared by the CLI, HTTP, MCP and SDKs."""
    report = engine._inspect_cached()
    return {
        "schema_version": report.schema_version,
        "session_id": report.session_id,
        "is_monorepo": bool((report.repository or {}).get("is_monorepo", False)),
        "modules": [module.to_dict() for module in modules],
    }


def _agent_rules_files(workspace: Path, override: str | None = None) -> list[Path]:
    """Return every guidance file that should carry the block.

    All of them, not one. Picking a single file leaves any other agent file in
    the repository holding an older copy, and a stale build instruction is worse
    than none because an agent will trust it. Whichever file a given tool reads,
    it gets the same answer.
    """
    if override:
        return [(workspace / override).resolve()]
    existing = [workspace / name for name in AGENT_RULES_FILENAMES if (workspace / name).is_file()]
    # Also refresh any other file that already carries the block, so a file
    # renamed or added by hand does not drift.
    for candidate in sorted(workspace.glob("*.md")):
        if candidate in existing or not candidate.is_file():
            continue
        try:
            if RULES_MARKER in candidate.read_text(encoding="utf-8", errors="replace"):
                existing.append(candidate)
        except OSError:
            continue
    return existing or [workspace / DEFAULT_AGENT_RULES_FILENAME]


def _apply_rule_block(path: Path, block: str) -> str:
    """Write ``block`` into ``path``, refreshing in place. Returns the action taken."""
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Agent Guidelines\n\n" + block, encoding="utf-8")
        return "created"
    existing = path.read_text(encoding="utf-8", errors="replace")
    if RULES_MARKER in existing and RULES_END_MARKER in existing:
        head, _, tail = existing.partition(RULES_MARKER)
        _, _, after = tail.partition(RULES_END_MARKER)
        updated = head.rstrip("\n") + "\n" + block + after
        if updated == existing:
            return "unchanged"
        path.write_text(updated, encoding="utf-8")
        return "refreshed"
    path.write_text(existing.rstrip("\n") + "\n\n" + block, encoding="utf-8")
    return "appended"


def _remove_rule_block(path: Path) -> bool:
    """Strip BuildAnchor's block from ``path``, keeping everything else."""
    if not path.is_file():
        return False
    existing = path.read_text(encoding="utf-8", errors="replace")
    if RULES_MARKER not in existing or RULES_END_MARKER not in existing:
        return False
    head, _, tail = existing.partition(RULES_MARKER)
    _, _, after = tail.partition(RULES_END_MARKER)
    remaining = (head.rstrip() + "\n" + after.lstrip("\n")).strip()
    if remaining in ("", "# Agent Guidelines"):
        # The file existed only to hold the block; leave nothing behind.
        path.unlink()
        return True
    path.write_text(remaining + "\n", encoding="utf-8")
    return True


def _rule_block_is_current(path: Path, block: str) -> bool:
    """Whether ``path`` already carries exactly this block."""
    if not path.is_file():
        return False
    existing = path.read_text(encoding="utf-8", errors="replace")
    if RULES_MARKER not in existing or RULES_END_MARKER not in existing:
        return False
    start = existing.index(RULES_MARKER)
    end = existing.index(RULES_END_MARKER) + len(RULES_END_MARKER)
    return (_without_verification_state(existing[start:end])
            == _without_verification_state(block))


def _agent_rule_block(report: Any, phases: dict, shape: str) -> str:
    """Render the block injected into the repository's agent guidance file.

    This is the highest-leverage surface BuildAnchor has: agents read these
    files unprompted, every session, with no decision to make and no tool to
    call. So the block states the commands and where they run, says how far each
    is proven, and stops — advice that does not apply to this repository is
    noise, and noise makes an agent discount the rest.
    """
    lines = ["<!-- BuildAnchor Rules Block -->", "## Build and test commands (BuildAnchor)", ""]

    test = phases.get("test", {})
    if test.get("command"):
        where = test.get("working_directory", ".")
        status = test.get("command_status", "declared")
        lines.append("Run the tests with:")
        lines.append("")
        lines.append("```bash")
        lines.append(test.get("command_shell") or (
            test["command"] if where == "." else f"cd {where} && {test['command']}"
        ))
        lines.append("```")
        lines.append("")
        if status == "declared":
            lines.append(
                "This command is **declared** — read from a manifest, not yet executed. "
                "Run `buildanchor verify` to prove it runs before relying on it."
            )
        else:
            lines.append(
                f"Verified: this command reached **{status}** on the current manifests "
                "(`buildanchor verify`). It re-checks itself when a manifest changes."
            )
        lines.append("")

    for phase in ("build", "lint", "format"):
        resolved = phases.get(phase, {})
        if resolved.get("command"):
            where = resolved.get("working_directory", ".")
            suffix = "" if where == "." else f"  (run in `{where}`)"
            lines.append(f"- **{phase}**: `{resolved['command']}`{suffix}")
    if lines[-1] != "":
        lines.append("")

    if shape == "monorepo" and report.module_details:
        lines.append(f"This is a monorepo of {len(report.module_details)} modules. "
                     "Do not run the whole suite; target what you changed:")
        lines.append("")
        lines.append("```bash")
        lines.append("buildanchor cmd test --changed          # only modules with git changes")
        lines.append("buildanchor cmd test --scope ui         # or: backend, shared, <module name>")
        lines.append("```")
        lines.append("")
        lines.append("| Module | Runs in | Test command | Proven |")
        lines.append("| --- | --- | --- | --- |")
        for module in report.module_details[:12]:
            if module.get("test_command"):
                lines.append(
                    f"| `{module.get('name')}` | `{module.get('working_directory', '.')}` "
                    f"| `{module['test_command']}` | {_proven_label(module)} |"
                )
        lines.append("")
        lines.append("`declared` means read from a manifest and not executed. "
                     "Run `buildanchor verify` to raise it.")
        lines.append("")
    elif shape == "root-plus-satellites" and report.module_details:
        satellites = ", ".join(f"`{m.get('path')}`" for m in report.module_details[:6])
        lines.append(f"One project at the root, with subordinate package(s): {satellites}. "
                     "The command above is the root project's.")
        lines.append("")
    elif shape == "single-project":
        lines.append("Single-project repository: one test command, no scoping needed.")
        lines.append("")

    lines.append("Before adding a dependency, check whether it is already present:")
    lines.append("")
    lines.append("```bash")
    lines.append("buildanchor find --package <name>")
    lines.append("```")
    lines.append("")
    lines.append("<!-- End BuildAnchor Rules Block -->")
    return "\n".join(lines) + "\n"


def _render_cli_banner(output: TextIO | None = None) -> None:
    """Render the terminal wordmark once at the start of a human CLI session."""
    output = output or sys.stdout
    output.write(f"{_CLI_IDENTITY.wordmark}\n")
    output.write(f"{_CLI_IDENTITY.name} {_buildanchor_version()} | {_CLI_IDENTITY.tagline}\n\n")
    output.flush()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="buildanchor",
        # A wall of thirty flags before the first useful line is not a usage
        # string; the commands are what a reader needs first.
        usage="buildanchor COMMAND [--workspace PATH] [--format json|text|markdown] [options]",
        description=(
            "BuildAnchor tells you the command that builds and tests this repository,\n"
            "the directory it must run in, and whether it actually runs.\n"
            "Local-first, offline, no LLM calls."
        ),
        epilog=COMMAND_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "command",
        metavar="COMMAND",
        help="One of the commands listed below.",
        choices=[
            "llm-prompt", "token-estimate",
            "inspect", "context", "preflight", "plan",
            "change-impact", "validate-change", "repair", "compatibility",
            "explain-dependency", "find", "cmd", "modules", "verify", "doctor", "init",
            "mcp", "serve", "setup-copilot", "setup-mcp",
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
    parser.add_argument("--schema", default=schema_module.CURRENT_SCHEMA,
                        help=f"Report schema to emit. Supported: {', '.join(schema_module.SUPPORTED_SCHEMAS)}. "
                             f"Default: {schema_module.CURRENT_SCHEMA}. 'v1' is deprecated and removed at 2.0.")
    parser.add_argument("--rules-file",
                        help="With 'init': write the agent guidance block to this file only.")
    parser.add_argument("--list-tools", action="store_true",
                        help="With 'mcp': print the tool schemas as JSON and exit. For building an agent.")
    parser.add_argument("--call-tool", default=None,
                        help="With 'mcp': execute one tool by name and print its result as JSON.")
    parser.add_argument("--tool-input", default="{}",
                        help="JSON object of arguments for --call-tool.")
    parser.add_argument("--dry-run", action="store_true",
                        help="With 'init': print exactly what would be written and change nothing.")
    parser.add_argument("--undo", action="store_true",
                        help="With 'init': remove everything 'init' wrote, keeping your own content.")
    parser.add_argument("--check", action="store_true",
                        help="With 'init': report whether the agent guidance block matches the "
                             "repository and exit 1 if it is stale. Changes nothing. For CI and hooks.")
    parser.add_argument("--verify", action="store_true",
                        help="With 'init': run 'verify' first so the written commands carry a proven status.")
    parser.add_argument("--verify-level", default="collects", choices=["resolvable", "collects", "passes"],
                        help="How far 'verify' should climb the ladder. 'resolvable' executes nothing, "
                             "'collects' runs a discovery-only probe, 'passes' runs the full suite.")
    parser.add_argument("--jobs", type=int, default=None,
                        help="With 'verify': how many modules to probe concurrently. "
                             "Default: the machine's parallelism, capped at 8.")
    parser.add_argument("--no-cache", action="store_true",
                        help="Ignore and do not write .buildanchor/verified.json.")
    parser.add_argument("--explain", action="store_true",
                        help="Add a plain-English 'why:' explanation to each finding.")
    parser.add_argument("--staged", action="store_true",
                        help="Only inspect files staged in git (for pre-commit hooks).")
    parser.add_argument("--force", action="store_true",
                        help="Replace an existing BuildAnchor MCP server configuration when used with setup-copilot.")
    parser.add_argument("--clients", default="copilot",
                        help=("Comma-separated MCP clients for setup-mcp: "
                              "copilot,cursor,claude-code,claude-desktop,codex,all. "
                              "'claude' is an alias for claude-code."))
    parser.add_argument("-i", "--interactive", action="store_true",
                        help="Interactively collect inputs; setup-mcp uses a keyboard client selector.")

    return parser


def _mcp_server_spec(workspace: Path, workspace_variable: bool = False) -> dict[str, object]:
    return {
        # Keep the virtual-environment interpreter path intact. Resolving its
        # symlink on macOS can bypass the environment's installed packages.
        "command": str(Path(sys.executable).absolute()),
        "args": [
            "-m", "buildanchor", "mcp", "--stdio", "--allow-root",
            "${workspaceFolder}" if workspace_variable else str(workspace),
        ],
    }


@dataclass(frozen=True)
class _MCPClientOption:
    """One installable MCP client and the scope its configuration owns."""

    identifier: str
    name: str
    configuration_scope: str
    configuration_location: str


# This registry is the single source of truth for accepted client identifiers,
# interactive labels, display order, and the meaning of `all`.
_MCP_CLIENT_OPTIONS = (
    _MCPClientOption("copilot", "GitHub Copilot", "Repository", ".vscode/mcp.json"),
    _MCPClientOption("cursor", "Cursor", "Repository", ".cursor/mcp.json"),
    _MCPClientOption("claude-code", "Claude Code", "Repository", ".mcp.json"),
    _MCPClientOption("claude-desktop", "Claude Desktop", "Global", "Claude Desktop settings"),
    _MCPClientOption("codex", "Codex", "Global", "~/.codex/config.toml"),
)


def _mcp_client_ids() -> tuple[str, ...]:
    return tuple(option.identifier for option in _MCP_CLIENT_OPTIONS)


def _select_mcp_clients_prompt(
    input_fn: Callable[[str], str] = input,
    output: TextIO = sys.stdout,
) -> str:
    """Fallback selector for piped input and terminals without raw-key support."""
    selected: set[str] = set()
    while True:
        print("\nSelect MCP clients:", file=output)
        for index, option in enumerate(_MCP_CLIENT_OPTIONS, start=1):
            marker = "x" if option.identifier in selected else " "
            print(
                f"  {index}. [{marker}] {option.name} "
                f"({option.configuration_scope.lower()})",
                file=output,
            )
        choice = input_fn("Toggle numbers (for example 1,3); a=all; n=none; i=install; q=cancel: ").strip().lower()
        if choice == "q":
            raise BuildAnchorError("MCP setup cancelled")
        if choice == "a":
            selected = set(_mcp_client_ids())
            continue
        if choice == "n":
            selected.clear()
            continue
        if choice == "i":
            if selected:
                return ",".join(client for client in _mcp_client_ids() if client in selected)
            print("Select at least one client before installing.", file=output)
            continue
        try:
            indexes = {int(item.strip()) for item in choice.split(",") if item.strip()}
        except ValueError:
            indexes = set()
        if not indexes or any(index < 1 or index > len(_MCP_CLIENT_OPTIONS) for index in indexes):
            print("Enter one or more displayed numbers, or a, n, i, or q.", file=output)
            continue
        for index in indexes:
            client = _MCP_CLIENT_OPTIONS[index - 1].identifier
            if client in selected:
                selected.remove(client)
            else:
                selected.add(client)


@dataclass(frozen=True)
class _TerminalPalette:
    """ANSI presentation owned by the interactive terminal experience."""

    accent: str
    title: str
    muted: str
    selected: str
    reset: str


_TERMINAL_PALETTE = _TerminalPalette(
    accent="\x1b[38;5;111m",
    title="\x1b[1;38;5;255m",
    muted="\x1b[38;5;245m",
    selected="\x1b[38;5;114m",
    reset="\x1b[0m",
)


def _selector_colors_enabled(output: TextIO) -> bool:
    """Respect standard terminal conventions and keep redirected output plain."""
    return (
        output.isatty()
        and os.environ.get("NO_COLOR") is None
        and os.environ.get("TERM") != "dumb"
    )


def _selector_style(value: str, color: str, enabled: bool) -> str:
    if not enabled:
        return value
    return f"{color}{value}{_TERMINAL_PALETTE.reset}"


def _selector_width() -> int:
    """Fit the setup surface to the active terminal without horizontal overflow."""
    columns = shutil.get_terminal_size(fallback=(80, 24)).columns
    return max(20, min(88, columns - 2))


def _selector_fit(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    return value[: max(1, width - 3)] + "..."


def _render_mcp_client_selector(
    output: TextIO,
    selected: set[str],
    cursor: int,
    workspace: Path,
    message: str = "",
) -> None:
    """Render a focused, keyboard-first setup surface in the alternate screen."""
    # The selector runs on the alternate screen. Clear only that screen so
    # normal terminal scrollback and the invoking command remain intact.
    colors_enabled = _selector_colors_enabled(output)
    width = _selector_width()
    inner_width = width - 4

    def frame(
        left: str = "",
        right: str = "",
        left_color: str | None = None,
        right_color: str | None = None,
    ) -> str:
        right = _selector_fit(right, max(1, inner_width - 1))
        available = inner_width - len(right)
        left = _selector_fit(left, max(1, available))
        spacing = " " * max(0, inner_width - len(left) - len(right))
        left = _selector_style(left, left_color, colors_enabled) if left_color else left
        right = _selector_style(right, right_color, colors_enabled) if right_color else right
        return f"| {left}{spacing}{right} |\n"

    selected_count = len(selected)
    selected_label = f"{selected_count} selected"

    output.write("\x1b[H\x1b[J")
    output.write(_selector_style("+" + "-" * (width - 2) + "+\n", _TERMINAL_PALETTE.accent, colors_enabled))
    output.write(frame(
        "BUILDANCHOR",
        f"MCP SETUP  v{_buildanchor_version()}",
        _TERMINAL_PALETTE.title,
        _TERMINAL_PALETTE.muted,
    ))
    output.write(frame("Build Truth for AI coding agents", left_color=_TERMINAL_PALETTE.muted))
    output.write(_selector_style("+" + "-" * (width - 2) + "+\n", _TERMINAL_PALETTE.accent, colors_enabled))
    output.write("\n")
    output.write(_selector_style("  WORKSPACE\n", _TERMINAL_PALETTE.muted, colors_enabled))
    output.write(f"  {_selector_fit(str(workspace), width - 4)}\n\n")
    output.write(_selector_style("  SELECT MCP CLIENTS", _TERMINAL_PALETTE.title, colors_enabled))
    output.write("  ")
    output.write(_selector_style(selected_label, _TERMINAL_PALETTE.selected, colors_enabled))
    output.write("\n\n")
    for index, option in enumerate(_MCP_CLIENT_OPTIONS):
        pointer = ">" if index == cursor else " "
        marker = "x" if option.identifier in selected else " "
        client_line = f"  {pointer} [{marker}] {option.name}  {option.configuration_scope}"
        output.write(f"{_selector_style(_selector_fit(client_line, width - 2), _TERMINAL_PALETTE.selected if option.identifier in selected else _TERMINAL_PALETTE.title, colors_enabled)}\n")
        output.write(f"      {_selector_fit(option.configuration_location, width - 6)}\n")
    output.write("\n")
    output.write(_selector_style("  [Up/Down] Move   [Space] Toggle   [Enter] Install   [q] Cancel\n", _TERMINAL_PALETTE.muted, colors_enabled))
    if message:
        output.write(f"\n  {_selector_style(message, _TERMINAL_PALETTE.accent, colors_enabled)}\n")
    output.flush()


def _select_mcp_clients_keyboard(
    read_key: Callable[[], str],
    output: TextIO,
    workspace: Path | None = None,
) -> str:
    """Render and operate a keyboard-first terminal checkbox selector."""
    selected: set[str] = set()
    cursor = 0
    message = ""
    workspace = workspace or Path.cwd()
    while True:
        _render_mcp_client_selector(output, selected, cursor, workspace, message)
        key = read_key()
        message = ""
        if key in {"\x1b[A", "k"}:
            cursor = (cursor - 1) % len(_MCP_CLIENT_OPTIONS)
        elif key in {"\x1b[B", "j"}:
            cursor = (cursor + 1) % len(_MCP_CLIENT_OPTIONS)
        elif key == " ":
            client = _MCP_CLIENT_OPTIONS[cursor].identifier
            if client in selected:
                selected.remove(client)
            else:
                selected.add(client)
        elif key in {"\r", "\n"}:
            if selected:
                return ",".join(client for client in _mcp_client_ids() if client in selected)
            message = "Select at least one client before installing."
        elif key.lower() == "q":
            raise BuildAnchorError("MCP setup cancelled")


def _select_mcp_clients(workspace: Path) -> str:
    """Use a managed terminal session when possible, with a prompt fallback."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return _select_mcp_clients_prompt()
    try:
        import termios
        import tty
    except ImportError:
        return _select_mcp_clients_prompt()

    terminal = sys.stdin.fileno()
    original_settings = termios.tcgetattr(terminal)
    cbreak_enabled = False
    alternate_screen_enabled = False

    def read_key() -> str:
        key = sys.stdin.read(1)
        if key != "\x1b":
            return key
        if sys.stdin.read(1) != "[":
            return key
        return key + "[" + sys.stdin.read(1)

    try:
        # cbreak disables line buffering and echo but preserves output
        # post-processing. Raw mode disabled it, which caused newlines to
        # advance vertically without returning to column zero.
        tty.setcbreak(terminal)
        cbreak_enabled = True
        sys.stdout.write("\x1b[?1049h\x1b[?25l")
        sys.stdout.flush()
        alternate_screen_enabled = True
        return _select_mcp_clients_keyboard(read_key, sys.stdout, workspace)
    except KeyboardInterrupt as exc:
        raise BuildAnchorError("MCP setup cancelled") from exc
    finally:
        if alternate_screen_enabled:
            sys.stdout.write("\x1b[?25h\x1b[?1049l")
            sys.stdout.flush()
        if cbreak_enabled:
            termios.tcsetattr(terminal, termios.TCSADRAIN, original_settings)


def _setup_json_mcp(config_path: Path, key: str, server: dict[str, object], force: bool) -> str:
    """Safely merge one local MCP server into a JSON configuration file."""
    config: dict = {}
    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BuildAnchorError(
                f"cannot update {config_path}: expected valid JSON"
            ) from exc
        if not isinstance(config, dict):
            raise BuildAnchorError(f"cannot update {config_path}: expected a JSON object")

    servers = config.setdefault(key, {})
    if not isinstance(servers, dict):
        raise BuildAnchorError(f"cannot update {config_path}: '{key}' must be a JSON object")
    existing = servers.get("buildanchor")
    if existing == server:
        status = "already configured"
    elif existing is not None and not force:
        raise BuildAnchorError(
            "a buildanchor MCP server is already configured; rerun with --force to replace it"
        )
    else:
        servers["buildanchor"] = server
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        status = "updated" if existing is not None else "configured"

    return status


def _setup_codex_mcp(config_path: Path, workspace: Path, force: bool) -> str:
    """Append a bounded stdio server to Codex's TOML configuration."""
    section = "[mcp_servers.buildanchor]"
    block = (
        f'{section}\n'
        f'command = {json.dumps(str(Path(sys.executable).absolute()))}\n'
        f'args = {json.dumps(["-m", "buildanchor", "mcp", "--stdio", "--allow-root", str(workspace)])}\n'
    )
    content = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    if section in content:
        start = content.index(section)
        next_section = content.find("\n[", start + len(section))
        existing_block = content[start:next_section if next_section >= 0 else len(content)].strip()
        if existing_block == block.strip():
            return "already configured"
        if not force:
            raise BuildAnchorError(
                "a buildanchor Codex MCP server is already configured; rerun with --force to replace it"
            )
        content = content[:start] + (content[next_section + 1:] if next_section >= 0 else "")
        status = "updated"
    else:
        status = "configured"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(content.rstrip() + "\n\n" + block, encoding="utf-8")
    return status


def _claude_config_path(home: Path) -> Path:
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming"))) / "Claude" / "claude_desktop_config.json"
    return home / ".config" / "Claude" / "claude_desktop_config.json"


def _setup_mcp_clients(workspace: Path, clients: str, force: bool = False, home: Path | None = None) -> list[dict[str, str]]:
    """Configure supported local MCP clients without overwriting other servers."""
    requested = {client.strip().lower() for client in clients.split(",") if client.strip()}
    if "all" in requested:
        requested = set(_mcp_client_ids())
    aliases = {"claude": "claude-code", "gpt": "codex"}
    requested = {aliases.get(client, client) for client in requested}
    unknown = requested - set(_mcp_client_ids())
    if unknown:
        raise BuildAnchorError(f"unsupported MCP client(s): {', '.join(sorted(unknown))}")
    if not requested:
        raise BuildAnchorError("at least one MCP client is required")

    user_home = home or Path.home()
    outcomes: list[dict[str, str]] = []
    if "copilot" in requested:
        path = workspace / ".vscode" / "mcp.json"
        outcomes.append({"client": "copilot", "status": _setup_json_mcp(path, "servers", _mcp_server_spec(workspace, workspace_variable=True), force), "config_file": str(path)})
    if "cursor" in requested:
        path = workspace / ".cursor" / "mcp.json"
        outcomes.append({"client": "cursor", "status": _setup_json_mcp(path, "mcpServers", _mcp_server_spec(workspace), force), "config_file": str(path)})
    if "claude-code" in requested:
        path = workspace / ".mcp.json"
        outcomes.append({"client": "claude-code", "status": _setup_json_mcp(path, "mcpServers", _mcp_server_spec(workspace), force), "config_file": str(path)})
    if "claude-desktop" in requested:
        path = _claude_config_path(user_home)
        outcomes.append({"client": "claude-desktop", "status": _setup_json_mcp(path, "mcpServers", _mcp_server_spec(workspace), force), "config_file": str(path)})
    if "codex" in requested:
        path = user_home / ".codex" / "config.toml"
        outcomes.append({"client": "codex", "status": _setup_codex_mcp(path, workspace, force), "config_file": str(path)})
    return outcomes


@dataclass(frozen=True)
class _InteractiveField:
    """A command input owned by BuildAnchor's shared interactive workflow."""

    attribute: str
    label: str
    hint: str
    required: bool = False
    choices: tuple[str, ...] = ()
    numeric: bool = False
    boolean: bool = False


_INTERACTIVE_FIELDS: dict[str, tuple[_InteractiveField, ...]] = {
    "llm-prompt": (_InteractiveField("objective", "Objective", "Optional; describe the intended change."),),
    "context": (_InteractiveField("token_budget", "Token budget", "Maximum context tokens.", numeric=True),),
    "preflight": (_InteractiveField("objective", "Objective", "Optional; check a proposed change against this workspace."),),
    "plan": (_InteractiveField("objective", "Objective", "Describe the change you want to plan.", required=True),),
    "change-impact": (
        _InteractiveField("baseline", "Baseline", "Git revision to compare against."),
        _InteractiveField("staged", "Use staged changes only", "Analyze only staged files.", boolean=True),
    ),
    "validate-change": (
        _InteractiveField("baseline", "Baseline", "Git revision to compare against."),
        _InteractiveField("staged", "Use staged changes only", "Analyze only staged files.", boolean=True),
        _InteractiveField("execute", "Run validation probes", "May execute detected build or test commands.", boolean=True),
    ),
    "explain-dependency": (_InteractiveField("dependency", "Dependency", "Package or coordinate to explain.", required=True),),
    "find": (_InteractiveField("package", "Package", "Package name to find.", required=True),),
    "cmd": (
        _InteractiveField("phase", "Build phase", "Choose test, build, lint, format, or clean.", choices=("test", "build", "lint", "format", "clean")),
        _InteractiveField("scope", "Scope", "Optional: ui, backend, shared, package name, or path."),
        _InteractiveField("changed", "Target changed modules only", "Limit the command to modified modules.", boolean=True),
    ),
    "serve": (_InteractiveField("listen", "Listen address", "Host and port for the HTTP server."),),
}


def _interactive_default(args: argparse.Namespace, field: _InteractiveField) -> str:
    value = getattr(args, field.attribute)
    if field.boolean:
        return "yes" if value else "no"
    return str(value or "")


def _interactive_value(raw: str, field: _InteractiveField) -> object:
    if field.boolean:
        if raw.lower() in {"y", "yes", "true", "1"}:
            return True
        if raw.lower() in {"n", "no", "false", "0"}:
            return False
        raise ValueError("enter yes or no")
    if field.numeric:
        value = int(raw)
        if value <= 0:
            raise ValueError("enter a positive number")
        return value
    if field.choices and raw.lower() not in field.choices:
        raise ValueError(f"choose one of: {', '.join(field.choices)}")
    return raw


def _render_interactive_header(command: str, workspace: Path, output: TextIO) -> None:
    colors_enabled = _selector_colors_enabled(output)
    output.write("\n")
    output.write(_selector_style(f"  {command.upper()}  /  INTERACTIVE MODE\n", _TERMINAL_PALETTE.title, colors_enabled))
    output.write(_selector_style(f"  Workspace: {workspace}\n", _TERMINAL_PALETTE.muted, colors_enabled))
    output.write(_selector_style("  Press Enter to accept a default. Type q to cancel.\n\n", _TERMINAL_PALETTE.muted, colors_enabled))
    output.flush()


def _collect_interactive_inputs(
    args: argparse.Namespace,
    workspace: Path,
    input_fn: Callable[[], str] | None = None,
    output: TextIO | None = None,
) -> None:
    """Collect each command's optional inputs through one predictable terminal flow."""
    input_fn = input_fn or input
    output = output or sys.stdout
    fields = _INTERACTIVE_FIELDS.get(args.command, ())
    _render_interactive_header(args.command, workspace, output)
    if not fields:
        output.write("  This command has no additional inputs. Continuing with workspace defaults.\n\n")
        output.flush()
        return

    for field in fields:
        default = _interactive_default(args, field)
        while True:
            output.write(_selector_style(f"  {field.label}\n", _TERMINAL_PALETTE.title, _selector_colors_enabled(output)))
            output.write(_selector_style(f"  {field.hint}\n", _TERMINAL_PALETTE.muted, _selector_colors_enabled(output)))
            suffix = f" [{default}]" if default else ""
            output.write(f"  >{suffix} ")
            output.flush()
            try:
                raw = input_fn().strip()
            except (EOFError, KeyboardInterrupt) as exc:
                raise BuildAnchorError("interactive input cancelled") from exc
            if raw.lower() == "q":
                raise BuildAnchorError("interactive input cancelled")
            if not raw:
                raw = default
            if not raw and field.required:
                output.write(_selector_style("  An answer is required.\n\n", _TERMINAL_PALETTE.accent, _selector_colors_enabled(output)))
                continue
            if not raw:
                setattr(args, field.attribute, "")
                break
            try:
                setattr(args, field.attribute, _interactive_value(raw, field))
                break
            except ValueError as exc:
                output.write(_selector_style(f"  {exc}. Try again.\n\n", _TERMINAL_PALETTE.accent, _selector_colors_enabled(output)))

        output.write("\n")
        output.flush()


def _validate_interactive_mode(args: argparse.Namespace) -> None:
    """Prevent prompts or branding from corrupting protocol and structured output."""
    if not args.interactive:
        return
    if args.command == "mcp":
        raise BuildAnchorError("mcp is a stdio protocol server and cannot run interactively")
    if args.ci or args.agent or args.format != "text":
        raise BuildAnchorError("--interactive requires human-readable text output outside CI and agent modes")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise BuildAnchorError("--interactive requires an attached terminal")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    # --ci implies --quiet --only-errors --exit-on-mismatch
    if args.ci:
        args.quiet = True
        args.only_errors = True
        args.exit_on_mismatch = True

    try:
        _validate_interactive_mode(args)

        if _should_render_cli_banner(args):
            _render_cli_banner()

        if args.command == "mcp":
            server = MCPServer(args.allow_root or args.workspace)
            # Two non-protocol modes, so an SDK can build an agent on top of
            # BuildAnchor without speaking MCP over a pipe. The schemas are the
            # same ones `tools/list` advertises — there is no second definition.
            if getattr(args, "list_tools", False):
                print(json.dumps(advertised_tools(), indent=2))
                return 0
            if getattr(args, "call_tool", None):
                try:
                    tool_input = json.loads(args.tool_input or "{}")
                except json.JSONDecodeError as exc:
                    raise BuildAnchorError(f"--tool-input is not valid JSON: {exc}") from exc
                if not isinstance(tool_input, dict):
                    raise BuildAnchorError("--tool-input must be a JSON object")
                print(json.dumps(server.call_tool(args.call_tool, tool_input), indent=2))
                return 0
            server.run(sys.stdin, sys.stdout)
            return 0

        engine = BuildAnchor(args.workspace, args.allow_root)

        if args.interactive and args.command != "setup-mcp":
            _collect_interactive_inputs(args, engine.workspace)

        if args.command == "serve":
            host, port = args.listen.rsplit(":", 1)
            serve_http(args.allow_root or args.workspace, host, int(port))
            return 0

        if args.command in {"setup-copilot", "setup-mcp"}:
            clients = (
                "copilot" if args.command == "setup-copilot"
                else _select_mcp_clients(engine.workspace) if args.interactive
                else args.clients
            )
            result = _setup_mcp_clients(engine.workspace, clients, force=args.force)
            if args.format == "json":
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                for outcome in result:
                    print(f"{outcome['client']}: {outcome['status']}")
                    print(f"  Config: {outcome['config_file']}")
            return 0

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
                    print(json.dumps(_modules_envelope(engine, modules), indent=2))
                else:
                    if not modules:
                        print("No monorepo modules detected (single-project repository).")
                    else:
                        print(f"Monorepo modules ({len(modules)} found):")
                        for m in modules:
                            cat_upper = m.category.upper()
                            print(f"  - {m.name} ({m.path}) [{cat_upper}]")
                            if m.test_command:
                                print(f"      test : {m.test_command}  (in {m.working_directory}, {m.test_command_status})")
                            if m.build_command:
                                print(f"      build: {m.build_command}  (in {m.working_directory})")
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
                        print(f"working_directory: {resolved.get('working_directory', '.')}")
                        print(f"status: {resolved.get('command_status', 'declared')}")
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

        if args.command == "doctor":
            target = args.phase_arg or getattr(args, "scope", None)
            result = engine.diagnose(target)
            if args.format == "json":
                print(json.dumps(result, indent=2, sort_keys=True))
            elif target:
                print(f"{result['path']}: {result['reason']}")
                if result.get("markers"):
                    print(f"  markers found: {', '.join(result['markers'])}")
                considered = result.get("considered") or {}
                if (not considered.get("included", True)
                        and considered.get("reason", "") not in result.get("reason", "")):
                    print(f"  not considered: {considered['reason']}")
                module = result.get("module")
                if module:
                    print(f"  ecosystem: {module.get('ecosystem')}")
                    print(f"  test: {module.get('test_command')}  (in {module.get('working_directory')})")
                    print(f"  proven: {_proven_label(module)}")
                for suggestion in result.get("suggestions", []):
                    print(f"  -> {suggestion}")
            else:
                repository = result.get("repository") or {}
                print(f"Repository: {repository.get('shape', 'unknown')} — {repository.get('reason', '')}")
                print(f"Build systems: {', '.join(result['build_systems']) or 'none detected'}")
                print(f"Languages: {', '.join(result['languages']) or 'none detected'}")
                if result.get("declared_runners"):
                    print(f"Task runners you declare: {', '.join(result['declared_runners'])} "
                          "(these take precedence)")
                for phase, entry in (result.get("commands") or {}).items():
                    location = "" if entry["working_directory"] == "." else f"  in {entry['working_directory']}"
                    print(f"\n{phase.capitalize()}: {entry['command']}{location}")
                    print(f"  from {entry['source']} — {entry['status']}")
                if result["modules"]:
                    print(f"\nModules ({len(result['modules'])}):")
                    for module in result["modules"]:
                        print(f"  {module['path']:24} {module['ecosystem']:8} "
                              f"{_proven_label({'test_command_status': module['status'], 'test_command_outcome': module['outcome']})}")
                if result["findings"]:
                    print("\nFindings:")
                    for finding in result["findings"]:
                        print(f"  [{finding['severity']}] {finding['detail']}")
                else:
                    print("\nNothing to report.")
                print("\nAsk about one directory with: buildanchor doctor <path>")
            return 0 if result["status"] == "valid" else (1 if result["status"] == "invalid" else 2)

        if args.command == "verify":
            result = engine.verify_commands(
                level=args.verify_level,
                scope=args.scope,
                timeout_seconds=args.timeout if args.timeout != 300 else None,
                use_cache=not args.no_cache,
                write_cache=not args.no_cache,
                jobs=args.jobs,
                dry_run=getattr(args, "dry_run", False),
            )
            if result.get("dry_run"):
                if args.format == "json":
                    print(json.dumps(result, indent=2, sort_keys=True))
                else:
                    print(f"Would verify to level '{result['requested_level']}'. "
                          f"Nothing is executed by this command.\n")
                    for entry in result["plan"]:
                        print(f"  {entry['name']} ({entry['path']}) — in {entry['working_directory']}")
                        for step in entry["would_run"]:
                            print(f"      {step['rung']:10} {step['command']}")
                        if entry.get("note"):
                            print(f"      note       {entry['note']}")
                    print("\nRe-run without --dry-run to execute these.")
                return 0
            if args.format == "json":
                print(json.dumps(result, indent=2, sort_keys=True))
            else:
                print(f"Verifying test commands to level '{result['requested_level']}' "
                      f"({result['modules_verified']} module(s)):\n")
                for item in result["results"]:
                    mark = {"passes": "PASSES", "collects": "COLLECTS", "resolvable": "RESOLVABLE",
                            "declared": "DECLARED", "skipped": "SKIPPED", "failed": "FAILED"}
                    label = mark.get(item["outcome"], item["outcome"].upper())
                    cached = " (cached)" if item.get("cached") else ""
                    print(f"  [{label}]{cached} {item['name']} ({item['path']})")
                    if item.get("command"):
                        print(f"      command: {item['command']}")
                        print(f"      run in : {item['working_directory']}")
                    if item.get("reason"):
                        print(f"      reason : {item['reason']}")
                    for rung in item.get("rungs", []):
                        if rung.get("passed") is False and rung.get("output_tail"):
                            tail = rung["output_tail"].strip().splitlines()[-4:]
                            for line in tail:
                                print(f"      | {line}")
                    print()
                proven = result["modules_at_collects_or_better"]
                print(f"{proven}/{result['modules_verified']} module(s) verified at 'collects' or better.")
                if result.get("workers", 1) > 1 and result.get("wall_clock_saved_ms"):
                    print(f"Probed {result['workers']} modules at a time; "
                          f"{result['wall_clock_saved_ms'] / 1000:.1f}s faster than one at a time.")
                if not args.no_cache:
                    print(f"Recorded in {result['cache_path']}; later inspect/cmd calls report it without re-running.")
            return 0 if result["status"] == "valid" else (1 if result["status"] == "invalid" else 2)

        if args.command == "modules":
            modules = engine.discover_modules()
            if args.format == "json":
                # The same envelope every other surface returns. A bare array
                # here meant an SDK saw a different shape in local mode than
                # over HTTP — the same call, the same tool, two contracts.
                print(json.dumps(_modules_envelope(engine, modules), indent=2))
            else:
                if not modules:
                    print("No monorepo modules detected (single-project repository).")
                else:
                    print(f"Monorepo modules ({len(modules)} found):")
                    for m in modules:
                        cat_upper = m.category.upper()
                        print(f"  - {m.name} ({m.path}) [{cat_upper}]")
                        if m.test_command:
                            print(f"      test : {m.test_command}  (in {m.working_directory}, {m.test_command_status})")
                        if m.build_command:
                            print(f"      build: {m.build_command}  (in {m.working_directory})")
                    print("\nCommands are relative to each module's working directory.")
                    print("Prove they run with:  buildanchor verify")
                    print("\nRun targeted tests with:")
                    print("  buildanchor cmd test --scope ui")
                    print("  buildanchor cmd test --scope backend")
                    print("  buildanchor cmd test --scope <name>")
                    print("  buildanchor cmd test --changed")
            return 0

        if args.command == "init":
            report = engine._inspect_cached()
            phases = {phase: engine.resolve_command(phase) for phase in ("test", "build", "lint", "format")}
            test_resolved = phases["test"]
            test_cmd = test_resolved.get("command")
            shape = (report.repository or {}).get("shape", "unknown")

            # Verify whenever there is anything to verify. Gating on a root
            # command skipped verification entirely in a monorepo with no root
            # project — precisely the case where it matters most.
            if getattr(args, "verify", False) and (test_cmd or report.module_details):
                engine.verify_commands(level=args.verify_level)
                report = engine.inspect()
                phases = {phase: engine.resolve_command(phase) for phase in ("test", "build", "lint", "format")}
                test_resolved = phases["test"]

            config_path = engine.workspace / ".buildanchor.json"
            config_data = {
                "version": "1.1",
                "build_systems": report.build_systems,
                "languages": report.languages,
                "repository": report.repository,
                "commands": {
                    phase: {
                        "command": resolved.get("command"),
                        "working_directory": resolved.get("working_directory", "."),
                        "status": resolved.get("command_status", "declared"),
                    }
                    for phase, resolved in phases.items()
                },
                "modules": [
                    {
                        "name": module.get("name"),
                        "path": module.get("path"),
                        "working_directory": module.get("working_directory", "."),
                        "test_command": module.get("test_command"),
                        "test_command_status": module.get("test_command_status", "declared"),
                    }
                    for module in report.module_details
                ],
                # Retained under its historical name for existing readers.
                "verified_commands": {phase: resolved.get("command") for phase, resolved in phases.items()},
                "policy": "allow",
            }
            rule_block = _agent_rule_block(report, phases, shape)
            rules_files = _agent_rules_files(engine.workspace, getattr(args, "rules_file", None))

            if not getattr(args, "dry_run", False):
                # Written after the dry-run check, because a dry run that writes
                # a file is not a dry run.
                config_path.write_text(json.dumps(config_data, indent=2) + "\n", encoding="utf-8")

            if getattr(args, "undo", False):
                # Everything `init` writes, removed. A tool that edits the file
                # agents read has to be trivially reversible, or a careful team
                # will simply not run it.
                removed: list[str] = []
                for path in _agent_rules_files(engine.workspace, getattr(args, "rules_file", None)):
                    if _remove_rule_block(path):
                        removed.append(path.relative_to(engine.workspace).as_posix())
                config_path = engine.workspace / ".buildanchor.json"
                if config_path.is_file():
                    config_path.unlink()
                    removed.append(config_path.name)
                payload = {"status": "removed", "removed": removed}
                if args.format == "json":
                    print(json.dumps(payload, indent=2))
                elif removed:
                    for item in removed:
                        print(f"removed BuildAnchor content from {item}")
                    record = engine.workspace / ".buildanchor" / "verified.json"
                    if record.is_file():
                        print(f"\nLeft in place: {record.relative_to(engine.workspace).as_posix()} — "
                              "that is verification evidence, written by 'verify', not by 'init'.")
                else:
                    print("Nothing of BuildAnchor's to remove.")
                return 0

            if getattr(args, "check", False):
                # CI / pre-commit mode: report drift, change nothing. A block
                # that no longer matches the repository is the failure worth
                # catching, because an agent has no way to notice it is stale.
                stale = [f for f in rules_files if not _rule_block_is_current(f, rule_block)]
                payload = {
                    "status": "stale" if stale else "current",
                    "checked": [f.relative_to(engine.workspace).as_posix() for f in rules_files],
                    "stale": [f.relative_to(engine.workspace).as_posix() for f in stale],
                }
                if args.format == "json":
                    print(json.dumps(payload, indent=2))
                elif stale:
                    for path in payload["stale"]:
                        print(f"stale: {path} does not match this repository's build truth", file=sys.stderr)
                    print("Run 'buildanchor init' to refresh.", file=sys.stderr)
                else:
                    print(f"Agent guidance is current ({', '.join(payload['checked'])}).")
                return 1 if stale else 0

            # Every agent file gets the same block. Updating only one leaves the
            # others holding an older answer, and an agent will trust whichever
            # one its tool happens to read.
            if getattr(args, "dry_run", False):
                # Show exactly what would be written, change nothing. "What will
                # this do to my repository?" should be answerable without
                # finding out the hard way.
                planned = {
                    path.relative_to(engine.workspace).as_posix():
                        ("create" if not path.is_file()
                         else ("refresh" if RULES_MARKER in path.read_text(encoding="utf-8", errors="replace")
                               else "append"))
                    for path in rules_files
                }
                if args.format == "json":
                    print(json.dumps({"status": "dry-run", "files": planned,
                                      "config": ".buildanchor.json", "block": rule_block}, indent=2))
                else:
                    print("Would write .buildanchor.json, and this block into:")
                    for name, action in planned.items():
                        print(f"  {name} ({action})")
                    print("\n" + "-" * 62)
                    print(rule_block.rstrip())
                    print("-" * 62)
                    print("\nNothing was written. Re-run without --dry-run to apply, "
                          "or 'buildanchor init --undo' to remove it later.")
                return 0

            actions = {}
            for path in rules_files:
                actions[path] = _apply_rule_block(path, rule_block)
            rules_file = rules_files[0]

            badge = "[![BuildAnchor Verified](https://img.shields.io/badge/BuildAnchor-Protected-blue)](https://github.com/tensilestream/buildanchor)"

            if args.format == "json":
                print(json.dumps({
                    "status": "initialized",
                    "workspace": str(engine.workspace),
                    "repository_shape": shape,
                    "config_file": config_path.relative_to(engine.workspace).as_posix(),
                    "rules_file": rules_file.relative_to(engine.workspace).as_posix(),
                    "rules_files": {
                        path.relative_to(engine.workspace).as_posix(): action
                        for path, action in actions.items()
                    },
                    "commands": config_data["commands"],
                    "badge": badge,
                }, indent=2))
            else:
                print(f"BuildAnchor initialized for {engine.workspace.name or engine.workspace}")
                print(f"  Repository: {shape} — {(report.repository or {}).get('reason', '')}")
                print(f"  Config: {config_path.name}")
                rendered = ", ".join(f"{path.name} ({action})" for path, action in actions.items())
                print(f"  Rules:  {rendered}")
                print("          agents read these without being asked")
                for phase in ("test", "build"):
                    resolved = phases[phase]
                    if resolved.get("command"):
                        where = resolved.get("working_directory", ".")
                        status = resolved.get("command_status", "declared")
                        location = "" if where == "." else f"  (in {where})"
                        print(f"  {phase.capitalize():6} {resolved['command']}{location}  [{status}]")
                proven_modules = [
                    module for module in report.module_details
                    if module.get("test_command_status", "declared") != "declared"
                ]
                if report.module_details:
                    print(f"  Modules: {len(report.module_details)}"
                          f" ({len(proven_modules)} with a proven test command)")
                nothing_proven = (
                    phases["test"].get("command_status", "declared") == "declared"
                    and not proven_modules
                )
                if nothing_proven:
                    print()
                    print("  These commands are declared, not proven. Run 'buildanchor verify'")
                    print("  to execute a discovery probe and record how far each one gets.")
                print()
                print("README badge snippet:")
                print(f"  {badge}")
            return 0

        report = engine._inspect_cached()
        if args.command == "inspect":
            result = schema_module.render(report.to_dict(), args.schema)
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
            result = engine.repair_guidance(
                report=report,
                change=engine.change_impact(args.baseline, report, staged=args.staged),
            )

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
        elif args.format == "text" and sys.stderr.isatty():
            print(f"BuildAnchor blocked: {exc}", file=sys.stderr)
            if not args.interactive and args.command not in {"mcp", "serve"}:
                print(f"Tip: run `buildanchor {args.command} --interactive` for guided input.", file=sys.stderr)
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
    #
    # `inspect` and `change-impact` are included: a caller that scripts
    # `buildanchor inspect` on a directory with no build system was previously
    # told the run succeeded, while the report itself said "inconclusive". An
    # exit code that disagrees with the payload it accompanies is worse than no
    # exit code.
    if args.command in {"validate-change", "compatibility", "preflight", "plan",
                        "inspect", "change-impact"}:
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
            print("Status: installed")
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
            rec["why"] = _EXPLANATIONS.get(code, "why: BuildAnchor detected this based on static analysis of your project configuration.")
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
                    rec["why"] = _EXPLANATIONS.get(code, "why: BuildAnchor detected this based on static analysis of your project configuration.")
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
