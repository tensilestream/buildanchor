# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .engine import BuildAnchor, BuildAnchorError


# HTTP routes are owned by the HTTP transport. SDKs derive their operation
# paths from this registry instead of maintaining divergent endpoint lists.
HTTP_ENDPOINTS = {
    "/v1/llm-prompt": "build.llm_prompt",
    "/v1/token-estimate": "build.token_estimate",
    "/v1/inspect": "build.inspect",
    "/v1/context": "build.context",
    "/v1/preflight": "build.preflight",
    "/v1/plan": "build.plan",
    "/v1/change-impact": "build.change_impact",
    "/v1/validate-change": "build.validate_change",
    "/v1/repair-guidance": "build.repair_guidance",
    "/v1/compatibility": "build.compatibility",
    "/v1/explain-dependency": "build.explain_dependency",
    "/v1/find-package": "build.find_package",
    "/v1/cmd": "build.cmd",
    "/v1/modules": "build.modules",
}

# ---------------------------------------------------------------------------
# MCP tool registry — descriptions include explicit "when to call" rules
# so agents choose the cheapest tool on the first attempt.
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "build.llm_prompt",
        "description": (
            "CALL THIS FIRST before any build-affecting action. "
            "Returns a compact (<= 400 token) authoritative system prompt block that you should "
            "inject into your context. Covers build system, runtime versions, compatibility "
            "constraints, and validation commands. Costs ~80-250 tokens. "
            "Never call build.inspect when build.llm_prompt is sufficient."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string", "description": "Path to the repository root."},
                "objective": {"type": "string", "description": "What you are about to do (used for mismatch detection)."},
            },
        },
    },
    {
        "name": "build.token_estimate",
        "description": (
            "Returns estimated token cost for every BuildAnchor tool and a recommendation. "
            "Call this if you are unsure which tool is cheapest for your use case. "
            "Has near-zero latency and costs < 5 tokens."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string"},
            },
        },
    },
    {
        "name": "build.context",
        "description": (
            "Returns a compact Build Context Pack with constraints and validation commands. "
            "Use after build.llm_prompt when you need a structured JSON view of constraints. "
            "Includes llm_context (ready-to-inject text) and token_estimate fields."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string"},
                "token_budget": {"type": "integer", "description": "Max tokens for the context pack (default 2500)."},
            },
        },
    },
    {
        "name": "build.preflight",
        "description": (
            "Mandatory pre-change gate. Call before modifying build files, dependency manifests, "
            "or framework configuration. Returns ready_to_act flag, compatibility errors, "
            "and a ready-to-inject llm_prompt. Blocks on hard compatibility errors."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string"},
                "objective": {"type": "string"},
                "token_budget": {"type": "integer"},
            },
        },
    },
    {
        "name": "build.plan",
        "description": (
            "Creates a repository-aware execution plan before the agent edits files. "
            "Returns objective, ecosystem context, ordered steps with gates, compatibility "
            "warnings (including objective-ecosystem mismatch), and a llm_prompt block. "
            "Call before acting when the objective involves build, dependency, or framework changes."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["objective"],
            "properties": {
                "workspace": {"type": "string"},
                "objective": {"type": "string"},
                "token_budget": {"type": "integer"},
            },
        },
    },
    {
        "name": "build.change_impact",
        "description": (
            "Compares the current workspace to a git baseline and identifies which build facts "
            "are affected. Call after making changes and before calling build.validate_change."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string"},
                "baseline": {"type": "string", "description": "Git ref (commit/branch/tag). Default: HEAD."},
                "staged": {"type": "boolean", "description": "Analyze only staged files."},
            },
        },
    },
    {
        "name": "build.validate_change",
        "description": (
            "Validates a change using Git impact analysis. Optionally executes bounded test probes. "
            "Returns passed/failed/inconclusive per probe. Call after build.change_impact confirms "
            "build-affecting files changed. Pass execute=true to run tests."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string"},
                "baseline": {"type": "string"},
                "execute": {"type": "boolean"},
                "timeout": {"type": "integer", "minimum": 1, "maximum": 900},
                "staged": {"type": "boolean", "description": "Validate only staged files."},
            },
        },
    },
    {
        "name": "build.repair_guidance",
        "description": (
            "Returns structured repair steps when build.validate_change reports invalid or "
            "inconclusive. Each issue has a code, message, affected_files, and next_action."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string"},
                "baseline": {"type": "string"},
                "staged": {"type": "boolean", "description": "Use staged changes for repair context."},
            },
        },
    },
    {
        "name": "build.compatibility",
        "description": (
            "Returns package and API compatibility recommendations with affected files and evidence. "
            "Covers Jakarta namespace migrations (Java), Python packaging, Node ESM, Go modules, "
            "and Rust editions. Call before adding or replacing packages."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string"},
            },
        },
    },
    {
        "name": "build.inspect",
        "description": (
            "Full Build Truth report including all evidence, dependencies, and facts. "
            "WARNING: Returns large JSON that is expensive to inject into an LLM context. "
            "Use build.llm_prompt or build.context instead for inference-time context. "
            "Use build.inspect only for deep investigation or when building developer tooling."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string"},
                "freshness": {"type": "string", "enum": ["cached", "refresh"]},
            },
        },
    },
    {
        "name": "build.explain_dependency",
        "description": "Explains a specific dependency found during static inspection.",
        "inputSchema": {
            "type": "object",
            "required": ["dependency"],
            "properties": {
                "workspace": {"type": "string"},
                "dependency": {"type": "string"},
            },
        },
    },
    {
        "name": "build.find_package",
        "description": (
            "Search for a package across all detected ecosystems (Node, Python, Java/Maven, "
            "Go, Rust). Returns installed version, declared version, import patterns used in "
            "the project, and an LLM guidance sentence. Works with any agent — Claude, GPT, "
            "Gemini, Cursor, Aider, Windsurf, Ollama SLMs, or custom frameworks. "
            "Call this before adding or importing a package to avoid duplicates and version conflicts."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["package"],
            "properties": {
                "workspace": {"type": "string", "description": "Path to the repository root."},
                "package": {"type": "string", "description": "Package name to search for (e.g. 'axios', 'fastapi', 'spring-boot-starter-data-jpa')."},
                "show_usage": {"type": "boolean", "description": "Scan source files for import patterns. Default: true."},
                "installed_only": {"type": "boolean", "description": "Only return results if the package is actually installed. Default: false."},
            },
        },
    },
    {
        "name": "build.modules",
        "description": (
            "Discover monorepo topology, subpackages, directories, categories ('ui', 'backend', 'shared'), "
            "and localized test/build commands. Call to inspect multi-package repositories."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string", "description": "Path to the repository root."},
            },
        },
    },
    {
        "name": "build.cmd",
        "description": (
            "Resolve verified build, test, lint, format, or clean shell command for this repository. "
            "Supports monorepo scoping via 'scope' ('ui', 'backend', or package name) and 'changed' (test modified packages only). "
            "Inspects manifest scripts (package.json, pyproject.toml, pom.xml, Makefile, etc.) to eliminate hallucinated commands."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string", "description": "Path to the repository root."},
                "phase": {
                    "type": "string",
                    "description": "Phase to resolve: 'test', 'build', 'lint', 'format', or 'clean'. Default: 'test'.",
                    "enum": ["test", "build", "lint", "format", "clean"],
                },
                "scope": {
                    "type": "string",
                    "description": "Target category ('ui', 'backend', 'shared') or specific package name/path in monorepos.",
                },
                "changed": {
                    "type": "boolean",
                    "description": "Target only packages containing modified files according to git diff.",
                },
            },
        },
    },
    {
        "name": "get_build_truth",
        "description": (
            "Returns a compact (<= 400 token) authoritative build truth summary for the repository. "
            "Covers build system, runtime versions, compatibility constraints, and verified validation commands. "
            "Inject this first before modifying code or running builds."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string", "description": "Path to the repository root."},
                "objective": {"type": "string", "description": "What you are about to do (used for mismatch detection)."},
            },
        },
    },
    {
        "name": "find_package",
        "description": (
            "Search for a package across detected ecosystems (Node, Python, Java/Maven, Go, Rust). "
            "Returns installed version, declared version, import patterns, and guidance."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["package"],
            "properties": {
                "workspace": {"type": "string", "description": "Path to the repository root."},
                "package": {"type": "string", "description": "Package name to search for."},
                "show_usage": {"type": "boolean", "description": "Scan source files for import patterns. Default: true."},
                "installed_only": {"type": "boolean", "description": "Only return results if installed. Default: false."},
            },
        },
    },
    {
        "name": "get_test_command",
        "description": (
            "Resolve verified test command with optional monorepo scoping ('ui', 'backend', or specific package) "
            "and git-diff change detection."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string", "description": "Path to the repository root."},
                "phase": {
                    "type": "string",
                    "description": "Phase to resolve: 'test', 'build', 'lint', 'format', or 'clean'. Default: 'test'.",
                    "enum": ["test", "build", "lint", "format", "clean"],
                },
                "scope": {
                    "type": "string",
                    "description": "Target category ('ui', 'backend', 'shared') or specific package name/path in monorepos.",
                },
                "changed": {
                    "type": "boolean",
                    "description": "Target only packages containing modified files according to git diff.",
                },
            },
        },
    },
]

