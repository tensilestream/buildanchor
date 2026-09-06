# BuildAnchor feedback from a real monorepo

Evaluated as a possible replacement for the hand-rolled monorepo topology and
test-command detection in an AI coding service. The design is a close fit for
that job and the code reads carefully — `shell=False` throughout, fixed argument
vectors, timeouts on every subprocess, output bounded, and honest limitation
strings (`network_used: "unknown"` rather than a claim of offline; *"Probe
commands use shell=False, but selected build tools may resolve dependencies or
run project-defined test code"*). Those are the marks of an author who has
thought about being wrong.

We did not adopt it, for the reasons below. Each is reproducible on a public
monorepo shape, and each has a concrete suggested change.

> **Status (2026-09-06): all six items are fixed in 1.2.0.** Each fix carries a
> regression test in `tests/test_report_correctness.py`, and the effect is
> measured against the 1.1.6 baseline by
> `benchmarks/credibility_benchmark.py` on a fixture reproducing this
> repository's shape:
>
> | | 1.1.6 | 1.2.0 |
> | --- | --- | --- |
> | Emitted test commands that run | 0 / 3 (0%) | 3 / 3 (100%) |
> | Project markers resolving to a module | 3 / 5 | 5 / 5 |
> | Languages claimed | 8, five with no file or marker | 2, all evidence-backed |
> | Malformed dependency coordinates | 2 | 0 |
> | Modules contributing dependencies | 1 of 5 | 5 of 5 |
> | Inspect latency, 7,812-file repo | 372 ms | 104 ms |
>
> Item-by-item resolutions are recorded below each finding. Beyond the six:
> commands now carry the `working_directory` they must run in, and
> `buildanchor verify` climbs a `declared -> resolvable -> collects -> passes`
> ladder so a caller can tell a candidate from a checked fact.

> **Maintainer verification (2026-09-06).** Every item below was reproduced
> against `buildanchor` 1.1.6 (commit `07e8a1b`) on a synthetic fixture with the
> same shape as the reported repository: root-level `service-a/`, `lib-a/`
> (Python, `uv.lock`), `web-ui/`, `bot/` (Node), plus a root `Dockerfile`. All
> five findings stand on the current release. Three of the five *diagnoses* were
> off, and the corrections change what the fix is — see the verification blocks
> per item. One further defect surfaced while reproducing #1; it is added as #6.

## The repository

A polyglot monorepo, ~11 sibling projects at the repository root. No root
`package.json`, no `pnpm-workspace.yaml`, no `turbo.json`, no `nx.json`, no
Maven reactor — each directory is simply an independent project:

```
repo/
├── service-a/          pyproject.toml + uv.lock   (Python)
├── service-b/          pyproject.toml + uv.lock   (Python)
├── web-ui/             package.json (vitest)      (Node)
├── bot/                package.json (vitest)      (Node)
└── … 7 more
```

Command run: `buildanchor inspect --format json` at the repository root.

---

## 1. Node modules are absent from `module_details` — Blocker

**What happened.** `module_details` returned 8 modules, every one of them
Python. `web-ui` and `bot` are missing, even though the same report's own
`evidence` block lists them:

```json
{ "detail": "detected node marker", "path": "web-ui/package.json" }
{ "detail": "detected node marker", "path": "web-ui/package-lock.json" }
{ "detail": "detected node marker", "path": "bot/package.json" }
```

**What we expected.** Both listed as modules, with `ecosystem: "node"`. The
evidence collector found them; the module discovery did not.

**Why it matters to us.** Every ticket we were trying to route was a UI ticket.
The one module we needed was the one missing, so the topology was not merely
less useful than ours — it was the wrong answer for the question we asked.

**Probable cause.** The two discovery paths are asymmetric. Reading
`build_truth/features/inspection.py`, the Python path appears to walk markers
wherever they are, which is why all 8 Python projects were found at arbitrary
root paths. The Node path is gated on a declared workspace — root `workspaces`,
`pnpm-workspace.yaml`, `turbo.json`, `nx.json` — or on a parent directory drawn
from `standard_roots`:

```python
standard_roots = {"apps", "packages", "services", "libs", "modules",
                  "frontend", "backend", "client", "server", "web", "api"}
```

A `package.json` at `<root>/web-ui/` satisfies none of these, so it is never
promoted to a module.

**Verified — confirmed, cause as stated.** `_discover_modules`
(`src/buildanchor/build_truth/features/inspection.py:256-284`) admits a
`package.json` only if it matches a declared workspace glob, its first path
segment is in `standard_roots`, or `turbo.json` / `nx.json` /
`pnpm-workspace.yaml` exists at the root. The Python branch at
`inspection.py:403-417` has an extra clause — `len(Path(rel_dir).parts) <= 2` —
which admits any project at most two levels deep regardless of its name. That
single clause is the whole asymmetry. On the fixture, `module_details` returned
`lib-a` and `service-a` and omitted `web-ui` and `bot`, while `build_systems`
listed `node` and `evidence` carried both `package.json` markers.

**Suggested change.** Give Node the same fallback Python already has: a
`package.json` that declares a `test` script and is not under an ignored
directory is a module, wherever it sits. The declared-workspace and
`standard_roots` paths stay as they are and keep their better metadata; this is
purely an additional last resort, and it is the rule that already makes the
Python side work. A cheap invariant to assert in your own tests: *every marker
in `evidence` resolves to either a module or a stated reason for exclusion.*
That single check would have caught this.

**Resolved in 1.2.0.** The fallback is implemented exactly as suggested: a
`package.json` declaring a `test` or `build` script is a module wherever it
sits, with the declared-workspace and `standard_roots` paths unchanged above it.
The invariant is implemented too — every project marker in `evidence` now
resolves to a module or produces an explicit limitation naming the directory and
why it was excluded, and a test asserts it holds.

---

**Resolved in 1.2.0.** All three suggestions implemented. Every module reports
`working_directory`; commands are relative to it, with a `*_command_shell`
variant carrying the `cd` for callers that paste from the root; and a project's
declared environment is preferred — `uv.lock` yields `uv run pytest`, a `.venv/`
yields that interpreter, `pnpm-lock.yaml` yields `pnpm test`. The confidence
signal asked for is the verification ladder: `buildanchor verify` executes a
discovery-only probe and records `resolvable` / `collects` / `passes` against a
digest of the manifests, so `test_command_status` says how far the command is
proven and reverts to `declared` when a manifest changes. The module with no
tests of its own now fails the `collects` rung with the collection output
attached, rather than being emitted silently.

---

## 2. Generated `test_command`s do not run — Blocker

**What happened.** Every Python module got a command of the form
`python -m pytest <module-path>`, implicitly from the repository root. All three
we tried fail:

| Suggested command | Result |
| --- | --- |
| `python -m pytest service-a` | no tests collected |
| `python -m pytest lib-a` | no tests collected |
| `python -m pytest service-b` | **126 errors during collection** |

The same suites pass when run correctly — `cd service-b && .venv/bin/python -m
pytest tests/unit` gives 1778 passed.

**What we expected.** Either a command that runs as given, or an explicit
working directory alongside it.

**Why it fails.** Each project is independent: its own virtualenv, its own
dependencies, its own `pytest` rootdir and `conftest.py`. Run from the
repository root, the package under test is not importable and collection dies.
`python -m pytest <path>` expresses *"run this path's tests using the current
interpreter and environment"*, when what is needed is *"run tests inside that
directory, using that project's environment"*.

A related case: one module has no tests of its own (they live in a sibling
project). The command was still emitted, with nothing to indicate it would
collect nothing.

**Verified — confirmed.** `working_directory` exists nowhere in the codebase:
not in the dicts built by `_discover_modules`, not on `ModuleInfo`
(`src/buildanchor/models.py`), not in the JSON schema. The Python `test_command`
is the literal f-string `f"python -m pytest {rel_dir}"` (`inspection.py:415`),
with no reference to `uv.lock` or `.venv` even though both are already declared
markers read during the same pass. The Node branch has the same shape
(`npm --prefix <dir> test`), which happens to work for npm and would not for a
project needing its own environment.

**Suggested change.** Three parts, in order of value:

1. **Add `working_directory` to each entry in `module_details`.** A command
   without a cwd is ambiguous the moment more than one project exists, which is
   the only situation `module_details` is for. This alone makes the output
   usable by a caller that does the right thing.
2. **Emit the command as relative to that directory** — `python -m pytest`,
   `npm test` — rather than embedding the module path as an argument.
3. **Prefer the project's own environment when one is declared.** A `uv.lock`
   next to `pyproject.toml` means `uv run pytest`; a `.venv/` means that
   interpreter. You already read both files as markers, so the signal is in
   hand. Where none is declared, say so — a `confidence` field, or
   `status: "candidate"` as `validation_commands` already uses, would let a
   caller decide whether to trust it.

---

**Resolved in 1.2.0.** One quote-aware parser (`core/manifest_parsing.py`),
using `tomllib` where the interpreter has it. The regression test pins a
one-line array in both the main and the extras section, since the trigger was
the layout rather than the section. `optionalDependencies` is now collected on
the Node side as well.

---

## 3. `[project.optional-dependencies]` is parsed as one coordinate — Bug

**What happened.** This TOML:

```toml
[project.optional-dependencies]
dev = ["pytest>=7", "httpx>=0.25.0", "pytest-asyncio>=0.23"]
```

produced a single dependency entry:

```json
{ "coordinate": "pytest>=7\", \"httpx>=0.25.0\", \"pytest-asyncio>=0.23",
  "source": "declared", "status": "unresolved" }
```

**What we expected.** Three coordinates, as the main `[project] dependencies`
array in the same file produced correctly — `fastapi>=0.100.0`,
`sqlalchemy>=2.0.0` and the rest all parsed individually.

**Probable cause.** The optional-dependencies path looks like it takes the text
between the outermost quotes on the line, where the main-dependencies path
splits the array properly. Two parsers, one correct.

**Verified — confirmed, but the cause is not two parsers.** There is only one
parser. `PythonAdapter.collect_facts`
(`src/buildanchor/build_truth/adapters/python.py:23`) scans the whole file with

```python
re.findall(r"[\"']([A-Za-z0-9_.-]+(?:[<>=!~].*)?)[\"']", text)
```

The `.*` is greedy, so it runs to the last quote **on the line**. The trigger is
therefore *a single-line array*, not the `optional-dependencies` section: the
same file's `[project] dependencies` parsed correctly only because it was
written one entry per line. Collapsed to one line it fails identically —

```
dependencies = ["fastapi>=0.1", "sqlalchemy>=2.0"]
  -> ['fastapi>=0.1", "sqlalchemy>=2.0']
```

So the regression test should pin *a one-line array*, in both the main and the
extras section; fixing only the extras path would leave the bug reachable. The
fix is a non-greedy `[^"']*` (or a real TOML parse), not a second parser.

On the Node side there is no equivalent split — `NodeAdapter` uses
`json.loads`. There is a smaller gap in the same place: `collect_facts`
(`adapters/node.py:27`) collects `dependencies`, `devDependencies` and
`peerDependencies`, but never `optionalDependencies`.

**Suggested change.** Reuse the array parser for both. A regression test over a
`dev = [...]` extra with three entries pins it. Worth checking the equivalent
Node path — `peerDependencies` and `optionalDependencies` may have the same
split.

---

**Resolved in 1.2.0**, via the second suggestion — the first was a no-op, as
noted. Languages are derived from source-file extensions and from markers that
imply a language unambiguously; `Dockerfile`, `Makefile`, `CMakeLists.txt`,
`BUILD`, `WORKSPACE` and `global.json` imply a build system and contribute
nothing. The report gained `language_details`, giving each language its file
count, markers and sample paths, so an entry can be checked the way a fact can.
A test asserts no ambiguous marker can re-enter the language map.

---

## 4. `languages` reports languages the repository does not contain — Bug

**What happened.**

```json
"languages": ["C/C++", "Dart", "JavaScript", "PHP", "Python", "Ruby", "Swift", "TypeScript"]
```

Counting files, excluding `node_modules/` and `.venv/`:

| Language | Files outside dependencies | Files anywhere |
| --- | --- | --- |
| C/C++ | 0 | 137 |
| Dart | 0 | 10 |
| PHP | 0 | 1 |
| Ruby | 0 | **0** |
| Swift | 0 | **0** |

C/C++, Dart and PHP come entirely from `node_modules/` (439 MB of it). Ruby and
Swift have no files at all, and no marker either — no `Gemfile`, no
`Package.swift`, no `.gemspec`, no `.podspec` anywhere in the tree.

**What we expected.** `["JavaScript", "Python", "TypeScript"]`.

**Why it matters.** For an agent-facing tool this is the most quietly damaging
of the four. A model told the repository contains Ruby and Swift will believe
it, and `languages` sits right beside `facts`, which carries a `proven` status
and evidence ids. Adjacency implies a shared standard the field does not meet.

**Verified — confirmed, and worse than diagnosed.** The fixture reproduces the
reported list *exactly* — `["C/C++", "Dart", "JavaScript", "PHP", "Python",
"Ruby", "Swift", "TypeScript"]` — from a tree whose only generic marker is a
single empty root `Dockerfile`. No `node_modules` was present at all.

The `ignored_dirs` hypothesis is wrong on both halves. `_files()`
(`inspection.py:146-159`) already excludes `node_modules`, `.venv`, `target`,
`build`, `dist` and more, so dependency directories were never contributing.
And `languages` is not computed from files in the first place: it is a fixed
tuple per build system, unioned whenever any one marker for that system matches
(`inspection.py:34-41`, `core/build_systems.py`). The `generic` row is the
culprit —

```python
("generic",
 ("Makefile", "CMakeLists.txt", "BUILD", "WORKSPACE", "Package.swift",
  "composer.json", "Gemfile", "pubspec.yaml", "Dockerfile"),
 ("C/C++", "Swift", "PHP", "Ruby", "Dart")),
```

markers and languages are two unrelated lists, so *any* one of those nine files
asserts all five languages. A `Dockerfile` claims Ruby and Swift. This also
means `dotnet` claims C#, F# and Visual Basic together, and `gradle` claims
Java, Kotlin, Groovy and Scala together — the same defect, just less visible.

Consequently suggestion 1 is a no-op and suggestion 2 is the whole fix: pair
each marker with the language it actually implies, and carry the evidence ids
alongside as the reporter asks. That change subsumes the `generic` case.

**Suggested change.** Two things:

1. **Apply `ignored_dirs` to language detection.** Module discovery already
   excludes `node_modules`, `.venv`, `target`, `dist` and the rest; language
   detection evidently does not. Sharing that one constant fixes C/C++, Dart and
   PHP.
2. **Find the Ruby/Swift source and require evidence.** These appear with zero
   files and zero markers, so something is inferring them from nothing. Better
   still, give `languages` the same shape as `facts` — a value with the evidence
   ids behind it. Then a language with nothing behind it cannot be emitted, and
   a reader can check any entry the way they can check a fact today.

---

**Resolved in 1.2.0.** `core` is no longer a backend token — it names a shared
core library at least as often as a service, which was the whole of the
inversion — and a shared-library name carrying a backend dependency stays
`shared`. Dependency signals are now available outside Node, and the function
returns `unknown` when nothing points anywhere instead of defaulting to
`shared`, which is the standard you asked for.

---

## 5. Module `category` is inverted for two modules — Minor

`lib-a`, a shared library with no service entry point, was categorised
`backend`. `service-b`, an HTTP service, was categorised `shared`. Low impact
for us since we ignore the field, but if a caller routes on it the values are
misleading. If the heuristic cannot be made reliable, `unknown` is more useful
than a confident wrong answer — which is the standard the rest of the output
already holds itself to.

**Verified — confirmed.** `_categorize_module` (`inspection.py:202-225`) scores
the tokens of `f"{name} {rel_path}"` against fixed keyword sets. The fixture
reproduces the inversion: a `lib-a/` directory whose `pyproject.toml` declares
`name = "lib-a-core"` is categorised `backend`, because `core` is in the
backend token set and the shared-token check is only reached when neither the
ui nor the backend set matched. The dependency signal that would disambiguate
is unavailable outside Node: the Python branch passes `[]`, Gradle and Rust
pass `[]`, and Maven passes every `<artifactId>` in the POM including the
module's own. There is also no `unknown` value to fall back to — the function
returns `"shared"` for "matched nothing" and for "matched both weakly", so a
caller cannot tell a guess from a finding. Agreed on the suggested standard.

---

**Resolved in 1.2.0.** Every manifest is read rather than the first in sort
order. Each dependency carries the `module` it came from, and per-module facts
are keyed `runtime.python@<module>`; a single-project repository keeps
unqualified keys, so existing readers are unaffected.

---

## 6. `dependencies` and `facts` describe one arbitrary project — Blocker

**What happened.** Found while reproducing #1, not in the original report.
On the fixture, with `service-a/pyproject.toml` declaring `fastapi` and
`sqlalchemy` and `lib-a/pyproject.toml` declaring none, the whole report's
`dependencies` array came back **empty** for Python. Adding a root
`pyproject.toml` did not change it.

**Why.** Each adapter's `collect_facts` calls `engine._first_text(...)`
(`inspection.py:195-200`), which returns *the first readable path in sorted
order* and stops. `lib-a/pyproject.toml` sorts before `service-a/pyproject.toml`
— and before a root `pyproject.toml`, since `l` < `p` — so one alphabetically
arbitrary manifest supplies every declared dependency and every `runtime.*` /
`node.*` fact for its ecosystem. The same holds on the Node side, where `bot`
shadowed `web-ui`.

**Why it matters.** Unlike #1 this is silent: there is no evidence entry to
contradict it, and a `facts` list carrying `status: "proven"` for
`runtime.python` sourced from whichever project happened to sort first is a
confident wrong answer of the kind the rest of the tool avoids. It also
compounds #2 — a caller that fixes the working directory still gets one
project's dependency picture applied to all of them.

**Suggested change.** Fold dependency and fact collection into the per-module
pass rather than a single per-ecosystem pass, and attach the results to the
module. If that is too large a change for a point release, at minimum iterate
all manifests instead of the first, and tag each dependency with the manifest
path it came from — the evidence id is already computed per file.

---

## What we would adopt tomorrow if 1 and 2 were fixed

Not a consolation: these are the parts we are copying by hand instead.

- **`module_details` as a shape.** `{name, path, ecosystem, category,
  build_command, test_command}` is the right structure. We are building the same
  thing internally and would rather not.
- **Evidence with content digests.** `{path, sha256, detail}` per marker is
  better than what we do. Our findings cite a git commit; a digest pins the exact
  file state a conclusion was drawn from, which survives a rebase.
- **The refusal discipline.** *"Git baseline 'x' could not be resolved"* rather
  than a pass, and *"Static mode does not claim that dependencies resolved or
  tests passed"*, are exactly right. A tool for agents is worth more for what it
  declines to assert than for what it asserts.
- **Exit codes `0/1/2/3`** for valid/invalid/inconclusive/blocked. Distinguishing
  *inconclusive* from *invalid* is the distinction most tools skip and the one an
  automated caller most needs.

## Ranked

1. **Node modules missing from `module_details`** — blocker; the asymmetry with
   the Python path looks like a small fix.
2. **`test_command` needs a `working_directory`** — blocker for any multi-project
   repository, which is the whole use case.
3. **`optional-dependencies` parsing** — contained bug, easy regression test.
4. **`languages` ignoring dependency directories, and inventing two entries** —
   correctness, and it undermines trust in the report as a whole.
5. **`category` inversion** — cosmetic unless someone routes on it.
6. **`dependencies`/`facts` drawn from one arbitrary manifest** — added during
   verification; blocker for the same reason as 2, and silent where 1 is loud.

Tested against `buildanchor` 1.1.5 on macOS, Python 3.11. Re-verified against
1.1.6 (`07e8a1b`) on macOS, Python 3.11 — all five findings reproduce
unchanged.