#!/usr/bin/env python3
# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""How often is the obvious guess wrong?

Every other benchmark here compares BuildAnchor to an older BuildAnchor. That is
a changelog, not a reason to adopt anything. This one measures a fact about the
world instead:

    **A real repository frequently declares a test entry point that is not its
    ecosystem's default — and nothing about the repository tells you that until
    you have already run the wrong command.**

An agent that sees a ``pyproject.toml`` guesses ``pytest``. Flask declares
``[tool.tox]``. An agent that sees a ``go.mod`` guesses ``go test ./...``. Cobra
has a Makefile. Guessing is right often enough to feel safe and wrong often
enough to cost a turn every time, and you cannot tell which case you are in
without looking.

Ground truth here is not a heuristic and not our opinion: it is **the declaration
in the repository**, cited by file, so any reader can confirm a row by opening
that file. Where a project declares nothing, the ecosystem default *is* the right
answer and the row is scored that way — those are the repositories where this
tool earns nothing, and they are counted honestly.

The corpus is real, unmodified, public repositories cloned at benchmark time.
Nothing is a fixture we wrote.

    python benchmarks/head_to_head.py --format text
    python benchmarks/head_to_head.py --format json --output results.json
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Real repositories, chosen to span ecosystems and conventions — some declare a
#: task runner, some do not; some are single projects, some are workspaces.
CORPUS: tuple[dict[str, str], ...] = (
    {"repo": "pallets/flask", "ecosystem": "python"},
    {"repo": "psf/requests", "ecosystem": "python"},
    {"repo": "encode/httpx", "ecosystem": "python"},
    {"repo": "pydantic/pydantic", "ecosystem": "python"},
    {"repo": "sindresorhus/execa", "ecosystem": "node"},
    {"repo": "chalk/chalk", "ecosystem": "node"},
    {"repo": "expressjs/express", "ecosystem": "node"},
    {"repo": "spf13/cobra", "ecosystem": "go"},
    {"repo": "stretchr/testify", "ecosystem": "go"},
    {"repo": "BurntSushi/ripgrep", "ecosystem": "rust"},
    {"repo": "clap-rs/clap", "ecosystem": "rust"},
    {"repo": "casey/just", "ecosystem": "rust"},
)

#: What a single glance at the manifest suggests. This is the baseline: it is
#: what an agent writes down when it sees a marker file and nothing else.
NAIVE_GUESS: dict[str, tuple[str, str]] = {
    "pyproject.toml": ("python", "pytest"),
    "setup.py": ("python", "pytest"),
    "package.json": ("node", "npm test"),
    "go.mod": ("go", "go test ./..."),
    "Cargo.toml": ("rust", "cargo test"),
    "pom.xml": ("maven", "mvn test"),
    "build.gradle": ("gradle", "gradle test"),
}

#: Tokens that identify the runner a command actually invokes. Two commands
#: match when they invoke the same runner — `cargo test --workspace` and
#: `cargo test` are the same answer; `cargo test` and `just test` are not.
RUNNER_TOKENS: tuple[str, ...] = (
    "tox", "nox", "just", "task", "make", "mise", "hatch", "poetry",
    "pytest", "unittest", "npm", "pnpm", "yarn", "bun", "jest", "vitest", "mocha", "ava",
    "cargo", "go", "mvn", "gradle", "dotnet", "rake", "bundle",
)


def clone(repo: str, destination: Path) -> Path | None:
    """Shallow-clone a repository, or return ``None`` if it cannot be fetched."""
    target = destination / repo.split("/")[-1]
    result = subprocess.run(
        ["git", "clone", "--quiet", "--depth", "1", f"https://github.com/{repo}", str(target)],
        capture_output=True, text=True, timeout=180, check=False,
    )
    return target if result.returncode == 0 and target.is_dir() else None


# ---------------------------------------------------------------------------
# Ground truth: what the repository itself declares
#
# Not a heuristic and not our opinion. Each finding names the file and the line
# that declares it, so a reader can confirm any row by opening that file. CI
# workflow scraping was tried first and abandoned: reusable workflows, matrix
# expressions and Makefile indirection made it unreliable on half the corpus,
# and a benchmark whose ground truth is unreliable is worse than no benchmark.
# ---------------------------------------------------------------------------

