# BuildAnchor v0.3 — Implementation Preparation & Specification

**Document Version:** 1.0.0  
**Target Milestone:** v0.3.0  
**Status:** Ready for Implementation  
**Audience:** Core Maintainers, AI Coding Agents, CI/CD Integrators  

---

## 1. Executive Summary & Core Value Proposition

AI coding agents (Claude Code, Cursor, Aider, Windsurf, Copilot) frequently hallucinate build commands, import incompatible libraries (e.g., trying to write JPA entities in a Node.js repository), and burn 10,000+ tokens injecting massive raw configuration files into their context windows.

**BuildAnchor** solves this by providing a **local-first, offline-first Build Truth engine**. It converts repository state into deterministic, authoritative context packs that cost under **300 tokens**, prevents framework mismatches before agents write code, and provides a strict exit-code contract for CI/CD gates.

This specification details:
1. Current tool availability across Engine, MCP, CLI, and HTTP transports.
2. An exact gap analysis against the existing implementation plan.
3. High-adoption product features (`--agent`, `--ci`, `init`, `cmd`, `--staged`).
4. A phased, zero-ambiguity implementation roadmap.

---

## 2. Current Tool Availability Inventory (As-Is)

### 2.1 Core Engine (`src/buildanchor/engine.py`)

| Engine Method | Status | Purpose & Capabilities | Token Cost |
|---|---|---|---|
| `llm_prompt(objective)` | **Active** | Returns a compact Markdown system prompt block with build system, runtime versions, constraints, and validation commands. | ~80–250 |
| `token_estimate()` | **Active** | Returns a token cost matrix for all tools with a recommended cheapest tool. | < 5 |
| `context(report, budget)` | **Active** | Structured JSON context pack containing `llm_context`, constraints, and evidence refs. | ~500–1200 |
| `preflight(objective)` | **Active** | Pre-change readiness check returning `ready_to_act`, compatibility errors, and prompt block. | ~200–400 |
| `plan(objective, budget)` | **Active** | Creates an ordered execution plan with ecosystem context, phase gates, and mismatch warnings. | ~400–800 |
| `inspect()` | **Active** | Full static build truth report with all evidence, facts, dependencies, and limitations. | 10k+ (JSON) |
| `change_impact(baseline)` | **Active** | Compares workspace to Git baseline (`HEAD`) and identifies affected build facts. | ~100–300 |
| `validate_change(baseline)` | **Active** | Git impact analysis and bounded test probes (supports optional execution). | ~200–500 |
| `repair_guidance()` | **Active** | Structured remediation steps when validation fails or is inconclusive. | ~150–350 |
| `compatibility()` | **Active** | Multi-ecosystem compatibility checks (Jakarta/Java, Node ESM, Python packaging, Go, Rust). | ~100–300 |
| `find_package(name)` | **Active (Needs 1 fix)** | Inspects declared & installed versions in `node_modules`, `.venv`, `~/.m2`, `vendor`, `Cargo.lock` + usage grep. | ~80–200 |
| `explain_dependency(dep)` | **Active** | Provenance and coordinate lookup for detected dependencies. | ~50–100 |

### 2.2 MCP Server (`src/buildanchor/transports.py`)

Currently exposes **11 MCP Tools**:
1. `build.llm_prompt` — Call first before acting; compact prompt block.
2. `build.token_estimate` — Returns cost matrix and recommended tool.
3. `build.context` — Compact context pack for structured JSON consumers.
4. `build.preflight` — Pre-change gate checking readiness and hard errors.
5. `build.plan` — Repository-aware execution plan with gates.
6. `build.change_impact` — Compares workspace against Git baseline.
7. `build.validate_change` — Git impact analysis & probe execution.
8. `build.repair_guidance` — Structured repair steps for failures.
9. `build.compatibility` — Multi-ecosystem compatibility recommendations.
10. `build.inspect` — Full build truth report (warning: expensive token size).
11. `build.explain_dependency` — Detailed lookup of a single dependency.

*MCP Prompts Available:*
- `buildanchor-preflight` — Injectable prompt template for Cursor / Claude Desktop / Continue.dev.

### 2.3 CLI (`src/buildanchor/cli.py`)

*Available Commands:*
`llm-prompt`, `token-estimate`, `inspect`, `context`, `preflight`, `plan`, `change-impact`, `validate-change`, `repair`, `compatibility`, `explain-dependency`, `mcp`, `serve`.

*Available Formats:*
`--format text` (default), `--format json`, `--format markdown`, `--format sarif`, `--format llm`.

*Available Flags:*
`--workspace`, `--allow-root`, `--baseline`, `--token-budget`, `--dependency`, `--objective`, `--execute`, `--timeout`, `--quiet`, `--stdio`, `--listen`.

---

## 3. Gap Analysis vs v0.3 Implementation Plan

