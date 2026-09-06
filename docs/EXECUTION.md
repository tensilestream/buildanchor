# What BuildAnchor executes, and when

BuildAnchor runs build commands. That is the point of it, and it is also the
reason a careful person hesitates to install a tool they have not read.

This is the complete inventory. It is not a reassurance — it is enforced by
`tests/test_execution_surface.py`, which parses the package, finds every
subprocess call site, and fails if one appears that is not listed here.

## The short version

| | |
| --- | --- |
| Total subprocess call sites in the package | **10** |
| That run project-defined code | **2** |
| That use a shell | **0** |
| That build a command from a string | **0** |
| Runtime dependencies | **0** |
| Lines of Python you would have to read to check all of this | **~7,800** |

## Nothing runs unless you ask

Every command listed below is reached only from something you typed. There is no
daemon, no background process, no post-install hook, and no telemetry.

**`inspect`, `context`, `modules`, `cmd`, `doctor`, `find`, `preflight`, `plan`
and the MCP server never execute project code.** They read files and, at most,
ask git what it already knows. Static mode is static.

## The two places project code runs

Both are reached only from an explicit command, and neither is reachable over
MCP or HTTP — a remote caller cannot consent to running code on your machine, so
those operations are refused with an error that says so.

### `_run_probe` — `buildanchor verify`

Runs one discovery probe per module, and the full test command only at
`--verify-level passes`. Preview exactly what it would run, without running any
of it:

```bash
buildanchor verify --dry-run
```

A probe is chosen to load your test files without running any test body:
`pytest --collect-only`, `jest --listTests`, `cargo test --no-run`,
`go test -run '^$'`, `mvn -DskipTests test-compile`. Your project's code is
imported by these, which is exactly what makes the result meaningful — a probe
that imported nothing would prove nothing.

### `_execute_validation` — `buildanchor validate-change --execute`

Runs the validation commands from the report. Without `--execute` it runs
nothing, which is the default.

## The eight read-only git calls

These observe a repository and cannot modify one. They are how BuildAnchor knows
what changed and which files are tracked.

| Function | Runs |
| --- | --- |
| `_git_tracked_files` | `git ls-files --cached --others --exclude-standard` |
| `_git_info` | `git rev-parse --show-toplevel`, `git rev-parse --verify HEAD` |
| `_git_changed_files` | `git rev-parse`, `git diff --name-status`, `git status --porcelain` |

No `git` subcommand here writes: no `add`, no `commit`, no `checkout`, no
`clean`, no `reset`.

## How every call is constrained

- **No shell.** Every call passes a fixed argument vector, so there is no string
  for a repository's contents to be interpolated into. `shell=True` appears
  nowhere, and a test fails if it ever does.
- **Always bounded.** Every call has a timeout. A build tool that hangs must not
  hang the agent that called it.
- **Bounded output.** Captured output is truncated, so a runaway process cannot
  exhaust memory.
- **Inside the workspace.** Paths are checked against the allowed root before
  anything is run.

## What it writes

| Path | Written by | Contents |
| --- | --- | --- |
| `.buildanchor/verified.json` | `verify` | Which commands are proven, and for which manifest digest. No absolute paths, no hostnames, nothing about your machine. |
| `.buildanchor.json` | `init` | The resolved commands. |
| `CLAUDE.md` / `AGENTS.md` | `init` | A marked block, refreshed in place. |

Preview with `buildanchor init --dry-run`; remove everything `init` wrote with
`buildanchor init --undo`, which restores your file byte-for-byte.

## What it never does

- Install, update or remove a dependency.
- Modify your source, your git history, or your working tree.
- Run anything on import, at install time, or on a schedule.
- Send telemetry, or contact any endpoint of ours. There is none.

## The one place a network call exists

`sdk.py` uses `urllib` to reach a BuildAnchor HTTP server — and only when *you*
construct a client with an `endpoint=` you chose. The CLI, the engine and the
MCP server make no network calls at all; `grep -rn urllib src/buildanchor` finds
that file and nothing else, and a test asserts the engine stays clean.

This is the kind of claim worth checking rather than believing: an earlier draft
of this document said "there is no network call in the package", which was
wrong.

## Checking this yourself

```bash
grep -rn "subprocess" src/buildanchor        # every call site
grep -rn "shell=True" src/buildanchor        # returns nothing
grep -rn "urllib" src/buildanchor            # only sdk.py, only when you set an endpoint
uv run python -m unittest tests.test_execution_surface -v
```

The package is about 7,800 lines with no runtime dependencies. That is small
enough to read in an afternoon, and for a tool that runs commands on your
machine, reading it is a reasonable thing to want to do.
