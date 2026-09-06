# Changelog

All notable changes to BuildAnchor are documented here.

## [1.12.1] - 2026-09-06

CI was failing. Four causes, three of them real bugs rather than CI problems.

### Fixed

- **Repository-relative paths are always forward-slashed.** `str(Path("sdk") / "node")` is `sdk\node` on Windows, and those strings are data: they appear in reports, in evidence entries, and in the **keys of the committed verification cache**. A Windows contributor and a Linux one would have produced different reports for the same repository and a cache that churns on every platform switch. Git speaks forward slashes; so does this now, through one `_relative()` helper used at all fourteen sites.
- **`doctor` stopped refusing paths outside the workspace.** Introduced by the change above: the refusal relied on `relative_to` raising, and the new helper deliberately does not raise. Containment is now checked explicitly, because a guard that depends on an exception someone else may remove is not a guard. Caught by an existing test.
- **The workflow BuildAnchor ships as a template was running against BuildAnchor itself**, installing the *published* release from PyPI and checking it against flags that only exist in the checkout — which is why `init --check` reported `unrecognized arguments: --check` from a 1.1.6 CLI. The template moved to `docs/integration/buildanchor.yml`, where GitHub does not execute it, and CI now checks BuildAnchor with the code under review.
- **A lint failure**: an en dash in the compatibility generator that reads as a hyphen.
- **`tests/test_report_correctness.py` only passed when another test module had already imported `unittest.mock`.** Every test file now passes standalone, which is what a sharded or filtered CI run does.

### Notes

- The Windows job stays non-blocking. The path-separator fix addresses the most likely cause, but that was diagnosed by reading the code rather than the failing log, so it is not yet a claim that Windows is green.

## [1.12.0] - 2026-09-06

### Added

- **A compatibility chart in the README**, covering ecosystems and how each command is resolved and verified, the six task runners a repository can declare, the five agent dialects and what each was validated against, every interface with its operation count, and platform support. It is **generated from the tables the tool actually uses** by `scripts/generate_compatibility.py`, and CI fails when it drifts — a support claim that is not true is worse than no chart. Verified by adding a probe and a task runner and confirming the check caught both.
- **Benchmark detail behind expandable sections**: the twelve repositories in the head-to-head with what each declares and how each strategy scored, and the method behind every offline metric. Both include what was tried and discarded, so a reader can see where the numbers could have been wrong.

## [1.11.0] - 2026-09-06

Five dialects, one tool surface.

### Added

- **`tool_definitions(format=...)` supports `anthropic`, `openai`, `gemini`, `bedrock` and `mcp`.** The same three tools with the same descriptions and schemas, shaped for whichever client consumes them; a test asserts every dialect describes an identical surface. `LITELLM_FORMAT`, `LANGCHAIN_FORMAT` and `OPENROUTER_FORMAT` are named aliases for `openai`, because those are what people search for.
- **`run_tool_call()` reads every provider's call shape** — OpenAI's `function`, Anthropic's `tool_use`, Gemini's `functionCall` and Bedrock's `toolUse`, in both object and wire-dict form, and in both camelCase and snake_case.
- **`tool_result(call, result, format=...)`** returns what each API expects back: a `role: "tool"` message, an Anthropic `tool_result` block, a Gemini `functionResponse`, or a Converse `toolResult`. Bedrock's is the only shape with an explicit `status`, so a failed call is reported as a failure rather than as text that happens to say "error". `tool_message` and `tool_result_block` remain as the named forms.

### Notes

- Gemini declarations use `parameters_json_schema`, not `parameters`. The latter expects Gemini's OpenAPI 3.0 subset and silently ignores keywords outside it, which loses argument descriptions with no error.
- Every shape was checked against the library that consumes it: `google.genai.types.Tool` and `FunctionResponse` accepted the Gemini output, botocore validated the Bedrock `ToolConfiguration` and `ToolResultBlock` against its own service model, and LiteLLM 1.100.0's completion path accepted the OpenAI schemas. None of those libraries are dependencies — BuildAnchor still installs with zero.

## [1.10.0] - 2026-09-06

### Added

- **`tool_definitions(format="openai")`** emits the function-calling shape used by LiteLLM, OpenAI, LangChain and most gateways, alongside the existing Messages API shape. Same tools, same descriptions, same schemas — a test asserts the two dialects describe an identical surface.
- **`run_tool_call()` and `tool_message()`** handle the two differences that are easy to miss: an OpenAI-shaped client returns `function.arguments` as a **JSON string** rather than a dict, and results go back as a `role: "tool"` message rather than a content block. Malformed arguments come back as an error result, not an exception.
- Verified against LiteLLM 1.100.0: its completion path accepts the schemas, and the `ChatCompletionMessageToolCall` object it returns dispatches through `run_tool_call` unchanged.

