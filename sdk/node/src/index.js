// Copyright 2026 Tensilestream and BuildAnchor contributors
// SPDX-License-Identifier: Apache-2.0

/**
 * Dependency-free Node.js SDK for embedding BuildAnchor in coding agents.
 * Every operation returns the same v1 JSON contract in local and HTTP modes.
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
  modules: "/v1/modules"
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
  modules: () => ["modules"]
};

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

  async #call(operation, payload) {
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
    // inconclusive validation or a package not being found. Preserve the v1
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
