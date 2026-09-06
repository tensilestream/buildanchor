# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""Per-directory toolchain resolution.

A monorepo module declares its own environment: a ``uv.lock`` beside a
``pyproject.toml`` means ``uv run``, a ``.venv/`` means that interpreter, a
``pnpm-lock.yaml`` means ``pnpm``. BuildAnchor already reads all of these as
markers, so the signal is in hand; this module turns it into the command the
module actually needs, expressed relative to the module's own directory.

Everything here is a pure function of the filesystem plus ``PATH``. Nothing is
executed.
"""

from __future__ import annotations

import shutil
from pathlib import Path

# Manifest and lock files whose contents determine a module's toolchain. A
# verification result stays valid exactly as long as the digest of these does.
MANIFEST_FILES: tuple[str, ...] = (
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "requirements-dev.txt",
    "uv.lock", "poetry.lock", "Pipfile.lock",
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb", "bun.lock",
    "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
    "Cargo.toml", "Cargo.lock", "go.mod", "go.sum",
)


def _venv_python(directory: Path) -> Path | None:
    """Return the interpreter of a virtualenv declared inside ``directory``."""
    for env_name in (".venv", "venv"):
        for relative in ("bin/python", "Scripts/python.exe"):
            candidate = directory / env_name / relative
            if candidate.is_file():
                return candidate
    return None


def python_runner(directory: Path) -> tuple[list[str], str]:
    """Return ``(argv_prefix, source)`` for running Python tools in ``directory``.

    The prefix is expressed relative to ``directory`` so that the resulting
    command is correct when run with that directory as the working directory.
    """
    if (directory / "uv.lock").is_file() and shutil.which("uv"):
        return ["uv", "run"], "uv.lock"
    if (directory / "poetry.lock").is_file() and shutil.which("poetry"):
        return ["poetry", "run"], "poetry.lock"
    interpreter = _venv_python(directory)
    if interpreter is not None:
        return [str(interpreter.relative_to(directory))], str(interpreter.relative_to(directory))
    # No environment is declared. Say so by falling back to the ambient
    # interpreter; callers surface this as a lower-confidence command.
    return ["python"], "ambient interpreter"


def python_tool_available(directory: Path, tool: str = "pytest") -> tuple[bool | None, str]:
    """Whether ``tool`` is installed in the environment declared in ``directory``.

    Returns ``None`` when no environment is declared, because an ambient
    interpreter cannot be inspected without importing — which would be an
    execution, and the ``resolvable`` rung promises not to run anything.
    """
    for env_name in (".venv", "venv"):
        env = directory / env_name
        if not env.is_dir():
            continue
        for relative in (f"bin/{tool}", f"Scripts/{tool}.exe"):
            if (env / relative).is_file():
                return True, f"{env_name}/{relative} exists"
        site_packages = [*env.glob("lib/*/site-packages"), env / "Lib" / "site-packages"]
        for location in site_packages:
            if location.is_dir() and any(
                entry.suffix == ".dist-info" and entry.name.split("-")[0].lower() == tool.lower()
                for entry in location.iterdir()
            ):
                return True, f"{tool} is installed in {env_name}"
        return False, f"{tool} is not installed in {env_name}"
    return None, "no virtualenv is declared; the runner cannot be checked without executing it"


def python_test_command(directory: Path) -> tuple[list[str], str]:
    """Return ``(argv, source)`` for the module's pytest invocation."""
    prefix, source = python_runner(directory)
    if prefix[:2] == ["uv", "run"] or prefix[:2] == ["poetry", "run"]:
        return [*prefix, "pytest"], source
    return [*prefix, "-m", "pytest"], source


def python_collect_probe(directory: Path) -> tuple[list[str], str]:
    """Return a cheap probe that proves the suite imports and collects."""
    argv, source = python_test_command(directory)
    return [*argv, "--collect-only", "-q"], source


def node_runner(directory: Path, workspace: Path | None = None) -> tuple[str, str]:
    """Return ``(runner, source)`` — the package manager governing ``directory``.

    Lockfiles are looked up in the module directory first, then at the workspace
    root, because a workspace member usually inherits the root's lockfile.
    """
    roots = [directory]
    if workspace is not None and workspace != directory:
        roots.append(workspace)
    for root in roots:
        if (root / "pnpm-lock.yaml").is_file():
            return "pnpm", "pnpm-lock.yaml"
        if (root / "yarn.lock").is_file():
            return "yarn", "yarn.lock"
        if (root / "bun.lockb").is_file() or (root / "bun.lock").is_file():
            return "bun", "bun.lock"
    return "npm", "package-lock.json" if (directory / "package-lock.json").is_file() else "default"


