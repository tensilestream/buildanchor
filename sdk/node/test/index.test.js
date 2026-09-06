import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { BuildAnchorClient, BuildAnchorHTTPError } from "../src/index.js";

const repository = join(dirname(fileURLToPath(import.meta.url)), "../../..");
const executable = process.env.BUILDANCHOR_EXECUTABLE ?? join(repository, ".venv/bin/buildanchor");

// Tracked rather than hardcoded: pinning a literal schema version made this
// suite fail on the v1 -> v2 bump for no reason a reader could act on.
const CURRENT_SCHEMA = "v2";

test("local client returns the current BuildAnchor contracts without a shell", async () => {
  const workspace = mkdtempSync(join(tmpdir(), "buildanchor-node-sdk-"));
  try {
    writeFileSync(join(workspace, "package.json"), JSON.stringify({
      dependencies: { express: "^4.19.0" },
      scripts: { test: "node --test" }
    }));
    const client = new BuildAnchorClient({ workspace, executable });
    assert.equal((await client.inspect()).schema_version, CURRENT_SCHEMA);
    assert.ok((await client.llmPrompt("Add an endpoint")).content);
    assert.equal((await client.findPackage("express")).found, true);

    // The command comes with the directory it runs in and how far it is proven.
    const resolved = await client.resolveCommand("test");
    assert.equal(resolved.command, "npm test");
    assert.equal(resolved.working_directory, ".");
    assert.ok(resolved.command_status);

    assert.equal((await client.compatibility()).status, "valid");
  } finally {
    rmSync(workspace, { recursive: true, force: true });
  }
});

test("modules returns the same envelope the other transports return", async () => {
  const workspace = mkdtempSync(join(tmpdir(), "buildanchor-node-sdk-"));
  try {
    writeFileSync(join(workspace, "package.json"), JSON.stringify({ scripts: { test: "node --test" } }));
    const client = new BuildAnchorClient({ workspace, executable });
    const modules = await client.modules();
    assert.ok(Array.isArray(modules.modules), "modules must be an envelope, not a bare array");
    assert.equal(typeof modules.is_monorepo, "boolean");
    assert.equal(modules.schema_version, CURRENT_SCHEMA);
  } finally {
    rmSync(workspace, { recursive: true, force: true });
  }
});

test("doctor explains the repository", async () => {
  const workspace = mkdtempSync(join(tmpdir(), "buildanchor-node-sdk-"));
  try {
    writeFileSync(join(workspace, "package.json"), JSON.stringify({ scripts: { test: "node --test" } }));
    const client = new BuildAnchorClient({ workspace, executable });
    const diagnosis = await client.diagnose();
    assert.ok(diagnosis.status);
    assert.ok(diagnosis.repository, "doctor should report the repository shape");
  } finally {
    rmSync(workspace, { recursive: true, force: true });
  }
});

test("verify is refused over HTTP because it executes project code", async () => {
  const client = new BuildAnchorClient({ workspace: ".", endpoint: "http://example.invalid" });
  await assert.rejects(() => client.verifyCommands(), /local-only/);
});

test("verify --dry-run executes nothing", async () => {
  const workspace = mkdtempSync(join(tmpdir(), "buildanchor-node-sdk-"));
  try {
    writeFileSync(join(workspace, "package.json"), JSON.stringify({ scripts: { test: "node --test" } }));
    const client = new BuildAnchorClient({ workspace, executable });
    const plan = await client.verifyCommands({ dryRun: true });
    assert.equal(plan.dry_run, true);
    assert.deepEqual(plan.results, []);
  } finally {
    rmSync(workspace, { recursive: true, force: true });
  }
});

test("HTTP client sends the workspace to every route and exposes endpoint failures", async () => {
  const requests = [];
  const fetch = async (url, options) => {
    const payload = JSON.parse(options.body);
    requests.push({ url, payload });
    if (payload.workspace === "..") {
      return { ok: false, status: 400, text: async () => JSON.stringify({ status: "blocked" }) };
    }
    return { ok: true, status: 200, text: async () => JSON.stringify({ schema_version: CURRENT_SCHEMA, status: "valid" }) };
  };
  const client = new BuildAnchorClient({ workspace: ".", endpoint: "http://buildanchor.test", fetch });
  assert.equal((await client.inspect({ freshness: "refresh" })).schema_version, CURRENT_SCHEMA);
  assert.equal((await client.compatibility()).status, "valid");
  assert.equal((await client.validateChange({ staged: true })).schema_version, CURRENT_SCHEMA);
  await assert.rejects(
    () => new BuildAnchorClient({ workspace: "..", endpoint: "http://buildanchor.test", fetch }).inspect(),
    BuildAnchorHTTPError
  );
  assert.ok(requests.every(({ payload }) => "workspace" in payload));
  assert.ok(requests.some(({ url }) => url.endsWith("/v1/compatibility")));
});

test("local SDK output stays machine-readable when the CLI includes terminal branding", () => {
  const output = execFileSync(executable, ["inspect", "--workspace", repository, "--format", "json"], { encoding: "utf8" });
  assert.doesNotThrow(() => JSON.parse(output));
});
