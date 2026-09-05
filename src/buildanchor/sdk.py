# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""Stable Python SDK for embedding BuildAnchor in coding agents.

The client deliberately exposes the same operation names and v1 response
contracts whether it runs the local engine or talks to a bounded HTTP server.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .engine import BuildAnchor
from .transports import HTTP_ENDPOINTS


class BuildAnchorClientError(RuntimeError):
    """Base error raised when an SDK transport cannot complete an operation."""


class BuildAnchorHTTPError(BuildAnchorClientError):
    """An HTTP BuildAnchor endpoint returned an unsuccessful response."""

    def __init__(self, status_code: int, response: object):
        self.status_code = status_code
        self.response = response
        super().__init__(f"BuildAnchor HTTP request failed with status {status_code}: {response}")


_HTTP_PATHS = {
    tool.removeprefix("build.").replace("_", "-"): path
    for path, tool in HTTP_ENDPOINTS.items()
}


class BuildAnchorClient:
    """Synchronous BuildAnchor client for local or bounded HTTP deployments.

    Local mode is the default and never opens a network connection. Endpoint
    mode sends the configured workspace with every request; it must be inside
    the server's allowed root.
    """

    def __init__(
        self,
        workspace: str | Path = ".",
        endpoint: str | None = None,
        token: str | None = None,
        allow_root: str | Path | None = None,
        *,
        request_timeout_seconds: float = 30.0,
    ) -> None:
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        self.workspace = str(workspace)
        self.endpoint = endpoint.rstrip("/") if endpoint else None
        self.token = token
        self.allow_root = str(allow_root) if allow_root is not None else None
        self.request_timeout_seconds = request_timeout_seconds
        self._engine = None if self.endpoint else BuildAnchor(workspace, allow_root)

    def llm_prompt(self, objective: str = "") -> dict[str, Any]:
        """Return the compact authoritative prompt to inject before an agent acts."""
        return self._call("llm-prompt", {"objective": objective}, lambda: self._engine.llm_prompt(objective).to_dict())

    def token_estimate(self) -> dict[str, Any]:
        """Return token-cost guidance for choosing the smallest sufficient operation."""
        return self._call("token-estimate", {}, lambda: self._engine.token_estimate())

    def inspect(self, freshness: str = "cached") -> dict[str, Any]:
        """Return the full Build Truth report; use ``refresh`` to bypass local cache."""
        if freshness not in {"cached", "refresh"}:
            raise ValueError("freshness must be 'cached' or 'refresh'")
        return self._call(
            "inspect",
            {"freshness": freshness},
            lambda: (self._engine.inspect() if freshness == "refresh" else self._engine._inspect_cached()).to_dict(),
        )

    def context(self, token_budget: int = 2500) -> dict[str, Any]:
        """Return a compact structured context pack for an agent turn."""
        return self._call("context", {"token_budget": token_budget}, lambda: self._engine.context(token_budget=token_budget).to_dict())

    def preflight(self, objective: str = "", token_budget: int = 2500) -> dict[str, Any]:
        """Run the mandatory pre-change compatibility gate."""
        return self._call("preflight", {"objective": objective, "token_budget": token_budget}, lambda: self._engine.preflight(objective, token_budget))

    def plan(self, objective: str, token_budget: int = 2500) -> dict[str, Any]:
        """Create a repository-aware, gated execution plan before edits begin."""
        return self._call("plan", {"objective": objective, "token_budget": token_budget}, lambda: self._engine.plan(objective, token_budget))

    def change_impact(self, baseline: str = "HEAD", staged: bool = False) -> dict[str, Any]:
        """Assess changed files against a Git baseline or the staged index."""
        return self._call(
            "change-impact",
            {"baseline": baseline, "staged": staged},
            lambda: self._engine.change_impact(baseline, staged=staged).to_dict(),
        )

    def validate_change(
        self,
        baseline: str = "HEAD",
        execute: bool = False,
        timeout_seconds: int = 300,
        staged: bool = False,
    ) -> dict[str, Any]:
        """Validate a change; execution is opt-in because it runs project commands."""
        payload = {"baseline": baseline, "execute": execute, "timeout": timeout_seconds, "staged": staged}
        return self._call(
            "validate-change",
            payload,
            lambda: self._engine.validate_change(
                baseline,
                execute=execute,
                timeout_seconds=timeout_seconds,
                staged=staged,
            ),
        )

    def repair_guidance(self, baseline: str = "HEAD", staged: bool = False) -> dict[str, Any]:
        """Return structured next actions for invalid or inconclusive validation."""
        return self._call(
            "repair-guidance",
            {"baseline": baseline, "staged": staged},
            lambda: self._engine.repair_guidance(change=self._engine.change_impact(baseline, staged=staged)),
        )

    def compatibility(self) -> dict[str, Any]:
        """Return compatibility recommendations without a change-validation run."""
        def local() -> dict[str, Any]:
            report = self._engine._inspect_cached()
            return {
                "schema_version": "v1",
                "session_id": report.session_id,
                "status": "invalid" if any(item["severity"] == "error" for item in report.recommendations) else "valid",
                "recommendations": report.recommendations,
            }

        return self._call("compatibility", {}, local)

    def explain_dependency(self, dependency: str) -> dict[str, Any]:
        """Explain declared dependency evidence matching a package or coordinate."""
        return self._call("explain-dependency", {"dependency": dependency}, lambda: self._explain_local(dependency))

    def find_package(self, package: str, *, show_usage: bool = True, installed_only: bool = False) -> dict[str, Any]:
        """Find package declarations, installed evidence, and import conventions."""
        payload = {"package": package, "show_usage": show_usage, "installed_only": installed_only}
        return self._call("find-package", payload, lambda: self._engine.find_package(package, show_usage=show_usage, installed_only=installed_only))

    def modules(self) -> dict[str, Any]:
        """Return discovered monorepo modules and their verified command metadata."""
        def local() -> dict[str, Any]:
            report = self._engine._inspect_cached()
            modules = self._engine.discover_modules()
            return {
                "schema_version": "v1",
                "session_id": report.session_id,
                "is_monorepo": len(modules) > 1 or (len(modules) == 1 and modules[0].path != "."),
                "modules": [module.to_dict() for module in modules],
            }

        return self._call("modules", {}, local)

    def resolve_command(self, phase: str = "test", *, scope: str | None = None, changed: bool = False) -> dict[str, Any]:
        """Resolve the verified command for a build phase and optional monorepo scope."""
        return self._call(
            "cmd",
            {"phase": phase, "scope": scope, "changed": changed},
            lambda: self._engine.resolve_command(phase, scope=scope, changed=changed),
        )

    def _explain_local(self, dependency: str) -> dict[str, Any]:
        report = self._engine._inspect_cached()
        matches = [item for item in report.dependencies if dependency.lower() in str(item.get("coordinate", "")).lower()]
        return {
            "schema_version": "v1",
            "session_id": report.session_id,
            "dependency": dependency,
            "matches": matches,
            "status": "proven" if matches else "unknown",
        }

    def _call(self, operation: str, payload: dict[str, Any], local: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        if not self.endpoint:
            return local()
        request_payload = {"workspace": self.workspace, **payload}
        request = urllib.request.Request(
            self.endpoint + _HTTP_PATHS[operation],
            data=json.dumps(request_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=self.request_timeout_seconds) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                response: object = json.loads(body)
            except json.JSONDecodeError:
                response = body
            raise BuildAnchorHTTPError(exc.code, response) from exc
        except urllib.error.URLError as exc:
            raise BuildAnchorClientError(f"BuildAnchor HTTP request could not be completed: {exc.reason}") from exc


class AsyncBuildAnchorClient:
    """Async facade with the same methods and response contracts as the sync client."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._client = BuildAnchorClient(*args, **kwargs)

    async def __aenter__(self) -> "AsyncBuildAnchorClient":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        return False

    async def llm_prompt(self, objective: str = "") -> dict[str, Any]:
        return await asyncio.to_thread(self._client.llm_prompt, objective)

    async def token_estimate(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._client.token_estimate)

    async def inspect(self, freshness: str = "cached") -> dict[str, Any]:
        return await asyncio.to_thread(self._client.inspect, freshness)

    async def context(self, token_budget: int = 2500) -> dict[str, Any]:
        return await asyncio.to_thread(self._client.context, token_budget)

    async def preflight(self, objective: str = "", token_budget: int = 2500) -> dict[str, Any]:
        return await asyncio.to_thread(self._client.preflight, objective, token_budget)

    async def plan(self, objective: str, token_budget: int = 2500) -> dict[str, Any]:
        return await asyncio.to_thread(self._client.plan, objective, token_budget)

    async def change_impact(self, baseline: str = "HEAD", staged: bool = False) -> dict[str, Any]:
        return await asyncio.to_thread(self._client.change_impact, baseline, staged)

    async def validate_change(
        self,
        baseline: str = "HEAD",
        execute: bool = False,
        timeout_seconds: int = 300,
        staged: bool = False,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._client.validate_change,
            baseline,
            execute=execute,
            timeout_seconds=timeout_seconds,
            staged=staged,
        )

    async def repair_guidance(self, baseline: str = "HEAD", staged: bool = False) -> dict[str, Any]:
        return await asyncio.to_thread(self._client.repair_guidance, baseline, staged)

    async def compatibility(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._client.compatibility)

    async def explain_dependency(self, dependency: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._client.explain_dependency, dependency)

    async def find_package(self, package: str, *, show_usage: bool = True, installed_only: bool = False) -> dict[str, Any]:
        return await asyncio.to_thread(self._client.find_package, package, show_usage=show_usage, installed_only=installed_only)

    async def modules(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._client.modules)

    async def resolve_command(self, phase: str = "test", *, scope: str | None = None, changed: bool = False) -> dict[str, Any]:
        return await asyncio.to_thread(self._client.resolve_command, phase, scope=scope, changed=changed)


__all__ = [
    "AsyncBuildAnchorClient",
    "BuildAnchorClient",
    "BuildAnchorClientError",
    "BuildAnchorHTTPError",
]
