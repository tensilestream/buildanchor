# BuildAnchor Node.js SDK

`@tensilestream/buildanchor` is the supported Node.js embedding API for coding
agents, extensions, and developer tools. It has no runtime dependencies and
requires Node.js 18 or later.

For the complete method reference, error contract, and HTTP examples, see the
[Node.js SDK API reference](../../docs/sdk/node.md).

```bash
npm install @tensilestream/buildanchor
```

## Recommended agent workflow

```js
import { BuildAnchorClient } from "@tensilestream/buildanchor";

const buildanchor = new BuildAnchorClient({ workspace: "." });
const objective = "Add rate limiting to the API";

// Inject compact verified context before asking the model to edit.
const systemContext = (await buildanchor.llmPrompt(objective)).content;

// Gate dependency/build changes before acting.
const preflight = await buildanchor.preflight({ objective });
if (!preflight.ready_to_act) throw new Error(JSON.stringify(preflight.recommendations));

const plan = await buildanchor.plan(objective);
// Let the agent act using the plan, then validate statically by default.
const validation = await buildanchor.validateChange();
```

In local mode, the client invokes `buildanchor` with a fixed argument array and
`shell: false`; no user value is interpolated into a shell command. The client
returns BuildAnchor's v1 JSON result even when the CLI uses a non-zero exit
code for a domain outcome such as `inconclusive` validation or a package not
being found.

## Operations

All SDK calls are asynchronous and return a Promise for the v1 response.

| Method | Purpose |
| --- | --- |
| `llmPrompt(objective)` | Compact authoritative context; call first. |
| `tokenEstimate()` | Token-cost guidance. |
| `inspect({ freshness })`, `context({ tokenBudget })` | Full or compact Build Truth. |
| `preflight({ objective })`, `plan(objective)` | Pre-change gate and execution plan. |
| `changeImpact({ baseline, staged })` | Git impact analysis. |
| `validateChange({ execute, timeoutSeconds, staged })` | Static or explicit probe validation. |
| `repairGuidance()`, `compatibility()` | Repair and compatibility advice. |
| `findPackage(name)`, `explainDependency(name)` | Dependency evidence and conventions. |
| `modules()`, `resolveCommand(phase, { scope, changed })` | Monorepo topology and verified commands. |

`validateChange({ execute: true })` is deliberate: it permits BuildAnchor to
run detected project commands. Keep `execute` false for normal agent turns,
and set `timeoutSeconds` to a bounded value when execution is authorized.

## Bounded HTTP mode

```js
import { BuildAnchorClient, BuildAnchorHTTPError } from "@tensilestream/buildanchor";

const buildanchor = new BuildAnchorClient({
  workspace: ".",
  endpoint: "http://127.0.0.1:8787",
  requestTimeoutMs: 10_000
});

try {
  const report = await buildanchor.inspect({ freshness: "refresh" });
} catch (error) {
  if (error instanceof BuildAnchorHTTPError) {
    console.error(error.statusCode, error.response);
  }
  throw error;
}
```

Start the service with an explicit workspace bound:

```bash
buildanchor serve --workspace /path/to/repository --listen 127.0.0.1:8787
```

The SDK sends `workspace` on every HTTP request. The server rejects a path
outside its configured root. Handle `BuildAnchorHTTPError` for endpoint errors,
`BuildAnchorCLIError` for an unavailable or malformed local CLI response, and
`BuildAnchorClientError` for other transport failures.

## Development checks

```bash
cd sdk/node
npm test
```
