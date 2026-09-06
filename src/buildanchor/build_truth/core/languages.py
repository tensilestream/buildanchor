# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""Evidence-backed language detection.

``languages`` previously came from a fixed tuple attached to each build system
and unioned whenever any one of that system's markers matched. Because the
``generic`` row paired nine unrelated markers with five unrelated languages, a
lone ``Dockerfile`` asserted C/C++, Swift, PHP, Ruby and Dart — languages with no
file and no marker anywhere in the tree. The field sat beside ``facts``, which
carries ``proven`` and evidence ids, and did not meet that standard.

Here a language is reported only when something in the repository demonstrates
it: source files with its extension, or a marker that unambiguously implies it.
Ambiguous markers (a ``Dockerfile``, a ``Makefile``, a ``global.json``) imply a
build system, not a language, and contribute nothing.
"""

from __future__ import annotations

from pathlib import Path

# Extensions that identify a language on sight. Deliberately conservative:
# a header shared by C and C++ is reported as C/C++, not guessed either way.
EXTENSION_LANGUAGES: dict[str, str] = {
    ".py": "Python", ".pyi": "Python",
    ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".mts": "TypeScript", ".cts": "TypeScript",
    ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin", ".scala": "Scala", ".groovy": "Groovy",
    ".go": "Go", ".rs": "Rust",
    ".cs": "C#", ".fs": "F#", ".fsx": "F#", ".vb": "Visual Basic",
    ".c": "C/C++", ".h": "C/C++", ".cc": "C/C++", ".cpp": "C/C++", ".cxx": "C/C++",
    ".hpp": "C/C++", ".hh": "C/C++",
    ".rb": "Ruby", ".swift": "Swift", ".php": "PHP", ".dart": "Dart",
    ".m": "Objective-C", ".mm": "Objective-C",
    ".sh": "Shell", ".bash": "Shell",
    ".sql": "SQL", ".r": "R", ".jl": "Julia", ".ex": "Elixir", ".exs": "Elixir",
    ".clj": "Clojure", ".hs": "Haskell", ".lua": "Lua", ".pl": "Perl", ".pm": "Perl",
}

# Markers whose presence implies a language on its own. A marker that implies
# only a build system is absent on purpose — `Dockerfile`, `Makefile`,
# `CMakeLists.txt`, `BUILD`, `WORKSPACE`, `global.json` say nothing about which
# language the repository is written in.
MARKER_LANGUAGES: dict[str, tuple[str, ...]] = {
    "Gemfile": ("Ruby",),
    "Gemfile.lock": ("Ruby",),
    "Package.swift": ("Swift",),
    "composer.json": ("PHP",),
    "pubspec.yaml": ("Dart",),
    "go.mod": ("Go",),
    "Cargo.toml": ("Rust",),
    "pyproject.toml": ("Python",),
    "setup.py": ("Python",),
    "requirements.txt": ("Python",),
}

# Suffixes that identify a language but describe build tooling rather than the
# project's own source. Counted separately so a repository is not called a
# Groovy project because of one `build.gradle`.
BUILD_SCRIPT_NAMES: frozenset[str] = frozenset({
    "build.gradle", "settings.gradle", "build.gradle.kts", "settings.gradle.kts",
})

# A language claimed by extension alone needs at least this many files. One
# stray vendored `.rb` should not make a Python repository a Ruby one.
MINIMUM_FILES_BY_EXTENSION = 1


def detect(files: list[Path], workspace: Path) -> list[dict]:
    """Return one entry per detected language, each with its own evidence.

    ``files`` is the already-filtered workspace listing, so dependency
    directories (``node_modules``, ``.venv``, ``target``, ``dist``) are excluded
    before anything is counted.
    """
    counts: dict[str, int] = {}
    samples: dict[str, list[str]] = {}
    markers: dict[str, list[str]] = {}

    for path in files:
        try:
            relative = str(path.relative_to(workspace))
        except ValueError:
            continue

        implied = MARKER_LANGUAGES.get(path.name)
        if implied:
            for language in implied:
                markers.setdefault(language, [])
                if relative not in markers[language]:
                    markers[language].append(relative)
            continue

        if path.name in BUILD_SCRIPT_NAMES:
            continue

        language = EXTENSION_LANGUAGES.get(path.suffix.lower())
        if not language:
            continue
        counts[language] = counts.get(language, 0) + 1
        if len(samples.setdefault(language, [])) < 3:
            samples[language].append(relative)

    detected: list[dict] = []
    for language in sorted(set(counts) | set(markers)):
        file_count = counts.get(language, 0)
        language_markers = markers.get(language, [])
        if file_count < MINIMUM_FILES_BY_EXTENSION and not language_markers:
            continue
        detected.append({
            "language": language,
            "file_count": file_count,
            "markers": language_markers[:3],
            "sample_paths": samples.get(language, [])[:3],
            "basis": "marker" if language_markers and not file_count else ("marker+files" if language_markers else "files"),
        })
    return detected


def names(detected: list[dict]) -> list[str]:
    """Return just the language names, for the flat ``languages`` field."""
    return sorted(entry["language"] for entry in detected)
