# Node.js SDK API reference

Install the Node.js SDK from npm:

```bash
npm install @tensilestream/buildanchor
```

```js
import { BuildAnchorClient } from "@tensilestream/buildanchor";

const client = new BuildAnchorClient({ workspace: "." });
```

The SDK is ESM, requires Node.js 18 or later, and has no runtime dependencies.
All methods return a `Promise` for a BuildAnchor `v1` JSON response.

## Client configuration

```js
new BuildAnchorClient({
  workspace: ".",
  endpoint: undefined,
  token: undefined,
  executable: "buildanchor",
  requestTimeoutMs: 30_000,
  fetch: globalThis.fetch
});
```

Without `endpoint`, the client invokes the local CLI using a fixed argument
array and `shell: false`. With an `endpoint`, it includes `workspace` in every
request; the server must authorize that workspace below its allowed root.

## API

| Method | Parameters | Result |
| --- | --- | --- |
| `llmPrompt` | `objective=""` | Compact agent context. |
| `tokenEstimate` | — | Token-cost guidance. |
| `inspect` | `{ freshness: "cached" \| "refresh" }` | Full evidence report. |
| `context` | `{ tokenBudget }` | Compact repository context. |
| `preflight` | `{ objective, tokenBudget }` | Readiness and compatibility gate. |
| `plan` | `objective`, `{ tokenBudget }` | Ordered implementation plan. |
| `changeImpact` | `{ baseline, staged }` | Git impact report. |
| `validateChange` | `{ baseline, execute, timeoutSeconds, staged }` | Static validation or opt-in probes. |
| `repairGuidance` | `{ baseline, staged }` | Structured repair actions. |
| `compatibility` | — | Compatibility recommendations. |
| `explainDependency` | `dependency` | Declared dependency evidence. |
| `findPackage` | `packageName`, `{ showUsage, installedOnly }` | Package declarations and usage evidence. |
| `modules` | — | Monorepo topology. |
| `resolveCommand` | `phase="test"`, `{ scope, changed }` | Verified build command. |

`validateChange({ execute: true })` can run detected project commands. Keep it
false for normal coding-agent turns and provide a bounded `timeoutSeconds` only
when execution is authorized.

## Typical agent flow

```js
const objective = "Add rate limiting to the API";
const prompt = (await client.llmPrompt(objective)).content;
const preflight = await client.preflight({ objective });

if (!preflight.ready_to_act) {
  throw new Error(JSON.stringify(preflight.recommendations));
}

const plan = await client.plan(objective);
// Perform the planned edits, then run safe static validation.
const validation = await client.validateChange();
```

## Errors

`BuildAnchorClientError` is the base transport error.
`BuildAnchorHTTPError` includes `statusCode` and `response` for endpoint
failures. `BuildAnchorCLIError` includes `exitCode`, `response`, and `stderr`
when local CLI output cannot be handled as a v1 response.

## HTTP mode

```bash
buildanchor serve --workspace /path/to/repository --listen 127.0.0.1:8787
```

```js
const client = new BuildAnchorClient({
  workspace: ".",
  endpoint: "http://127.0.0.1:8787",
  requestTimeoutMs: 10_000
});

const report = await client.inspect({ freshness: "refresh" });
```
