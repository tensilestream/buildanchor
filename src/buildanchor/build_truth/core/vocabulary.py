# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""The names BuildAnchor recognises, defined once.

Several modules each kept their own copy of "what counts as a project marker"
and "what a test phase might be called". They diverged, quietly and with
consequences:

* ``build.gradle`` was a project marker for ``doctor`` and for repository-shape
  detection but not for the evidence invariant — so the guarantee that every
  marker resolves to a module or a stated reason had a Gradle-shaped hole.
* A ``justfile`` target named ``unit`` was found, while an npm script named
  ``unit`` was not, because the two alias tables listed different names.

Neither was a hard failure. Both were the same defect: the same knowledge
written down twice, drifting apart where nobody was looking. This module is the
one place it is written down.
"""

from __future__ import annotations

#: Marker filename to the ecosystem it implies. Presence makes a directory a
#: candidate project root — which is a different question from whether that
#: project can be built, and a much easier one to answer honestly.
PROJECT_MARKERS: dict[str, str] = {
    "package.json": "node",
    "pyproject.toml": "python",
    "setup.py": "python",
    "pom.xml": "maven",
    "build.gradle": "gradle",
    "build.gradle.kts": "gradle",
    "Cargo.toml": "rust",
    "go.mod": "go",
}

#: Marker filenames only, for callers that do not care about the ecosystem.
PROJECT_MARKER_NAMES: tuple[str, ...] = tuple(PROJECT_MARKERS)

#: What a phase might be called, in the order they are tried. First match wins,
#: so the unambiguous names come first and the shared ones (``check``) come last.
#:
#: ``release`` and ``publish`` are deliberately absent from ``build``: a task by
#: that name often pushes an artifact somewhere, and running it because somebody
#: asked to build is not a mistake a tool should be able to make.
PHASE_ALIASES: dict[str, tuple[str, ...]] = {
    "test": ("test", "tests", "test:unit", "test-unit", "unit", "test:all", "check"),
    "build": ("build", "compile", "dist", "bundle"),
    "lint": ("lint", "lint:fix", "eslint", "clippy", "vet", "typecheck", "tidy", "check"),
    "format": ("format", "fmt", "prettier", "prettier:write", "fix", "style"),
    "clean": ("clean", "reset", "clobber"),
}

#: Task names that must never be run on a caller's behalf, whatever they are
#: aliased to. A phase lookup that lands on one of these is a bug.
NEVER_RUN: frozenset[str] = frozenset({
    "release", "publish", "deploy", "push", "upload", "promote",
})


def aliases_for(phase: str) -> tuple[str, ...]:
    """Names to try for ``phase``, never including a publishing task."""
    return tuple(
        alias for alias in PHASE_ALIASES.get(phase, (phase,))
        if alias not in NEVER_RUN
    )
