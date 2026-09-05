<p align="center">
  <img src="docs/branding/buildanchor_project_shield.png" width="120" alt="BuildAnchor Logo">
</p>
 
# BuildAnchor

[![CI](https://github.com/tensilestream/buildanchor/actions/workflows/ci.yml/badge.svg)](https://github.com/tensilestream/buildanchor/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Organization](https://img.shields.io/badge/org-Tensilestream-orange.svg)](https://github.com/tensilestream)

BuildAnchor is an open-source, local-first **Build Truth and change-validation layer for AI coding agents** by [Tensilestream](https://github.com/tensilestream).

Every time a coding agent guesses which test command to run, which Java runtime a repo targets, or whether to use `javax.persistence` or `jakarta.persistence`, it wastes tokens and risks making an incompatible change. BuildAnchor answers those questions in milliseconds — locally, offline, and with zero LLM calls — so the agent doesn't have to.

## Quick Start (Get Started in 10 Seconds)

### 1. Homebrew (macOS & Linux — Recommended)
```bash
brew install tensilestream/tap/buildanchor

# Or via explicit tap:
brew tap tensilestream/tap
brew install buildanchor
```

> **Note for Homebrew 6.0+**: If Homebrew prompts that third-party taps require trust, simply run:
> ```bash
> brew trust tensilestream/tap
> ```

### 2. uvx (Instant Run — Zero Installation)
```bash
uvx buildanchor inspect
```

### 3. Standalone Shell Installer (macOS & Linux)
```bash
curl -fsSL https://raw.githubusercontent.com/tensilestream/buildanchor/main/scripts/install.sh | bash
```

### 4. pip / pipx (Python 3.10+)
```bash
pipx install buildanchor
# or
pip install buildanchor
```

---

## How it saves LLM tokens

| Without BuildAnchor | With BuildAnchor |
|---|---|
| Agent reads `pom.xml`, `build.gradle`, `pyproject.toml`, `package.json`, … | Agent injects one ~150-token block from `build.llm_prompt` |
| Agent guesses `javax.persistence` vs `jakarta.persistence` | BuildAnchor detects Spring Boot 3+ and flags the correct namespace |
| Agent tries the wrong test command and wastes a turn repairing it | BuildAnchor proves the exact validated test command |
| Agent silently uses the 2015 Rust edition | BuildAnchor warns and recommends edition 2021 |

**Typical savings: 500–2000 tokens per agent invocation** on polyglot repositories.

## Universal Kickstart by Ecosystem

BuildAnchor works automatically across all major programming stacks without manual configuration. Run these from your project root:

```bash
# Initialize your project: auto-detects stack and writes AGENT.md rules
buildanchor init

# Get instant, zero-noise context to inject into your agent
buildanchor llm-prompt --agent

# Check if a package is already installed before adding duplicate dependencies
buildanchor find --package express       # Node / TypeScript
buildanchor find --package pydantic      # Python
buildanchor find --package jackson       # Java / Kotlin

# Run verified tests without guessing test runners or flags
buildanchor cmd test
```

### Monorepo Intelligence & Targeted Test Scoping

In multi-package repositories (Turborepo, Nx, pnpm/npm/yarn workspaces, Cargo, Maven multi-module, Gradle multi-project, Go workspaces, and Python monorepos), running the entire repository test suite wastes minutes and tokens, and frequently causes coding agents to fail. BuildAnchor detects monorepo topology and enables targeted, scoped testing:

```bash
# Discover all packages, directories, and categories (UI vs BACKEND vs SHARED)
buildanchor modules

# Run ONLY UI / frontend tests
buildanchor cmd test --scope ui

# Run ONLY backend / API / database tests
buildanchor cmd test --scope backend

# Target a specific package or directory
buildanchor cmd test --scope @acme/web
buildanchor cmd test --scope apps/api

# Automatically detect and test ONLY packages modified in git diff
buildanchor cmd test --changed
```

### Real-World Developer Tasks & Agent Prompts

Below are generic prompts that work across the most common project types in the world:

| Ecosystem | Common Generic Task | BuildAnchor Pre-Flight Command |
|---|---|---|
| **Node / TypeScript** | *"Add a JWT authentication middleware"* | `buildanchor plan --workspace . --objective "Add a JWT authentication middleware"` |
| **Python (FastAPI / Django)** | *"Add an async health check route with DB ping"* | `buildanchor plan --workspace . --objective "Add an async health check route with DB ping"` |
| **Java (Spring Boot)** | *"Add a REST controller with validation"* | `buildanchor plan --workspace . --objective "Add a REST controller with validation"` |
| **Go** | *"Implement structured logging and graceful shutdown"* | `buildanchor plan --workspace . --objective "Implement structured logging and graceful shutdown"` |
| **Rust** | *"Implement request rate limiting worker"* | `buildanchor plan --workspace . --objective "Implement request rate limiting worker"` |
| **.NET (C#)** | *"Add an EF Core DbContext entity and migration"* | `buildanchor plan --workspace . --objective "Add an EF Core DbContext entity and migration"` |

### Ready-to-Use Agent Prompt Templates

Copy and paste these snippets into your agent's instructions (`.cursorrules`, `AGENT.md`, `CLAUDE.md`, or System Prompt):

#### For Cursor / Windsurf / Copilot (`.cursorrules` or instructions):
```markdown
Before modifying build configs, adding dependencies, or running tests:
1. Run `buildanchor preflight --agent` to inspect repository runtime truth.
2. If adding an import or package, run `buildanchor find --package <name>` first to verify existing versions and import conventions.
3. Run verified tests using `buildanchor cmd test`.
4. After completing code changes, run `buildanchor validate-change --baseline HEAD`.
```

#### For Terminal Agents (Claude Code, Aider, OpenCodeInterpreter):
```bash
# In your agent prompt or slash-command:
"Please implement [TASK]. First run `buildanchor plan --objective '[TASK]' --agent` to verify stack constraints and baseline, then write the code and run `buildanchor cmd test`."
```

## Recommended call sequence for agents

```
1. build.llm_prompt   →  inject into system prompt (150 tokens, zero risk)
2. build.preflight    →  gate before touching build/dependency files
3. [agent acts]
4. build.validate_change  →  confirm the change is coherent
5. build.repair_guidance  →  fix if invalid/inconclusive
```

Use `build.token_estimate` first if you want to see the cost of each tool before calling.

## Ecosystem compatibility rules (catches what LLMs hallucinate)

| Rule | Languages | What it catches |
|---|---|---|
| Jakarta namespace migration | Java, Kotlin | `javax.persistence/validation/servlet` → `jakarta.*` (Spring Boot 3+) |
| Python packaging | Python | `setup.py`-only, deprecated `distutils`, `pkg_resources` → `importlib` |
| Node ESM | JavaScript, TypeScript | `"main"` without `"exports"`, deprecated `request` package |
| Go modules | Go | Pre-module layout (no `go.mod`) |
| Rust edition | Rust | Edition 2015 → recommend 2021 |
| Objective mismatch | All | JPA objective on a Node.js repo → explicit warning |

It helps an agent follow this lifecycle:

```text
Inspect → Act → Validate → Repair → Validate again
```

BuildAnchor reports what a repository can prove about its build system, runtime, dependencies, compatibility constraints, and validation commands. It does not claim that a build passed unless an approved validation runner actually ran it.


## Who this is for

BuildAnchor is designed for:

- Teams building AI coding agents that need repository-aware context before editing code.
- Developer-platform and DevOps teams supporting many repositories and build systems.
- Maintainers who want a repeatable pre-change check and evidence-backed change validation.
- Security-conscious engineering teams that need local-first, bounded, auditable diagnostics.

## What it saves

BuildAnchor is intended to save investigation time and wasted validation cycles by answering the
questions that usually slow down an automated or unfamiliar change: which build system is active,
which runtimes and dependencies are present, which compatibility constraints matter, what changed,
and which validation commands are appropriate. It reports evidence and uncertainty instead of
guessing, so teams can compare its local latency with their own workflow before adopting it.

It is a repository inspection and change-validation layer, not a replacement for the repository's
build tool, test runner, CI system, or security sandbox.

## Copyright and project identity

BuildAnchor is copyright © 2026 Tensilestream and BuildAnchor contributors and is distributed
under the Apache License, Version 2.0. Projects may use, modify, and include the code when they
retain the required license, copyright, attribution, NOTICE, and modification notices.

The BuildAnchor and Tensilestream names and marks identify the upstream project. A fork or
derivative must not remove attribution, present the upstream code as entirely original, use the
upstream name as its own product branding, or imply official endorsement. See
[`TRADEMARKS.md`](TRADEMARKS.md) for the project identity policy.

## Quick start

### Install the CLI

#### Homebrew (macOS & Linux)
```bash
brew install tensilestream/tap/buildanchor

# On Homebrew 6.0+, trust the tap if prompted:
brew trust tensilestream/tap
```

#### uv (Fastest)
```bash
uv tool install buildanchor

# Or run ephemerally with zero install:
uvx buildanchor inspect
```

#### pipx / pip (Python 3.10+)
```bash
pipx install buildanchor
# or
pip install buildanchor
```

#### Standalone Shell Installer (macOS & Linux)
```bash
curl -fsSL https://raw.githubusercontent.com/tensilestream/buildanchor/main/scripts/install.sh | bash
```

#### Windows PowerShell
```powershell
irm https://raw.githubusercontent.com/tensilestream/buildanchor/main/scripts/install.ps1 | iex
```

For a contributor checkout, install the local files globally with the platform installer:

```bash
./scripts/install.sh --local --global
```

### Inspect another repository

Once installed, BuildAnchor can be run from any directory:

```bash
buildanchor inspect --workspace /path/to/another/repository --format text
cd /path/to/another/repository
buildanchor context --workspace . --format json
```

### Contributor quick start

To test the current checkout before installing a package:

```bash
uv sync
uv run python -m unittest discover -s tests -v
uv run buildanchor inspect --workspace /path/to/another/repository --format text
```

### Version management and releases

BuildAnchor synchronizes versions across Python (`pyproject.toml`, `uv.lock`), Java (`sdk/java/pom.xml`), and Homebrew (`Formula/buildanchor.rb`).

Use the atomic version helper to update all files simultaneously:

```bash
# Bump patch (e.g. 0.3.3 -> 0.3.4):
python3 scripts/bump_version.py --patch

# Bump minor (e.g. 0.3.3 -> 0.4.0):
python3 scripts/bump_version.py --minor

# Explicit version:
python3 scripts/bump_version.py 1.0.0
```

> **Automated CI/CD Releases**: Pushing a tag (`git tag v0.3.3 && git push origin v0.3.3`) or dispatching the **Release & Publish** action builds packages, attaches release notes and checksums to GitHub Releases, publishes to PyPI, updates the Homebrew tap, and auto-increments the next development version on `main`.

The platform installer below is for contributors who specifically need the current checkout
exposed as a command outside `uv run`.

### Run directly from the source checkout

```bash
uv sync
uv run buildanchor inspect --workspace . --format text
uv run buildanchor compatibility --workspace . --format json
uv run buildanchor plan --workspace . --objective "Add a health check endpoint" --format json
uv run buildanchor context --workspace . --format json
uv run buildanchor validate-change --workspace . --baseline HEAD --format json
# Execute the detected, bounded validation probes as an explicit opt-in:
uv run buildanchor validate-change --workspace . --baseline HEAD --execute --format json
```

`uv sync` creates and manages the project virtual environment automatically. The current reference implementation uses Python 3.10+ and has no runtime dependencies.

If `uv` is unavailable, the equivalent standard-library fallback is:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
buildanchor inspect --workspace . --format text
```

### Install the current checkout globally for development

Run the installer from the repository root. It installs this checkout, including uncommitted
source changes, globally for your user account:

```bash
./scripts/install.sh --local --global
```

On Windows PowerShell:

```powershell
.\scripts\install.ps1 -Local -Global
```

The script uses `pipx` or a user-scoped Python installation and does not clone or fetch the
repository when run from a checkout. If a Python virtual environment is active, deactivate it
first or install `pipx`; the script intentionally avoids turning a project virtual environment
into a global installation.

### One-command installers

From a macOS or Linux shell, install the latest main branch with:

```bash
curl -fsSL https://raw.githubusercontent.com/tensilestream/buildanchor/main/scripts/install.sh | bash
```

When run from this checkout, the script installs the local source tree, including uncommitted
changes. It does not clone or fetch the repository.

Homebrew installation is reserved for a published formula/tap; it is not used by this local
checkout installer because contributors need to test the files before pushing them.

From Windows PowerShell, use:

```powershell
irm https://raw.githubusercontent.com/tensilestream/buildanchor/main/scripts/install.ps1 | iex
```

The bootstrap scripts use `pipx` or a user-scoped Python installation. Review the script before
piping it to a shell in a restricted environment. Set `BUILDANCHOR_SOURCE_URL` to install from a
pinned release archive or an internal mirror. The Homebrew formula is intended for a published
tap, not for pre-push local testing.

## Interactive CLI workflows

Add `--interactive` (or `-i`) to use BuildAnchor as a guided terminal workflow rather than
memorising flags. Interactive mode displays the workspace, explains each requested value, accepts
defaults with Enter, and lets you cancel safely with `q`.

```bash
# Instead of an error for a missing objective, BuildAnchor asks for one.
buildanchor plan -i

# The same guided flow is available for commands with other inputs.
buildanchor find --interactive
buildanchor cmd -i
buildanchor validate-change -i
```

| Command | Interactive inputs |
| --- | --- |
| `plan` | Required objective |
| `preflight`, `llm-prompt` | Optional objective |
| `find` | Required package name |
| `explain-dependency` | Required dependency or coordinate |
| `cmd` | Build phase, optional scope, changed-modules choice |
| `context` | Token budget |
| `change-impact` | Baseline and staged-only choice |
| `validate-change` | Baseline, staged-only choice, and explicit probe-execution choice |
| `serve` | Listen address |
| `inspect`, `token-estimate`, `repair`, `compatibility`, `modules`, `init`, `setup-copilot` | Confirms workspace defaults before running |
| `setup-mcp` | Keyboard selector: Up/Down to move, Space to select, Enter to install |

Interactive mode requires an attached terminal and plain text output. It is intentionally disabled
for `mcp` (a JSON-RPC stdio server), `--format json`, `--format llm`, `--agent`, CI, and piped
execution so that automation and protocol consumers always receive clean machine-readable output.

## Agent integration

Start the MCP server with a bounded workspace:

```bash
buildanchor mcp --stdio --allow-root /path/to/repository
```

For GitHub Copilot in VS Code, create or update the workspace MCP configuration automatically:

```bash
buildanchor setup-copilot --workspace .
```

This adds the local BuildAnchor server to `.vscode/mcp.json` without removing other configured servers. Use `--force` only to replace an existing `buildanchor` entry.

Configure several local coding agents in one command:

```bash
buildanchor setup-mcp --workspace . --clients copilot,cursor,claude-code,codex
# Or configure every supported client:
buildanchor setup-mcp --workspace . --clients all
```

For an interactive terminal selector:

```bash
buildanchor setup-mcp --workspace . --interactive
```

Use the Up/Down arrow keys to move, Space to toggle a client, and Enter to install the selected clients. In a non-interactive terminal or CI pipe, BuildAnchor falls back to the numbered prompt.

The selector explicitly distinguishes client scope:

- `copilot` writes workspace `.vscode/mcp.json`.
- `cursor` writes workspace `.cursor/mcp.json`.
- `claude-code` writes workspace `.mcp.json`.
- `claude-desktop` writes the user-level Claude Desktop configuration.
- `codex` writes user-level `~/.codex/config.toml`.

Use `claude-code` for repository setup; `claude-desktop` is available only when an app-wide Claude Desktop installation is intended. `claude` remains an alias for `claude-code`, and `gpt` is an alias for `codex`.

Available MCP tools:

- `build.inspect`
- `build.context`
- `build.preflight`
- `build.plan`
- `build.change_impact`
- `build.validate_change`
- `build.repair_guidance`
- `build.explain_dependency`

`build.validate_change` is static by default. Pass `execute: true` and an optional `timeout` to run detected validation probes with `shell=False`, bounded output, and per-command timeouts. BuildAnchor reports each probe as `passed`, `failed`, `timed_out`, or `unavailable`; it never turns a missing baseline or missing tool into a pass.

For automation, `validate-change` exits `0` for `valid`, `1` for `invalid`, `2` for `inconclusive`, and `3` for `blocked`.

The compact context pack gives an agent authoritative facts first and evidence references on demand.

Call `build.plan` or the SDK `plan()` method before the agent acts. The plan contains the objective, baseline digest, authoritative context, compatibility decisions, ordered steps, and validation gates. For example, a Spring Boot 3 repository using `javax.persistence` receives an evidence-backed recommendation to use `jakarta.persistence` and the `jakarta.persistence:jakarta.persistence-api` coordinate instead. Framework-managed versions are reported as managed rather than guessed.

## HTTP integration

```bash
buildanchor serve --workspace . --listen 127.0.0.1:8787
curl -X POST http://127.0.0.1:8787/v1/inspect \
  -H 'content-type: application/json' \
  -d '{}'
```

## Python SDK

Full API reference: [Python SDK guide](docs/sdk/python.md).

```python
from buildanchor import BuildAnchorClient

client = BuildAnchorClient(workspace=".")
context = client.llm_prompt("Add rate limiting")["content"]
preflight = client.preflight("Add rate limiting")
result = client.validate_change()  # Static and non-executing by default.
```

Use `AsyncBuildAnchorClient` for asynchronous orchestration. For a bounded remote server, set
`endpoint="http://127.0.0.1:8787"`; the SDK sends the requested workspace on every call and the
server rejects paths outside its allowed root. See the complete agent workflow, safety contract,
and API reference in [`sdk/python/README.md`](sdk/python/README.md).

## Node.js SDK

Full API reference: [Node.js SDK guide](docs/sdk/node.md).

```js
import { BuildAnchorClient } from "@tensilestream/buildanchor";

const client = new BuildAnchorClient({ workspace: "." });
const context = await client.llmPrompt("Add rate limiting");
const preflight = await client.preflight({ objective: "Add rate limiting" });
const result = await client.validateChange(); // Static and non-executing by default.
```

The dependency-free Node.js SDK supports local CLI and bounded HTTP modes with the same v1
response contract. See [`sdk/node/README.md`](sdk/node/README.md) for its full agent workflow,
typed API, error model, and publishing checks.

### Distribution identities

| Ecosystem | Package identity |
| --- | --- |
| PyPI | `buildanchor` |
| npm | `@tensilestream/buildanchor` |
| Maven | `io.github.tensilestream:buildanchor-sdk` |

These names use each registry's native namespace convention while identifying
the same Tensilestream-maintained BuildAnchor product.

## Java SDK

Full API reference: [Java SDK guide](docs/sdk/java.md).

Maven coordinates:

```text
io.github.tensilestream:buildanchor-sdk:1.0.0
```

```java
try (BuildAnchorClient client = BuildAnchorClient.builder()
        .workspace(Path.of("."))
        .build()) {
    BuildAnchorResponse baseline = client.inspect();
    BuildAnchorResponse result = client.validateChange("HEAD");
}
```

See [`sdk/java/README.md`](sdk/java/README.md) for the package-level overview.

## GitHub and agent integration

BuildAnchor needs a Git baseline for change validation. In a new checkout, create one before validating:

```bash
git add .
git commit -m "baseline"
buildanchor validate-change --baseline HEAD --execute --format markdown
```

For pull requests, use the base commit supplied by GitHub Actions:

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0
- uses: astral-sh/setup-uv@v6
- run: uvx --from buildanchor buildanchor validate-change --baseline "${{ github.event.pull_request.base.sha }}" --execute --format sarif > buildanchor.sarif
```

To connect an agent through MCP, point it at a bounded checkout:

```json
{
  "mcpServers": {
    "buildanchor": {
      "command": "buildanchor",
      "args": ["mcp", "--stdio", "--allow-root", "/path/to/repository"]
    }
  }
}
```

## Supported ecosystems

The first MVP detects Maven, Gradle, Node, Python, Go, Rust, .NET, and generic build markers such as Make, CMake, Bazel, Swift Package Manager, Composer, Bundler, and pub. Static inspection is always explicit about unsupported, unavailable, and policy-blocked capabilities. Validation is intentionally two-stage: static Git/change analysis first, then explicit probe execution when the caller opts in.

## Frequently Asked Questions (FAQ)

### What is BuildAnchor?
BuildAnchor is an open-source, local-first Build Truth and change-validation layer for AI coding assistants and autonomous agents. It deterministically proves repository build facts, runtime environments, dependency compatibility, and test command scopes in milliseconds with zero LLM overhead.

### How does BuildAnchor stop AI coding agents from breaking builds?
Coding agents (such as Claude Code, Cursor, Copilot Workspace, Devin, and Aider) typically inspect a codebase by reading multiple configuration files (`pom.xml`, `package.json`, `build.gradle`, `Cargo.toml`), frequently hallucinating wrong test commands or deprecated runtime APIs (e.g., mixing `javax.*` with `jakarta.*` in Spring Boot 3+). BuildAnchor deterministically inspects the repository offline in milliseconds, proving the exact build facts and test command scopes before and after code edits.

### How does BuildAnchor save LLM tokens?
Instead of forcing an agent to ingest thousands of tokens of verbose configuration files and dependency graphs, BuildAnchor injects a compact, ~150-token authoritative context pack (`build.llm_prompt`). This saves between 500 and 2,000 tokens on every agent turn, reducing latency and API costs.

### Does BuildAnchor support monorepos?
Yes. BuildAnchor features automatic monorepo topology discovery across 8 major ecosystems (pnpm, Cargo workspaces, Gradle multi-project, Maven multi-module, Nx, Turborepo, Go multi-module, and npm/yarn workspaces). It semantically categorizes packages into `ui`, `backend`, and `shared`, enabling agents to invoke targeted scoped tests (e.g., `buildanchor cmd --scope ui` or `--changed HEAD~1`) without running the entire repository test suite.

### How do I connect BuildAnchor to Claude Code, Cursor, or other MCP agents?
BuildAnchor provides a built-in Model Context Protocol (MCP) server. You can connect any MCP-compatible agent by adding BuildAnchor to your agent's MCP configuration:
```json
{
  "mcpServers": {
    "buildanchor": {
      "command": "buildanchor",
      "args": ["mcp", "--stdio", "--allow-root", "/path/to/repository"]
    }
  }
}
```
This gives your agent instant access to tools like `build.inspect`, `build.context`, `build.validate_change`, and `build.modules`.

### Is BuildAnchor offline-capable and secure?
Yes. BuildAnchor is read-only, local-first, and completely offline by default. It executes zero external LLM API calls, redacts sensitive tokens and secrets, uses strictly bounded probes, and never sends your proprietary source code to external servers.

## Security model

BuildAnchor is read-only and offline-capable by default. It contains workspace paths, uses fixed probe names, does not accept raw agent commands, limits output and execution, redacts credential-bearing values, and records evidence digests. Live resolution and validation should run only through an enterprise-approved sandbox and policy.

Read [`SECURITY.md`](SECURITY.md) before enabling networked or executable probes.

## Support and feedback

Use [GitHub Discussions](https://github.com/tensilestream/buildanchor/discussions) for usage
questions and design conversations. Use [GitHub Issues](https://github.com/tensilestream/buildanchor/issues)
for reproducible bugs, installation problems, and feature requests. Use the private reporting
channel described in [`SECURITY.md`](SECURITY.md) for vulnerabilities.

## Development

```bash
uv run python -m unittest discover -s tests -v
javac --release 17 -d /tmp/buildanchor-java-classes sdk/java/src/main/java/com/buildanchor/*.java
```

Run the local benchmark harness when changing inspection, context, or planning behavior:

```bash
uv run python benchmarks/benchmark_cli.py --iterations 20 --warmups 3 --format text
```

The benchmarks report local latency for a deterministic representative fixture. They are
engineering baselines, not claims of guaranteed time saved for every repository or team.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for development, testing, adapter, and release guidelines.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