| Feature / Plan Item | Plan Target | Current Status | Remediation Required |
|---|---|---|---|
| **`find_package` in engine** | Full ecosystem support + usage grep | **95% Implemented** | Fix regex capture group in `_find_rust` (`engine.py:1027`). |
| **`build.find_package` MCP tool** | Expose to AI agents via MCP protocol | **Missing** | Add to `TOOLS` list in `transports.py`, route in `call_tool()`, add `/v1/find-package` in `HTTPHandler`. |
| **`find` command in CLI** | `buildanchor find --package <name>` | **Missing** | Add `find` to `_parser()` choices and wire arguments (`--package`, `--installed-only`, `--no-show-usage`). |
| **CLI Flags** | `--exit-on-mismatch`, `--assert-ecosystem`, `--only-errors`, `--explain` | **Missing** | Add flags to `_parser()`, implement filtering and exit code behavior in `main()`. |
| **Exit Code Contract (0-4)** | Deterministic exit codes for CI & bash `&&` | **Partially Enforced** | Enforce strict status mapping across all commands in `cli.py`. |
| **`ADOPTION.md`** | Single entry point for developer & agent onboarding | **Missing** | Create `ADOPTION.md` with copy-paste configs for CI, Claude Code, Cursor, Aider, and SLMs. |
| **Benchmarks & Fixtures** | Empirical token & latency validation | **Missing** | Create `benchmarks/run_benchmarks.py` with 6 fixture repos (Maven, Node ESM, Python, Go, Rust, Polyglot). |
| **Test Suite Coverage** | Unit tests for new tools and exit codes | **17/17 passing** | Add new test cases for `find_package`, exit codes, and MCP routing. |

---

## 4. High-Adoption Tools & Flags (New Strategic Features)

To achieve **maximum product adoption**, BuildAnchor must remove all integration friction for both human developers and autonomous agents:

### 4.1 `--agent` Flag (Zero-Friction Prompt Injection)
* **Goal:** Direct pipe into AI coding agent CLI tools without JSON parsing or banner text.
* **Behavior:** Outputs *only* the raw Markdown LLM block, stripped of metadata and ANSI colors.
* **Adoption Pattern:**
  ```bash
  # In .bashrc / .zshrc:
  alias claude-build="claude --prompt \"\$(buildanchor preflight --agent)\""
  
  # Or copy to clipboard:
  buildanchor plan --agent --objective "Add Stripe webhook" | pbcopy
  ```

### 4.2 `--ci` Flag (One-Line GitHub Actions & GitLab Gate)
* **Goal:** Zero-config automated PR gating that fails builds on drift or ecosystem mismatches.
* **Behavior:**
  - Implicitly enables `--only-errors --exit-on-mismatch --quiet`.
  - Automatically formats errors as GitHub Actions workflow annotations: `::error file=package.json::Missing exports field`.
  - Exits with `0` (pass), `1` (compatibility fail), or `3` (mismatch).
* **Adoption Pattern:**
  ```yaml
  # .github/workflows/build-truth.yml
  - name: Build Truth Preflight
    run: uvx buildanchor preflight --ci --objective "${{ github.event.pull_request.title }}"
  ```

### 4.3 `buildanchor init` Command (Instant Project Onboarding)
* **Goal:** Turn any repository into a BuildAnchor-enabled project in < 3 seconds.
* **Behavior:**
  1. Inspects the repo, identifies the package manager (`pnpm`, `npm`, `yarn`, `bun`, `poetry`, `uv`, `mvn`, `gradle`, `cargo`, `go`).
  2. Creates `.buildanchor.json` locking in target versions and verified test commands.
  3. Appends/generates an authoritative rule block in `.cursorrules`, `CLAUDE.md`, or `AGENT.md`.
  4. Generates a README badge snippet:
     `[![BuildAnchor Verified](https://img.shields.io/badge/BuildAnchor-Protected-blue)](https://github.com/tensilestream/buildanchor)`

### 4.4 `buildanchor cmd <phase>` (Verified Build Command Resolver)
* **Goal:** End hallucinated build/test commands by agents (e.g., agent running `npm test` instead of `pnpm run test:unit`).
* **Phases:** `test`, `build`, `lint`, `format`, `clean`.
* **Behavior:** Reads manifest scripts and lockfiles, returns the single verified shell command string.
* **Output:**
  ```bash
  $ buildanchor cmd test
  pnpm run test:unit
  ```

### 4.5 `--staged` Flag (Git Pre-Commit Hook Integration)
* **Goal:** Microsecond check (< 50ms) before `git commit` to block commits that break build configuration.
* **Behavior:** Restricts change inspection to files in `git diff --cached --name-only`.

---

## 5. Exit Code Contract (Version 1.0)

Every BuildAnchor CLI command adheres to this versioned contract:

