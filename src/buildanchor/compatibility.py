# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Java / Jakarta rules (original)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Rule review horizon
#
# Every rule below encodes a fact about the world outside this repository:
# which namespace Spring Boot 3 uses, which Rust edition is current, which
# package is abandoned. Those facts move, and nothing in a codebase notices when
# one stops being true — it simply keeps giving confident, outdated advice.
#
# Each rule therefore carries the date it was last confirmed, and a test fails
# once any rule passes the horizon. The failure is the point: it forces someone
# to re-confirm the claim rather than inherit it.
# ---------------------------------------------------------------------------

RULE_REVIEW_HORIZON_DAYS = 548  # 18 months


_JAKARTA_RULES = (
    {
        "code": "JAKARTA_PERSISTENCE_NAMESPACE",
        "reviewed": "2026-09-06",
        "legacy_prefix": "javax.persistence",
        "modern_prefix": "jakarta.persistence",
        "dependency": "jakarta.persistence:jakarta.persistence-api",
        "trigger": "Spring Boot 3+ and Jakarta Persistence use the jakarta namespace.",
        "replacement": "Replace javax.persistence imports and dependencies with jakarta.persistence equivalents.",
        "ecosystems": {"maven", "gradle"},
    },
    {
        "code": "JAKARTA_VALIDATION_NAMESPACE",
        "reviewed": "2026-09-06",
        "legacy_prefix": "javax.validation",
        "modern_prefix": "jakarta.validation",
        "dependency": "jakarta.validation:jakarta.validation-api",
        "trigger": "Spring Boot 3+ and Jakarta Validation use the jakarta namespace.",
        "replacement": "Replace javax.validation imports and dependencies with jakarta.validation equivalents.",
        "ecosystems": {"maven", "gradle"},
    },
    {
        "code": "JAKARTA_SERVLET_NAMESPACE",
        "reviewed": "2026-09-06",
        "legacy_prefix": "javax.servlet",
        "modern_prefix": "jakarta.servlet",
        "dependency": "jakarta.servlet:jakarta.servlet-api",
        "trigger": "Spring Boot 3+ uses Jakarta Servlet APIs.",
        "replacement": "Replace javax.servlet imports and dependencies with jakarta.servlet equivalents.",
        "ecosystems": {"maven", "gradle"},
    },
)

# ---------------------------------------------------------------------------
# Python rules
# ---------------------------------------------------------------------------

_PYTHON_RULES = (
    {
        "code": "PYTHON_SETUP_PY_ONLY",
        "reviewed": "2026-09-06",
        "severity": "warning",
        "check": "setup_py_only",
        "message": "Project uses setup.py without pyproject.toml.",
        "recommendation": "Migrate to pyproject.toml (PEP 517/518). All major build backends (setuptools, hatch, flit, poetry) support it.",
        "repair": "Run: pip install build; add a [build-system] table to pyproject.toml; move metadata from setup.py/setup.cfg.",
    },
    {
        "code": "PYTHON_DEPRECATED_DISTUTILS",
        "reviewed": "2026-09-06",
        "severity": "error",
        "check": "distutils_import",
        "message": "distutils is imported directly. It was removed in Python 3.12.",
        "recommendation": "Replace distutils with setuptools or sysconfig equivalents.",
        "repair": "Search for 'from distutils' or 'import distutils' and replace with setuptools or stdlib equivalents.",
    },
    {
        "code": "PYTHON_PKG_RESOURCES",
        "reviewed": "2026-09-06",
        "severity": "warning",
        "check": "pkg_resources_import",
        "message": "pkg_resources is imported. Prefer importlib.resources (stdlib, Python 3.9+).",
        "recommendation": "Replace pkg_resources with importlib.resources or importlib.metadata.",
        "repair": "Replace 'import pkg_resources' with 'from importlib import resources' and update call sites.",
    },
)

# ---------------------------------------------------------------------------
# Node / npm rules
# ---------------------------------------------------------------------------

