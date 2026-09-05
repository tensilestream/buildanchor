# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""Authoritative definitions for supported build-system detection."""

from __future__ import annotations

MARKERS: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    ("maven", ("pom.xml",), ("Java", "Kotlin", "Scala")),
    ("gradle", ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "gradlew"), ("Java", "Kotlin", "Groovy", "Scala")),
    ("node", ("package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"), ("JavaScript", "TypeScript")),
    ("python", ("pyproject.toml", "requirements.txt", "requirements-dev.txt", "uv.lock", "poetry.lock", "setup.py"), ("Python",)),
    ("go", ("go.mod", "go.sum"), ("Go",)),
    ("rust", ("Cargo.toml", "Cargo.lock"), ("Rust",)),
    ("dotnet", ("global.json", "packages.lock.json"), ("C#", "F#", "Visual Basic")),
    ("generic", ("Makefile", "CMakeLists.txt", "BUILD", "WORKSPACE", "Package.swift", "composer.json", "Gemfile", "pubspec.yaml", "Dockerfile"), ("C/C++", "Swift", "PHP", "Ruby", "Dart")),
]

ECOSYSTEM_LABELS: dict[str, str] = {
    "maven": "Java/Maven", "gradle": "Java/Gradle", "node": "Node.js",
    "python": "Python", "go": "Go", "rust": "Rust", "dotnet": ".NET",
    "generic": "Generic/Make",
}