def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def declared_entry_point(root: Path) -> dict | None:
    """What this repository declares as its way to run tests, and where.

    Returns ``None`` when the project declares nothing beyond its ecosystem's
    manifest — in which case the ecosystem default is the correct answer.
    """
    for name in ("justfile", "Justfile", ".justfile"):
        if (root / name).is_file() and re.search(r"(?m)^@?test\s*(\(|[a-z_ ]*)?:", _read(root / name)):
            return {"runner": "just", "evidence": f"{name}: a `test` recipe"}

    for name in ("Taskfile.yml", "Taskfile.yaml"):
        if (root / name).is_file() and re.search(r"(?m)^  test:", _read(root / name)):
            return {"runner": "task", "evidence": f"{name}: a `test` task"}

    for name in ("Makefile", "makefile", "GNUmakefile"):
        if (root / name).is_file() and re.search(r"(?m)^test\s*:(?!=)", _read(root / name)):
            return {"runner": "make", "evidence": f"{name}: a `test` target"}

    for name in ("mise.toml", ".mise.toml"):
        if (root / name).is_file() and "[tasks.test" in _read(root / name):
            return {"runner": "mise", "evidence": f"{name}: a `[tasks.test]` table"}

    if (root / "tox.ini").is_file():
        return {"runner": "tox", "evidence": "tox.ini"}
    if (root / "pyproject.toml").is_file():
        pyproject = _read(root / "pyproject.toml")
        if re.search(r"(?m)^\[tool\.tox[\].]", pyproject):
            return {"runner": "tox", "evidence": "pyproject.toml: a `[tool.tox]` table"}
        if re.search(r"(?m)^\[tool\.hatch\.envs\.[a-z]*\.scripts\]", pyproject):
            return {"runner": "hatch", "evidence": "pyproject.toml: hatch env scripts"}
    if (root / "noxfile.py").is_file():
        return {"runner": "nox", "evidence": "noxfile.py"}

    if (root / "package.json").is_file():
        try:
            scripts = json.loads(_read(root / "package.json")).get("scripts") or {}
        except (json.JSONDecodeError, AttributeError):
            scripts = {}
        if isinstance(scripts, dict) and "test" in scripts:
            # An npm `test` script IS the ecosystem default entry point, so this
            # is not a case where guessing fails. Recorded for completeness.
            return {"runner": "npm", "evidence": 'package.json: a "test" script',
                    "matches_default": True}
    return None


RUNNER_TOKENS: tuple[str, ...] = (
    "tox", "nox", "just", "task", "make", "mise", "hatch", "poetry",
    "pytest", "unittest", "npm", "pnpm", "yarn", "bun", "jest", "vitest", "mocha", "ava",
    "cargo", "go", "mvn", "gradle", "dotnet", "rake", "bundle",
)


def runner_of(command: str | None) -> str | None:
    """The tool a command actually invokes, ignoring wrappers and flags."""
    if not command:
        return None
    words = [word for word in re.split(r"[\s;&|]+", command.lower()) if word]
    for word in words:
        base = word.split("/")[-1]
        if base in RUNNER_TOKENS:
            if base in {"npm", "pnpm", "yarn", "bun"} and "run" in words:
                continue
            return base
    for word in words:
        if word.split("/")[-1] in RUNNER_TOKENS:
            return word.split("/")[-1]
    return None


# ---------------------------------------------------------------------------
# The two strategies
# ---------------------------------------------------------------------------

def strategy_guess(root: Path) -> str | None:
    """What a single glance at the manifest suggests."""
    for marker, (_ecosystem, command) in NAIVE_GUESS.items():
        if (root / marker).is_file():
            return command
    return None


def strategy_buildanchor(root: Path, executable: str) -> str | None:
    result = subprocess.run(
        [executable, "cmd", "test", "--workspace", str(root)],
        capture_output=True, text=True, timeout=120, check=False,
    )
    return result.stdout.strip() or None


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def evaluate(root: Path, executable: str) -> dict:
    declared = declared_entry_point(root)
    guess = strategy_guess(root)
    resolved = strategy_buildanchor(root, executable)

    # The expected answer is whatever the repository declares; where it declares
    # nothing, its ecosystem's default is the right answer.
    expected_runner = declared["runner"] if declared else runner_of(guess)
    guess_is_wrong = bool(declared) and not declared.get("matches_default") \
        and runner_of(guess) != expected_runner

    return {
        "declares": declared["evidence"] if declared else None,
        "expected_runner": expected_runner,
        "guess": guess,
        "guess_runner": runner_of(guess),
        "guess_correct": runner_of(guess) == expected_runner,
        "buildanchor": resolved,
        "buildanchor_runner": runner_of(resolved),
        "buildanchor_correct": runner_of(resolved) == expected_runner,
        "guessing_fails_here": guess_is_wrong,
        # `make test` that runs pytest is the same tool behind a different door.
        # Worth separating: only a genuinely different tool is likely to fail
        # outright, and overstating that would be the same sin this tool exists
        # to prevent.
        "different_tool": guess_is_wrong and _wraps_a_different_tool(root, declared, guess),
    }


