# BuildAnchor benchmarks

Two harnesses, measuring different things.

## `head_to_head.py` — how often is the obvious guess wrong?

The other harness compares BuildAnchor to an older BuildAnchor, which is a
changelog rather than a reason to adopt anything. This one compares it to the
thing people actually do instead: look at the manifest and name the ecosystem's
default.

```bash
uv run python benchmarks/head_to_head.py --format text
```

It clones twelve real public repositories and asks, for each, what the project
declares as its way to run tests. Ground truth is that declaration, cited by
file, so any row can be confirmed by opening it. Where a project declares
nothing, the ecosystem default is correct and is scored that way.

| | Gets the project's own test command |
| --- | --- |
| Guessing from the manifest | 7 / 12 (58%) |
| BuildAnchor | 12 / 12 (100%) |

Five of the twelve declare something other than their default: Flask's
`[tool.tox]`, a `test` target in the Makefiles of requests, pydantic and cobra,
and a `test` recipe in `just`'s own justfile. Two of those run a genuinely
different tool; the rest wrap the same tool with the project's own arguments,
which the report separates rather than counting as the same thing.

**Why this is not in CI.** It needs the network and it depends on twelve
repositories that other people are free to change. A per-push gate that fails
because Flask reorganised its Makefile teaches everyone to ignore CI. Run it
before a release, and when the corpus changes, read the diff.

Two attempts were discarded getting here, and the reasons are worth keeping:
scraping each project's CI workflow for its test step was too fragile — reusable
workflows, matrix expressions and Makefile indirection made it unreliable on half
the corpus — and an early version of the declaration reader matched `test-mypy`
as a `test` target, which would have credited projects that declare no such entry
point. A benchmark whose ground truth is unreliable is worse than no benchmark.

## `credibility_benchmark.py` — does the output actually work?

The number that matters for an agent is not how small the report is. It is
whether the command in the report runs. An agent that receives a plausible but
wrong test command pays for the tool call, the wall of collection errors in its
context, and a repair turn — which costs far more than the report saved.

This harness therefore **executes** the commands BuildAnchor emits and counts
how many exit 0. It measures the working tree against a released baseline, so
every figure is a comparison you can reproduce and falsify.

```bash
uv run python benchmarks/credibility_benchmark.py --format text
uv run python benchmarks/credibility_benchmark.py --format json --output results.json
uv run python benchmarks/credibility_benchmark.py --baseline v1.1.6
```

### What it measures

| Metric | Method |
| --- | --- |
| Command correctness | Runs each module's emitted test command in its stated working directory; counts exit 0. |
| Discovery completeness | Fraction of project markers in the report's own `evidence` that resolve to a module. |
| Report correctness | Languages claimed vs. demonstrable; malformed dependency coordinates; how many modules contribute dependencies. |
| Latency at scale | Median and p95 inspection time on a ~9,300-file git repository, 4,500 files of which are gitignored. |
| Agent context cost | Tokens of MCP tool schema resident in the agent's context on every turn. |

### Fixtures

Generated offline and deterministically — no network, no package installs.

The polyglot fixture reproduces the shape reported from production: three Python
projects and two Node packages as **siblings at the repository root**, with no
root `package.json`, no workspace file, and no `apps/`-style convention. Each
Python project gets a real virtualenv holding a private dependency, so its tests
are genuinely importable from that project's own environment and genuinely not
importable from the repository root. That is what makes the correctness metric
meaningful rather than a restatement of the code's own assumptions.

### Results

Measured on macOS, Python 3.10, comparing the working tree against the previous
release. Reproduce with the command above; the raw data is in
`credibility_results.json`.

| Metric | Baseline (1.1.6) | Current |
| --- | --- | --- |
| Emitted test commands that run | **0 / 3 (0%)** | **5 / 5 (100%)** |
| Project markers resolving to a module | 3 / 5 (60%) | 5 / 5 (100%) |
| Modules discovered | 3 (Python only) | 5 (Python + Node) |
| Languages claimed | 8, of which 5 have no file or marker | 2, all evidence-backed |
| Malformed dependency coordinates | 2 | 0 |
| Modules contributing dependencies | 1 of 5 | 5 of 5 |
| Inspect latency, 9,331-file git repo | 478 ms | **83 ms** |
| MCP schema tokens per agent turn | 2,510 | **702** |

The baseline's 0% is not a rhetorical figure: `python -m pytest <module>` run
from the repository root cannot import a package installed in that module's own
virtualenv, so all three commands fail during collection. This is the failure
that was reported from a real monorepo, reproduced here as a fixture.

### Both repository shapes

A tool that only pays off on a large monorepo does not get installed by the
people who would benefit from it on an ordinary single-project repository, so
both are measured on the same terms.

| Fixture | Expected shape | Classified | Command runs |
| --- | --- | --- | --- |
| Python project at the root | single-project | correct | 1 / 1 |
| Node project at the root | single-project | correct | 1 / 1 |
| Go project at the root | single-project | correct | `go` not installed here |
| Rust project at the root | single-project | correct | `cargo` not installed here |
| Root project + SDK subdirectory | root-plus-satellites | correct | 2 / 2 |
| 3 Python + 2 Node siblings | monorepo | correct | 5 / 5 |

1.1.6 had no notion of repository shape at all, so it reports `not-reported` for
every row — it described a root project with one SDK subdirectory as a monorepo
and offered `--scope ui` for it.

A command whose toolchain is not installed on the machine running the benchmark
is reported as `toolchain_absent` and excluded from the correctness figure,
rather than counted as either a pass or a failure. Node and Python fixtures use
runners that need no install (`node --test`, a virtualenv built in place), so
the measurement is of BuildAnchor's command rather than of whether someone
remembered to run `npm install`.

Token counts use `tiktoken` (`cl100k_base`) when it is installed and a
characters÷4 approximation otherwise; the harness states which it used.

## One harness

`benchmark_cli.py` and `run_benchmarks.py` used to sit beside this one. Both
measured latency; neither measured whether anything worked, and the numbers they
published came from a third place. Their per-ecosystem fixture corpus is now
section 5 of this harness, so one command produces every number that appears in
the README and one threshold set defends all of them.

These are engineering measurements, not a claim that any team will save a
specific amount of time. Compare them against your current workflow before
adopting anything.
