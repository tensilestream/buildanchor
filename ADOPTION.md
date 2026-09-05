# BuildAnchor — Universal Adoption & Integration Guide

[![BuildAnchor Verified](https://img.shields.io/badge/BuildAnchor-Protected-blue)](https://github.com/tensilestream/buildanchor)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Tokens: <300](https://img.shields.io/badge/Context- <300_tokens-green.svg)](https://github.com/tensilestream/buildanchor)

> **The local-first Build Truth engine for software developers and AI coding agents.**  
> Eliminates hallucinated commands, prevents framework mismatches (e.g. JPA in Node repos), and cuts LLM context token consumption by **85–98%**.

---

## 1. 30-Second Quickstart

### Instant Onboarding (Zero Configuration)
Run `buildanchor init` inside any repository:
```bash
# Using uv (fastest, no install needed):
uvx buildanchor init

# Or using pip / pipx:
pipx install buildanchor
buildanchor init
```

`buildanchor init` automatically:
1. Inspects the repository build systems, package managers, and runtimes.
2. Creates `.buildanchor.json` locking in target versions and verified test/build commands.
3. Appends authoritative verification rules to `CLAUDE.md`, `AGENT.md`, or `.cursorrules`.
4. Outputs the repository verification badge for your `README.md`.

---

## 2. The Core Problems BuildAnchor Solves

| Problem | Without BuildAnchor | With BuildAnchor |
|---|---|---|
| **Ecosystem Hallucinations** | Agent writes Jakarta/JPA code in a Node repository or uses `javax.*` in Spring Boot 3 | `buildanchor plan` detects mismatch before a single line is written (Exit code `3`) |
| **Token Waste** | Agent reads 10+ raw manifests (`package.json`, `pom.xml`, `go.mod`), burning 10,000+ tokens | `buildanchor preflight --agent` injects deterministic Build Truth in **< 250 tokens** |
| **Hallucinated Commands** | Agent guesses `npm test` when the project requires `pnpm run test:unit` | `buildanchor cmd test` returns the single verified shell command string |
| **Dependency Collisions** | Agent installs duplicate libraries or incompatible major versions | `buildanchor find --package <name>` reveals installed versions and import patterns |
| **CI Drift** | Incompatible AI pull requests break CI pipelines after merge | `uvx buildanchor preflight --ci` fails builds immediately with GitHub Actions annotations |

---

## 3. Integration Guides by AI Agent / Tool

### 3.1 Claude Code
Add to your project's `CLAUDE.md`:
```markdown
## Build Truth & Verification
Before implementing features or modifying dependencies, run:
```bash
buildanchor preflight --agent --objective "$OBJECTIVE"
```
To find if a package is installed and its project import conventions:
```bash
buildanchor find --package <name>
```
To run tests using the verified build command:
```bash
$(buildanchor cmd test)
```
```

#### Terminal Pipe Alias
Add to your `~/.zshrc` or `~/.bashrc`:
```bash
alias claude-task="claude --prompt \"\$(buildanchor preflight --agent)\""
```

---

### 3.2 Cursor (`.cursorrules`)
Add the following to `.cursorrules` in your project root:
```markdown
# BuildAnchor Build Truth Rules
1. Before adding any package or writing imports, verify availability:
   `buildanchor find --package <name>`
2. Follow import patterns returned by BuildAnchor (e.g. ESM vs CJS, Jakarta vs Javax).
3. Run verified project tests using:
   `buildanchor cmd test`
4. If modifying project manifests, run preflight check:
   `buildanchor preflight --exit-on-mismatch`
```

---

### 3.3 Aider (`.aider.conf.yml`)
Add to `.aider.conf.yml` or invoke via CLI:
```yaml
# .aider.conf.yml
read:
  - .buildanchor.json
```
Or pipe Build Truth directly on launch:
```bash
aider --message "$(buildanchor preflight --agent)"
```

---

### 3.4 Windsurf / Cascade
Add to `.windsurfrules`:
```markdown
Always execute `buildanchor preflight --agent` before attempting refactors or dependency upgrades.
Use `buildanchor cmd test` to run tests. Do not guess build commands.
```

---

### 3.5 Local Small Language Models (SLMs)
*(Ollama, DeepSeek-Coder, Qwen, Llama 3, Phi-3)*

Small models get easily overwhelmed by large context windows. Use `--agent` to provide ultra-dense context:
```bash
PROMPT=$(buildanchor preflight --agent --objective "Add rate limiting")
ollama run deepseek-coder:6.7b "$PROMPT\n\nImplement the objective."
```

---

## 4. MCP Server Setup (Claude Desktop & Continue.dev)

BuildAnchor exposes **13 MCP tools** over standard JSON-RPC Stdio.

### Configuration (`claude_desktop_config.json`)
```json
{
  "mcpServers": {
    "buildanchor": {
      "command": "uvx",
      "args": ["buildanchor", "mcp", "--workspace", "/absolute/path/to/your/repo"]
    }
  }
}
```

### Available MCP Tools:
- `build.llm_prompt` — Call first before acting; compact prompt block (~80–250 tokens).
- `build.token_estimate` — Cost matrix and recommended tool (< 5 tokens).
- `build.find_package` — Find installed version, declared version, and import patterns.
- `build.cmd` — Verified shell command for `test`, `build`, `lint`, `format`, `clean`.
- `build.preflight` — Pre-change gate checking readiness and hard compatibility errors.
- `build.plan` — Repository-aware execution plan with gates.
- `build.change_impact` — Git impact analysis comparing workspace to baseline.
- `build.validate_change` — Runs test probes and checks for drift.
- `build.repair_guidance` — Structured repair steps for failures.
- `build.compatibility` — Multi-ecosystem compatibility checks.
- `build.inspect` — Full build truth report.

---

## 5. CI/CD Automated PR Gating (`--ci`)

Add a fast, offline gate to your pull requests:

### GitHub Actions (`.github/workflows/buildanchor.yml`)
```yaml
name: Build Truth Gate

on:
  pull_request:
    branches: [main]

jobs:
  preflight:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Install uv
        uses: astral-sh/setup-uv@v2

      - name: BuildAnchor Preflight Check
        run: |
          uvx buildanchor preflight --ci --objective "${{ github.event.pull_request.title }}"
```

### GitHub Actions Annotations
When running with `--ci`, BuildAnchor formats errors directly as workflow annotations:
```
::error file=package.json::Missing exports field — ESM consumers may fail.
```

---

## 6. Git Pre-Commit Hook (`--staged`)

Prevent commits that break build configuration with `< 50ms` latency:

### `.pre-commit-config.yaml`
```yaml
repos:
  - repo: local
    hooks:
      - id: buildanchor-staged
        name: BuildAnchor Staged Verification
        entry: uvx buildanchor change-impact --staged --exit-on-mismatch
        language: system
        pass_filenames: false
```

---

## 7. Versioned Exit Code Contract

BuildAnchor guarantees deterministic exit codes for shell composition (`&&` / `||`):

| Exit Code | Status | Meaning | Developer Action |
|---|---|---|---|
| **`0`** | `valid` / `found` | Safe to proceed; package found; change compatible | Continue agent execution |
| **`1`** | `invalid` / `not found` | Breaking compatibility error or package missing | Halt; apply suggested repair |
| **`2`** | `inconclusive` | Missing Git baseline or no build system detected | Run `git init` or add manifest |
| **`3`** | `blocked` / `mismatch` | Objective contradicts stack (e.g. JPA in Node) | Halt; redirect agent objective |
| **`4`** | `error` | Bad arguments, permissions, workspace escape | Fix CLI arguments |

### Shell Chaining Example
```bash
# Run agent only if objective is compatible with repository stack:
buildanchor plan --objective "Add JPA entity" --exit-on-mismatch \
  && claude-code "Add JPA entity" \
  || echo "Aborted: Objective contradicts repository technology stack"
```

---

## 8. Command & Flag Cheat Sheet

```bash
# Search for installed packages & usage patterns
buildanchor find --package axios
buildanchor find --package pytest --installed-only
buildanchor find --package fastapi --format llm

# Resolve verified build/test commands
buildanchor cmd test
buildanchor cmd build
buildanchor cmd lint

# Output raw prompt block for agent piping
buildanchor preflight --agent
buildanchor plan --agent --objective "Add Webhook"

# Assert expected stack
buildanchor preflight --assert-ecosystem node
buildanchor preflight --assert-ecosystem python

# Filter out non-blocking warnings
buildanchor preflight --only-errors

# Add plain-English explanations to findings
buildanchor preflight --explain
```