_NODE_RULES = (
    {
        "code": "NODE_MISSING_EXPORTS_FIELD",
        "reviewed": "2026-09-06",
        "severity": "warning",
        "check": "missing_exports",
        "message": 'package.json has "main" but no "exports" field — ESM consumers may fail.',
        "recommendation": 'Add an "exports" field to package.json for dual CJS/ESM compatibility.',
        "repair": 'Add: "exports": {".": {"import": "./dist/index.mjs", "require": "./dist/index.cjs"}} in package.json.',
    },
    {
        "code": "NODE_DEPRECATED_REQUEST_PACKAGE",
        "reviewed": "2026-09-06",
        "severity": "error",
        "check": "deprecated_request",
        "message": 'The "request" package is deprecated (archived since 2020) and has unpatched CVEs.',
        "recommendation": "Replace with node-fetch, axios, undici, or the built-in fetch (Node 18+).",
        "repair": "Remove 'request' from dependencies; replace usage with a maintained HTTP client.",
    },
    {
        "code": "NODE_NATIVE_BUILD_DEPENDENCY",
        "reviewed": "2026-09-06",
        "severity": "warning",
        "check": "node_gyp",
        "message": "node-gyp dependency detected. Native compilation required at install time.",
        "recommendation": "Ensure build tools (Python, C++ compiler) are available in CI and production images.",
        "repair": "Document build prerequisites in README; add build toolchain to Dockerfile and CI configuration.",
    },
)

# ---------------------------------------------------------------------------
# Go rules
# ---------------------------------------------------------------------------

_GO_RULES = (
    {
        "code": "GO_PRE_MODULE_LAYOUT",
        "reviewed": "2026-09-06",
        "severity": "error",
        "check": "no_go_mod",
        "message": "Go source files detected but no go.mod found. Pre-module layout is not supported by modern tooling.",
        "recommendation": "Initialise a Go module: run 'go mod init <module-path>'.",
        "repair": "Run: go mod init <your-module-path> && go mod tidy",
    },
)

# ---------------------------------------------------------------------------
# Rust rules
# ---------------------------------------------------------------------------

_RUST_RULES = (
    {
        "code": "RUST_EDITION_2015",
        "reviewed": "2026-09-06",
        "severity": "warning",
        "check": "rust_edition_2015",
        "message": "Cargo.toml uses the Rust 2015 edition. The 2021 edition adds important improvements.",
        "recommendation": "Upgrade to edition = \"2021\" in Cargo.toml for resolver improvements and ergonomic fixes.",
        "repair": "Change edition = \"2015\" to edition = \"2021\" in Cargo.toml; run 'cargo fix --edition' to migrate code.",
    },
)

# ---------------------------------------------------------------------------
# Objective-mismatch detection (ecosystem ↔ objective keyword matching)
# ---------------------------------------------------------------------------

# Maps technology keywords to the ecosystems that actually support them.
_OBJECTIVE_ECOSYSTEM_MAP: dict[str, set[str]] = {
    # Java / JVM
    "jpa": {"maven", "gradle"},
    "hibernate": {"maven", "gradle"},
    "spring": {"maven", "gradle"},
    "jakarta": {"maven", "gradle"},
    "javax": {"maven", "gradle"},
    "maven": {"maven"},
    "gradle": {"gradle"},
    "pom": {"maven"},
    "bean": {"maven", "gradle"},
    "servlet": {"maven", "gradle"},
    "tomcat": {"maven", "gradle"},
    "war": {"maven", "gradle"},
    "jar": {"maven", "gradle"},
    "classpath": {"maven", "gradle"},
    # Node / JavaScript
    "npm": {"node"},
    "yarn": {"node"},
    "webpack": {"node"},
    "vite": {"node"},
    "react": {"node"},
    "vue": {"node"},
    "express": {"node"},
    "typescript": {"node"},
    "eslint": {"node"},
    # Python
    "pypi": {"python"},
    "pip": {"python"},
    "poetry": {"python"},
    "django": {"python"},
    "flask": {"python"},
    "fastapi": {"python"},
    "pytest": {"python"},
    "virtualenv": {"python"},
    "conda": {"python"},
    # Go
    "goroutine": {"go"},
    "gofmt": {"go"},
    # Rust
    "cargo": {"rust"},
    "crate": {"rust"},
    "tokio": {"rust"},
}


