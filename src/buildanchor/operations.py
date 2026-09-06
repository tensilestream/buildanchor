# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""The operations every BuildAnchor SDK is expected to expose.

There are four client surfaces — the CLI, the Python SDK, the Node SDK and the
Java SDK — and nothing previously connected them. They drifted: the Java client
was missing six operations the other two had, ``verify`` existed only in Python,
and ``doctor`` existed nowhere but the CLI. A user who picks the wrong language
finds a smaller product and has no way to know that is what happened.

This is the shared definition, and a conformance test fails when any SDK does not
match it. Adding an operation here is what makes it required everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Operation:
    """One operation, and how each surface is expected to name it."""

    name: str
    """Canonical snake_case name, used by the Python SDK."""

    camel_case: str
    """Name used by the Node and Java SDKs."""

    http_path: str | None
    """HTTP route, or ``None`` for operations that stay local."""

    summary: str

    local_only: bool = False
    """True when the operation executes project code and must not be remote."""


OPERATIONS: tuple[Operation, ...] = (
    Operation("llm_prompt", "llmPrompt", "/v1/llm-prompt",
              "A compact authoritative block to inject into an agent's context."),
    Operation("token_estimate", "tokenEstimate", "/v1/token-estimate",
              "Estimated token cost of each operation."),
    Operation("inspect", "inspect", "/v1/inspect",
              "The full report: modules, dependencies, evidence, limitations."),
    Operation("context", "context", "/v1/context",
              "A context pack bounded by a token budget."),
    Operation("preflight", "preflight", "/v1/preflight",
              "Context plus compatibility checks before acting."),
    Operation("plan", "plan", "/v1/plan",
              "An ordered plan with validation gates for an objective."),
    Operation("change_impact", "changeImpact", "/v1/change-impact",
              "What changed against a git baseline, and what it affects."),
    Operation("validate_change", "validateChange", "/v1/validate-change",
              "Validate a change; optionally execute the validation probes."),
    Operation("repair_guidance", "repairGuidance", "/v1/repair-guidance",
              "Guidance for a failed validation."),
    Operation("compatibility", "compatibility", "/v1/compatibility",
              "Ecosystem rules that catch incompatible edits."),
    Operation("explain_dependency", "explainDependency", "/v1/explain-dependency",
              "Where a dependency is declared and what it resolves to."),
    Operation("find_package", "findPackage", "/v1/find-package",
              "Whether a package is installed, declared, and already imported."),
    Operation("modules", "modules", "/v1/modules",
              "Every project, its working directory and its commands."),
    Operation("resolve_command", "resolveCommand", "/v1/cmd",
              "The command for a phase, where it runs, and how far it is proven."),
    Operation("diagnose", "diagnose", "/v1/doctor",
              "Explain the repository, or why a directory is not a module."),
    Operation("verify_commands", "verifyCommands", None,
              "Execute a discovery probe per module and record what genuinely runs.",
              local_only=True),
)

#: Canonical Python-SDK names.
OPERATION_NAMES: tuple[str, ...] = tuple(operation.name for operation in OPERATIONS)

#: Names used by the Node and Java SDKs.
CAMEL_CASE_NAMES: tuple[str, ...] = tuple(operation.camel_case for operation in OPERATIONS)

#: Operations that must never be reachable over HTTP or MCP, because they
#: execute project-defined code. A remote caller cannot consent to that.
LOCAL_ONLY: frozenset[str] = frozenset(
    operation.name for operation in OPERATIONS if operation.local_only
)


def http_routes() -> dict[str, str]:
    """Route to canonical operation name, for the HTTP transport."""
    return {
        operation.http_path: operation.name
        for operation in OPERATIONS
        if operation.http_path is not None
    }
