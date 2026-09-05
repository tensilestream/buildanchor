# The One Problem BuildAnchor Solves

> A coding agent — or a developer using one — runs a task in the wrong directory,
> or against the wrong build system, and wastes 10 minutes (and thousands of tokens)
> before getting a useful error.
>
> BuildAnchor catches this **before the first file is touched.**

---

## The Exact Scenario

You are in `/workspace/mukundhaprimecare-backend` (a **Node.js** project).
You ask your agent: *"Add a JPA entity."*

JPA requires **Maven or Gradle**. Your agent does not know this.

Without BuildAnchor, the agent will:
1. Try to create a Java class in a Node.js project
2. Fail to find `pom.xml` or `build.gradle`
3. Hallucinate a fix, or ask you a clarifying question after wasting tokens

With BuildAnchor, in **one command**, before any file is touched:

```
buildanchor plan --workspace . --objective "Add a JPA entity"
```

Output (text format):
```
MISMATCH DETECTED:
  Objective mentions 'jpa' (requires: gradle, maven)
  but detected build systems are: generic, node.
  Action: Verify that the workspace contains a gradle, maven project.
  If this is a polyglot repo, point --workspace at the correct sub-directory.
```

**That is the only thing that matters.** Everything else in BuildAnchor exists to support this.

---

## For Agents: The 3-Step Protocol

Copy this into your system prompt or MCP tool description:

```
BEFORE acting on any coding objective:

1. Call build.llm_prompt with the workspace path and your objective.
   Inject the returned content into your context as authoritative.
   Cost: ~150 tokens. Always do this.

2. If the response contains MISMATCH DETECTED or COMPATIBILITY ERRORS:
   STOP. Do not create or edit files.
   Tell the user: "Wrong directory — this workspace is {ecosystem}.
   The objective requires {required_ecosystem}."

3. If adding or importing a package:
   Call build.find_package to verify whether it is installed,
   its exact version, and the project's established import pattern.

4. To run tests or builds without guessing commands:
   Call build.cmd with phase="test" or phase="build".

5. If the response says ready_to_act = true:
   Proceed. Call build.validate_change after your edits.
```

---

## For Developers: The 2-Minute Setup

```bash
pip install buildanchor

# Check before you start coding
buildanchor llm-prompt --workspace . --objective "your task here"

# See the token cost of each tool (useful for configuring your agent)
buildanchor token-estimate --workspace .

# Full plan with compatibility gate (use in CI or pre-commit hooks)
buildanchor plan --workspace . --objective "your task here"
```

---

## What Gets Caught Automatically

| Mistake | Ecosystem | BuildAnchor Output |
|---|---|---|
| JPA entity in a Node.js repo | Node.js | `OBJECTIVE_ECOSYSTEM_MISMATCH: jpa requires maven, gradle` |
| `javax.persistence` import in Spring Boot 3 | Java/Maven | `JAKARTA_PERSISTENCE_NAMESPACE: use jakarta.persistence` |
| `import distutils` in Python 3.12+ | Python | `PYTHON_DEPRECATED_DISTUTILS: removed in Python 3.12` |
| No `go.mod` in a Go project | Go | `GO_PRE_MODULE_LAYOUT: run go mod init` |
| Rust edition 2015 | Rust | `RUST_EDITION_2015: upgrade to edition = "2021"` |
| Missing `exports` field in package.json | Node.js | `NODE_MISSING_EXPORTS_FIELD: ESM consumers may fail` |

---

## Monorepo Workflows for AI Coding Agents

Coding agents frequently fail or time out in large monorepos because they attempt to run the root test suite or guess inaccurate sub-package commands. BuildAnchor provides automated monorepo topology detection and targeted command execution.

### Discover Modules & Categories
```bash
buildanchor modules
# Or inspect via JSON:
buildanchor modules --format json
```

### Targeted Test Execution
Target only the package category relevant to your edits:

```bash
# Validate frontend / UI changes only:
buildanchor cmd test --scope ui

# Validate backend / API / database changes only:
buildanchor cmd test --scope backend

# Target a specific module by package name or sub-path:
buildanchor cmd test --scope @acme/web
buildanchor cmd test --scope apps/api

# Automatically scope to packages affected by your current Git changes:
buildanchor cmd test --changed
```

### MCP Tools for Monorepos
In Cursor, Claude Desktop, Windsurf, or Continue.dev, use:
- `build.modules`: returns full module hierarchy and category tags (`ui`, `backend`, `shared`).
- `build.cmd`: pass `"scope": "ui"`, `"scope": "backend"`, or `"changed": true` to resolve localized validation commands.

---

## For Small Language Models (Phi, Qwen, Gemma, etc.)

If you are a small model with limited context, use only this:

```
Tool: build.llm_prompt
Input: { "workspace": "<path>", "objective": "<what you are about to do>" }

Rules:
- If output contains "MISMATCH DETECTED": stop. Wrong directory or wrong ecosystem.
- If output contains "[ERROR]": stop. Incompatible change. Read the repair field.
- If output contains "[WARN]": note it, but you may proceed with care.
- If output contains "Validate with:": run that command after your change.
- Otherwise: proceed. The build system is compatible with your objective.
```

The entire output of `build.llm_prompt` is under 400 tokens on most real repositories.
A model with a 2,000-token context window can use this without any truncation.

---

## MCP Configuration (Cursor, Continue.dev, Claude Desktop)

Add to your MCP config:

```json
{
  "mcpServers": {
    "buildanchor": {
      "command": "buildanchor",
      "args": ["mcp", "--workspace", "/path/to/your/repo"]
    }
  }
}
```

Then tell your agent:
> "Always call build.llm_prompt before editing build files, dependency manifests,
>  or framework configuration. If it returns a mismatch or error, stop and report it."

---

## Why This Is Different from Just Reading the Files

| Approach | Tokens consumed | Catches mismatch? | Catches wrong namespace? |
|---|---|---|---|
| Agent reads `pom.xml` | 800–4000 | No | Sometimes |
| Agent reads all build files | 2000–10000 | No | Sometimes |
| `build.llm_prompt` | **~150** | **Yes** | **Yes** |

BuildAnchor reads the files once, locally, offline. The agent gets the answer.
No redundant file reads per invocation. No LLM calls. No network.

---

## The Bottom Line

**BuildAnchor is not a build tool. It is a guard rail.**

It answers the question every agent gets wrong at least once per project:
*"Is this task compatible with this workspace, and if not, why?"*

It answers in under 200 tokens, before any code is written.