def _detect_objective_mismatch(objective: str, build_systems: list[str]) -> list[dict[str, Any]]:
    """Return warnings when the objective mentions technology from a different ecosystem."""
    if not objective or not build_systems:
        return []
    obj_lower = objective.lower()
    active_ecosystems = set(build_systems)
    warnings = []
    for keyword, required_ecosystems in _OBJECTIVE_ECOSYSTEM_MAP.items():
        if keyword in obj_lower:
            overlap = required_ecosystems & active_ecosystems
            if not overlap:
                detected_str = ", ".join(sorted(active_ecosystems)) or "none"
                required_str = ", ".join(sorted(required_ecosystems))
                warnings.append({
                    "code": "OBJECTIVE_ECOSYSTEM_MISMATCH",
                    "severity": "warning",
                    "status": "review",
                    "keyword": keyword,
                    "message": (
                        f"Objective mentions '{keyword}' (requires: {required_str}) "
                        f"but detected build systems are: {detected_str}."
                    ),
                    "recommendation": (
                        f"Verify that the workspace contains a {required_str} project. "
                        "If this is a polyglot repo, point --workspace at the correct sub-directory."
                    ),
                    "repair": (
                        f"Run: buildanchor inspect --workspace <path-to-{required_str}-subdir> "
                        "to confirm the correct workspace root."
                    ),
                })
            break  # one warning per objective is enough
    return warnings


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------