# MCP prompts — injectable templates surfaced to Claude Desktop / Cursor / Continue.dev
PROMPTS = [
    {
        "name": "buildanchor-preflight",
        "description": "Inject authoritative build context before acting on a repository.",
        "arguments": [
            {"name": "workspace", "description": "Path to the repository root.", "required": False},
            {"name": "objective", "description": "What you are about to do.", "required": False},
        ],
    }
]


def _mcp_version() -> str:
    try:
        from importlib.metadata import version
        return version("buildanchor")
    except Exception:
        return "1.1.6"


def _json_response(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True)


class MCPServer:
    def __init__(self, root: str):
        self.root = BuildAnchor(root)
        # In-process result cache keyed by workspace_digest
        self._cache: dict[str, Any] = {}

    def run(self, stdin, stdout) -> None:
        for line in stdin:
            if not line.strip():
                continue
            try:
                request = json.loads(line)
                response = self.handle(request)
            except (ValueError, json.JSONDecodeError) as exc:
                response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": str(exc)}}
            if response is not None:
                stdout.write(json.dumps(response) + "\n")
                stdout.flush()

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        request_id = request.get("id")
        if method == "notifications/initialized":
            return None
        if method == "initialize":
            return {
                "jsonrpc": "2.0", "id": request_id,
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "prompts": {"listChanged": False},
                        "resources": {"listChanged": False},
                    },
                    "serverInfo": {"name": "buildanchor", "version": _mcp_version()},
                },
            }
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
        if method == "prompts/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"prompts": PROMPTS}}
        if method == "resources/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"resources": []}}
        if method == "resources/templates/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"resourceTemplates": []}}
        if method == "prompts/get":
            params = request.get("params", {})
            name = params.get("name", "")
            args = params.get("arguments", {})
            if name == "buildanchor-preflight":
                engine = self._engine(args.get("workspace"))
                block = engine.llm_prompt(args.get("objective", ""))
                return {
                    "jsonrpc": "2.0", "id": request_id,
                    "result": {
                        "description": "Authoritative build context — inject before acting.",
                        "messages": [{"role": "user", "content": {"type": "text", "text": block.content}}],
                    },
                }
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": f"unknown prompt: {name}"}}
        if method == "tools/call":
            params = request.get("params", {})
            try:
                value = self.call_tool(params.get("name", ""), params.get("arguments", {}))
                return {"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": _json_response(value)}], "structuredContent": value}}
            except BuildAnchorError as exc:
                return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32001, "message": str(exc)}}
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"method not found: {method}"}}

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        engine = self._engine(arguments.get("workspace"))
        freshness = str(arguments.get("freshness", "cached"))
        if freshness not in {"cached", "refresh"}:
            raise BuildAnchorError("freshness must be 'cached' or 'refresh'")
        report = engine.inspect() if freshness == "refresh" else engine._inspect_cached()
        if name in {"build.llm_prompt", "get_build_truth"}:
            block = engine.llm_prompt(str(arguments.get("objective", "")))
            return block.to_dict()
        if name == "build.token_estimate":
            return engine.token_estimate()
        if name == "build.inspect":
            return report.to_dict()
        if name == "build.context":
            return engine.context(report, int(arguments.get("token_budget", 2500))).to_dict()
        if name == "build.preflight":
            return engine.preflight(str(arguments.get("objective", "")), int(arguments.get("token_budget", 2500)), report=report)
        if name == "build.plan":
            return engine.plan(str(arguments.get("objective", "")), int(arguments.get("token_budget", 2500)))
        if name == "build.change_impact":
            return engine.change_impact(
                str(arguments.get("baseline", "HEAD")),
                report,
                staged=bool(arguments.get("staged", False)),
            ).to_dict()
        if name == "build.validate_change":
            return engine.validate_change(
                str(arguments.get("baseline", "HEAD")),
                report,
                execute=bool(arguments.get("execute", False)),
                timeout_seconds=int(arguments.get("timeout", 300)),
                staged=bool(arguments.get("staged", False)),
            )
        if name == "build.repair_guidance":
            return engine.repair_guidance(
                report,
                engine.change_impact(
                    str(arguments.get("baseline", "HEAD")),
                    report,
                    staged=bool(arguments.get("staged", False)),
                ),
            )
        if name == "build.compatibility":
            return {"schema_version": "v1", "session_id": report.session_id, "status": "invalid" if any(r["severity"] == "error" for r in report.recommendations) else "valid", "recommendations": report.recommendations}
        if name == "build.explain_dependency":
            needle = str(arguments.get("dependency", "")).lower()
            matches = [item for item in report.dependencies if needle in str(item.get("coordinate", "")).lower()]
            return {"schema_version": "v1", "session_id": report.session_id, "dependency": arguments.get("dependency"), "matches": matches, "status": "proven" if matches else "unknown"}
        if name in {"build.find_package", "find_package"}:
            pkg = str(arguments.get("package", ""))
            show_usage = bool(arguments.get("show_usage", True))
            installed_only = bool(arguments.get("installed_only", False))
            return engine.find_package(pkg, show_usage=show_usage, installed_only=installed_only)
        if name == "build.modules":
            modules = engine.discover_modules()
            return {
                "schema_version": "v1",
                "session_id": report.session_id,
                "is_monorepo": len(modules) > 1 or (len(modules) == 1 and modules[0].path != "."),
                "modules": [m.to_dict() for m in modules],
            }
        if name in {"build.cmd", "get_test_command"}:
            phase = str(arguments.get("phase", "test"))
            scope = arguments.get("scope")
            changed = bool(arguments.get("changed", False))
            return engine.resolve_command(phase, scope=scope, changed=changed)
        raise BuildAnchorError(f"unknown tool: {name}")

    def _engine(self, workspace: str | None) -> BuildAnchor:
        if not workspace:
            return self.root
        candidate = (self.root.workspace / workspace).resolve() if not workspace.startswith("/") else Path(workspace).expanduser().resolve()
        try:
            candidate.relative_to(self.root.workspace)
        except ValueError as exc:
            raise BuildAnchorError(f"workspace is outside MCP allowed root: {workspace}") from exc
        return BuildAnchor(candidate, self.root.workspace)


class HTTPHandler(BaseHTTPRequestHandler):
    server_version = "BuildAnchor/0.2"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._send(200, {"status": "ok", "service": "buildanchor"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length", "0"))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            result = self.server.buildanchor_mcp.call_tool(self._tool_name(), body)
            self._send(200, result)
        except (ValueError, json.JSONDecodeError, BuildAnchorError) as exc:
            self._send(400, {"status": "blocked", "error": str(exc)})

    def _tool_name(self) -> str:
        return HTTP_ENDPOINTS.get(self.path, "")

    def _send(self, code: int, value: dict[str, Any]) -> None:
        payload = json.dumps(value, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        return


def serve_http(root: str, host: str = "127.0.0.1", port: int = 8787) -> None:
    httpd = ThreadingHTTPServer((host, port), HTTPHandler)
    httpd.buildanchor_mcp = MCPServer(root)
    httpd.serve_forever()
