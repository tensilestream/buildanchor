# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Evidence:
    id: str
    kind: str
    path: str
    digest: str
    detail: str


@dataclass(frozen=True)
class Fact:
    key: str
    value: Any
    status: str = "proven"
    evidence_ids: tuple[str, ...] = ()
    detail: str = ""
    #: Which module this fact is about, relative to the workspace, or ``None``
    #: for a repository-wide fact. Two projects can declare different runtimes;
    #: encoding that in the key as ``runtime.python@service-a`` made a
    #: structural relationship into something every caller had to parse.
    module: str | None = None


@dataclass(frozen=True)
class ModuleInfo:
    name: str
    path: str
    ecosystem: str
    category: str = "unknown"  # "ui" | "backend" | "shared" | "unknown"
    #: How much evidence stands behind ``category``: "high" when two signals of
    #: different kinds agreed, "low" when only one was available, "none" when
    #: nothing pointed anywhere.
    category_confidence: str = "none"
    test_command: str | None = None
    build_command: str | None = None
    # Directory the commands above must run in, relative to the workspace root.
    # A command without a working directory is ambiguous in any repository that
    # holds more than one project, which is the only situation modules exist for.
    working_directory: str = "."
    # Copy-pasteable equivalents, safe to run from the workspace root.
    test_command_shell: str | None = None
    build_command_shell: str | None = None
    # Verification ladder rung reached for test_command: see
    # buildanchor.build_truth.core.verification_levels.LEVELS.
    test_command_status: str = "declared"
    # What happened at the rung above ``test_command_status``: "failed" when a
    # probe was run and did not succeed, "skipped" when no honest probe exists.
    # A rung reached is not the same as a clean result, and reporting only the
    # rung would overstate it.
    test_command_outcome: str = "declared"
    #: Observed wall-clock of the full test command, when it has been run at the
    #: ``passes`` rung. ``None`` means nobody has paid for that measurement yet.
    test_command_duration_ms: int | None = None
    verified_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass
class BuildReport:
    schema_version: str
    session_id: str
    workspace: str
    workspace_digest: str
    status: str
    build_systems: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    # Per-language evidence: file counts, markers and sample paths. A language
    # with nothing behind it cannot appear here, and a reader can check any
    # entry the way they can check a fact.
    language_details: list[dict[str, Any]] = field(default_factory=list)
    modules: list[str] = field(default_factory=list)
    module_details: list[dict[str, Any]] = field(default_factory=list)
    # How the repository is laid out, and why. Drives the advice given to an
    # agent: a single-project repository has no scoping decision to make.
    repository: dict[str, Any] = field(default_factory=dict)
    facts: list[Fact] = field(default_factory=list)
    dependencies: list[dict[str, Any]] = field(default_factory=list)
    recommendations: list[dict[str, Any]] = field(default_factory=list)
    validation_commands: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    policy: dict[str, Any] = field(default_factory=lambda: {"decision": "allow"})
    git: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass
class ContextPack:
    schema_version: str
    session_id: str
    summary: str
    constraints: list[str]
    validation_commands: list[list[str]]
    fact_refs: list[str]
    evidence_refs: list[str]
    limitations: list[str]
    # LLM cost-saving fields — inject llm_context into system prompt instead of raw files
    llm_context: str = ""    # Ready-to-inject authoritative context block
    token_estimate: int = 0  # Estimated token count (tiktoken or chars/4)
    cost_tier: str = "low"   # "low" / "medium" / "high"

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass
class PromptBlock:
    """A ready-to-inject system prompt block for LLM consumption.

    Inject ``content`` into the agent's system prompt BEFORE it acts on any
    build-affecting change.  This replaces the need for the LLM to re-read
    pyproject.toml, package.json, pom.xml, go.mod, Cargo.toml, etc. at
    inference time — saving hundreds of tokens per agent invocation.
    """
    role: str            # Always "system"
    content: str         # The authoritative context text to inject
    token_estimate: int  # Estimated token count
    format: str          # "mcp" | "cli" | "inline"
    workspace_digest: str = ""  # Digest at generation time (for cache validation)
    session_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass
class ChangeReport:
    schema_version: str
    session_id: str
    baseline: str
    changed_files: list[dict[str, str]]
    affected_facts: list[str]
    status: str
    guidance: list[str]
    baseline_resolved: bool = False
    change_detected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


def _jsonable(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value
