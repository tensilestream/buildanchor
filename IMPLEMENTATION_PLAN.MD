# BuildAnchor v0.3 — Full Implementation Plan

## What We Are Building

Two capabilities that together answer the question every agent asks before writing code:

**1. `buildanchor find --package <name>`**
> "Is this package available? What version is actually installed? How does this project import it?"

**2. Zero-ambiguity flags + exit codes**
> "Can I use this in a CI gate with `&&` without parsing any JSON?"

---

## User Review Required

> [!IMPORTANT]
> The `find` command inspects **installed** packages (node_modules, .venv/site-packages, ~/.m2).
> It is read-only and makes no network calls. It works offline.

> [!WARNING]
> `--exit-on-mismatch` and `--assert-ecosystem` change exit codes. Any script currently relying
> on `buildanchor plan` always returning exit 0 will need updating. This is documented clearly.

---

## Open Questions

> [!IMPORTANT]
> Should `find` search source files for import usage patterns by default, or only behind a `--show-usage` flag?
> Current plan: **default on** — usage patterns are the most valuable output for LLMs.
> (Adds ~100ms per search on large repos due to grep walk.)

---

## Proposed Changes

---

### Core: `build.find_package` (new engine method + MCP tool + CLI command)

#### What it returns to an LLM

```
Package: axios
Status: installed
Version (installed): 1.7.2
Version (declared): ^1.7.0  [dependencies]
Location: node_modules/axios/package.json

Exports:
  import axios from 'axios'           (ESM default)
  const axios = require('axios')      (CJS)
  import { AxiosError } from 'axios'  (named)

How this project uses it (3 files):
  src/api/client.ts:3     import axios from 'axios'
  src/api/health.ts:1     import axios, { AxiosError } from 'axios'
  tests/api.test.ts:2     const axios = require('axios')

LLM guidance:
  Use the ESM import pattern: import axios from 'axios'
  This project targets Node 20.x — axios 1.7.x is compatible.
  Do not add axios again — it is already a direct dependency.
```

#### What it checks, per ecosystem

| Ecosystem | Installed check | Declared check | Usage scan |
|---|---|---|---|
| Node.js | `node_modules/<pkg>/package.json` | `package.json` deps | `import`/`require` in `.js`,`.ts`,`.mjs` |
| Python | `.venv/`, `venv/`, `site-packages/` | `pyproject.toml`, `requirements.txt` | `import <pkg>` in `.py` |
| Java/Maven | `~/.m2/repository/<group>/<artifact>` | `pom.xml` `<dependency>` | `import <pkg>` in `.java`,`.kt` |
| Gradle | same as Maven | `build.gradle` `implementation` | same as Maven |
| Go | `vendor/<module>` or module cache | `go.mod require` | `import "<module>"` in `.go` |
| Rust | `Cargo.lock` resolved entry | `Cargo.toml [dependencies]` | `use <crate>` in `.rs` |

---

### CLI Flags (zero ambiguity — each does exactly one thing)

| Flag | Commands | Job | Output change |
|---|---|---|---|
| `--exit-on-mismatch` | `plan`, `preflight` | Exit code 3 if OBJECTIVE_ECOSYSTEM_MISMATCH | Exit code only, no output change |
| `--assert-ecosystem <name>` | any | Exit code 3 + message if workspace ≠ expected | Adds one line: `assert: FAIL — expected maven, got node` |
| `--only-errors` | `plan`, `preflight`, `compatibility` | Remove all warnings, show blocking issues only | Removes `[WARN]` lines |
| `--explain` | any | Add one plain-English sentence per rule | Adds `why: <sentence>` under each finding |
| `--show-usage` | `find` | Show import patterns from source files | Default ON; use `--no-show-usage` to disable |
| `--installed-only` | `find` | Only return result if package is actually installed | Default OFF |

---

### Exit Code Contract (stable, documented, versioned)

```
0  = compatible / found / valid — safe to proceed
1  = incompatible / not found / invalid — stop, fix first
2  = inconclusive — missing git baseline or no build system detected
3  = blocked — ecosystem mismatch or --assert-ecosystem failed
4  = BuildAnchor error — bad --workspace path, permission denied
```

Enables shell composition:
```bash
buildanchor plan --workspace . --objective "Add a JPA entity" --exit-on-mismatch \
  && my-agent-script
```

---

## Files

### [MODIFY] [engine.py](file:///Users/kumudham/PramalSystems/projects/BuildAnchor/src/buildanchor/engine.py)
Add `find_package(name)` method covering Node, Python, Java/Maven, Go, Rust.

### [MODIFY] [cli.py](file:///Users/kumudham/PramalSystems/projects/BuildAnchor/src/buildanchor/cli.py)
- Add `find` command
- Add `--exit-on-mismatch`, `--assert-ecosystem`, `--only-errors`, `--explain`, `--installed-only` flags
- Fix `--help` grouping

### [MODIFY] [transports.py](file:///Users/kumudham/PramalSystems/projects/BuildAnchor/src/buildanchor/transports.py)
Add `build.find_package` MCP tool.

### [NEW] [ADOPTION.md](file:///Users/kumudham/PramalSystems/projects/BuildAnchor/ADOPTION.md)
Single doc for million-user adoption. Replaces AGENT_GUIDE.md as the primary entry point.

