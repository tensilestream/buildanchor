#!/usr/bin/env python3
# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""Does BuildAnchor's output actually work?

Token-reduction percentages measure the wrong thing. An agent does not read
every manifest in a repository; it guesses a command, runs it, reads the
failure, and repairs — and that loop is where the cost is. The number that
matters is therefore not how small the report is but whether the command in it
runs.

This harness measures four things, each re-runnable and each falsifiable:

1. **Command correctness** — the fraction of discovered modules whose emitted
   test command exits 0 when actually executed. Commands are run, not inspected.
2. **Discovery completeness** — the fraction of project markers in the report's
   own evidence that resolve to a module.
3. **Latency** — wall-clock inspection time against repository size.
4. **Agent context cost** — tokens of MCP tool schema resident on every turn.

Every measurement is taken twice: once against the released baseline (the
version at git HEAD~ or a given ref) and once against the working tree, so the
numbers are a comparison rather than an assertion.

Fixtures are generated offline and deterministically. Each Python project gets a
real virtualenv holding a private dependency, so that dependency is genuinely
importable from the project's own environment and genuinely not importable from
the repository root — the failure reported from production, reproduced without a
network install.

    python benchmarks/credibility_benchmark.py --format text
    python benchmarks/credibility_benchmark.py --format json --baseline v1.1.6
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import venv
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_polyglot_fixture(root: Path, *, with_environments: bool = True) -> dict:
    """A polyglot monorepo of sibling projects at the root.

    No root ``package.json``, no workspace file, no ``apps/``-style convention —
    the shape reported from production, and the one that separates a discovery
    rule that generalises from one that only handles conventional layouts.
    """
    modules = {"service-a": "svc_a_dep", "service-b": "svc_b_dep", "lib-a": "lib_a_dep"}
    for name, dependency in modules.items():
        module = root / name
        _write(module / "pyproject.toml",
               f'[project]\nname = "{name}"\nversion = "0.1.0"\n'
               f'dependencies = ["{dependency}>=1.0", "httpx>=0.25.0"]\n\n'
               f'[project.optional-dependencies]\n'
               f'dev = ["pytest>=7", "pytest-asyncio>=0.23", "coverage>=7"]\n\n'
               f'[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
        # The test imports a dependency that exists only in this project's
        # environment. Run from the repository root, it cannot be imported.
        _write(module / "tests" / "test_unit.py",
               f"import {dependency}\n\n\ndef test_dependency_is_available():\n"
               f"    assert {dependency}.VALUE == 42\n")
        if with_environments:
            _install_local_dependency(module, dependency)

    for name in ("web-ui", "bot"):
        # `node --test` needs no install, so the emitted command can be
        # executed. The question under test is whether BuildAnchor's command is
        # correct, not whether someone remembered to run `npm install`.
        _write(root / name / "package.json", json.dumps({
            "name": name,
            "scripts": {"test": "node --test", "build": "node build.js"},
            "devDependencies": {"vitest": "^1.0.0"},
            "dependencies": {"react": "^18.0.0"} if name == "web-ui" else {},
        }, indent=2))
        _write(root / name / "package-lock.json", json.dumps({"lockfileVersion": 3}))
        _write(root / name / "src" / "index.ts", "export const ready = true;\n")
        _write(root / name / "index.test.js",
               "const test = require('node:test');\n"
               "const assert = require('node:assert');\n"
               f"test('{name} loads', () => {{ assert.ok(true); }});\n")

    # A generic marker that implies a build system but no language.
    _write(root / "Dockerfile", "FROM python:3.11-slim\n")
    return {"python_modules": list(modules), "node_modules": ["web-ui", "bot"]}


def _install_local_dependency(module: Path, dependency: str) -> None:
    """Create a real virtualenv for the module holding one private dependency.

    The environment gets pytest by pointing a ``.pth`` file at the site-packages
    of the interpreter running this harness, and gets its private dependency
    from a directory only this module can see. No network, no install step, and
    the dependency is genuinely unimportable from anywhere else — which is the
    condition that makes a root-run command fail the way it does in production.
    """
    environment = module / ".venv"
    venv.EnvBuilder(system_site_packages=False, with_pip=False, symlinks=True).create(environment)
    site_packages = next(iter(environment.glob("lib/*/site-packages")), None)
    if site_packages is None:
        site_packages = environment / "Lib" / "site-packages"
        site_packages.mkdir(parents=True, exist_ok=True)

    private = module / "_vendor"
    _write(private / f"{dependency}.py", "VALUE = 42\n")

    shared = [path for path in sys.path if path.endswith("site-packages")]
    _write(site_packages / "_benchmark_paths.pth", "\n".join([*shared, str(private.resolve())]) + "\n")

    # A lockfile beside the manifest is the signal that the project owns an
    # environment; BuildAnchor should prefer it over the ambient interpreter.
    _write(module / "requirements.txt", f"{dependency}>=1.0\n")


def build_single_project_fixture(root: Path, kind: str = "python") -> dict:
    """One project at the repository root — the common case, not the exotic one.

    A tool that only pays off on a 40-package monorepo will not be installed by
    the people who would benefit from it on a Tuesday, so the single-project
    shape is measured on the same terms as the monorepo one.
    """
    if kind == "python":
        _write(root / "pyproject.toml",
               '[project]\nname = "solo"\nversion = "0.1.0"\n'
               'dependencies = ["solo_dep>=1.0"]\n\n'
               '[project.optional-dependencies]\ndev = ["pytest>=7", "coverage>=7"]\n\n'
               '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
        _write(root / "src" / "solo" / "__init__.py", "VERSION = '0.1.0'\n")
        _write(root / "tests" / "test_solo.py",
               "import solo_dep\n\n\ndef test_dependency():\n    assert solo_dep.VALUE == 42\n")
        _install_local_dependency(root, "solo_dep")
        return {"kind": kind, "expected_ecosystem": "python", "executable": True}

    if kind == "node":
        # `node --test` is built into Node 18+, so this project's suite runs
        # with no install and no network — the command can actually be executed
        # rather than merely inspected.
        _write(root / "package.json", json.dumps({
            "name": "solo-app", "version": "1.0.0",
            "scripts": {"test": "node --test", "build": "node build.js"},
        }, indent=2))
        _write(root / "package-lock.json", json.dumps({"lockfileVersion": 3}))
        _write(root / "index.js", "module.exports.ready = true;\n")
        _write(root / "index.test.js",
               "const test = require('node:test');\n"
               "const assert = require('node:assert');\n"
               "const { ready } = require('./index.js');\n\n"
               "test('ready', () => { assert.strictEqual(ready, true); });\n")
        return {"kind": kind, "expected_ecosystem": "node", "executable": True}

    if kind == "go":
        _write(root / "go.mod", "module example.com/solo\n\ngo 1.22\n")
        _write(root / "main.go", "package main\n\nfunc main() {}\n")
        _write(root / "main_test.go", "package main\n\nimport \"testing\"\n\nfunc TestMain(t *testing.T) {}\n")
        return {"kind": kind, "expected_ecosystem": "go", "executable": False}

    if kind == "rust":
        _write(root / "Cargo.toml", '[package]\nname = "solo"\nversion = "0.1.0"\nedition = "2021"\n')
        _write(root / "src" / "lib.rs", "pub fn add(a: i32, b: i32) -> i32 { a + b }\n")
        return {"kind": kind, "expected_ecosystem": "rust", "executable": False}

    raise ValueError(f"unknown single-project kind: {kind}")


def build_root_plus_satellites_fixture(root: Path) -> dict:
    """A root project with an SDK subdirectory — not a monorepo, and easy to
    mislabel as one. Advertising `--scope ui` here is noise."""
    _write(root / "pyproject.toml",
           '[project]\nname = "core"\nversion = "0.1.0"\n\n'
           '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    _write(root / "tests" / "test_core.py", "import core_dep\n\n\ndef test_x():\n    assert core_dep.VALUE == 42\n")
    _install_local_dependency(root, "core_dep")
    sdk = root / "sdk" / "node"
    _write(sdk / "package.json", json.dumps({"name": "@acme/sdk", "scripts": {"test": "node --test"}}))
    _write(sdk / "index.js", "module.exports.ok = true;\n")
    _write(sdk / "index.test.js",
           "const test = require('node:test');\n"
           "const assert = require('node:assert');\n"
           "test('ok', () => { assert.ok(require('./index.js').ok); });\n")
    return {"expected_shape": "root-plus-satellites"}


def build_scale_fixture(root: Path, packages: int = 12, files_each: int = 400,
                        vendored_files: int = 3000) -> dict:
    """A repository large enough for traversal cost to be visible."""
    for index in range(packages):
        module = root / "packages" / f"mod-{index:02d}"
        _write(module / "package.json", json.dumps(
            {"name": f"mod-{index:02d}", "scripts": {"test": "vitest run"}}))
        for file_index in range(files_each):
            _write(module / "src" / f"file_{file_index:04d}.ts",
                   f"export const value{file_index} = {file_index};\n" + "// padding\n" * 120)
    vendored = root / "packages" / "mod-00" / "node_modules"
    for index in range(vendored_files):
        _write(vendored / f"pkg{index % 50}" / "lib" / f"f{index}.js", "x" * 2000)

    # Real repositories are git repositories, and they ignore their generated
    # trees. A fixture that is neither measures a path almost nobody is on.
    _write(root / ".gitignore", "node_modules/\ncoverage/\n")
    coverage = root / "coverage" / "html"
    for index in range(1500):
        _write(coverage / f"report{index}.html", "<html></html>" * 40)
    subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True, check=False)

    total = sum(1 for path in root.rglob("*") if path.is_file())
    return {
        "files_on_disk": total,
        "vendored_files": vendored_files,
        "ignored_files": vendored_files + 1500,
        "git": True,
    }


# ---------------------------------------------------------------------------
# Running a given version of BuildAnchor
# ---------------------------------------------------------------------------

PROBE_SCRIPT = r'''
import json, sys, time
sys.path.insert(0, sys.argv[1])
from buildanchor.engine import BuildAnchor

workspace = sys.argv[2]
iterations = int(sys.argv[3])

latencies = []
for _ in range(iterations):
    started = time.perf_counter()
    report = BuildAnchor(workspace).inspect()
    latencies.append((time.perf_counter() - started) * 1000)

report = BuildAnchor(workspace).inspect()
modules = []
for detail in report.module_details:
    modules.append({
        "path": detail.get("path"),
        "ecosystem": detail.get("ecosystem"),
        "category": detail.get("category"),
        "test_command": detail.get("test_command"),
        "working_directory": detail.get("working_directory", "."),
    })

markers = ("package.json", "pyproject.toml", "setup.py", "pom.xml", "Cargo.toml", "go.mod")
evidence_markers = sorted({e.path for e in report.evidence if e.path.split("/")[-1] in markers})

print(json.dumps({
    "latencies_ms": latencies,
    "repository": getattr(report, "repository", {}),
    "modules": modules,
    "languages": report.languages,
    "dependencies": [
        {"coordinate": d.get("coordinate"), "module": d.get("module")}
        for d in report.dependencies
    ],
    "evidence_markers": evidence_markers,
}))
'''


def materialise_baseline(ref: str, destination: Path) -> Path | None:
    """Check out ``ref``'s ``src`` tree so the released code can be run as-is."""
    destination.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["git", "archive", ref, "src"], cwd=REPO_ROOT,
        capture_output=True, check=False,
    )
    if result.returncode != 0:
        return None
    archive = destination / "baseline.tar"
    archive.write_bytes(result.stdout)
    subprocess.run(["tar", "-xf", str(archive)], cwd=destination, check=False,
                   capture_output=True)
    source = destination / "src"
    return source if (source / "buildanchor").is_dir() else None


def probe(source_root: Path, workspace: Path, iterations: int) -> dict | None:
    """Run one BuildAnchor version against a workspace, out of process."""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
        handle.write(PROBE_SCRIPT)
        script = handle.name
    try:
        result = subprocess.run(
            [sys.executable, script, str(source_root), str(workspace), str(iterations)],
            capture_output=True, text=True, timeout=600, check=False,
        )
    finally:
        os.unlink(script)
    if result.returncode != 0:
        return {"error": result.stderr.strip()[-800:]}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": f"unparseable probe output: {result.stdout[:400]}"}


ROOT_COMMAND_SCRIPT = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from buildanchor.engine import BuildAnchor
resolved = BuildAnchor(sys.argv[2]).resolve_command("test")
print(json.dumps({
    "command": resolved.get("command"),
    "working_directory": resolved.get("working_directory", "."),
    "ecosystem": resolved.get("ecosystem"),
}))
'''


def _root_command(source_root: Path, workspace: Path) -> str | None:
    """Ask a given version for the single-project test command."""
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
        handle.write(ROOT_COMMAND_SCRIPT)
        script = handle.name
    try:
        result = subprocess.run(
            [sys.executable, script, str(source_root), str(workspace)],
            capture_output=True, text=True, timeout=120, check=False,
        )
    finally:
        os.unlink(script)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout).get("command")
    except json.JSONDecodeError:
        return None


def _root_ecosystem(workspace: Path) -> str:
    for name, ecosystem in (("pyproject.toml", "python"), ("package.json", "node"),
                            ("go.mod", "go"), ("Cargo.toml", "rust"), ("pom.xml", "maven")):
        if (workspace / name).is_file():
            return ecosystem
    return "generic"


# ---------------------------------------------------------------------------
# Measurements
# ---------------------------------------------------------------------------

#: Ecosystems whose fixtures here are dependency-free, so their commands can be
#: executed without a package install. A benchmark that needs the network is a
#: benchmark nobody re-runs.
EXECUTABLE_ECOSYSTEMS = frozenset({"python", "node", "go", "rust"})


def measure_command_correctness(workspace: Path, modules: list[dict], timeout: int = 180) -> dict:
    """Execute each emitted test command and record whether it exits 0.

    A command whose entrypoint is not installed on this machine is reported as
    ``toolchain_absent`` and excluded from the correctness figure, rather than
    counted as either a pass or a failure.
    """
    outcomes = []
    for module in modules:
        command = module.get("test_command")
        if not command:
            outcomes.append({**module, "result": "no_command"})
            continue
        directory = workspace / module.get("working_directory", ".")
        head = command.split()[0]
        entrypoint_resolves = bool(shutil.which(head)) or (directory / head).is_file()
        if not entrypoint_resolves:
            # The toolchain is not installed here. Reporting that plainly is
            # better than folding it into a correctness figure either way.
            outcomes.append({**module, "result": "toolchain_absent", "missing": head})
            continue
        if module.get("ecosystem") not in EXECUTABLE_ECOSYSTEMS:
            outcomes.append({**module, "result": "not_executable"})
            continue
        if not directory.is_dir():
            outcomes.append({**module, "result": "bad_working_directory"})
            continue
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command.split(), cwd=directory, capture_output=True, text=True,
                timeout=timeout, check=False, shell=False,
            )
            exit_code = completed.returncode
            tail = ((completed.stdout or "") + (completed.stderr or ""))[-400:]
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
            exit_code, tail = None, str(exc)[-400:]
        outcomes.append({
            **module,
            "result": "runs" if exit_code == 0 else "fails",
            "exit_code": exit_code,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "output_tail": "" if exit_code == 0 else tail,
        })

    executed = [o for o in outcomes if o["result"] in {"runs", "fails"}]
    return {
        "modules_total": len(outcomes),
        "modules_executed": len(executed),
        "modules_runs": sum(1 for o in executed if o["result"] == "runs"),
        "correctness_pct": round(
            100.0 * sum(1 for o in executed if o["result"] == "runs") / len(executed), 1
        ) if executed else None,
        "outcomes": outcomes,
    }


def measure_discovery(result: dict) -> dict:
    """Fraction of project markers in evidence that resolve to a module."""
    module_dirs = {m.get("path", ".") for m in result.get("modules", [])} | {"."}
    markers = result.get("evidence_markers", [])
    resolved = [m for m in markers if (str(Path(m).parent) or ".") in module_dirs]
    return {
        "markers_total": len(markers),
        "markers_resolved": len(resolved),
        "unresolved": sorted(set(markers) - set(resolved)),
        "completeness_pct": round(100.0 * len(resolved) / len(markers), 1) if markers else None,
    }


def measure_mcp_context_cost() -> dict:
    """Tokens of MCP tool schema resident in the agent's context per turn."""
    sys.path.insert(0, str(SRC))
    try:
        from buildanchor.transports import TOOLS, advertised_tools
    except ImportError as exc:
        return {"error": str(exc)}

    def tokens(payload) -> int:
        text = json.dumps(payload)
        try:
            import tiktoken
            return len(tiktoken.get_encoding("cl100k_base").encode(text))
        except Exception:
            return len(text) // 4

    return {
        "full_registry_tools": len(TOOLS),
        "full_registry_tokens": tokens(TOOLS),
        "advertised_tools": len(advertised_tools("core")),
        "advertised_tokens": tokens(advertised_tools("core")),
        "tokens_saved_per_turn": tokens(TOOLS) - tokens(advertised_tools("core")),
        "estimator": "tiktoken cl100k_base" if _has_tiktoken() else "chars/4 approximation",
    }


def _has_tiktoken() -> bool:
    try:
        import tiktoken  # noqa: F401
        return True
    except ImportError:
        return False


def summarise_latency(latencies: list[float]) -> dict:
    if not latencies:
        return {}
    ordered = sorted(latencies)
    return {
        "median_ms": round(statistics.median(ordered), 1),
        "p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 1),
        "iterations": len(ordered),
    }


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def run(baseline_ref: str, iterations: int, keep: bool) -> dict:
    workdir = Path(tempfile.mkdtemp(prefix="buildanchor-credibility-"))
    results: dict = {
        "baseline_ref": baseline_ref,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "fixtures": {},
        "mcp_context_cost": measure_mcp_context_cost(),
    }
    try:
        baseline_src = materialise_baseline(baseline_ref, workdir / "baseline")
        results["baseline_available"] = baseline_src is not None

        # --- correctness and discovery, on the reported repository shape ---
        for label, versions in (("baseline", baseline_src), ("working_tree", SRC)):
            if versions is None:
                continue
            fixture = workdir / f"polyglot-{label}"
            fixture.mkdir(parents=True, exist_ok=True)
            build_polyglot_fixture(fixture)
            probed = probe(versions, fixture, iterations=3)
            if probed is None or "error" in probed:
                results["fixtures"].setdefault("polyglot", {})[label] = {
                    "error": (probed or {}).get("error", "probe failed")}
                continue
            results["fixtures"].setdefault("polyglot", {})[label] = {
                "modules_found": len(probed["modules"]),
                "module_paths": sorted(m["path"] for m in probed["modules"]),
                "ecosystems": sorted({m["ecosystem"] for m in probed["modules"]}),
                "languages": probed["languages"],
                "dependency_count": len(probed["dependencies"]),
                "dependency_modules": sorted({
                    d.get("module") or "unattributed" for d in probed["dependencies"]
                }),
                "malformed_coordinates": [
                    d["coordinate"] for d in probed["dependencies"]
                    if d.get("coordinate") and ('"' in d["coordinate"] or "," in d["coordinate"])
                ],
                "discovery": measure_discovery(probed),
                "command_correctness": measure_command_correctness(fixture, probed["modules"]),
                "latency": summarise_latency(probed["latencies_ms"]),
            }

        # --- single-project and root-plus-satellites shapes ---
        shapes: list[tuple[str, str, object]] = [
            ("single-python", "single-project", lambda r: build_single_project_fixture(r, "python")),
            ("single-node", "single-project", lambda r: build_single_project_fixture(r, "node")),
            ("single-go", "single-project", lambda r: build_single_project_fixture(r, "go")),
            ("single-rust", "single-project", lambda r: build_single_project_fixture(r, "rust")),
            ("root-plus-sdk", "root-plus-satellites", build_root_plus_satellites_fixture),
        ]
        for fixture_name, expected_shape, builder in shapes:
            entry: dict = {"expected_shape": expected_shape}
            for label, versions in (("baseline", baseline_src), ("working_tree", SRC)):
                if versions is None:
                    continue
                fixture = workdir / f"{fixture_name}-{label}"
                fixture.mkdir(parents=True, exist_ok=True)
                builder(fixture)
                probed = probe(versions, fixture, iterations=2)
                if probed is None or "error" in probed:
                    entry[label] = {"error": (probed or {}).get("error", "probe failed")}
                    continue
                # The root project is not in `module_details` — modules are
                # sub-projects — so measure it explicitly alongside them, or a
                # root-plus-satellites repository would only ever be judged on
                # its satellite.
                to_measure = [{
                    "path": ".",
                    "ecosystem": _root_ecosystem(fixture),
                    "working_directory": ".",
                    "test_command": _root_command(versions, fixture),
                }]
                to_measure += [m for m in probed["modules"] if m.get("path") not in ("", ".")]
                entry[label] = {
                    "shape": (probed.get("repository") or {}).get("shape", "not-reported"),
                    "languages": probed["languages"],
                    "command_correctness": measure_command_correctness(fixture, to_measure),
                    "latency": summarise_latency(probed["latencies_ms"]),
                }
            results["fixtures"].setdefault("shapes", {})[fixture_name] = entry

        # --- per-ecosystem corpus (absorbed from the retired harnesses) ---
        corpus = REPO_ROOT / "benchmarks" / "fixtures"
        if corpus.is_dir():
            for fixture in sorted(path for path in corpus.iterdir() if path.is_dir()):
                probed = probe(SRC, fixture, iterations=3)
                if probed is None or "error" in probed:
                    results["fixtures"].setdefault("ecosystems", {})[fixture.name] = {
                        "error": (probed or {}).get("error", "probe failed")}
                    continue
                results["fixtures"].setdefault("ecosystems", {})[fixture.name] = {
                    "command": _root_command(SRC, fixture),
                    "languages": probed["languages"],
                    "shape": (probed.get("repository") or {}).get("shape", "not-reported"),
                    "latency": summarise_latency(probed["latencies_ms"]),
                }

        # --- latency at scale ---
        scale = workdir / "scale"
        scale.mkdir(parents=True, exist_ok=True)
        scale_info = build_scale_fixture(scale)
        results["fixtures"]["scale"] = {"repository": scale_info}
        for label, versions in (("baseline", baseline_src), ("working_tree", SRC)):
            if versions is None:
                continue
            probed = probe(versions, scale, iterations=iterations)
            if probed is None or "error" in probed:
                results["fixtures"]["scale"][label] = {"error": (probed or {}).get("error", "probe failed")}
                continue
            results["fixtures"]["scale"][label] = {
                "latency": summarise_latency(probed["latencies_ms"]),
                "modules_found": len(probed["modules"]),
            }
        return results
    finally:
        if keep:
            print(f"fixtures kept in {workdir}", file=sys.stderr)
        else:
            shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

#: What CI enforces. These are the claims the README makes; if one stops being
#: true, the build should fail rather than the documentation quietly becoming
#: wrong. Latency is deliberately absent — it varies too much across runners to
#: assert without producing flaky failures that teach people to ignore CI.
THRESHOLDS: dict[str, object] = {
    "polyglot_command_correctness_pct": 100.0,
    "polyglot_discovery_completeness_pct": 100.0,
    "polyglot_max_malformed_coordinates": 0,
    "polyglot_min_modules": 5,
    "shape_classification_must_match": True,
    "every_ecosystem_resolves_a_command": True,
}


def evaluate_thresholds(results: dict) -> list[str]:
    """Return one message per violated threshold; empty means the claims hold."""
    failures: list[str] = []
    current = results.get("fixtures", {}).get("polyglot", {}).get("working_tree")
    if not current or "error" in current:
        return [f"polyglot fixture did not run: {(current or {}).get('error', 'missing')}"]

    correctness = current["command_correctness"]
    actual = correctness.get("correctness_pct")
    expected = THRESHOLDS["polyglot_command_correctness_pct"]
    if actual is None or actual < expected:
        broken = [o for o in correctness["outcomes"] if o.get("result") == "fails"]
        detail = "; ".join(f"{o.get('path')}: {o.get('output_tail', '')[:120]}" for o in broken[:3])
        failures.append(f"command correctness {actual}% < {expected}% — {detail}")

    discovery = current["discovery"].get("completeness_pct")
    if discovery is None or discovery < THRESHOLDS["polyglot_discovery_completeness_pct"]:
        unresolved = ", ".join(current["discovery"]["unresolved"][:4])
        failures.append(f"discovery completeness {discovery}% — unresolved markers: {unresolved}")

    malformed = current["malformed_coordinates"]
    if len(malformed) > THRESHOLDS["polyglot_max_malformed_coordinates"]:
        failures.append(f"{len(malformed)} malformed dependency coordinate(s): {malformed[:2]}")

    if current["modules_found"] < THRESHOLDS["polyglot_min_modules"]:
        failures.append(
            f"discovered {current['modules_found']} modules, expected at least "
            f"{THRESHOLDS['polyglot_min_modules']} — a discovery regression"
        )

    if THRESHOLDS["every_ecosystem_resolves_a_command"]:
        for name, entry in results.get("fixtures", {}).get("ecosystems", {}).items():
            if "error" in entry:
                failures.append(f"{name}: probe failed — {entry['error'][:80]}")
            elif not entry.get("command"):
                failures.append(f"{name}: no test command resolved")

    if THRESHOLDS["shape_classification_must_match"]:
        for name, entry in results.get("fixtures", {}).get("shapes", {}).items():
            reported = entry.get("working_tree", {}).get("shape")
            expected_shape = entry.get("expected_shape")
            if reported and reported != expected_shape:
                failures.append(f"{name}: classified '{reported}', expected '{expected_shape}'")
    return failures


def render_text(results: dict) -> str:
    lines: list[str] = []
    add = lines.append
    add("BuildAnchor credibility benchmark")
    add(f"baseline: {results['baseline_ref']}   python: {results['python']}   platform: {results['platform']}")
    if not results.get("baseline_available"):
        add("WARNING: baseline ref could not be materialised; showing working tree only.")
    add("")

    polyglot = results["fixtures"].get("polyglot", {})
    if polyglot:
        add("1. Does the emitted test command run?")
        add("   Polyglot monorepo: 3 Python projects with their own virtualenvs, 2 Node packages,")
        add("   all at the repository root. Commands are executed, not inspected.")
        add("")
        add(f"   {'':14} {'modules':>8} {'executed':>9} {'run OK':>7} {'correctness':>12}")
        for label in ("baseline", "working_tree"):
            entry = polyglot.get(label)
            if not entry or "error" in entry:
                add(f"   {label:14} {'error':>8}  {entry.get('error', '')[:40] if entry else ''}")
                continue
            correctness = entry["command_correctness"]
            pct = correctness["correctness_pct"]
            add(f"   {label:14} {correctness['modules_total']:>8} {correctness['modules_executed']:>9} "
                f"{correctness['modules_runs']:>7} {(str(pct) + '%') if pct is not None else 'n/a':>12}")
        add("")

        add("2. Discovery completeness — project markers in evidence that resolve to a module")
        for label in ("baseline", "working_tree"):
            entry = polyglot.get(label)
            if not entry or "error" in entry:
                continue
            discovery = entry["discovery"]
            add(f"   {label:14} {discovery['markers_resolved']}/{discovery['markers_total']} "
                f"({discovery['completeness_pct']}%)  ecosystems: {', '.join(entry['ecosystems'])}")
            if discovery["unresolved"]:
                add(f"   {'':14} unresolved: {', '.join(discovery['unresolved'][:4])}")
        add("")

        add("3. Report correctness")
        for label in ("baseline", "working_tree"):
            entry = polyglot.get(label)
            if not entry or "error" in entry:
                continue
            add(f"   {label:14} languages: {', '.join(entry['languages']) or 'none'}")
            add(f"   {'':14} dependencies: {entry['dependency_count']} "
                f"from {len(entry['dependency_modules'])} module(s); "
                f"malformed: {len(entry['malformed_coordinates'])}")
        add("")

    shapes = results["fixtures"].get("shapes", {})
    if shapes:
        add("4. Repository shapes — single-project repositories, not just monorepos")
        add("   Shape classification, and whether the root command runs where one can be executed.")
        add("")
        add(f"   {'fixture':16} {'expected':22} {'baseline says':22} {'now says':22} {'command'}")
        for name, entry in shapes.items():
            expected = entry.get("expected_shape", "?")
            baseline = entry.get("baseline", {}).get("shape", "-")
            current = entry.get("working_tree", {}).get("shape", "-")
            correctness = entry.get("working_tree", {}).get("command_correctness", {})
            executed = correctness.get("modules_executed", 0)
            if executed:
                base_correct = entry.get("baseline", {}).get("command_correctness", {})
                verdict = (f"{base_correct.get('modules_runs', 0)}/{base_correct.get('modules_executed', 0)}"
                           f" -> {correctness.get('modules_runs', 0)}/{executed} run")
            else:
                missing = sorted({
                    o.get("missing", "?") for o in correctness.get("outcomes", [])
                    if o.get("result") == "toolchain_absent"
                })
                verdict = f"{', '.join(missing)} not installed" if missing else "no command"
            add(f"   {name:16} {expected:22} {baseline:22} {current:22} {verdict}")
        add("")
        add("   'not-reported' means that version had no notion of repository shape.")
        add("")

    ecosystems = results["fixtures"].get("ecosystems", {})
    if ecosystems:
        add("5. Per-ecosystem corpus — a command is resolved for every supported ecosystem")
        add("")
        add(f"   {'fixture':20} {'shape':22} {'median':>8}  command")
        for name, entry in ecosystems.items():
            if "error" in entry:
                add(f"   {name:20} error: {entry['error'][:50]}")
                continue
            latency = entry["latency"].get("median_ms")
            add(f"   {name:20} {entry['shape']:22} {str(latency) + ' ms':>8}  {entry['command'] or 'none'}")
        add("")

    scale = results["fixtures"].get("scale", {})
    if scale:
        repository = scale.get("repository", {})
        add(f"6. Latency at scale — {repository.get('files_on_disk', '?')} files on disk in a git "
            f"repository, {repository.get('ignored_files', 0)} of them gitignored")
        for label in ("baseline", "working_tree"):
            entry = scale.get(label)
            if not entry or "error" in entry:
                continue
            latency = entry["latency"]
            add(f"   {label:14} median {latency.get('median_ms')} ms   p95 {latency.get('p95_ms')} ms")
        add("")

    cost = results.get("mcp_context_cost", {})
    if cost and "error" not in cost:
        add("7. Agent context cost — MCP tool schema resident on every turn")
        add(f"   full registry  {cost['full_registry_tools']:>3} tools  {cost['full_registry_tokens']:>6} tokens")
        add(f"   advertised     {cost['advertised_tools']:>3} tools  {cost['advertised_tokens']:>6} tokens")
        add(f"   saved per turn                 {cost['tokens_saved_per_turn']:>6} tokens  ({cost['estimator']})")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--baseline", default="HEAD",
                        help="Git ref to compare against (default: HEAD, the last release).")
    parser.add_argument("--iterations", type=int, default=5, help="Latency iterations.")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--output", help="Write the JSON results to this path as well.")
    parser.add_argument("--keep-fixtures", action="store_true",
                        help="Leave the generated fixtures on disk for inspection.")
    parser.add_argument("--assert-thresholds", action="store_true",
                        help="Exit 1 if any published claim no longer holds. For CI.")
    args = parser.parse_args()

    results = run(args.baseline, args.iterations, args.keep_fixtures)
    if args.output:
        Path(args.output).write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2) if args.format == "json" else render_text(results))

    if args.assert_thresholds:
        failures = evaluate_thresholds(results)
        if failures:
            print("\nTHRESHOLDS NOT MET:", file=sys.stderr)
            for failure in failures:
                print(f"  - {failure}", file=sys.stderr)
            return 1
        print("\nAll published claims hold.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
