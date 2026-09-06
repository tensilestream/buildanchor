# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""The verification ladder: how far a resolved command has actually been proven.

BuildAnchor's static analysis can only ever show that a command was *declared*.
Whether it runs is a separate question, and answering it honestly needs an
execution. The ladder names the rungs between those two states so a caller can
tell a guess from a checked fact, and so BuildAnchor never has to claim more
than it has done.

``declared``    the command exists in a manifest. Static evidence only.
``resolvable``  the entrypoint it names exists on disk or on PATH. No execution.
``collects``    a cheap, side-effect-free probe (test discovery / compile-only)
                exited 0 in the module's own working directory.
``passes``      the full command exited 0.

``skipped`` and ``failed`` are outcomes, not rungs: ``skipped`` means BuildAnchor
has no honest probe for that toolchain and declines to guess, ``failed`` means a
rung was attempted and did not succeed.
"""

from __future__ import annotations

LEVELS: tuple[str, ...] = ("declared", "resolvable", "collects", "passes")

OUTCOMES: tuple[str, ...] = ("declared", "resolvable", "collects", "passes", "skipped", "failed")

LEVEL_DESCRIPTIONS: dict[str, str] = {
    "declared": "Command is declared in a manifest. Not executed; not proven to run.",
    "resolvable": "Entrypoint resolves on disk or PATH. Still not executed.",
    "collects": "Cheap discovery/compile probe exited 0 in the module's working directory.",
    "passes": "The full command exited 0.",
    "skipped": "No honest probe exists for this toolchain; BuildAnchor declines to guess.",
    "failed": "A rung was attempted and did not succeed.",
}


def rank(level: str) -> int:
    """Return the ladder position of ``level``; -1 for outcomes off the ladder."""
    try:
        return LEVELS.index(level)
    except ValueError:
        return -1


def at_least(level: str, minimum: str) -> bool:
    """True when ``level`` sits at or above ``minimum`` on the ladder."""
    return rank(level) >= rank(minimum) >= 0