def _wraps_a_different_tool(root: Path, declared: dict | None, guess: str | None) -> bool:
    """Whether the declared entry point ultimately runs something else."""
    if not declared or not guess:
        return False
    bodies = {
        "make": ("Makefile", "makefile", "GNUmakefile"),
        "just": ("justfile", "Justfile", ".justfile"),
    }.get(declared["runner"])
    if not bodies:
        return True
    for name in bodies:
        if not (root / name).is_file():
            continue
        text = _read(root / name)
        match = re.search(r"(?ms)^@?test\s*(?:\([^)]*\))?[a-z_ ]*:[^\n]*\n((?:[ \t]+[^\n]*\n)+)", text)
        body = match.group(1) if match else ""
        return runner_of(body) != runner_of(guess)
    return True


def run(corpus: tuple[dict[str, str], ...], executable: str, keep: bool) -> dict:
    workdir = Path(tempfile.mkdtemp(prefix="buildanchor-head-to-head-"))
    rows = []
    try:
        for entry in corpus:
            root = clone(entry["repo"], workdir)
            if root is None:
                rows.append({**entry, "name": entry["repo"].split("/")[-1], "error": "clone failed"})
                continue
            rows.append({**entry, "name": entry["repo"].split("/")[-1], **evaluate(root, executable)})
    finally:
        if keep:
            print(f"clones kept in {workdir}", file=sys.stderr)
        else:
            shutil.rmtree(workdir, ignore_errors=True)

    scored = [row for row in rows if "error" not in row]
    trap = [row for row in scored if row["guessing_fails_here"]]
    return {
        "repositories": len(rows),
        "scored": len(scored),
        "repositories_where_guessing_fails": len(trap),
        "repositories_where_a_different_tool_runs": sum(1 for row in scored if row.get("different_tool")),
        "guess_correct": sum(1 for row in scored if row["guess_correct"]),
        "buildanchor_correct": sum(1 for row in scored if row["buildanchor_correct"]),
        "percentages": {
            "guess": round(100.0 * sum(1 for r in scored if r["guess_correct"]) / len(scored), 1) if scored else None,
            "buildanchor": round(100.0 * sum(1 for r in scored if r["buildanchor_correct"]) / len(scored), 1) if scored else None,
            "guessing_fails": round(100.0 * len(trap) / len(scored), 1) if scored else None,
        },
        "rows": rows,
    }


def render(results: dict) -> str:
    lines = [
        "How often is the obvious guess wrong?",
        "",
        "  Real public repositories, cloned unmodified at benchmark time. The expected",
        "  answer is what each repository declares — cited by file, so you can check any",
        "  row yourself. Where a project declares nothing, its ecosystem default is the",
        "  right answer and is scored as such.",
        "",
        f"  {'repository':12} {'declares':38} {'guess':14} {'BuildAnchor':16}",
        f"  {'-' * 12} {'-' * 38} {'-' * 14} {'-' * 16}",
    ]
    for row in results["rows"]:
        name = row.get("name", row.get("repo", "?"))
        if row.get("error"):
            lines.append(f"  {name:12} {row['error']}")
            continue
        declares = row["declares"] or "nothing — the default applies"
        guess = ("OK " if row["guess_correct"] else "X  ") + (row["guess"] or "none")[:10]
        anchor = ("OK " if row["buildanchor_correct"] else "X  ") + (row["buildanchor"] or "none")[:12]
        lines.append(f"  {name:12} {declares[:38]:38} {guess:14} {anchor:16}")

    scored = results["scored"]
    percentages = results["percentages"]
    lines += [
        "",
        f"  {results['repositories_where_guessing_fails']} of {scored} repositories "
        f"({percentages['guessing_fails']}%) declare a test entry point that is not their",
        "  ecosystem's default. Nothing about the repository announces that.",
        "",
        f"  In {results['repositories_where_a_different_tool_runs']} of those, the declared entry "
        "point runs a genuinely different tool, so",
        "  the guess does not just differ — it runs something the project does not use.",
        "  In the rest it wraps the same tool with the project's own arguments.",
        "",
        f"    guessing from the manifest   {results['guess_correct']}/{scored}   ({percentages['guess']}%)",
        f"    BuildAnchor                  {results['buildanchor_correct']}/{scored}   ({percentages['buildanchor']}%)",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--output", help="Also write the JSON results here.")
    parser.add_argument("--executable", default="", help="BuildAnchor executable to test.")
    parser.add_argument("--keep-clones", action="store_true")
    parser.add_argument("--limit", type=int, help="Use only the first N repositories.")
    args = parser.parse_args()

    executable = args.executable
    if not executable:
        candidate = REPO_ROOT / ".venv" / "bin" / "buildanchor"
        executable = str(candidate) if candidate.is_file() else "buildanchor"

    corpus = CORPUS[: args.limit] if args.limit else CORPUS
    results = run(corpus, executable, args.keep_clones)
    if args.output:
        Path(args.output).write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2) if args.format == "json" else render(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
