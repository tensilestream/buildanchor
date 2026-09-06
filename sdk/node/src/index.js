// Copyright 2026 Tensilestream and BuildAnchor contributors
// SPDX-License-Identifier: Apache-2.0

/**
 * Dependency-free Node.js SDK for embedding BuildAnchor in coding agents.
 * Every operation returns the same JSON contract in local and HTTP modes.
 * The operation set is defined once in buildanchor/operations.py and a
 * conformance test fails when this client does not match it.
 */

import { spawn } from "node:child_process";

const HTTP_PATHS = {
  "llm-prompt": "/v1/llm-prompt",
  "token-estimate": "/v1/token-estimate",
  inspect: "/v1/inspect",
  context: "/v1/context",
  preflight: "/v1/preflight",
  plan: "/v1/plan",
  "change-impact": "/v1/change-impact",
  "validate-change": "/v1/validate-change",
  "repair-guidance": "/v1/repair-guidance",
  compatibility: "/v1/compatibility",
  "explain-dependency": "/v1/explain-dependency",
  "find-package": "/v1/find-package",
  cmd: "/v1/cmd",
  modules: "/v1/modules",
  doctor: "/v1/doctor"
  // `verify` is deliberately absent: it executes project-defined code, which a
  // remote caller cannot consent to. It is available in local mode only.
};

const LOCAL_COMMANDS = {
  "llm-prompt": (payload) => ["llm-prompt", "--objective", payload.objective ?? ""],
  "token-estimate": () => ["token-estimate"],
  inspect: () => ["inspect"],
  context: (payload) => ["context", "--token-budget", String(payload.token_budget ?? 2500)],
  preflight: (payload) => ["preflight", "--objective", payload.objective ?? "", "--token-budget", String(payload.token_budget ?? 2500)],
  plan: (payload) => ["plan", "--objective", payload.objective, "--token-budget", String(payload.token_budget ?? 2500)],
  "change-impact": (payload) => withStaged(["change-impact", "--baseline", payload.baseline ?? "HEAD"], payload.staged),
  "validate-change": (payload) => {
    const args = ["validate-change", "--baseline", payload.baseline ?? "HEAD", "--timeout", String(payload.timeout ?? 300)];
    if (payload.execute) args.push("--execute");
    return withStaged(args, payload.staged);
  },
  "repair-guidance": (payload) => withStaged(["repair", "--baseline", payload.baseline ?? "HEAD"], payload.staged),
  compatibility: () => ["compatibility"],
  "explain-dependency": (payload) => ["explain-dependency", "--dependency", payload.dependency],
  "find-package": (payload) => {
    const args = ["find", "--package", payload.package];
    if (!payload.show_usage) args.push("--no-show-usage");
    if (payload.installed_only) args.push("--installed-only");
    return args;
  },
  cmd: (payload) => {
    const args = ["cmd", payload.phase ?? "test"];
    if (payload.scope) args.push("--scope", payload.scope);
    if (payload.changed) args.push("--changed");
    return args;
  },
  modules: () => ["modules"],
  doctor: (payload) => (payload.path ? ["doctor", payload.path] : ["doctor"]),
  "tool-schemas": () => ["mcp", "--list-tools"],
  "call-tool": (payload) => ["mcp", "--call-tool", payload.name, "--tool-input", JSON.stringify(payload.input ?? {})],
  verify: (payload) => {
    const args = ["verify", "--verify-level", payload.level ?? "collects"];
    if (payload.scope) args.push("--scope", payload.scope);
    if (payload.jobs) args.push("--jobs", String(payload.jobs));
    if (payload.dry_run) args.push("--dry-run");
    return args;
  }
};

/** Operations that must never leave the machine, because they execute code. */
const LOCAL_ONLY = new Set(["verify", "tool-schemas", "call-tool"]);

function withStaged(args, staged) {
  return staged ? [...args, "--staged"] : args;
}

/** Base error for an SDK transport failure. */
export class BuildAnchorClientError extends Error {}

/** Error returned by a bounded BuildAnchor HTTP service. */
export class BuildAnchorHTTPError extends BuildAnchorClientError {
  constructor(statusCode, response) {
    super(`BuildAnchor HTTP request failed with status ${statusCode}: ${JSON.stringify(response)}`);
    this.name = "BuildAnchorHTTPError";
    this.statusCode = statusCode;
    this.response = response;
  }
}

