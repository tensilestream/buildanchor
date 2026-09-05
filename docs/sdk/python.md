# Python SDK API reference

Install the Python SDK from PyPI:

```bash
pip install buildanchor
```

```python
from buildanchor import BuildAnchorClient

client = BuildAnchorClient(workspace=".")
```

`BuildAnchorClient` is synchronous. Every method returns a BuildAnchor `v1`
dictionary. Local mode is the default and is offline. Set `endpoint` to use a
bounded BuildAnchor HTTP server instead.

## Client configuration

```python
BuildAnchorClient(
    workspace=".",
    endpoint=None,
    token=None,
    allow_root=None,
    request_timeout_seconds=30.0,
)
```

`workspace` is the repository to inspect. In HTTP mode it is included in every
request and must be within the server's configured allowed root. `token`, when
provided, is sent as a Bearer token. `allow_root` is used only in local mode.

## API

| Method | Parameters | Result |
| --- | --- | --- |
| `llm_prompt` | `objective=""` | Compact, authoritative prompt content for an agent turn. |
| `token_estimate` | — | Token-cost guidance. |
| `inspect` | `freshness="cached"` | Full evidence report; use `"refresh"` to bypass cache. |
| `context` | `token_budget=2500` | Compact structured repository context. |
| `preflight` | `objective=""`, `token_budget=2500` | Compatibility and readiness gate. |
| `plan` | `objective`, `token_budget=2500` | Ordered implementation plan and validation gates. |
| `change_impact` | `baseline="HEAD"`, `staged=False` | Git change-impact report. |
| `validate_change` | `baseline="HEAD"`, `execute=False`, `timeout_seconds=300`, `staged=False` | Static validation, or explicit build/test probes. |
| `repair_guidance` | `baseline="HEAD"`, `staged=False` | Repair actions for invalid or inconclusive changes. |
| `compatibility` | — | Dependency and API compatibility recommendations. |
| `explain_dependency` | `dependency` | Matching declared-dependency evidence. |
| `find_package` | `package`, `show_usage=True`, `installed_only=False` | Package declarations, installed evidence, and usage conventions. |
| `modules` | — | Monorepo modules and verified command metadata. |
| `resolve_command` | `phase="test"`, `scope=None`, `changed=False` | Verified command for a build phase. |

`validate_change` never executes detected project commands unless `execute=True`.
When execution is enabled, use a bounded `timeout_seconds` value (1–900).

## Typical agent flow

```python
objective = "Add rate limiting to the API"
prompt = client.llm_prompt(objective)["content"]
preflight = client.preflight(objective)

if not preflight["ready_to_act"]:
    raise RuntimeError(preflight["recommendations"])

plan = client.plan(objective)
# Perform the planned edits, then validate without executing project commands.
validation = client.validate_change()
```

## Async API

`AsyncBuildAnchorClient` offers the same methods and parameters as
`BuildAnchorClient`, returning awaitable results:

```python
from buildanchor import AsyncBuildAnchorClient

async with AsyncBuildAnchorClient(workspace=".") as client:
    plan = await client.plan("Add rate limiting")
    validation = await client.validate_change(staged=True)
```

## Errors

`BuildAnchorClientError` is the base transport error. `BuildAnchorHTTPError`
adds `status_code` and the decoded `response` returned by a failed endpoint.

## HTTP mode

```bash
buildanchor serve --workspace /path/to/repository --listen 127.0.0.1:8787
```

```python
client = BuildAnchorClient(
    workspace=".", endpoint="http://127.0.0.1:8787", request_timeout_seconds=10
)
report = client.inspect(freshness="refresh")
```
