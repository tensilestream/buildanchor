# BuildAnchor Python SDK

For local development:

```bash
uv sync
uv run python -c 'from buildanchor import BuildAnchorClient; print(BuildAnchorClient(".").inspect()["status"])'
```

The Python SDK is included in the main `buildanchor` package:

```python
from buildanchor import BuildAnchorClient

client = BuildAnchorClient(workspace=".")
preflight = client.preflight(objective="Add a JPA entity")
plan = client.plan("Add a JPA entity")
if plan["status"] == "ready":
    # Give plan["agent_context"] and plan["steps"] to the LLM, then let it act.
    result = client.validate_change()
```

Use `AsyncBuildAnchorClient` for async agent orchestration. Set `endpoint="http://127.0.0.1:8787"` to use an HTTP BuildAnchor server. Both transports return the same `v1` report contract.