def node_script_command(runner: str, script: str) -> list[str]:
    """Return the argv that runs ``script`` from package.json under ``runner``.

    ``test`` uses each runner's built-in shorthand, which every runner supports.
    Everything else goes through ``run``: a bare ``pnpm add`` or ``yarn install``
    would invoke the package manager's own command instead of the project's
    script of that name.
    """
    if script == "test":
        return [runner, "test"]
    return [runner, "run", script]


# Test runners that expose a discovery-only mode. The probe loads the test files
# and runs no test bodies, which is what the ``collects`` rung is defined as. A
# runner absent from this table gets ``skipped``, never a guess.
NODE_COLLECT_PROBES: dict[str, list[str]] = {
    "jest": ["--listTests"],
    "vitest": ["list"],
    "mocha": ["--dry-run"],
    "playwright": ["--list"],
}

#: A pattern that cannot match any test name, used to load a suite without
#: running any of it. A negative lookahead on the empty string always fails, in
#: both the JavaScript and Python engines. (`$^` looks unsatisfiable but matches
#: the empty string in both, which is exactly the kind of nearly-right that this
#: tool exists to catch.)
NEVER_MATCHES = "(?!)"


def node_collect_probe(directory: Path, script_body: str) -> tuple[list[str] | None, str]:
    """Return ``(argv, reason)`` for a discovery-only probe, or ``(None, reason)``.

    ``script_body`` is the raw text of the package.json script, which is where
    the underlying runner is named.
    """
    body = script_body.lower()
    for name, probe_args in NODE_COLLECT_PROBES.items():
        if name in body:
            local_bin = directory / "node_modules" / ".bin" / name
            argv = [str(local_bin.relative_to(directory))] if local_bin.is_file() else [name]
            return [*argv, *probe_args], f"{name} discovery mode"

    # Node's built-in runner has no list mode, but a name filter that cannot
    # match loads every test file and executes no test body — the same bargain
    # `go test -run '^$'` makes, and the same thing `collects` claims.
    if "node --test" in body or "node --experimental-test-runner" in body:
        return ["node", "--test", "--test-name-pattern", NEVER_MATCHES], "node --test, no test body run"

    first = script_body.split()[0] if script_body.split() else "unknown"
    return None, f"no discovery-only mode is known for this runner ({first})"


def python_collect_probe_for(argv: list[str]) -> tuple[list[str] | None, str]:
    """Return a discovery-only probe derived from a Python test command.

    Derived from the command under verification rather than re-resolved, so the
    rung proves that command and not a separately resolved sibling.
    """
    joined = " ".join(argv)
    if "pytest" in joined:
        return [*argv, "--collect-only", "-q"], "pytest --collect-only"
    if "unittest" in joined:
        # `-k` filters by name; a pattern that cannot match still imports every
        # test module, which is where a broken environment actually fails.
        return [*argv, "-k", NEVER_MATCHES], "unittest discovery, no test body run"
    return None, "no discovery-only mode is known for this Python test runner"


# Compile-or-discover probes that run no test bodies. Each is the cheapest
# command that still proves the test sources build and the runner is wired up.
COMPILED_COLLECT_PROBES: dict[str, list[str]] = {
    "go": ["go", "test", "-run", "^$", "./..."],
    "rust": ["cargo", "test", "--no-run"],
    "maven": ["mvn", "-q", "-DskipTests", "test-compile"],
    "gradle": ["gradle", "testClasses"],
    "dotnet": ["dotnet", "test", "--list-tests"],
}


def wrapper_aware(ecosystem: str, argv: list[str], directory: Path, workspace: Path) -> list[str]:
    """Swap ``mvn``/``gradle`` for the repository's wrapper when one exists.

    The result is expressed relative to ``directory``, since that is the working
    directory the command will run in.
    """
    wrappers = {"maven": ("mvn", "mvnw"), "gradle": ("gradle", "gradlew")}
    if not argv or ecosystem not in wrappers:
        return argv
    binary, wrapper_name = wrappers[ecosystem]
    if argv[0] != binary:
        return argv
    if (directory / wrapper_name).is_file():
        return [f"./{wrapper_name}", *argv[1:]]
    if (workspace / wrapper_name).is_file():
        try:
            depth = len(directory.relative_to(workspace).parts)
        except ValueError:
            return argv
        prefix = "./" if depth == 0 else "../" * depth
        return [f"{prefix}{wrapper_name}", *argv[1:]]
    return argv


def entrypoint_exists(argv: list[str], directory: Path) -> tuple[bool, str]:
    """Return whether ``argv[0]`` resolves, without executing anything."""
    if not argv:
        return False, "empty command"
    head = argv[0]
    if "/" in head or "\\" in head:
        candidate = (directory / head).resolve()
        if candidate.is_file():
            return True, f"{head} exists in {directory.name or '.'}"
        return False, f"{head} does not exist in the module directory"
    if shutil.which(head):
        return True, f"{head} is on PATH"
    return False, f"{head} is not on PATH"
