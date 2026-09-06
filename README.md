<p align="center">
  <img src="docs/branding/buildanchor_project_shield.png" width="120" alt="BuildAnchor Logo">
</p>
 
# BuildAnchor

[![CI](https://github.com/tensilestream/buildanchor/actions/workflows/ci.yml/badge.svg)](https://github.com/tensilestream/buildanchor/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Organization](https://img.shields.io/badge/org-Tensilestream-orange.svg)](https://github.com/tensilestream)

**BuildAnchor tells you the command that builds and tests this repository — and
whether it actually runs.** Open source, local-first, offline, no LLM calls.
By [Tensilestream](https://github.com/tensilestream).

```bash
$ buildanchor cmd test --explain
command: uv run pytest
working_directory: .
status: collects          # a discovery probe was executed and exited 0
```

Every tool of this kind can read a manifest and report what it found. None of
them can tell you whether the command they found *works* — and a plausible,
wrong command is expensive: an agent pays for the tool call, a screen of
collection errors in its context, and a repair turn. So BuildAnchor executes a
cheap discovery probe and reports how far the command is actually proven:

| Status | Meaning |
| --- | --- |
| `declared` | Found in a manifest. A candidate — this is where other tools stop. |
| `resolvable` | Its entrypoint exists on disk or `PATH`. Nothing executed. |
| `collects` | A discovery-only probe exited 0 in the right directory. Seconds. |
| `passes` | The full command exited 0. |

Commands always come with the **directory they run in**, because `pytest lib-a`
from the repository root is not the same thing as `pytest` inside `lib-a`, and
in a repository with more than one project only the second one works.

It knows the difference between a monorepo, a single project, and a root project
with an SDK beside it — and only gives you scoping advice when there is a
scoping decision to make.

### How often is the obvious guess wrong?

On twelve real, unmodified public repositories — cloned at benchmark time, none
of them ours:

| | Gets the project's own test command |
| --- | --- |
| Guessing from the manifest (`pytest`, `npm test`, `go test ./...`) | **7 / 12 — 58%** |
| BuildAnchor | **12 / 12 — 100%** |

**Five of the twelve declare a test entry point that is not their ecosystem's
default, and nothing about the repository announces it.** Flask declares
`[tool.tox]` in its `pyproject.toml`. Requests, pydantic and cobra each have a
`test` target in a `Makefile`. `just` has a `test` recipe in its own justfile.
An agent that guesses `pytest` at Flask is not obviously wrong — it is wrong in
the way that costs a turn to discover.

Every row is verifiable: the benchmark cites the file that declares the answer,
so you can confirm any of them by opening it. Where a project declares nothing,
its ecosystem default *is* correct and is scored that way — those seven rows are
where this tool earns nothing, counted honestly.

```bash
uv run python benchmarks/head_to_head.py --format text     # clones the corpus
```

<details>
<summary><strong>The twelve repositories, and what each one declares</strong></summary>

Each is cloned at benchmark time, so these are current rather than a snapshot we
curated.

| Repository | Declares | Guess | BuildAnchor |
| --- | --- | --- | --- |
| pallets/flask | `pyproject.toml` `[tool.tox]` | ✗ `pytest` | ✓ `tox` |
| psf/requests | `Makefile` `test` target | ✗ `pytest` | ✓ `make test` |
| encode/httpx | nothing — the default applies | ✓ `pytest` | ✓ `python -m pytest` |
| pydantic/pydantic | `Makefile` `test` target | ✗ `pytest` | ✓ `make test` |
| sindresorhus/execa | `package.json` `test` script | ✓ `npm test` | ✓ `npm test` |
| chalk/chalk | `package.json` `test` script | ✓ `npm test` | ✓ `npm test` |
| expressjs/express | `package.json` `test` script | ✓ `npm test` | ✓ `npm test` |
| spf13/cobra | `Makefile` `test` target | ✗ `go test ./...` | ✓ `make test` |
| stretchr/testify | nothing — the default applies | ✓ `go test ./...` | ✓ `go test ./...` |
| BurntSushi/ripgrep | nothing — the default applies | ✓ `cargo test` | ✓ `cargo test` |
| clap-rs/clap | nothing — the default applies | ✓ `cargo test` | ✓ `cargo test` |
| casey/just | `justfile` `test` recipe | ✗ `cargo test` | ✓ `just test` |

**Method.** The expected answer is what each repository declares, cited by file,
so you can confirm any row by opening it. Where a project declares nothing, its
ecosystem default *is* correct and is scored that way — the seven ✓ in the guess
column are cases where this tool earns nothing, counted honestly.

Of the five failures, two (`flask`, `just`) run a genuinely different tool; the
other three wrap the same tool with the project's own arguments — cobra's
`make test` runs `go test` *after* `install_deps`. The benchmark reports that
split rather than counting them as the same thing.

**Two approaches were discarded getting here**, both recorded in
[`benchmarks/README.md`](benchmarks/README.md): scraping each project's CI
workflow proved unreliable on half the corpus, and an early declaration reader
matched `test-mypy` as a `test` target, which would have credited projects that
declare no such entry point. A benchmark whose ground truth is unreliable is
worse than no benchmark.

</details>

Measured against the previous release, on fixtures you can regenerate offline:

| | 1.1.6 | Now |
| --- | --- | --- |
| Emitted test commands that actually run (polyglot monorepo) | **0 / 3** | **5 / 5** |
| Single-project repositories classified correctly | not modelled | 4 / 4 |
| Inspect latency, 9,331-file git repository | 478 ms | **83 ms** |
| MCP tool call, back to back within one turn | 200 ms | **0.1 ms** |
| MCP schema tokens resident per agent turn | 2,510 | **702** |

```bash
uv run python benchmarks/credibility_benchmark.py --format text
```

<details>
<summary><strong>What the offline benchmark measures, and how</strong></summary>

| Metric | Method |
| --- | --- |
| Command correctness | **Executes** each module's emitted test command in its stated working directory and counts exit 0. Not inspected — run. |
| Discovery completeness | Fraction of project markers in the report's own `evidence` that resolve to a module. |
| Repository shape | Whether each fixture is classified `single-project`, `root-plus-satellites` or `monorepo` correctly. |
| Report correctness | Languages claimed vs. demonstrable; malformed dependency coordinates; how many modules contribute dependencies. |
| Latency at scale | Median and p95 on a 9,300-file git repository, 4,500 of them gitignored. |
| Agent context cost | Tokens of MCP tool schema resident on every turn. |

Fixtures are generated offline — no network, no package installs. Each Python
project gets a real virtualenv holding a private dependency, so its tests are
genuinely importable from that project and genuinely not from the repository
root. That is what makes the correctness number a test rather than a restatement
of the code's own assumptions.

**CI enforces these.** `--assert-thresholds` fails the build when command
correctness, discovery completeness, module count, malformed-coordinate count,
shape classification, or per-ecosystem command resolution regress — verified by
deliberately reintroducing a fixed bug and confirming the gate caught it with the
right diagnosis.

</details>

## Compatibility

<!-- compatibility:start -->

### Ecosystems

| Ecosystem | Command resolved from | Verified with |
| --- | --- | --- |
| Java/Maven | `pom.xml` `<modules>`, `mvnw` | `mvn -DskipTests test-compile` |
| Java/Gradle | `settings.gradle`, `gradlew` | `gradle testClasses` |
| Node.js | `package.json` scripts + the lockfile's package manager | `jest`, `mocha`, `playwright`, `vitest`, `node --test` |
| Python | `pyproject.toml`, `uv.lock`, `poetry.lock`, `.venv/` | `pytest --collect-only`, `unittest -k` |
| Go | `go.mod`, `go.work` | `go test -run '^$'` |
| Rust | `Cargo.toml` `[workspace]` | `cargo test --no-run` |
| .NET | `global.json`, `*.csproj` | `dotnet test --list-tests` |

An ecosystem without a discovery probe still resolves a command and reports
`resolvable (no probe available)` rather than guessing that it works.

### Task runners your repository already declares

These take precedence over the ecosystem default — if your `justfile` says
`test: cargo nextest run`, that is the answer, not `cargo test`.

| Runner | Declared in |
| --- | --- |
| just | `justfile`, `Justfile`, `.justfile` |
| Task | `Taskfile.yml`, `Taskfile.yaml` |
| mise | `mise.toml`, `.mise.toml` |
| make | `Makefile`, `makefile`, `GNUmakefile` |
| nox | `noxfile.py` |
| tox | `tox.ini`, `pyproject.toml` `[tool.tox]`, `setup.cfg` |

(6 runners.)

### Agent clients

One tool surface, in whatever dialect your client speaks.

| `format=` | Works with | Verified against |
| --- | --- | --- |
| `anthropic` *(default)* | Anthropic Messages API | — |
| `openai` | OpenAI, LiteLLM, LangChain, OpenRouter, vLLM, most gateways | LiteLLM 1.100.0 |
| `gemini` | Google GenAI, Vertex AI | `google-genai` `types.Tool` |
| `bedrock` | AWS Bedrock Converse | botocore service model |
| `mcp` | Any MCP client — Claude Code, Cursor, Copilot, Codex | — |

### Interfaces

| Surface | Operations | Notes |
| --- | --- | --- |
| CLI | all | `buildanchor <command>` |
| MCP server | 3 advertised | 3 core tools, ~700 tokens of schema |
| HTTP | 15 | local-only operations are refused |
| Python SDK | 16 | sync and async |
| Node SDK | 16 | local and HTTP transports |
| Java SDK | 16 | local and HTTP transports |

### Platforms

| | Status |
| --- | --- |
| Python | 3.10 to 3.13, tested on 3.10 and 3.13 in CI |
| Linux, macOS | tested in CI |
| Windows | non-blocking CI job; `command_shell` is POSIX `sh` — use `working_directory` |
| Runtime dependencies | none |

<!-- compatibility:end -->

This chart is generated from the tables the tool actually uses
(`scripts/generate_compatibility.py`) and a CI check fails when it drifts, so it
cannot claim support that does not exist or omit support that does.

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

## What it saves an agent

| Without BuildAnchor | With BuildAnchor |
|---|---|
| Agent reads `pom.xml`, `build.gradle`, `pyproject.toml`, `package.json`, … | Agent injects one ~150-token block from `build.llm_prompt` |
| Agent guesses `javax.persistence` vs `jakarta.persistence` | BuildAnchor detects Spring Boot 3+ and flags the correct namespace |
| Agent tries the wrong test command and wastes a turn repairing it | BuildAnchor gives the command, the directory it runs in, and how far it is proven (`buildanchor verify`) |
| Agent silently uses the 2015 Rust edition | BuildAnchor warns and recommends edition 2021 |

The tokens are the smaller half. The larger half is the turn an agent does not
spend running a wrong command and reading the failure — which is why the
benchmark measures whether the command runs rather than how short the report is.

### Put it where agents already look

The highest-leverage thing BuildAnchor does is not an MCP tool an agent has to
choose to call. It is a block in the file agents read anyway:

```bash
buildanchor init --verify
```

This writes the command, the directory it runs in, and how far it is proven into
**every** agent guidance file the repository has — `CLAUDE.md`, `AGENTS.md`,
`AGENT.md`, `GEMINI.md`, and any other file already carrying the block. Not one
of them: updating a single file leaves the others holding an older answer, and
an agent will trust whichever one its own tool happens to read. If the
repository has none, `AGENTS.md` is created, since that is the convention the
most tools understand. `--rules-file <path>` overrides all of this.

Re-running refreshes the block in place, keeping any surrounding content you
wrote. To stop it rotting, `--check` reports drift and changes nothing:

```bash
buildanchor init --check    # exit 1 if the guidance no longer matches the repo
```

That is wired as a `pre-commit` hook (`buildanchor-agent-guidance`) that runs
only when a manifest changes, and `buildanchor-verify` as a `pre-push` hook. A
stale build instruction is worse than none, because nothing about it looks
stale to the agent reading it.

### It uses the conventions you already have

If your repository declares how it builds, that is the answer — BuildAnchor
finds it and says where it came from, rather than replacing it with a default.

| You declare | It answers |
| --- | --- |
| `justfile` with a `test` recipe | `just test` |
| `Taskfile.yml` with a `test` task | `task test` |
| `Makefile` with a `test` target | `make test` |
| `mise.toml` with a `[tasks.test]` | `mise run test` |
| `noxfile.py` with a `tests` session | `nox -s tests` |
| `tox.ini` | `tox` |
| `package.json` scripts, `[tool.pytest]`, … | the ecosystem's own answer |

A repository with `test: cargo nextest run` in its justfile gets `just test`, not
`cargo test`. A tool that overrides your convention is a tool that tells your team
they are doing it wrong, and that is not something anyone adopts. Where a runner
declares no target for the phase you asked about, the ecosystem default answers
instead.

### Building your own agent on top of it

If you are *writing* an agent rather than using one, MCP is a lot of machinery
for something running in your own process. The tool definitions and a dispatcher
are exported directly:

```python
import anthropic
from buildanchor import agent

client = anthropic.Anthropic()
messages = [{"role": "user", "content": "Run this project's tests."}]

response = client.messages.create(
    model="claude-opus-5",
    max_tokens=16000,
    thinking={"type": "adaptive"},
    tools=agent.tool_definitions(),          # ~700 tokens of schema
    messages=messages,
)

# Return every tool_result from one turn in a single user message.
results = [
    agent.tool_result_block(block.id, agent.run_tool(block.name, block.input, workspace="."))
    for block in response.content
    if block.type == "tool_use"
]
if results:
    messages += [{"role": "assistant", "content": response.content},
                 {"role": "user", "content": results}]
```

Whatever you build on, the tools are the same — pass the dialect it speaks:

| `format=` | For | Shape |
| --- | --- | --- |
| `anthropic` *(default)* | Messages API | `input_schema` |
| `openai` | OpenAI, **LiteLLM**, LangChain, OpenRouter, most gateways | `{"type": "function", ...}` |
| `gemini` | Google GenAI / Vertex | `function_declarations` |
| `bedrock` | AWS Converse | `toolSpec` |
| `mcp` | Model Context Protocol | `inputSchema` |

Each shape was checked against the library that consumes it rather than written
from memory: `google.genai.types.Tool` accepts the Gemini declarations, botocore
validates the Bedrock `ToolConfiguration` against its own service model, and
LiteLLM 1.100.0's completion path accepts the OpenAI schemas. None of those are
dependencies — BuildAnchor still installs with none.

`run_tool_call` reads all four call shapes (`function`, `tool_use`,
`functionCall`, `toolUse`), and `tool_result(call, result, format=...)` returns
what that API expects back:

```python
import litellm
from buildanchor import agent

response = litellm.completion(
    model="claude-opus-5",                      # or any model LiteLLM routes to
    messages=[{"role": "user", "content": "Run this project's tests."}],
    tools=agent.tool_definitions(format="openai"),
)

for call in response.choices[0].message.tool_calls or []:
    result = agent.run_tool_call(call, workspace=".")            # parses JSON-string args
    messages.append(agent.tool_result(call, result, format="openai"))
```

`run_tool_call` exists because of a detail that bites people: OpenAI-shaped
clients return `function.arguments` as a **JSON string**, not a dict, so passing
it straight to a tool raises deep inside. It accepts provider objects and plain
dicts alike, and returns malformed arguments as an error result rather than an
exception.

Bedrock's `toolResult` is the only one with an explicit `status`, so a failed
call is reported as a failure there rather than as text that happens to say
"error".

Node is the same shape:

```javascript
import { toolDefinitions, runTool, toolResultBlock } from "@tensilestream/buildanchor";

const response = await client.messages.create({
  model: "claude-opus-5",
  max_tokens: 16000,
  thinking: { type: "adaptive" },
  tools: await toolDefinitions(),
  messages: [{ role: "user", content: "Run this project's tests." }],
});
```

Three things worth knowing:

- **The schemas are the same ones the MCP server advertises**, so an agent you
  build and an agent using the MCP server see an identical surface. There is no
  second definition to drift.
- **`run_tool` returns errors instead of raising them.** A model told what went
  wrong can correct itself; an exception in your process just ends the loop.
- **Tools that execute project code are excluded by default.** Handing an agent
  a tool list should not be how it acquires the ability to run your test suite;
  pass `include_executing=True` when you mean it.

Cheaper still, when you already know the agent will need it — put the build
truth in your cached system prompt instead of spending a tool call:

```python
system = [{"type": "text", "text": agent.system_prompt_block("."),
           "cache_control": {"type": "ephemeral"}}]
```

### Try it on your own repositories

The fastest way to judge this is to point it at code you know and see whether it
is right.

```bash
git clone https://github.com/tensilestream/buildanchor && cd buildanchor
./scripts/try-it.sh ~/code/*          # or any paths you like
```

It reads only — nothing is executed or written — and for each repository it
prints the shape, the resolved command, where that command came from, and what
`verify` *would* run. Then judge it on one question: **is the command right?**
Compare it with what you actually type. If it is wrong, that is worth reporting,
and `buildanchor doctor <path>` will name the rule that produced it.

Add `--verify` when you want it to prove the commands run.

### It runs commands on your machine. Here is exactly which.

That is the fair question to ask a tool you have not read, and the answer should
be an inventory rather than a reassurance:

| | |
| --- | --- |
| Subprocess call sites in the whole package | **10** |
| That run *your* project's code | **2** — `verify`, and `validate-change --execute` |
| That use a shell | **0** |
| Runtime dependencies | **0** |
| Lines of Python to read if you want to check all of this | **~7,800** |

The other eight are read-only `git` — `ls-files`, `rev-parse`, `diff`,
`status`. No subcommand there can modify a repository. `inspect`, `context`,
`modules`, `cmd`, `doctor` and the MCP server execute nothing at all: static
mode is static, and neither operation that runs code is reachable over MCP or
HTTP, because a remote caller cannot consent to that.

None of this is on trust. `tests/test_execution_surface.py` parses the package,
finds every subprocess call, and fails if one appears that is not in the
inventory — or if any of them ever loses its timeout, builds a command from a
string, or asks for a shell. [`docs/EXECUTION.md`](docs/EXECUTION.md) is the full
account, including what gets written to your repository and how to remove it.

```bash
grep -rn "shell=True" src/buildanchor     # returns nothing
buildanchor verify --dry-run              # exactly what it would run, runs nothing
```

### Try it without installing anything, and undo it in one command

```bash
uvx buildanchor doctor            # zero install: what does it know about my repo?
buildanchor init --dry-run        # exactly what it would write, writes nothing
buildanchor verify --dry-run      # exactly what it would execute, runs nothing
buildanchor init --undo           # removes everything init wrote
```

`--undo` restores your `CLAUDE.md` byte-for-byte, keeping every word you wrote,
and deletes `.buildanchor.json`. It leaves `.buildanchor/verified.json` alone:
that is evidence produced by `verify`, not something `init` created.

### Keeping it true

Setting this up once is not the hard part; staying correct is. Two hooks and a
workflow do that without anyone having to remember:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/tensilestream/buildanchor
    rev: v1.12.2
    hooks:
      - id: buildanchor-agent-guidance   # pre-commit, only when a manifest changes
      - id: buildanchor-verify           # pre-push, proves the commands still run
```

For CI, copy [`.github/workflows/buildanchor.yml`](.github/workflows/buildanchor.yml):
it fails when `CLAUDE.md` / `AGENTS.md` stop describing the repository, and when a
command the repository advertises does not run.

```bash
buildanchor init --check    # exit 1 if the guidance is stale. Changes nothing.
```

### When something is missing

```bash
$ buildanchor doctor web-ui
web-ui: 'package.json' declares no 'test' or 'build' script, so there is no entry point to report
  markers found: package.json
  -> Add a "test" or "build" script to package.json, or
  -> list this package in the workspace declaration at the repository root.
```

`buildanchor doctor` answers the question people actually ask — *why isn't my
project showing up?* — by naming the rule that applied, the evidence it saw, and
what would have to change. With no argument it diagnoses the whole repository:
shape, modules, which commands are unproven, and which ones are broken.

### Works the same on one project as on forty

The benchmark measures both shapes, because a tool that only pays off on a
40-package monorepo does not get installed by the people who would benefit from
it on an ordinary Tuesday.

| Fixture | Shape | Classified | Emitted command runs |
| --- | --- | --- | --- |
| Python project at the root | single-project | correct | 1 / 1 |
| Node project at the root | single-project | correct | 1 / 1 |
| Go project at the root | single-project | correct | toolchain not installed here |
| Rust project at the root | single-project | correct | toolchain not installed here |
| Root project + SDK subdirectory | root-plus-satellites | correct | 2 / 2 |
| 3 Python + 2 Node siblings, no workspace file | monorepo | correct | 5 / 5 |

The single-project rows matter as much as the monorepo one: BuildAnchor says
*"single-project repository: one test command, no scoping needed"* and stops,
rather than advertising `--scope ui` at a repository that has no scopes. Advice
that does not apply is noise, and noise makes an agent discount the rest of the
report.

Full method and raw numbers: [`benchmarks/README.md`](benchmarks/README.md).
Fixtures are generated offline and deterministically, so the figures are
reproducible on your machine and falsifiable if they are wrong.

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

### Verified commands, not guessed ones

Static analysis can show that a command was *declared*. It cannot show that the
command runs — the tests may fail to import, the runner may not be installed,
the suite may collect nothing. `buildanchor verify` closes that gap by climbing
a ladder, in each module's own working directory, and recording how far it got:

| Status | Meaning | Executes |
| --- | --- | --- |
| `declared` | Found in a manifest. A candidate, not a fact. | nothing |
| `resolvable` | The entrypoint it names exists on disk or `PATH`. | nothing |
| `collects` | A discovery-only probe exited 0 in the module's own directory. | a cheap probe |
| `passes` | The full command exited 0. | the suite |

```bash
buildanchor verify                      # climb to 'collects' — seconds, no tests run
buildanchor verify --verify-level passes --scope web-ui
buildanchor verify --jobs 8             # probe modules concurrently
```

A probe loads the test files and runs no test body:

| Runner | Probe |
| --- | --- |
| pytest | `--collect-only -q` |
| unittest | `-k '(?!)'` — imports every test module, matches no test |
| jest / vitest / mocha / playwright | `--listTests` / `list` / `--dry-run` / `--list` |
| `node --test` | `--test-name-pattern '(?!)'` |
| Go | `go test -run '^$' ./...` |
| Rust | `cargo test --no-run` |
| Maven / Gradle | `-DskipTests test-compile` / `testClasses` |
| .NET | `dotnet test --list-tests` |

A runner absent from that table reports `resolvable (no probe available)` — never
a guess. Modules are probed concurrently, so a monorepo pays for its slowest
module rather than the sum of all of them.

The result is cached in `.buildanchor/verified.json`, keyed by a digest of the
files that determine the toolchain. Every later `inspect`, `modules` and `cmd`
call reports the proven status **without re-running anything**, until a manifest
changes and the result honestly reverts to `declared`.

**Commit that file.** It records which commands are proven for which manifest
digest — a fact about the repository, not about the machine that ran the probe.
It holds no absolute paths and no hostnames, and a re-run that changes nothing
produces no diff, so it survives code review. Commit it and a fresh clone already
knows; let CI write it and the evidence from the suite it runs on every push stops
being thrown away. It records nothing about the machine that ran the probe, so the same result written by CI and by a developer is byte-identical.

Commands verified at `passes` also carry their observed duration, so an agent
choosing between a probe and the real suite knows whether that costs four seconds
or eleven minutes instead of guessing.

Verification executes project-defined code, so it is opt-in and local-only: it
is not an MCP tool and not an HTTP endpoint.

### Commands come with the directory they run in

A command without a working directory is ambiguous the moment a repository holds
more than one project — which is the only situation modules exist for. Every
module therefore reports both:

```json
{
  "name": "service-a",
  "path": "service-a",
  "working_directory": "service-a",
  "test_command": "uv run pytest",
  "test_command_shell": "cd service-a && uv run pytest",
  "test_command_status": "collects"
}
```

`test_command` is relative to `working_directory`; `test_command_shell` is the
same thing pasteable from the repository root — POSIX `sh` syntax, so a caller
that does not know the target shell should use `working_directory` plus the bare
command instead. Where a project declares its own
environment — a `uv.lock`, a `poetry.lock`, a `.venv/`, a `pnpm-lock.yaml` —
BuildAnchor uses it, because `python -m pytest <path>` run from the root cannot
import the package under test.

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

Two audiences, and the honesty is worth more than the breadth:

- **People building or running AI coding agents.** An agent entering an
  unfamiliar repository pays several turns to learn what BuildAnchor answers in
  one call, and gets a command that has been proven to run rather than guessed.
- **Platform teams with more repositories than they can hold in their head.**
  One tool and one hook keep forty repositories' build instructions true, which
  nobody can do by hand.

If you have a single repository you know well, write the command in your
`CLAUDE.md` yourself. It will be exact where a general heuristic can only be
close, and a `grep` is faster than any tool. BuildAnchor earns its place when the
repository is unfamiliar, when there are many of them, or when the instruction
needs to *stay* true — which is what `buildanchor init --check` does and a
hand-written file cannot.

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

- `get_build_truth` — build system, runtimes, compatibility constraints, validation
  commands. `detail: "summary"` (default, <= 400 tokens), `"full"` (modules,
  dependencies, evidence digests), or `"changed"` (git baseline impact).
- `get_test_command` — the command for a phase, the `working_directory` it must run
  in, and its `command_status` on the verification ladder. Supports `scope` and
  `changed` for monorepo targeting.
- `find_package` — installed and declared versions, import patterns, usage.

**Why only three.** Every advertised tool schema sits in the agent's context on
every turn, whether or not BuildAnchor is called. The full registry costs about
2,300 tokens per turn — more than a BuildAnchor call typically saves — and its
overlapping entries cost the agent a turn whenever it picks the wrong door.
These three cover the same surface for roughly 700.

The extended `build.*` tools (`build.inspect`, `build.context`, `build.preflight`,
`build.plan`, `build.change_impact`, `build.validate_change`,
`build.repair_guidance`, `build.explain_dependency`, `build.compatibility`,
`build.modules`, `build.cmd`, `build.llm_prompt`, `build.token_estimate`,
`build.find_package`) remain callable by name and are unchanged. To advertise the
full list in `tools/list` as before, set `BUILDANCHOR_MCP_TOOLS=full`.

Command verification (`buildanchor verify`) is deliberately absent from MCP: it
executes project-defined code, so it stays an explicit local action. Agents still
read its results through `command_status`.

`build.validate_change` is static by default. Pass `execute: true` and an optional `timeout` to run detected validation probes with `shell=False`, bounded output, and per-command timeouts. BuildAnchor reports each probe as `passed`, `failed`, `timed_out`, or `unavailable`; it never turns a missing baseline or missing tool into a pass.

For automation, `validate-change` exits `0` for `valid`, `1` for `invalid`, `2` for
`inconclusive`, and `3` for `blocked` by policy. Any command exits `4` when
BuildAnchor itself refuses the request — an unresolvable workspace, a path outside
the allowed root, an unsupported `--schema`. `4` is distinct from `3` on purpose:
`3` is a judgement about your repository, `4` means no judgement was made at all.

### Report schema

The report declares `schema_version`. The current schema is `v2`; `v1` is
deprecated, supported through the 1.3 series, and removed at 2.0.

`v1` changed meaning in 1.2.0 before this was versioned properly: `test_command`
had been relative to the repository root and became relative to the module's
`working_directory`. Pass `--schema v1` (or `schema: "v1"` to `get_build_truth`
with `detail: "full"`) to get v1's shape and v1's contract — a command runnable
from the repository root. Asking for an unsupported schema is an error, never a
silent substitution.

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
uv run python benchmarks/credibility_benchmark.py --format text
```

The benchmarks report local latency for a deterministic representative fixture. They are
engineering baselines, not claims of guaranteed time saved for every repository or team.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for development, testing, adapter, and release guidelines.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