/** Error returned when the local BuildAnchor CLI cannot produce a v1 response. */
export class BuildAnchorCLIError extends BuildAnchorClientError {
  constructor(exitCode, response, stderr) {
    super(`BuildAnchor CLI failed with exit code ${exitCode}: ${stderr || JSON.stringify(response)}`);
    this.name = "BuildAnchorCLIError";
    this.exitCode = exitCode;
    this.response = response;
    this.stderr = stderr;
  }
}

/**
 * Async Node client for local CLI or bounded HTTP BuildAnchor deployments.
 *
 * Local mode invokes the executable with a fixed argument array and
 * `shell: false`. HTTP mode includes the configured workspace on every call.
 */
export class BuildAnchorClient {
  constructor({
    workspace = ".",
    endpoint,
    token,
    executable = "buildanchor",
    requestTimeoutMs = 30_000,
    fetch: fetchImpl = globalThis.fetch
  } = {}) {
    if (!Number.isFinite(requestTimeoutMs) || requestTimeoutMs <= 0) {
      throw new TypeError("requestTimeoutMs must be a positive number");
    }
    if (endpoint && typeof fetchImpl !== "function") {
      throw new BuildAnchorClientError("HTTP mode requires a Fetch-compatible implementation");
    }
    this.workspace = String(workspace);
    this.endpoint = endpoint?.replace(/\/$/, "");
    this.token = token;
    this.executable = executable;
    this.requestTimeoutMs = requestTimeoutMs;
    this.fetch = fetchImpl;
  }

