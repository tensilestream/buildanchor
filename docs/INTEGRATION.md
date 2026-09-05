# BuildAnchor integration guide

BuildAnchor sits between an AI coding agent and a repository's build system:

```text
preflight → agent change → change impact → bounded validation → repair guidance
```

## Local agent loop

Run these commands from a Git checkout with at least one baseline commit:

```bash
buildanchor preflight --workspace . --objective "Add a health endpoint" --format json
buildanchor plan --workspace . --objective "Add a health endpoint" --format json
# The agent applies the approved change.
buildanchor validate-change --workspace . --baseline HEAD --execute --format markdown
```

The default validation mode is static and never claims that tests passed. `--execute` is an explicit opt-in. It runs only commands discovered by BuildAnchor's adapter catalogue, uses argument arrays rather than a shell, caps each probe at 900 seconds, and truncates captured output. The selected build tool can still run project-defined code or resolve dependencies, so use it only in a trusted runner or CI sandbox.

## Pull request validation

The base SHA is the correct baseline for a pull request. Fetch the complete history and run:

```yaml
name: BuildAnchor

on:
  pull_request:

permissions:
  contents: read

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: astral-sh/setup-uv@v6
      - run: uvx --from buildanchor buildanchor validate-change --baseline "${{ github.event.pull_request.base.sha }}" --execute --format markdown
```

For a released version, pin the package version in the `uvx` command. For repository-local development, replace `uvx --from buildanchor` with `uv sync` followed by `uv run buildanchor`.

## MCP

Start a server with a bounded root:

```bash
buildanchor mcp --stdio --allow-root /path/to/repository
```

The agent can call `build.preflight` before editing, then `build.validate_change` with `execute: true` after editing. The server rejects a workspace escape and returns evidence-linked JSON suitable for an agent context window.

## Interpreting statuses

| Status | Meaning |
| --- | --- |
| `valid` | Static compatibility checks and, when requested, every available probe passed. |
| `invalid` | A compatibility rule or executed probe failed. |
| `inconclusive` | No resolvable Git baseline, no change, or a required probe is unavailable. |
| `blocked` | A compatibility recommendation prevents the agent from safely proceeding. |

`inconclusive` is an honest result, not a failure disguised as success. It tells an integration to repair its environment or provide more evidence before making a claim.
