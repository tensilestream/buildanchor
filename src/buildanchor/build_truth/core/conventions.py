# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""Task runners a repository has already declared.

A repository with a ``justfile`` reading ``test: cargo nextest run`` has stated
how it wants to be tested. Answering ``cargo test`` there is not a helpful
default — it is the tool telling a team their own convention is wrong, which is
the fastest way to not get adopted.

So a declared task runner wins over an ecosystem default. What a person types is
the answer, and BuildAnchor's job is to find it and say where it came from, not
to replace it. Where no runner is declared, the ecosystem default is still the
best available answer.

Parsing here is deliberately shallow: enough to know a target exists, never
enough to pretend BuildAnchor understands the whole file.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .vocabulary import aliases_for


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _just_targets(text: str) -> set[str]:
    """Recipe names in a justfile: unindented ``name:`` or ``name arg:``."""
    targets = set()
    for line in text.splitlines():
        if not line or line[0].isspace() or line.lstrip().startswith("#"):
            continue
        match = re.match(r"^@?([A-Za-z0-9_][A-Za-z0-9_-]*)\s*[^:=]*:(?!=)", line)
        if match:
            targets.add(match.group(1))
    return targets


def _make_targets(text: str) -> set[str]:
    """Target names in a Makefile, excluding special and pattern targets."""
    targets = set()
    for line in text.splitlines():
        if not line or line[0].isspace() or line.lstrip().startswith("#"):
            continue
        match = re.match(r"^([A-Za-z0-9_][A-Za-z0-9_./-]*)\s*:(?!=)", line)
        if match:
            targets.add(match.group(1))
    return targets


def _taskfile_targets(text: str) -> set[str]:
    """Task names under the ``tasks:`` mapping of a Taskfile."""
    targets = set()
    in_tasks = False
    for line in text.splitlines():
        if re.match(r"^tasks:\s*$", line):
            in_tasks = True
            continue
        if in_tasks:
            if line and not line[0].isspace():
                break
            match = re.match(r"^  ([A-Za-z0-9_][A-Za-z0-9_:-]*):", line)
            if match:
                targets.add(match.group(1))
    return targets


def _nox_sessions(text: str) -> set[str]:
    """Session names from ``@nox.session``-decorated functions."""
    sessions = set()
    for match in re.finditer(r'@nox\.session[^\n]*\n(?:@[^\n]*\n)*def\s+([A-Za-z0-9_]+)', text):
        sessions.add(match.group(1))
    for match in re.finditer(r'@nox\.session\([^)]*name\s*=\s*[\'"]([^\'"]+)', text):
        sessions.add(match.group(1))
    return sessions


def _mise_tasks(text: str) -> set[str]:
    """Task names from a mise config's ``[tasks.<name>]`` tables."""
    return set(re.findall(r'(?m)^\[tasks\.["\']?([A-Za-z0-9_:-]+)', text))


#: Each entry: the files that declare the runner, how to list its targets, the
#: binary that runs them, and how a target becomes a command.
_RUNNERS: tuple[dict[str, Any], ...] = (
    {
        "name": "just",
        "files": ("justfile", "Justfile", ".justfile"),
        "targets": _just_targets,
        "command": lambda target: ["just", target],
    },
    {
        "name": "task",
        "files": ("Taskfile.yml", "Taskfile.yaml", "taskfile.yml"),
        "targets": _taskfile_targets,
        "command": lambda target: ["task", target],
    },
    {
        "name": "mise",
        "files": ("mise.toml", ".mise.toml"),
        "targets": _mise_tasks,
        "command": lambda target: ["mise", "run", target],
    },
    {
        "name": "make",
        "files": ("Makefile", "makefile", "GNUmakefile"),
        "targets": _make_targets,
        "command": lambda target: ["make", target],
    },
    {
        "name": "nox",
        "files": ("noxfile.py",),
        "targets": _nox_sessions,
        "command": lambda target: ["nox", "-s", target],
    },
)


def declared_command(directory: Path, phase: str) -> dict[str, Any] | None:
    """Return the repository's own command for ``phase``, if it declares one.

    Returns ``None`` when no declared runner covers the phase, leaving the
    ecosystem default to answer.
    """
    aliases = aliases_for(phase)
    for runner in _RUNNERS:
        declaring_file = next((name for name in runner["files"] if (directory / name).is_file()), None)
        if declaring_file is None:
            continue
        targets = runner["targets"](_read(directory / declaring_file))
        for alias in aliases:
            if alias in targets:
                return {
                    "command": runner["command"](alias),
                    "runner": runner["name"],
                    "source": declaring_file,
                    "target": alias,
                }

    # tox declares environments rather than named tasks, and `tox` alone runs
    # the declared envlist — which is the repository's answer for "test".
    if phase == "test":
        source = _tox_declaration(directory)
        if source:
            return {"command": ["tox"], "runner": "tox", "source": source, "target": "envlist"}
    return None


def _tox_declaration(directory: Path) -> str | None:
    """Where tox is configured, if it is.

    tox 4 reads ``[tool.tox]`` from ``pyproject.toml``, which is how several
    large projects declare it — Flask among them. Looking only for ``tox.ini``
    missed those entirely and answered with the ecosystem default instead.
    """
    if (directory / "tox.ini").is_file():
        return "tox.ini"
    if (directory / "setup.cfg").is_file() and "[tox:tox]" in _read(directory / "setup.cfg"):
        return "setup.cfg"
    if (directory / "pyproject.toml").is_file():
        text = _read(directory / "pyproject.toml")
        if re.search(r"(?m)^\[tool\.tox[\].]", text):
            return "pyproject.toml [tool.tox]"
    return None


def declared_runners(directory: Path) -> list[str]:
    """Every task-runner file present, for reporting and diagnosis.

    Deduplicated by the name on disk: a case-insensitive filesystem answers
    ``is_file()`` for both ``justfile`` and ``Justfile``, and reporting the same
    file twice reads as a bug in the tool.
    """
    candidates = [name for runner in _RUNNERS for name in runner["files"]]

    found: list[str] = []
    seen: set[str] = set()
    for name in candidates:
        path = directory / name
        if not path.is_file():
            continue
        try:
            actual = path.resolve().name
        except OSError:
            actual = name
        if actual.lower() in seen:
            continue
        seen.add(actual.lower())
        found.append(actual)
    return found