## [1.9.0] - 2026-09-06

The SDK is what lets someone build an agent on this, rather than only wire it
into an agent that already exists. It was missing the piece that makes that
possible.

### Added

- **`buildanchor.agent`** — tool definitions and a dispatcher for building an agent directly, without speaking MCP over a pipe to your own process. `tool_definitions()` returns the schemas in Messages API form (`input_schema`, not MCP's `inputSchema`), `run_tool()` executes one call, `tool_result_block()` wraps the result, and `system_prompt_block()` returns the build truth for a cached system prompt when you already know the agent will need it.
- **The same helpers in the Node SDK** — `toolDefinitions()`, `runTool()`, `toolResultBlock()`.
- **`buildanchor mcp --list-tools` and `--call-tool`** expose the tool surface without the stdio protocol, which is how the Node and Java SDKs reach it.

### Notes

- The schemas come from the same place the MCP server advertises them, asserted by a test — an agent built on the SDK and an agent using the MCP server see an identical surface, with no second definition to drift.
- `run_tool` returns errors rather than raising: a model told what went wrong can correct itself, while an exception in the host process just ends the loop.
- Tools that execute project-defined code are excluded from the default set. Handing an agent a tool list should not be how it acquires the ability to run your test suite; `include_executing=True` is the explicit opt-in.
- No new dependencies. The helpers return plain dicts and import no model provider's SDK, so they work with whichever one you use.

## [1.8.2] - 2026-09-06

### Added

- **`scripts/try-it.sh`** evaluates BuildAnchor against your own repositories in about a minute. It reads only — nothing is executed or written — and for each repository prints the shape, the resolved command, where that command came from, and exactly what `verify` would run. `--verify` proves the commands.
- **Conformance failures now emit the code to add.** A missing SDK operation previously reported "the Java SDK is missing operations"; it now prints a paste-ready stub per language. Three SDKs are only a liability if keeping them in step is manual work, and telling a contributor what to paste is cheaper than dropping a client somebody depends on.

## [1.8.1] - 2026-09-06

Trust, for a tool that runs commands on your machine.

### Added

- **`docs/EXECUTION.md`** is a complete inventory of what BuildAnchor executes and when: ten subprocess call sites, eight of them read-only `git`, two that run project code and only from a command you typed. It also lists everything written to your repository and how to remove it.
- **`tests/test_execution_surface.py`** enforces that inventory. It parses the package with `ast` and fails when a subprocess call appears that is not listed, when one loses its timeout, builds its command from a string, or asks for a shell — and when an operation that executes code becomes reachable over MCP or HTTP. A guarantee nobody checks is a paragraph; this one is a test.
- The engine is asserted to be network-free. `sdk.py` may reach an HTTP endpoint *you* configure; nothing else in the package may import a network library.
- **PyPI releases now publish signed provenance** (`attestations: true`), so an artifact can be verified as built from this repository by this workflow.

### Fixed

- An earlier draft of `docs/EXECUTION.md` claimed "there is no network call in the package". The SDK has one, for the endpoint a user configures. Corrected, and now covered by a test — a document about trustworthiness is the worst possible place for an unchecked claim.

## [1.8.0] - 2026-09-06

Evidence. Every benchmark here compared BuildAnchor to an older BuildAnchor,
which is a changelog rather than a reason to adopt anything.

### Added

- **`benchmarks/head_to_head.py`** clones twelve real, unmodified public repositories and asks what each declares as its way to run tests, against the thing people do instead: guess the ecosystem default. Guessing gets 7 of 12; BuildAnchor gets 12 of 12. Five of the twelve declare an entry point that is not their default — Flask's `[tool.tox]`, `test` targets in the Makefiles of requests, pydantic and cobra, a `test` recipe in `just`'s justfile — and nothing about those repositories announces it. Ground truth is the declaration itself, cited by file, so any row can be confirmed by opening it. Where a project declares nothing its default is correct and is scored that way; those seven rows are where the tool earns nothing, counted honestly.

### Fixed

- **tox declared in `pyproject.toml` was not detected.** tox 4 reads `[tool.tox]` from `pyproject.toml`, which is how Flask and several other large projects configure it. Only `tox.ini` was recognised, so those repositories got the ecosystem default instead of their own runner. `setup.cfg`'s `[tox:tox]` is recognised too. Found by the first real repository the new benchmark was pointed at.

### Notes

- The head-to-head benchmark is deliberately **not** in CI. It needs the network and depends on twelve repositories other people are free to change; a per-push gate that fails because Flask reorganised its Makefile teaches everyone to ignore CI. Run it before a release and read the diff.
- Two approaches were discarded reaching this, both recorded in `benchmarks/README.md`: scraping each project's CI workflow for its test step proved unreliable on half the corpus, and an early declaration reader matched `test-mypy` as a `test` target, which would have credited projects that declare no such entry point.

## [1.7.1] - 2026-09-06

One fact, one place. Nearly every defect found late in this project was the same
shape — the same knowledge written down twice, drifting apart where nobody was
looking. This closes the remaining instances and makes new ones fail the build.

### Fixed

- **Two phase-alias tables disagreed.** `command_resolution` and `conventions` each kept their own list of what a phase might be called, so a `justfile` target named `unit` was found and an npm script named `unit` was not — and vice versa for `test:all`. Both now read one table.
- **`build.gradle` was a project marker for `doctor` and for repository-shape detection, but not for the evidence invariant.** The guarantee that every marker resolves to a module or a stated reason had a Gradle-shaped hole: a Gradle project that was not promoted produced no explanation at all. Three marker tables are now one.

### Changed

- **`release`, `publish`, `deploy`, `push`, `upload` and `promote` can never be reached by a phase lookup.** A `build` alias list containing `release` meant asking to build could run a task that publishes an artifact. That is not a mistake a tool should be able to make, and it is now asserted rather than avoided by accident.

### Added

- **`build_truth/core/vocabulary.py`** owns the project markers and phase aliases that five modules previously each defined for themselves.
- **`tests/test_single_source_of_truth.py`** fails when a second definition appears — of the markers, the phase aliases, the ignored-directory list, or `is_monorepo` — and when the injected block contradicts the command tool, or `doctor` contradicts the report. Each guard was verified by reintroducing the exact defect that shipped and confirming it fails.

## [1.7.0] - 2026-09-06

SDK parity. Four client surfaces, one product.

### Fixed

- **The SDKs had drifted apart.** The Java client was missing six operations the Node and Python clients had (`llmPrompt`, `tokenEstimate`, `compatibility`, `findPackage`, `modules`, `resolveCommand`); `verify` existed only in Python; `doctor` existed nowhere but the CLI. A user who picked the wrong language found a smaller product with no way to know that is what happened. All three now implement the same sixteen operations.
- **The same call returned different shapes on different transports.** `modules` produced a bare array from the CLI and an envelope from HTTP, MCP and the Python SDK — so the Node SDK saw one contract in local mode and another over the wire, for the same repository. Every surface now returns the same envelope, and `is_monorepo` comes from the report's repository shape rather than being re-derived three times with three slightly different rules.
- **The async Python client had fallen behind the sync one.** Caught by the new conformance suite on its first run.
- **`index.d.ts` had fallen behind `index.js`.** Also caught by the conformance suite.

### Added

- **`buildanchor/operations.py`** defines the operation set once — canonical name, camelCase name, HTTP route, and whether an operation is local-only — and `tests/test_sdk_conformance.py` fails when any SDK does not match it. The SDK checks are source-level on purpose: the Node and Java clients cannot be imported from Python, and a check that only runs where a toolchain happens to be installed is a check that silently stops running.
- **`doctor` is available everywhere**: `/v1/doctor` over HTTP, `build.doctor` over MCP, and `diagnose()` in all three SDKs.
- **`verifyCommands` in the Node and Java SDKs**, local-mode only. Every SDK refuses it when constructed with an endpoint, with an error that says why: it executes project-defined code, which a remote caller cannot consent to. That refusal is asserted per-SDK.

## [1.6.0] - 2026-09-06

Adoption. The barrier to a new tool is rarely quality — it is that finding out
costs more than the problem hurts, and that trying it means letting a stranger
edit your repository.

### Added

- **Task runners a repository already declares now answer first.** `justfile`, `Taskfile.yml`, `Makefile`, `mise.toml`, `noxfile.py` and `tox.ini` take precedence over the ecosystem default. A repository whose justfile says `test: cargo nextest run` was previously told the answer was `cargo test` — the tool overriding a team's own convention, which is the fastest way to not be adopted. Where a runner declares no target for the requested phase, the ecosystem default still answers. `Makefile` support moved from a last-resort fallback to this precedence.
- **`buildanchor init --dry-run`** prints exactly what would be written and writes nothing, and **`buildanchor init --undo`** removes everything `init` wrote, restoring the file byte-for-byte and keeping every word the user wrote. It leaves `.buildanchor/verified.json` alone, since that is evidence produced by `verify`.
- **`buildanchor verify --dry-run`** prints the exact argument vectors that would be executed, and executes none of them. Verification runs project-defined code, so "what will this run on my machine?" should be answerable without finding out.
- **`buildanchor doctor` leads with the resolved commands** and the task runners the repository declares. It is the command people run first, and it was reporting everything except the thing the tool is for.

### Fixed

- `init --dry-run` wrote `.buildanchor.json` before reaching the dry-run check. A dry run that writes a file is worse than no dry run at all.
- A case-insensitive filesystem reported `justfile` and `Justfile` as two separate runners.

## [1.5.1] - 2026-09-06

Found by using the tool as a stranger would: a clean install, hostile inputs, and
a first look at `--help`.

### Fixed

- **An unparseable manifest was reported as a clean bill of health.** A `package.json` with a syntax error produced `status: "valid"`, the Node ecosystem detected, zero modules, and no complaint — a confident empty answer, which is the worst failure mode available. Unreadable manifests are now named in `limitations`, the status drops to `inconclusive`, and `doctor` reports them as errors.
- **Exit codes disagreed with the payload they accompanied.** `buildanchor inspect` on a directory with no build system printed `"status": "inconclusive"` and exited `0`. `inspect` and `change-impact` now map status to the documented contract, like `validate-change` always did.
- **The committed verification record churned between local and CI runs.** Entries carried a `source` field that flipped between `"local"` and `"ci"`, rewriting the file on every run — and churn is precisely how a committed file ends up gitignored again. The manifest digest is what makes a result true; who ran the probe is not, so the field is gone and the file is byte-identical wherever it is written.
- `Any` was used in a type annotation in `cli.py` without being imported. Harmless at runtime under `from __future__ import annotations`, and a failure for any caller using `typing.get_type_hints()`.

### Changed

- **`--help` leads with the commands.** It opened with a thirty-flag usage block and an unordered list of twenty-one commands, with nothing to say where to begin. It now opens with what the tool does and groups the commands by purpose, starting with the four that matter.
- **Ruff is configured and enforced in CI**, with every rule that is ignored carrying the reason it is ignored. The lint pass found the undefined name above.
- **Two v0.3-era planning documents moved out of the repository root** to `docs/history/`, where a note says they are superseded. A contributor who opens a stale plan in the root has no way to know it is stale. The field report that prompted this work moved to `docs/field-report-2026-09.md`.
- **The audience claim is narrowed to the two it can support** — people building or running coding agents, and platform teams with more repositories than they can hold in their head — and now says plainly that a single repository you know well is better served by writing the command yourself.

## [1.5.0] - 2026-09-06

Honesty of the model. Four places where the tool knew less than it appeared to.

### Changed

- **`category` now reports how much evidence stands behind it.** Three signals of different kinds are weighed — what the project is called, what it depends on, and what files it contains (an `index.html`, a `vite.config.ts`, a `Dockerfile`, `.tsx` sources). Two or more agreeing gives `category_confidence: "high"`; a single available signal gives `"low"`; nothing pointing anywhere still gives `unknown`. Refusing to answer below two signals was tried first and was worse: Maven and Gradle modules expose neither dependency nor file evidence, so every one of them became `unknown` and `--scope backend` matched nothing. Reporting the support is more useful than withholding the answer.
- **`Fact` carries a real `module` field.** Per-module facts were encoded as `runtime.python@service-a`, which turned a structural relationship into something every caller had to parse. Keys are unqualified again and the module is a field; schema `v1` drops it and keeps the first fact per key, as v1 always did.

### Added

- **Every compatibility rule carries the date it was last confirmed**, and a test fails once any rule passes an 18-month review horizon. These rules encode facts about the world outside the repository — which namespace Spring Boot 3 uses, which Rust edition is current — and nothing in a codebase notices when one stops being true. A report whose rules are past the horizon now says so in its limitations. The failing test is the point: it forces a person to re-confirm rather than inherit.
- **A differential test proving the two TOML parsers agree.** `tomllib` arrived in Python 3.11 and this project supports 3.10, so there is a fallback. Collapsing to one parser would have meant either dropping 3.10 or taking a runtime dependency, and zero runtime dependencies is what makes this tool auditable and offline. The fallback stays; a seven-sample corpus now asserts both paths produce identical output, so the pair cannot diverge quietly. Two parsers proven to agree are a different thing from two parsers that merely have not been caught yet.

## [1.4.0] - 2026-09-06

Surface. Fewer things, each of which earns its place.

### Added

- **`buildanchor doctor`** answers the question people actually ask — *why isn't my project showing up?* Given a path it names the rule that applied, the evidence it saw, and what would have to change: no project marker, a `package.json` with no `test` or `build` script, a Python project too deep for the discovery rule, a directory git ignores, malformed JSON. With no argument it diagnoses the repository — shape, modules, which commands are unproven, which are broken. It reads the same report every other command reads, so a diagnosis cannot disagree with the thing it diagnoses.
- **A ready-to-copy GitHub Actions workflow** (`.github/workflows/buildanchor.yml`) that fails when the agent guidance stops describing the repository and when an advertised command does not run, and publishes the verification record as an artifact.

### Changed

- **The 14 legacy `build.*` MCP tools are deprecated.** They still dispatch unchanged, and every response now carries a `_deprecation` field naming the core tool that replaces it. They are removed at 2.0. Listing with `BUILDANCHOR_MCP_TOOLS=full` marks them in their descriptions.
- **One benchmark harness.** `benchmark_cli.py` and `run_benchmarks.py` are removed; both measured latency, neither measured whether anything worked, and published numbers came from a third place. Their per-ecosystem fixture corpus is now a section of `credibility_benchmark.py`, which also gained a threshold asserting that every supported ecosystem still resolves a command. CI and the release workflow run the one harness.

## [1.3.1] - 2026-09-06

Cost. Asking should never be expensive enough to make an agent guess instead.

### Changed

- **File enumeration asks git.** In a git repository the file list comes from `git ls-files --cached --others --exclude-standard`: tracked files plus untracked ones that are not ignored. Gitignore semantics — negations, nested files, `**` — are delegated to the only implementation guaranteed to agree with the repository, and a large ignored tree is skipped without ever being walked. A hardcoded list of eleven directory names cannot know about a project's own `.terraform/` or `coverage/`. On a 12,700-file repository with 4,500 ignored files, inspection went from 170 ms to 89 ms and stopped analysing generated content; on a repository with nothing extra to skip the two paths agree exactly. Outside git the pruning walk is unchanged.
- **Cache revalidation happens at most once per request.** Establishing that the cached report is still valid costs a walk, and one request could ask several times — the transport, the command resolver, the usage index. Within a 250 ms window the answer cannot meaningfully have changed, so it is computed once. Set `BUILDANCHOR_REVALIDATE_MS=0` to revalidate on every call; `freshness: "refresh"` over MCP still forces a fresh read.
- **`find_package` builds its usage index once per workspace digest.** It previously re-read the source tree on every lookup, and did so via `rglob`, traversing dependency directories before discarding them — 92% of the call's cost. Warm lookups went from 683 ms to under 1 ms.

### Performance

Measured on a 4,800-file repository, long-lived MCP server:

| | Before P2 | After |
| --- | --- | --- |
| Tool call, back to back within a turn | 52 ms | **0.1 ms** |
| Tool call after a pause | 52 ms | **56 ms** |
| `find_package` with usage scan, warm | 683 ms | **&lt;1 ms** |

### Not done, deliberately

- **Persisting the report across processes** was in the plan and is not worth it. A cold inspection costs 91 ms: 26 ms walk, 29 ms digest, 12 ms git state, ~24 ms analysis. Only the analysis could be restored from disk, git state must be recomputed anyway because `HEAD` is not part of the digest, and the result would be a second cache with its own staleness risk for a saving under a third of one call. The walk is the floor, so the effort went to making the walk smaller instead.

## [1.3.0] - 2026-09-06

Verification depth. Discovery and manifest parsing are table stakes; this is the
part that has no hand-rolled equivalent.

### Added

- **Probes for the runners that previously had none.** `node --test` (Node's built-in runner, via a name filter that cannot match), Python `unittest` (via `-k`), and .NET (`dotnet test --list-tests`). Across the fixture corpus, 89% of discovered modules now reach a real verdict rather than `resolvable (no probe available)`, against a target of 80%.
- **Modules are verified concurrently** (`--jobs`, defaulting to the machine's parallelism capped at 8). A monorepo pays for its slowest module rather than the sum; the run reports how much wall clock that saved. Result order is independent of completion order.
- **Verified commands carry their observed duration.** `full_run_duration_ms` is recorded at the `passes` rung, cached, and surfaced as `test_command_duration_ms` on modules and `command_duration_ms` on `resolve_command`, so an agent choosing between a probe and the real suite has a cost signal instead of a guess.

### Changed

- **`.buildanchor/verified.json` is now meant to be committed** and is no longer gitignored. It holds no absolute paths and no hostnames, entries record whether they came from `ci` or `local`, and a re-run that changes nothing reuses the existing timestamp — so a no-op produces no diff and the file survives code review. A fresh clone then knows which commands are proven without executing anything, and CI stops discarding the evidence it generates on every push.

### Fixed

- **The root project's own test command was never verified** in a repository whose root is a project. `module_details` holds sub-projects, so in a root-plus-satellites layout `verify` checked the SDK subdirectory and skipped the command the repository actually runs — found by running it against BuildAnchor itself.
- **Dead cache entries no longer accumulate.** An unscoped run prunes entries naming modules that no longer exist, so the committed file only contains statements still about this repository. A scoped run prunes nothing, since it never looked at the other modules.
- The never-match pattern used by the discovery probes was `$^`, which matches the empty string in both the JavaScript and Python engines. It is now `(?!)`, a negative lookahead that cannot match anything. Caught by a test asserting the property rather than the implementation.

## [1.2.0] - 2026-09-06

### Changed

- **The report declares `schema_version: "v2"`.** This release changed what `test_command` means — from relative to the repository root (`python -m pytest lib-a`) to relative to the module's `working_directory` (`uv run pytest`) — and the schema moves with it. Left unversioned, that would have been the same field, same type, different meaning, which is the one kind of change no test on our side catches. Pass `--schema v1` (or `schema: "v1"` to `get_build_truth` with `detail: "full"`) for v1's shape and v1's contract: `test_command` maps to the shell form, which is runnable from the repository root as v1 always promised. `v1` is supported through the 1.3 series and removed at 2.0. An unsupported schema is an error, never a silent substitution.

### Added

- **CI enforces the published claims.** `benchmarks/credibility_benchmark.py --assert-thresholds` fails the build when command correctness, discovery completeness, module count, malformed-coordinate count, or repository-shape classification regress. Previously every headline number in the README was defended only by the README. Verified by deliberately reintroducing the Node discovery bug: the gate failed with the three specific regressions named.
- **`scripts/bump_version.py --check`** reports version divergence across `pyproject.toml`, both SDKs, the Homebrew formula, and the MCP fallback constant, and exits 1. Wired into CI. The Node SDK, Java SDK, and formula had been left at 1.1.6 while the package said 1.2.0; all artifacts are now synchronised, and the bump script covers the constant it previously missed.
- **A Windows CI job**, non-blocking for now. The toolchain resolver handles `.venv/Scripts/python.exe`, so support is clearly intended and had never once been executed. It reports the truth without blocking; when it is green it becomes required, and if it cannot be made green Windows is documented as unsupported.

### Documented

- Exit code `4` (BuildAnchor refused the request) is distinct from `3` (blocked by policy) and was previously undocumented.
- `command_shell` is POSIX `sh` syntax. `working_directory` plus the bare command is the portable pair, and the MCP tool description now says so where an agent will read it.

### Added

- **Command verification ladder**: `buildanchor verify` climbs `declared` -> `resolvable` -> `collects` -> `passes` for each module's test command, in that module's own working directory, and records how far it got. `collects` runs a discovery-only probe (`pytest --collect-only`, `cargo test --no-run`, `jest --listTests`, `vitest list`, `mocha --dry-run`, `go test -run '^$'`, `mvn -DskipTests test-compile`, `gradle testClasses`) — seconds, no test bodies executed. A toolchain with no honest probe is reported as skipped rather than guessed.
- **Verification cache**: results are stored in `.buildanchor/verified.json`, keyed by a digest of the manifest and lock files that determine the toolchain. `inspect`, `modules` and `cmd` report the proven status without re-running anything, and revert to `declared` when a manifest changes.
- **`working_directory` on every module** in `module_details`, `ModuleInfo` and `resolve_command`, with `test_command_shell` / `build_command_shell` variants that are safe to paste from the repository root.
- **`command_status` on `resolve_command`** and `test_command_status` on every module.
- `benchmarks/credibility_benchmark.py`: executes the commands BuildAnchor emits and reports how many exit 0, alongside discovery completeness, language correctness, latency at scale and MCP context cost — each measured against a released baseline so the figures are a reproducible comparison rather than an assertion. Fixtures are generated offline, with real per-project virtualenvs, and reproduce the polyglot shape reported from production.
- **Repository shape classification** (`repository` on the report): `single-project`, `root-plus-satellites`, `monorepo` or `unknown`, each with the reason it was chosen. A root project with one SDK subdirectory was previously described as a monorepo and offered `--scope ui`; scoping advice is now given only where there is a scoping decision to make.
- `buildanchor init --check` reports whether the agent guidance block still matches the repository and exits 1 if it does not, changing nothing. Wired as the `buildanchor-agent-guidance` pre-commit hook, alongside `buildanchor-verify` at pre-push.
- `buildanchor init --rules-file <path>` writes the block to one chosen file.
- `buildanchor init` writes a working, shape-aware block into the repository's agent guidance file — the command, the directory it runs in, how far it is proven, and a per-module table for monorepos. It refreshes that block in place on re-run rather than appending a second, divergent copy, and it writes to **every** agent guidance file the repository has (`CLAUDE.md`, `AGENTS.md`, `AGENT.md`, `GEMINI.md`, plus any file already carrying the block) rather than to one — updating a single file left the others holding an older answer, which an agent has no way to notice. `AGENTS.md` is created when a repository has none. `--verify` runs verification first so the written commands carry a proven status.
- `language_details` on the report: per-language file counts, markers and sample paths, so any language claim can be checked the way a fact can.
- `verify_commands()` on the Python SDK. Verification is local-only by design: it executes project-defined code and is exposed neither as an MCP tool nor as an HTTP endpoint.

### Changed

- **Module commands are now relative to their working directory.** A Python module reports `uv run pytest` with `working_directory: "service-a"` instead of `python -m pytest service-a` implicitly from the root. The previous form did not run: each project owns its interpreter, dependencies and pytest rootdir, so collection failed to import the package under test. Callers that ran `test_command` from the repository root should use `test_command_shell` or honour `working_directory`.
- **Project environments are preferred over the ambient one.** A `uv.lock` next to `pyproject.toml` yields `uv run pytest`, a `.venv/` yields that interpreter, a `pnpm-lock.yaml` yields `pnpm test`. Where no environment is declared, the command falls back to the ambient interpreter and says so in `test_command_source`.
- **Node modules run inside their package directory** rather than through `npm --prefix`, which left the working directory at the root and broke scripts resolving relative paths.
- **MCP `tools/list` advertises three tools by default** — `get_build_truth`, `get_test_command`, `find_package` — reducing per-turn schema cost from roughly 2,300 tokens to roughly 700. Every advertised schema is resident in the agent's context on every turn, so the full registry could cost more than a BuildAnchor call saved. `get_build_truth` gains `detail: summary|full|changed` to cover the folded-in tools. All `build.*` tools remain callable by name and unchanged; `BUILDANCHOR_MCP_TOOLS=full` restores the previous listing.
- Root-level Python command resolution now shares the module toolchain resolver, so `cmd` and `modules` can no longer disagree about which runner a project uses.
- `buildanchor init` writes a `commands` block plus a `command_status` map; the `verified_commands` key is retained under its historical name but nothing is claimed as verified until `buildanchor verify` says so.

### Fixed

- **Node projects outside a declared workspace are discovered.** A `package.json` declaring a `test` or `build` script is now a module wherever it sits, matching the rule that already made the Python side work. A repository of sibling projects at the root previously reported its Python modules and silently omitted every Node one, while listing their markers in `evidence`.
- **Every project marker resolves to a module or to a stated reason.** Markers that are not promoted now produce an explicit limitation instead of vanishing — the invariant that makes the defect above impossible to reintroduce silently.
- **`[project.optional-dependencies]` parses one coordinate per entry.** A single greedy regex merged every element of a single-line array into one coordinate (`pytest>=7", "httpx>=0.25.0`). The trigger was the array's layout rather than the section, so `[project] dependencies` written on one line failed identically; both now go through one quote-aware parser, `tomllib` where available.
- **`optionalDependencies` are collected** from `package.json`, alongside the existing three sections.
- **Dependencies and facts are collected from every manifest**, not from whichever sorted first. In a polyglot repository, one arbitrary project previously supplied the whole report's declared dependencies and its `runtime.*` facts. Each dependency now carries the `module` it came from, and per-module facts are keyed `runtime.python@<module>`; single-project repositories keep unqualified keys.
- **`languages` is evidence-backed.** It was a fixed tuple per build system, unioned on any marker match, so a lone `Dockerfile` asserted C/C++, Swift, PHP, Ruby and Dart — languages with no file and no marker in the tree. Languages now come from source-file extensions and unambiguous markers only; ambiguous markers imply a build system, not a language.
- **Module `category` no longer inverts.** `core` was treated as a backend token, so shared libraries named `*-core` were classified `backend`; dependency signals are now available outside Node, and `unknown` is returned when nothing points anywhere, instead of defaulting to `shared`.
- The verification cache directory is excluded from the workspace digest, so recording a result no longer invalidates the report cache it enriches.

- **`get_build_truth` and `get_test_command` no longer contradict each other.** The `validation_commands` list was built from hardcoded per-ecosystem conventions rather than the resolver, so the tool whose description says "call this first" advertised `python -m unittest discover -s tests -v` for a pytest project while the dedicated command tool said `uv run pytest`. Both now resolve through the same toolchain, and the injected block renders the working directory (`cd sdk/node && npm test`) instead of a bare ambiguous command, labelling anything unproven as a candidate.
- **`validate-change --execute` runs each probe in its own working directory** and resolves entrypoints relative to it. Every probe previously ran at the repository root — the same fault as the commands themselves.
- **Script invocation is consistent between the root and module paths** (`npm test`, not `npm test` in one place and `npm run test` in another). Verification status is keyed by the exact command string, so the mismatch silently dropped a proven command back to `declared`. Non-`test` scripts always use `run`, since a bare `pnpm add` would invoke the package manager's own command instead of a project script of that name.

### Performance

- **A warm MCP tool call is roughly 4x faster** (200 ms -> 52 ms on a 4,800-file repository). `resolve_command` asked for the report twice — once for the modules, once for the repository shape — and the MCP transport asked a third time before dispatching, so a single request walked and digested the whole repository three times to validate a cache that then hit. `resolve_command` now accepts an already-computed report, and the transport passes the one it holds.
- **Cache validation no longer stats every file.** The workspace digest is the set of paths plus the contents of the files a conclusion is drawn from; nothing in the report depends on the contents of a source file, so editing a function body correctly no longer invalidates the report and validating the cache costs no `stat` at all.

- **Inspection is roughly 3.5x faster on a large repository** (372 ms -> 104 ms on a 7,812-file tree containing a vendored `node_modules`), from two changes: ignored directories are pruned during the walk rather than traversed and then discarded, and symlinks are not followed — which removes a `realpath` call per file, previously the single largest cost of an inspection, and makes escaping the workspace impossible by construction.
- The workspace digest hashes manifest and build-descriptor contents in full and identifies other files by path, size and modification time, so cache identity no longer costs a full read of the source tree. Evidence entries still carry a true SHA-256 of file contents.

## [0.3.2] - 2026-09-05

### Fixed

- PyPI publishing now uploads only Python distribution archives; `SHA256SUMS.txt` remains a GitHub Release asset.

## [0.3.1] - 2026-09-05

### Added

- **Monorepo Topology Intelligence**: Automatic discovery and component mapping across 8 major ecosystems (pnpm, npm/yarn workspaces, Cargo, Gradle multi-project, Maven multi-module, Go multi-module, Nx, and Turborepo).
- **Semantic Component Categorization**: Auto-classifies monorepo packages into `ui`, `backend`, and `shared` scopes.
- **Scoped Command Resolution**: Execute targeted tests and builds via `buildanchor cmd --scope <ui|backend|shared|module>` or `--changed [baseline]`.
- **Monorepo Inspection Tools**: Added `buildanchor modules` CLI command and `build.modules` MCP tool for AI coding agents.
- **Automated CI/CD Release Pipeline**: Added GitHub Actions workflow (`.github/workflows/release.yml`) for automated testing, artifact generation, PyPI publishing, and GitHub Releases with standardized release notes.

## [0.2.0] - 2026-09-05

### Added

- Copyright, attribution, trademark, and GitHub-only support policies with issue templates.
- User-scoped CLI installers for macOS/Linux and Windows, plus a Homebrew formula/tap layout.
- Git repository and baseline diagnostics, including tracked and untracked change impact.
- Optional bounded validation probes through `buildanchor validate-change --execute`.
- Probe exit status, duration, captured output, timeout handling, and unavailable-tool results.
- GitHub/MCP-friendly validation controls and clearer static-mode limitations.
- CI-friendly exit codes for valid, invalid, inconclusive, and blocked results.

## [0.1.0] - 2026-09-03

### Added

- Static Build Truth inspection for Maven, Gradle, Node, Python, Go, Rust, .NET, and generic build markers.
- Evidence-linked `v1` report, compact context pack, and change-impact models.
- CLI, MCP stdio, and HTTP interfaces.
- Inspect, context, change-impact, validate-change, repair, and dependency explanation operations.
- Python synchronous and asynchronous SDK clients.
- Java 17 dependency-free SDK with local and HTTP transports.
- Initial tests and open-source repository governance files.

### Limitations

- This release does not claim dependency resolution or test success in static mode.
- Live sandboxed resolution and Harness integration are planned next.