  llmPrompt(objective = "") { return this.#call("llm-prompt", { objective }); }
  tokenEstimate() { return this.#call("token-estimate", {}); }
  inspect({ freshness = "cached" } = {}) {
    if (!new Set(["cached", "refresh"]).has(freshness)) throw new TypeError("freshness must be 'cached' or 'refresh'");
    return this.#call("inspect", { freshness });
  }
  context({ tokenBudget = 2500 } = {}) { return this.#call("context", { token_budget: tokenBudget }); }
  preflight({ objective = "", tokenBudget = 2500 } = {}) { return this.#call("preflight", { objective, token_budget: tokenBudget }); }
  plan(objective, { tokenBudget = 2500 } = {}) {
    if (!objective?.trim()) throw new TypeError("objective is required");
    return this.#call("plan", { objective, token_budget: tokenBudget });
  }
  changeImpact({ baseline = "HEAD", staged = false } = {}) { return this.#call("change-impact", { baseline, staged }); }
  validateChange({ baseline = "HEAD", execute = false, timeoutSeconds = 300, staged = false } = {}) {
    return this.#call("validate-change", { baseline, execute, timeout: timeoutSeconds, staged });
  }
  repairGuidance({ baseline = "HEAD", staged = false } = {}) { return this.#call("repair-guidance", { baseline, staged }); }
  compatibility() { return this.#call("compatibility", {}); }
  explainDependency(dependency) {
    if (!dependency?.trim()) throw new TypeError("dependency is required");
    return this.#call("explain-dependency", { dependency });
  }
  findPackage(packageName, { showUsage = true, installedOnly = false } = {}) {
    if (!packageName?.trim()) throw new TypeError("packageName is required");
    return this.#call("find-package", { package: packageName, show_usage: showUsage, installed_only: installedOnly });
  }
  modules() { return this.#call("modules", {}); }
  resolveCommand(phase = "test", { scope, changed = false } = {}) { return this.#call("cmd", { phase, scope, changed }); }

  /** Explain the repository, or why one directory is not reported as a module. */
  diagnose(path) { return this.#call("doctor", { path }); }

  /** The MCP tool schemas, for building an agent on top of BuildAnchor. */
  async toolSchemas() {
    const listed = await this.#callLocal("tool-schemas", {});
    return Array.isArray(listed) ? listed : (listed.tools ?? []);
  }

  /** Call one MCP tool by name. Used by the agent helpers below. */
  callTool(name, input = {}) {
    return this.#callLocal("call-tool", { name, input });
  }

  /**
   * Execute a discovery probe per module and record which commands genuinely
   * run. Local mode only: this executes project-defined code, which a remote
   * caller cannot consent to.
   */
  verifyCommands({ level = "collects", scope, jobs, dryRun = false } = {}) {
    return this.#call("verify", { level, scope, jobs, dry_run: dryRun });
  }

  async #call(operation, payload) {
    if (this.endpoint && LOCAL_ONLY.has(operation)) {
      throw new BuildAnchorClientError(
        `${operation} is local-only: it executes project-defined code, which a remote ` +
        "caller cannot consent to. Construct the client without an endpoint."
      );
    }
    return this.endpoint ? this.#callHttp(operation, payload) : this.#callLocal(operation, payload);
  }

  async #callHttp(operation, payload) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.requestTimeoutMs);
    try {
      const response = await this.fetch(this.endpoint + HTTP_PATHS[operation], {
        method: "POST",
        headers: {
          "content-type": "application/json",
          ...(this.token ? { authorization: `Bearer ${this.token}` } : {})
        },
        body: JSON.stringify({ workspace: this.workspace, ...payload }),
        signal: controller.signal
      });
      const body = await response.text();
      const value = parseJson(body, "HTTP response");
      if (!response.ok) throw new BuildAnchorHTTPError(response.status, value);
      return value;
    } catch (error) {
      if (error instanceof BuildAnchorHTTPError || error instanceof BuildAnchorClientError) throw error;
      if (error?.name === "AbortError") throw new BuildAnchorClientError(`BuildAnchor HTTP request timed out after ${this.requestTimeoutMs}ms`);
      throw new BuildAnchorClientError(`BuildAnchor HTTP request could not be completed: ${error.message}`);
    } finally {
      clearTimeout(timeout);
    }
  }

  async #callLocal(operation, payload) {
    const args = [...LOCAL_COMMANDS[operation](payload), "--workspace", this.workspace, "--format", "json"];
    const result = await run(this.executable, args);
    const value = tryParseJson(result.stdout);
    // BuildAnchor uses non-zero exit statuses for valid domain outcomes such as
    // inconclusive validation or a package not being found. Preserve the
    // response whenever stdout contains one.
    if (value) return value;
    throw new BuildAnchorCLIError(result.code, tryParseJson(result.stderr), result.stderr.trim());
  }
}

function run(command, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { shell: false, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    const append = (current, chunk) => {
      const value = current + chunk;
      if (value.length > 10_000_000) {
        child.kill();
        reject(new BuildAnchorClientError("BuildAnchor CLI output exceeded the 10MB SDK safety limit"));
      }
      return value;
    };
    child.stdout.on("data", (chunk) => { stdout = append(stdout, chunk); });
    child.stderr.on("data", (chunk) => { stderr = append(stderr, chunk); });
    child.once("error", (error) => reject(new BuildAnchorClientError(`BuildAnchor CLI could not start: ${error.message}`)));
    child.once("close", (code) => resolve({ code: code ?? 1, stdout, stderr }));
  });
}

function parseJson(value, source) {
  const parsed = tryParseJson(value);
  if (parsed === undefined) throw new BuildAnchorClientError(`BuildAnchor ${source} was not valid JSON`);
  return parsed;
}

function tryParseJson(value) {
  try { return JSON.parse(value); } catch { return undefined; }
}

/**
 * Tool definitions for the Messages API, so you can wire BuildAnchor into an
 * agent you are building rather than one that speaks MCP.
 *
 * The schemas come from the same place the MCP server advertises, so an agent
 * built this way and an agent using the MCP server see the identical surface.
 *
 *   import Anthropic from "@anthropic-ai/sdk";
 *   import { toolDefinitions, runTool, toolResultBlock } from "@tensilestream/buildanchor";
 *
 *   const response = await client.messages.create({
 *     model: "claude-opus-5",
 *     max_tokens: 16000,
 *     thinking: { type: "adaptive" },
 *     tools: await toolDefinitions(),
 *     messages: [{ role: "user", content: "Run this project's tests." }]
 *   });
 */
export async function toolDefinitions({ workspace = ".", executable } = {}) {
  const client = new BuildAnchorClient({ workspace, ...(executable ? { executable } : {}) });
  const listed = await client.toolSchemas();
  return listed.map((tool) => ({
    name: tool.name,
    description: tool.description,
    // MCP names this `inputSchema`; the Messages API names it `input_schema`.
    input_schema: tool.inputSchema ?? { type: "object", properties: {} }
  }));
}

/**
 * Execute one tool call. Errors are returned rather than thrown, shaped so they
 * can go straight back to the model — a model told what went wrong can correct
 * itself; an exception in your process cannot.
 */
export async function runTool(name, input = {}, { workspace = ".", executable } = {}) {
  const client = new BuildAnchorClient({ workspace, ...(executable ? { executable } : {}) });
  try {
    return await client.callTool(name, input);
  } catch (error) {
    return { error: String(error?.message ?? error), tool: name };
  }
}

/**
 * Wrap a result as a tool_result block. Return every block from one turn in a
 * single user message: splitting them teaches the model to stop making
 * parallel calls.
 */
export function toolResultBlock(toolUseId, result) {
  return {
    type: "tool_result",
    tool_use_id: toolUseId,
    content: JSON.stringify(result, null, 2),
    is_error: Boolean(result && result.error)
  };
}
