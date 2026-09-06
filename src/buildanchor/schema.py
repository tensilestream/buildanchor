# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""Report schema versions and backward-compatible rendering.

Version 1.2.0 changed what ``test_command`` means. It had been relative to the
repository root (``python -m pytest lib-a``); it became relative to the module's
own ``working_directory`` (``uv run pytest``). Same field, same type, different
meaning — the one kind of change no amount of testing on our side catches,
because a consumer that upgrades sees nothing wrong until the command runs in
the wrong place.

Emitting that under an unchanged ``schema_version`` was a mistake. This module
fixes it: the current schema is ``v2``, and a caller that asks for ``v1`` gets
v1's shape and v1's contract. Asking for anything else is an error rather than a
silent substitution.

``v1`` is supported through the 1.3 series and removed at 2.0.
"""

from __future__ import annotations

import re
from typing import Any

from .build_truth.core.errors import BuildAnchorError

CURRENT_SCHEMA = "v2"
SUPPORTED_SCHEMAS: tuple[str, ...] = ("v1", "v2")
DEPRECATED_SCHEMAS: tuple[str, ...] = ("v1",)

#: Fields added to ``module_details`` in v2, removed when rendering v1.
_V2_MODULE_FIELDS: tuple[str, ...] = (
    "category_confidence",
    "working_directory", "test_command_shell", "build_command_shell",
    "test_command_status", "test_command_outcome", "test_command_source",
    "test_command_duration_ms", "verified_at",
)

#: Fields added to ``validation_commands`` in v2.
_V2_COMMAND_FIELDS: tuple[str, ...] = ("working_directory", "command_shell")

#: Top-level report fields introduced in v2.
_V2_REPORT_FIELDS: tuple[str, ...] = ("language_details", "repository")

#: Fields added to each dependency entry in v2.
_V2_DEPENDENCY_FIELDS: tuple[str, ...] = ("module",)

_MODULE_QUALIFIED_KEY = re.compile(r"^(?P<key>[^@]+)@(?P<module>.+)$")


def validate(schema: str) -> str:
    """Return ``schema`` if supported, else raise. Never substitutes silently."""
    if schema not in SUPPORTED_SCHEMAS:
        supported = ", ".join(SUPPORTED_SCHEMAS)
        raise BuildAnchorError(
            f"unsupported schema_version '{schema}'; supported: {supported}"
        )
    return schema


def render(report: dict[str, Any], schema: str = CURRENT_SCHEMA) -> dict[str, Any]:
    """Render an already-serialised report under the requested schema."""
    validate(schema)
    if schema == CURRENT_SCHEMA:
        return report
    return _render_v1(report)


def _render_v1(report: dict[str, Any]) -> dict[str, Any]:
    """Reproduce the v1 shape and the v1 contract.

    v1's contract for ``test_command`` was "a command you can run from the
    repository root", so it maps to v2's ``test_command_shell`` — which carries
    the ``cd`` and honours that contract, rather than to the bare v2 command,
    which does not. A v1 consumer therefore gets a command that means what it
    always meant, and happens to work where the original often did not.
    """
    rendered = dict(report)
    rendered["schema_version"] = "v1"

    rendered["module_details"] = [_v1_module(module) for module in report.get("module_details", [])]
    rendered["validation_commands"] = [
        _strip(command, _V2_COMMAND_FIELDS) for command in report.get("validation_commands", [])
    ]
    rendered["dependencies"] = [
        _strip(dependency, _V2_DEPENDENCY_FIELDS) for dependency in report.get("dependencies", [])
    ]
    rendered["facts"] = _v1_facts(report.get("facts", []))

    for field in _V2_REPORT_FIELDS:
        rendered.pop(field, None)
    return rendered


def _v1_module(module: dict[str, Any]) -> dict[str, Any]:
    entry = dict(module)
    for phase in ("test", "build"):
        shell = entry.get(f"{phase}_command_shell")
        if shell:
            entry[f"{phase}_command"] = shell
    return _strip(entry, _V2_MODULE_FIELDS)


def _v1_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse ``runtime.python@service-a`` back to one unqualified fact per key.

    v1 reported a single manifest per ecosystem, so it had no way to express a
    per-module fact. The first occurrence wins, matching the old behaviour.
    """
    seen: set[str] = set()
    collapsed: list[dict[str, Any]] = []
    for fact in facts:
        entry = dict(fact)
        match = _MODULE_QUALIFIED_KEY.match(str(entry.get("key", "")))
        if match:
            entry["key"] = match.group("key")
        entry.pop("module", None)
        if entry["key"] in seen:
            continue
        seen.add(entry["key"])
        collapsed.append(entry)
    return collapsed


def _strip(value: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in fields}