def compatibility_recommendations(
    workspace: Path,
    files: list[Path],
    build_systems: list[str],
    facts: list[Any],
    dependencies: list[dict[str, Any]],
    evidence_for,
    objective: str = "",
) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []

    # --- Java / Jakarta ---
    spring_boot = _fact_value(facts, "framework.spring_boot")
    jakarta_context = any("jakarta" in str(item.get("coordinate", "")).lower() for item in dependencies)
    spring_boot_3 = _major_version(spring_boot) is not None and _major_version(spring_boot) >= 3
    source_files = [path for path in files if path.suffix.lower() in {".java", ".kt", ".scala", ".groovy"}]

    for rule in _JAKARTA_RULES:
        if not any(sys in build_systems for sys in rule["ecosystems"]):
            continue
        findings: list[dict[str, str]] = []
        for path in source_files:
            text = path.read_text(encoding="utf-8", errors="replace")[:500_000]
            for match in re.finditer(rf"\b{re.escape(rule['legacy_prefix'])}(?:\.[A-Za-z_$][\w$]*)+", text):
                findings.append({"path": path.relative_to(workspace).as_posix(), "symbol": match.group(0)})
        legacy_dep = [
            item for item in dependencies
            if rule["legacy_prefix"].split(".")[1] in str(item.get("coordinate", "")).lower()
            or rule["legacy_prefix"].replace(".", "-") in str(item.get("coordinate", "")).lower()
        ]
        if not findings and not legacy_dep:
            continue
        evidence_ids = [evidence_for(workspace / f["path"]).id for f in findings]
        triggered = spring_boot_3 or jakarta_context
        recommendations.append({
            "code": rule["code"],
            "severity": "error" if triggered else "warning",
            "status": "incompatible" if triggered else "review",
            "requested": rule["legacy_prefix"],
            "recommended": rule["modern_prefix"],
            "dependency": rule["dependency"],
            "version": "managed-by-framework" if spring_boot_3 else "select-compatible-version",
            "affected_files": sorted({f["path"] for f in findings}),
            "symbols": sorted({f["symbol"] for f in findings}),
            "reason": rule["trigger"] if triggered else "A legacy namespace was detected; verify the framework and runtime compatibility before adding it.",
            "repair": rule["replacement"],
            "evidence_ids": sorted(set(evidence_ids)),
        })

    # --- Python ---
    if "python" in build_systems:
        has_pyproject = any(p.name == "pyproject.toml" for p in files)
        has_setup_py = any(p.name == "setup.py" for p in files)
        py_sources = [p for p in files if p.suffix == ".py"]

        for rule in _PYTHON_RULES:
            if rule["check"] == "setup_py_only" and has_setup_py and not has_pyproject:
                recommendations.append(_simple_rec(rule, [], "python"))
            elif rule["check"] == "distutils_import":
                hits = [
                    p for p in py_sources
                    if re.search(r"^\s*(?:import\s+distutils|from\s+distutils\b)", _read_head(p), re.MULTILINE)
                ]
                if hits:
                    recommendations.append(_simple_rec(rule, [p.relative_to(workspace).as_posix() for p in hits], "python"))
            elif rule["check"] == "pkg_resources_import":
                hits = [
                    p for p in py_sources
                    if re.search(r"^\s*(?:import\s+pkg_resources|from\s+pkg_resources\b)", _read_head(p), re.MULTILINE)
                ]
                if hits:
                    recommendations.append(_simple_rec(rule, [p.relative_to(workspace).as_posix() for p in hits], "python"))

    # --- Node ---
    if "node" in build_systems:
        pkg_files = [p for p in files if p.name == "package.json"]
        for path in pkg_files[:1]:
            try:
                pkg = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            except (json.JSONDecodeError, OSError):
                pkg = {}
            for rule in _NODE_RULES:
                if rule["check"] == "missing_exports":
                    if "main" in pkg and "exports" not in pkg:
                        recommendations.append(_simple_rec(rule, [path.relative_to(workspace).as_posix()], "node"))
                elif rule["check"] == "deprecated_request":
                    all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                    if "request" in all_deps:
                        recommendations.append(_simple_rec(rule, [path.relative_to(workspace).as_posix()], "node"))
                elif rule["check"] == "node_gyp":
                    all_deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                    if "node-gyp" in all_deps:
                        recommendations.append(_simple_rec(rule, [path.relative_to(workspace).as_posix()], "node"))

    # --- Go ---
    if "go" in build_systems:
        go_sources = [p for p in files if p.suffix == ".go"]
        has_go_mod = any(p.name == "go.mod" for p in files)
        if go_sources and not has_go_mod:
            for rule in _GO_RULES:
                recommendations.append(_simple_rec(rule, [], "go"))

    # --- Rust ---
    if "rust" in build_systems:
        cargo_files = [p for p in files if p.name == "Cargo.toml"]
        for path in cargo_files[:1]:
            text = _read_head(path)
            edition_match = re.search(r'edition\s*=\s*["\'](\d+)["\']', text)
            if edition_match and edition_match.group(1) == "2015":
                for rule in _RUST_RULES:
                    if rule["check"] == "rust_edition_2015":
                        recommendations.append(_simple_rec(rule, [path.relative_to(workspace).as_posix()], "rust"))

    # --- Objective mismatch detection ---
    recommendations.extend(_detect_objective_mismatch(objective, build_systems))

    return recommendations


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _simple_rec(rule: dict[str, Any], affected_files: list[str], ecosystem: str) -> dict[str, Any]:
    return {
        "code": rule["code"],
        "severity": rule.get("severity", "warning"),
        "status": "incompatible" if rule.get("severity") == "error" else "review",
        "ecosystem": ecosystem,
        "message": rule.get("message", ""),
        "recommendation": rule.get("recommendation", ""),
        "repair": rule.get("repair", ""),
        "affected_files": sorted(set(affected_files)),
    }


def _read_head(path: Path, limit: int = 100_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _fact_value(facts: list[Any], key: str) -> Any:
    for fact in facts:
        if fact.key == key:
            return fact.value
    return None


def _major_version(value: Any) -> int | None:
    if value is None:
        return None
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else None


def all_rules() -> tuple[dict, ...]:
    """Every compatibility rule, across ecosystems."""
    return (*_JAKARTA_RULES, *_PYTHON_RULES, *_NODE_RULES, *_GO_RULES, *_RUST_RULES)


def stale_rules(today: date | None = None, horizon_days: int = RULE_REVIEW_HORIZON_DAYS) -> list[dict]:
    """Return rules whose last review is older than the horizon.

    ``today`` defaults to the current UTC date rather than the machine's local
    one, so the horizon fires on the same day everywhere.
    """
    today = today or datetime.now(timezone.utc).date()
    stale = []
    for rule in all_rules():
        reviewed = rule.get("reviewed")
        if not reviewed:
            stale.append(rule)
            continue
        try:
            when = date.fromisoformat(str(reviewed))
        except ValueError:
            stale.append(rule)
            continue
        if (today - when).days > horizon_days:
            stale.append(rule)
    return stale