| Exit Code | Meaning | Example Scenario |
|---|---|---|
| `0` | **Success / Valid / Found** | Ready to act, package installed, compatible change, change passed probes. |
| `1` | **Invalid / Incompatible / Not Found** | Dependency not installed (`--installed-only`), hard compatibility error, probe failed. |
| `2` | **Inconclusive** | Missing Git baseline (`HEAD` not found) or no recognized build system. |
| `3` | **Blocked / Mismatch** | Objective contradicts repo ecosystem (e.g. JPA in Node repo), or `--assert-ecosystem` failed. |
| `4` | **Execution Error** | Workspace outside allow-root, invalid arguments, filesystem permission denied. |

---

## 6. Phased Implementation Roadmap

### Phase 1: Engine Bugfix & Command Resolver
1. **Fix `_find_rust` regex (`engine.py:1027`)**:
   Add missing capture group `([\d.^~*]+)` to avoid `IndexError`.
2. **Add `resolve_command(phase)` to engine**:
   Inspect package manifests and lockfiles to determine canonical build/test commands.

### Phase 2: Transports & MCP Server
1. **Add `build.find_package` to `TOOLS` list in `transports.py`**:
   Full parameter documentation, examples, and when-to-call hints.
2. **Wire `call_tool()` for `build.find_package`**:
   Return structured package details and import patterns.
3. **Add `/v1/find-package` and `/v1/cmd` endpoints to `HTTPHandler`**.

### Phase 3: CLI Commands & Flags
1. **Add `find` command to `cli.py`**:
   - `--package <name>` (required)
   - `--installed-only` (flag)
   - `--no-show-usage` (flag)
2. **Add `cmd` command to `cli.py`**:
   - `phase` positional argument (`test`, `build`, `lint`, `format`).
3. **Add new flags**:
   - `--agent` (raw prompt block output)
   - `--ci` (GitHub Actions workflow annotations + quiet mode)
   - `--exit-on-mismatch` (exit 3 on objective-ecosystem mismatch)
   - `--assert-ecosystem <name>` (assert repo matches expected ecosystem)
   - `--only-errors` (suppress warnings, exit 0 if no hard errors)
   - `--explain` (add plain-English rationale lines)
   - `--staged` (only inspect Git staged files)
4. **Implement `init` command**:
   - Generates `.buildanchor.json` and agent instructions.
5. **Strictly enforce Exit Code Contract (0-4)**.

### Phase 4: Documentation & Developer Onboarding
1. **Create `ADOPTION.md`**:
   - 30-second Quickstart.
   - Setup guide for Claude Code, Cursor, Aider, Windsurf.
   - GitHub Actions CI template.
   - Pre-commit hook configuration.
   - Small Language Model (SLM) prompting guide.
2. **Update `AGENT_GUIDE.md`**:
   - Reflect `find_package`, new flags, and exit code contract.

### Phase 5: Benchmark Suite & Empirical Proof
1. **Create `benchmarks/run_benchmarks.py`**:
   - Automated runner comparing standard context injection vs BuildAnchor.
2. **Create 6 Fixture Repositories in `benchmarks/fixtures/`**:
   - `maven-spring3` (Spring Boot 3 + Java 21)
   - `node-esm` (TypeScript + ESM package.json)
   - `python-pyproject` (FastAPI + uv/pyproject.toml)
   - `go-module` (Go 1.22 + go.mod)
   - `rust-2021` (Rust 2021 edition + Cargo.toml)
   - `polyglot` (Combined Node frontend + Python backend)
3. **Emit `benchmarks/results.json` & Markdown Summary Table**.

### Phase 6: Test Suite & Verification
1. **Extend `tests/test_engine.py`**:
   - Test `find_package` for Node, Python, Maven, Go, Rust.
   - Test `resolve_command`.
2. **Extend `tests/test_cli.py`**:
   - Test `find` command output and exit codes.
   - Test `--exit-on-mismatch` returning 3.
   - Test `--assert-ecosystem` returning 3 on mismatch.
   - Test `--agent` output cleanliness.
3. **Extend `tests/test_transports.py`**:
   - Test `build.find_package` over MCP protocol.

---

## 7. Verification Gates & Success Criteria

```bash
# 1. Full unit test suite must be 100% green
uv run python -m unittest discover -s tests -v

# 2. Benchmark suite must demonstrate >= 85% token reduction
uv run python benchmarks/run_benchmarks.py

# 3. CLI Smoke Tests
# Test 3.1: Mismatch detection exit code
buildanchor plan --workspace . --objective "Add a JPA entity" --exit-on-mismatch
# Assert exit code == 3

# Test 3.2: Package search
buildanchor find --package pytest --workspace .
# Assert outputs installed/declared version and project import patterns

# Test 3.3: Agent prompt direct pipe
buildanchor preflight --agent --objective "Fix bug"
# Assert outputs pure markdown block without header/footer noise
```
