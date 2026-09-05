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

test("local client returns BuildAnchor v1 contracts without a shell", async () => {
  const workspace = mkdtempSync(join(tmpdir(), "buildanchor-node-sdk-"));
  try {
    writeFileSync(join(workspace, "package.json"), JSON.stringify({
      dependencies: { express: "^4.19.0" },
      scripts: { test: "node --test" }
    }));
    const client = new BuildAnchorClient({ workspace, executable });
    assert.equal((await client.inspect()).schema_version, "v1");
    assert.ok((await client.llmPrompt("Add an endpoint")).content);
    assert.equal((await client.findPackage("express")).found, true);
    assert.equal((await client.resolveCommand("test")).command, "npm run test");
    assert.equal((await client.compatibility()).status, "valid");
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
    return { ok: true, status: 200, text: async () => JSON.stringify({ schema_version: "v1", status: "valid" }) };
  };
  const client = new BuildAnchorClient({ workspace: ".", endpoint: "http://buildanchor.test", fetch });
  assert.equal((await client.inspect({ freshness: "refresh" })).schema_version, "v1");
  assert.equal((await client.compatibility()).status, "valid");
  assert.equal((await client.validateChange({ staged: true })).schema_version, "v1");
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