### [NEW] [benchmarks/run_benchmarks.py](file:///Users/kumudham/PramalSystems/projects/BuildAnchor/benchmarks/run_benchmarks.py)
Measures real token counts across 6 fixture repos, writes `results.json`.

### [NEW] benchmarks/fixtures/
6 minimal repos (maven-spring3, node-esm, python-pyproject, go-module, rust-2021, polyglot).

---

## Verification Plan

### Automated Tests
```bash
uv run python -m unittest discover -s tests -v
uv run python benchmarks/run_benchmarks.py
```

### Manual — `find` command
```bash
# Node.js — installed package
buildanchor find --package axios --workspace /path/to/node-repo
# Expected: installed version + import pattern used in project

# Node.js — not installed
buildanchor find --package stripe --workspace /path/to/node-repo --installed-only
# Expected: exit 1, "stripe is not installed in node_modules"

# Python
buildanchor find --package fastapi --workspace /path/to/python-repo
# Expected: installed version from .venv + how project imports it

# Java (Maven)
buildanchor find --package spring-boot-starter-data-jpa --workspace /path/to/java-repo
# Expected: resolved version from ~/.m2 + import patterns in .java files
```

### Manual — flags
```bash
# Exit code contract
buildanchor plan --workspace /node-repo --objective "Add JPA entity" --exit-on-mismatch
echo $?  # must be 3

buildanchor plan --workspace /maven-repo --objective "Add JPA entity" --exit-on-mismatch
echo $?  # must be 0

# Assert
buildanchor inspect --workspace /node-repo --assert-ecosystem maven
# must print: "assert: FAIL — expected maven, got node" and exit 3

# Only errors
buildanchor compatibility --workspace /node-repo --only-errors
# must show nothing (no errors, only warnings) and exit 0

# Explain
buildanchor plan --workspace . --objective "Add JPA entity" --explain
# Each finding must have a "why:" line in plain English
```

--
Executing the plan. Starting with all 5 deliverables in parallel where possible:

Created task.md

**Step 1 — `find_package()` in engine.py:**

Ran command: `echo "test" && wc -l /Users/kumudham/PramalSystems/projects/BuildAnchor/src/buildanchor/engine.py`

Good. I'll write each piece as a small Python script saved to a temp file first, then executed — avoiding any large inline payloads:

Ran command: `echo ok`

I'll use `write_to_file` for all large content — no inline shell heredocs:

Created add_find_package.py
Ran command: `python3 /Users/kumudham/.gemini/antigravity-ide/brain/79f2cdc9-8478-4fdf-a437-132efb357e41/scratch/add_find_package.py 2>&1`
Ran command: `cd /Users/kumudham/PramalSystems/projects/BuildAnchor && python3 -W ignore -c "import ast; ast.parse(open('src/buildanchor/engine.py').read()); print('engine OK')"`

The escaped single-quotes in the regex got mangled. Let me fix those specific broken regex lines directly:

Ran command: `python3 - << 'EOF'
import pathlib, re

p = pathlib.Path('/Users/kumudham/PramalSystems/projects/BuildAnchor/src/buildanchor/engine.py')
text = p.read_text()

# Fix 1: broken regex in _find_python
old1 = r"r\"[\"\\']?\" + re.escape(norm) + r\"[\"\\']?\\s*([><=!~^][^\\s,;\\]]+)?\","
new1 = "r\"[\\\"']?\" + re.escape(norm) + r\"[\\\"']?\\s*([><=!~^][^\\s,;\\]]+)?\" ,"
text = text.replace(old1, new1)

# Fix 2: all \\' in raw strings (they should just be ' in raw strings)
# Replace r\"...\\'...\" patterns that are inside actual Python strings (not raw strings)
text = text.replace("import \\'{name}\\''", "import \\'{name}\\''")

# Simpler: find and fix the specific broken lines
lines = text.splitlines()
fixed = []
for line in lines:
    # Fix escaped single quotes inside double-quoted strings
    if "r\"[\"\\'\\']?" in line or "[\"\\'\\']?" in line:
        line = line.replace("[\"\\'\\']?", "[\"']?")
    fixed.append(line)
text = '\n'.join(fixed) + '\n'
p.write_text(text)
print("fixed")
EOF`
Ran command: `cd /Users/kumudham/PramalSystems/projects/BuildAnchor && python3 -W ignore -c "import ast; ast.parse(open('src/buildanchor/engine.py').read()); print('OK')" 2>&1`
Viewed engine.py:910-920
Edited engine.py
Ran command: `cd /Users/kumudham/PramalSystems/projects/BuildAnchor && python3 -W ignore -c "import ast; ast.parse(open('src/buildanchor/engine.py').read()); print('OK')" 2>&1`
Ran command: `cd /Users/kumudham/PramalSystems/projects/BuildAnchor && grep -n "artifact_id" src/buildanchor/engine.py | grep "re.search" | head -5`
Edited engine.py
Ran command: `cd /Users/kumudham/PramalSystems/projects/BuildAnchor && python3 -W ignore -c "import ast; src=open('src/buildanchor/engine.py').read(); ast.parse(src); print('engine OK')" 2>&1 | head -5`