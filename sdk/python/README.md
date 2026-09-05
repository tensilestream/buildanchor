# BuildAnchor Python SDK

`BuildAnchorClient` is the supported embedding API for coding agents and
developer tooling. It returns the same versioned (`v1`) dictionary contracts
in local and HTTP modes, with no runtime dependencies beyond Python.

For the complete method reference, error contract, and HTTP examples, see the
[Python SDK API reference](../../docs/sdk/python.md).

## Install and create a client

```bash
pip install buildanchor
```

```python
from buildanchor import BuildAnchorClient

# Local mode is offline and never opens a network connection.
client = BuildAnchorClient(workspace=".")
```

For repository-local development:

```bash
uv sync
uv run python -c 'from buildanchor import BuildAnchorClient; print(BuildAnchorClient(".").inspect()["status"])'
```

## Recommended agent workflow

```python
from buildanchor import BuildAnchorClient

client = BuildAnchorClient(workspace=".")
objective = "Add rate limiting to the API"

# 1. Inject this compact authoritative context before the agent edits files.
system_context = client.llm_prompt(objective)["content"]

# 2. Gate build- and dependency-affecting work.
preflight = client.preflight(objective)
if not preflight["ready_to_act"]:
    raise RuntimeError(preflight["recommendations"])

# 3. Give the agent an evidence-backed plan with validation gates.
plan = client.plan(objective)

# 4. After edits, static validation is safe by default.
result = client.validate_change()
```

`validate_change(execute=True)` is explicit because it can run detected project
test/build commands. Set `timeout_seconds` (1–900) and `staged=True` when
validating only the Git index.

## Available operations

| Method | Use in an agent |
| --- | --- |
| `llm_prompt(objective="")` | Compact system context; call first. |
| `token_estimate()` | Choose the least expensive sufficient operation. |
| `inspect(freshness="cached")` | Full evidence report; avoid injecting it wholesale into an LLM. |
| `context(token_budget=2500)` | Structured compact context. |
| `preflight(objective="", token_budget=2500)` | Pre-change compatibility gate. |
| `plan(objective, token_budget=2500)` | Ordered implementation plan and validation gates. |
| `change_impact(baseline="HEAD", staged=False)` | Git impact analysis. |
| `validate_change(..., execute=False, timeout_seconds=300, staged=False)` | Static or opt-in probe validation. |
| `repair_guidance(baseline="HEAD", staged=False)` | Structured repair actions. |
| `compatibility()` | Package/API compatibility recommendations. |
| `find_package(package, show_usage=True, installed_only=False)` | Existing package versions and import conventions. |
| `modules()` | Monorepo topology and command metadata. |
| `resolve_command(phase="test", scope=None, changed=False)` | Verified command selection. |
| `explain_dependency(dependency)` | Declared dependency evidence. |

## Async use

`AsyncBuildAnchorClient` provides the same methods and response contracts for
async agent orchestrators:

```python
from buildanchor import AsyncBuildAnchorClient

async with AsyncBuildAnchorClient(workspace=".") as client:
    preflight = await client.preflight("Upgrade the HTTP client")
    command = await client.resolve_command("test", changed=True)
```

## Bounded HTTP mode

Start a server with an explicitly bounded root:

```bash
buildanchor serve --workspace /path/to/repository --listen 127.0.0.1:8787
```

Then use the same API:

```python
from buildanchor import BuildAnchorClient, BuildAnchorHTTPError

client = BuildAnchorClient(
    workspace=".",
    endpoint="http://127.0.0.1:8787",
    request_timeout_seconds=10,
)

try:
    report = client.inspect()
except BuildAnchorHTTPError as error:
    print(error.status_code, error.response)
```

The SDK sends `workspace` on every endpoint request. The requested workspace
must be inside the server's configured root; this prevents an agent from using
the remote SDK to inspect files outside its authorized repository.
