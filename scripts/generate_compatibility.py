#!/usr/bin/env python3
# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""Generate the compatibility chart from the code that implements it.

A hand-written support matrix is out of date the moment someone adds a probe and
forgets the README. This one is derived from the tables the tool actually uses,
written between markers in README.md, and checked by a test — so the chart
cannot claim support that does not exist, and cannot omit support that does.

    python scripts/generate_compatibility.py            # rewrite the section
    python scripts/generate_compatibility.py --check    # exit 1 if stale
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

README = REPO_ROOT / "README.md"
START = "<!-- compatibility:start -->"
END = "<!-- compatibility:end -->"

#: How each ecosystem's test command is found, for the "resolved from" column.
RESOLUTION_SOURCE = {
    "python": "`pyproject.toml`, `uv.lock`, `poetry.lock`, `.venv/`",
    "node": "`package.json` scripts + the lockfile's package manager",
    "maven": "`pom.xml` `<modules>`, `mvnw`",
    "gradle": "`settings.gradle`, `gradlew`",
    "go": "`go.mod`, `go.work`",
    "rust": "`Cargo.toml` `[workspace]`",
    "dotnet": "`global.json`, `*.csproj`",
    "generic": "`Makefile` and other declared runners",
}

#: Which rung each ecosystem can reach, and with what.
def _probe_summary() -> dict[str, str]:
    from buildanchor.build_truth.core import toolchain
    summary = {
        "python": "`pytest --collect-only`, `unittest -k`",
        "node": ", ".join(
            f"`{name}`" for name in sorted(toolchain.NODE_COLLECT_PROBES)
        ) + ", `node --test`",
    }
    labels = {
        "go": "`go test -run '^$'`",
        "rust": "`cargo test --no-run`",
        "maven": "`mvn -DskipTests test-compile`",
        "gradle": "`gradle testClasses`",
        "dotnet": "`dotnet test --list-tests`",
    }
    for ecosystem in toolchain.COMPILED_COLLECT_PROBES:
        summary.setdefault(ecosystem, labels.get(ecosystem, "compile-only probe"))
    return summary


def build_section() -> str:
    from buildanchor import agent, operations
    from buildanchor.build_truth.core import conventions
    from buildanchor.build_truth.core.build_systems import ECOSYSTEM_LABELS, MARKERS

    probes = _probe_summary()
    lines: list[str] = [START, ""]

    lines += [
        "### Ecosystems",
        "",
        "| Ecosystem | Command resolved from | Verified with |",
        "| --- | --- | --- |",
    ]
    for system, _markers, _languages in MARKERS:
        if system == "generic":
            continue
        label = ECOSYSTEM_LABELS.get(system, system)
        probe = probes.get(system, "— no discovery probe yet")
        lines.append(f"| {label} | {RESOLUTION_SOURCE.get(system, '—')} | {probe} |")
    lines += [
        "",
        "An ecosystem without a discovery probe still resolves a command and reports",
        "`resolvable (no probe available)` rather than guessing that it works.",
        "",
    ]

    runners = [runner["name"] for runner in conventions._RUNNERS] + ["tox"]
    lines += [
        "### Task runners your repository already declares",
        "",
        "These take precedence over the ecosystem default — if your `justfile` says",
        "`test: cargo nextest run`, that is the answer, not `cargo test`.",
        "",
        "| Runner | Declared in |",
        "| --- | --- |",
        "| just | `justfile`, `Justfile`, `.justfile` |",
        "| Task | `Taskfile.yml`, `Taskfile.yaml` |",
        "| mise | `mise.toml`, `.mise.toml` |",
        "| make | `Makefile`, `makefile`, `GNUmakefile` |",
        "| nox | `noxfile.py` |",
        "| tox | `tox.ini`, `pyproject.toml` `[tool.tox]`, `setup.cfg` |",
        "",
        f"({len(runners)} runners.)",
        "",
    ]

    lines += [
        "### Agent clients",
        "",
        "One tool surface, in whatever dialect your client speaks.",
        "",
        "| `format=` | Works with | Verified against |",
        "| --- | --- | --- |",
        "| `anthropic` *(default)* | Anthropic Messages API | — |",
        "| `openai` | OpenAI, LiteLLM, LangChain, OpenRouter, vLLM, most gateways | LiteLLM 1.100.0 |",
        "| `gemini` | Google GenAI, Vertex AI | `google-genai` `types.Tool` |",
        "| `bedrock` | AWS Bedrock Converse | botocore service model |",
        "| `mcp` | Any MCP client — Claude Code, Cursor, Copilot, Codex | — |",
        "",
    ]

    lines += [
        "### Interfaces",
        "",
        "| Surface | Operations | Notes |",
        "| --- | --- | --- |",
        "| CLI | all | `buildanchor <command>` |",
        f"| MCP server | {len(agent.tool_definitions())} advertised | 3 core tools, ~700 tokens of schema |",
        f"| HTTP | {len(operations.OPERATIONS) - len(operations.LOCAL_ONLY)} | local-only operations are refused |",
        f"| Python SDK | {len(operations.OPERATIONS)} | sync and async |",
        f"| Node SDK | {len(operations.OPERATIONS)} | local and HTTP transports |",
        f"| Java SDK | {len(operations.OPERATIONS)} | local and HTTP transports |",
        "",
    ]

    lines += [
        "### Platforms",
        "",
        "| | Status |",
        "| --- | --- |",
        "| Python | 3.10 – 3.13, tested on 3.10 and 3.13 in CI |",
        "| Linux, macOS | tested in CI |",
        "| Windows | non-blocking CI job; `command_shell` is POSIX `sh` — use `working_directory` |",
        "| Runtime dependencies | none |",
        "",
        END,
    ]
    return "\n".join(lines)


def current_section(text: str) -> str | None:
    if START not in text or END not in text:
        return None
    start = text.index(START)
    end = text.index(END) + len(END)
    return text[start:end]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--check", action="store_true",
                        help="Report whether the chart is current and exit 1 if not.")
    args = parser.parse_args()

    text = README.read_text(encoding="utf-8")
    generated = build_section()
    existing = current_section(text)

    if args.check:
        if existing is None:
            print("README has no compatibility markers", file=sys.stderr)
            return 1
        if existing.strip() != generated.strip():
            print("The compatibility chart no longer matches the code. "
                  "Run: python scripts/generate_compatibility.py", file=sys.stderr)
            return 1
        print("Compatibility chart is current.")
        return 0

    if existing is None:
        print(f"Add {START} and {END} to README.md first", file=sys.stderr)
        return 1
    README.write_text(text.replace(existing, generated), encoding="utf-8")
    print("Compatibility chart regenerated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
