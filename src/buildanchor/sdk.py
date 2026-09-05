# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any

from .engine import BuildAnchor


class BuildAnchorClient:
    """Python SDK client using the local core or a BuildAnchor HTTP endpoint."""

    def __init__(self, workspace: str = ".", endpoint: str | None = None, token: str | None = None, allow_root: str | None = None):
        self.workspace = workspace
        self.endpoint = endpoint.rstrip("/") if endpoint else None
        self.token = token
        self.allow_root = allow_root
        self._engine = None if self.endpoint else BuildAnchor(workspace, allow_root)

    def inspect(self, freshness: str = "cached") -> dict[str, Any]:
        return self._call("inspect", {"freshness": freshness}, lambda: self._engine.inspect().to_dict())

    def context(self, token_budget: int = 2500) -> dict[str, Any]:
        return self._call("context", {"token_budget": token_budget}, lambda: self._engine.context(token_budget=token_budget).to_dict())

    def preflight(self, objective: str = "", token_budget: int = 2500) -> dict[str, Any]:
        """Return authoritative LLM context before the agent changes the workspace."""
        return self._call("preflight", {"objective": objective, "token_budget": token_budget}, lambda: self._engine.preflight(objective, token_budget))

    def plan(self, objective: str, token_budget: int = 2500) -> dict[str, Any]:
        """Create a repository-aware execution plan before the agent acts."""
        return self._call("plan", {"objective": objective, "token_budget": token_budget}, lambda: self._engine.plan(objective, token_budget))

    def change_impact(self, baseline: str = "HEAD") -> dict[str, Any]:
        return self._call("change-impact", {"baseline": baseline}, lambda: self._engine.change_impact(baseline).to_dict())

    def validate_change(self, baseline: str = "HEAD", execute: bool = False, timeout_seconds: int = 300) -> dict[str, Any]:
        payload = {"baseline": baseline, "execute": execute, "timeout": timeout_seconds}
        return self._call("validate-change", payload, lambda: self._engine.validate_change(baseline, execute=execute, timeout_seconds=timeout_seconds))

    def repair_guidance(self, baseline: str = "HEAD") -> dict[str, Any]:
        return self._call("repair-guidance", {"baseline": baseline}, lambda: self._engine.repair_guidance(change=self._engine.change_impact(baseline)))

    def explain_dependency(self, dependency: str) -> dict[str, Any]:
        return self._call("explain-dependency", {"dependency": dependency}, lambda: self._explain_local(dependency))

    def _explain_local(self, dependency: str) -> dict[str, Any]:
        report = self._engine.inspect()
        matches = [item for item in report.dependencies if dependency.lower() in str(item.get("coordinate", "")).lower()]
        return {"schema_version": "v1", "session_id": report.session_id, "dependency": dependency, "matches": matches, "status": "valid" if matches else "unknown"}

    def _call(self, operation: str, payload: dict[str, Any], local) -> dict[str, Any]:
        if not self.endpoint:
            return local()
        path = {"inspect": "/v1/inspect", "context": "/v1/context", "preflight": "/v1/preflight", "plan": "/v1/plan", "change-impact": "/v1/change-impact", "validate-change": "/v1/validate-change", "repair-guidance": "/v1/repair-guidance", "explain-dependency": "/v1/explain-dependency"}[operation]
        request = urllib.request.Request(self.endpoint + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"BuildAnchor HTTP request failed: {exc.code}") from exc


class AsyncBuildAnchorClient:
    def __init__(self, *args, **kwargs):
        self._client = BuildAnchorClient(*args, **kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def inspect(self, freshness: str = "cached"):
        return await asyncio.to_thread(self._client.inspect, freshness)

    async def context(self, token_budget: int = 2500):
        return await asyncio.to_thread(self._client.context, token_budget)

    async def preflight(self, objective: str = "", token_budget: int = 2500):
        return await asyncio.to_thread(self._client.preflight, objective, token_budget)

    async def plan(self, objective: str, token_budget: int = 2500):
        return await asyncio.to_thread(self._client.plan, objective, token_budget)

    async def change_impact(self, baseline: str = "HEAD"):
        return await asyncio.to_thread(self._client.change_impact, baseline)

    async def validate_change(self, baseline: str = "HEAD", execute: bool = False, timeout_seconds: int = 300):
        return await asyncio.to_thread(self._client.validate_change, baseline, execute, timeout_seconds)

    async def repair_guidance(self, baseline: str = "HEAD"):
        return await asyncio.to_thread(self._client.repair_guidance, baseline)

    async def explain_dependency(self, dependency: str):
        return await asyncio.to_thread(self._client.explain_dependency, dependency)
