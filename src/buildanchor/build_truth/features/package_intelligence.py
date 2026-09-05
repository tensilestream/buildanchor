# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import re

from ..adapters import adapter_for
from ..core.token_estimation import count_tokens
from ..core.errors import BuildAnchorError


class PackageIntelligenceMixin:
    def find_package(
        self,
        package: str,
        show_usage: bool = True,
        installed_only: bool = False,
    ) -> dict:
        """Search for a package across all ecosystems in the workspace.

        Returns authoritative LLM guidance: installed version, declared
        version, import pattern used in this project, and a single
        guidance sentence telling the agent what to do next.
        """
        if not package or not package.strip():
            raise BuildAnchorError("package name must not be empty")
        name = package.strip()
        report = self._inspect_cached()
        results = []

        for system in report.build_systems:
            adapter = adapter_for(system)
            if adapter:
                results.extend(adapter.find_package(self, name, show_usage))

        if installed_only:
            results = [r for r in results if r.get("installed")]

        found = bool(results)
        guidance = self._package_guidance(name, results, report)
        llm_block = self._package_llm_block(name, results, guidance, report)
        tokens = count_tokens(llm_block)

        return {
            "schema_version": "v1",
            "session_id": report.session_id,
            "package": name,
            "found": found,
            "results": results,
            "guidance": guidance,
            "llm_context": llm_block,
            "token_estimate": tokens,
        }

    def _grep_usage(self, name: str, extensions: set[str]) -> list[dict]:
        hits = []
        ignored = {"node_modules", ".venv", "venv", "target", "build", "dist", "__pycache__"}
        pattern = re.compile(re.escape(name), re.I)
        for path in self.workspace.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in extensions:
                continue
            if any(p in ignored for p in path.parts):
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                if pattern.search(line) and ("import" in line or "require" in line or "use " in line):
                    hits.append({
                        "file": str(path.relative_to(self.workspace)),
                        "line": i,
                        "text": line.strip()[:120],
                    })
                    if len(hits) >= 10:
                        return hits
        return hits

    def _package_guidance(self, name: str, results: list[dict], report) -> str:
        if not results:
            systems = ", ".join(report.build_systems) or "unknown"
            return f"{name} was not found in any detected build system ({systems}). Add it as a dependency before using it."
        r = results[0]
        eco = r.get("ecosystem", "")
        installed = r.get("installed")
        declared = r.get("declared_version")
        iv = r.get("installed_version")
        usage = r.get("usage", [])

        if installed and declared:
            pat = r.get("import_patterns", [])
            pattern_hint = f" Use: {pat[0]}" if pat else ""
            usage_hint = f" Already imported in {len(usage)} file(s) — match the existing pattern." if usage else ""
            return f"{name} is installed (v{iv}) and declared ({declared}).{pattern_hint}{usage_hint} Do not add it again."
        if declared and not installed:
            return f"{name} is declared ({declared}) but not installed. Run the install command for this ecosystem before using it."
        if installed and not declared:
            return f"{name} v{iv} is present in {eco} but not declared in the manifest. Add it as an explicit dependency."
        return f"{name} is not installed or declared. Add it as a dependency first."

    def _package_llm_block(self, name: str, results: list[dict], guidance: str, report) -> str:
        lines = [f"# BuildAnchor package: {name}"]
        if not results:
            lines.append(f"Status: NOT FOUND in {', '.join(report.build_systems) or 'unknown'}")
            lines.append(f"Action: {guidance}")
            return "\n".join(lines)
        for r in results:
            eco = r.get("ecosystem", "?")
            lines.append(f"Ecosystem: {eco}")
            if r.get("installed"):
                lines.append(f"Status: installed  version={r.get('installed_version')}")
            else:
                lines.append("Status: NOT installed")
            if r.get("declared_version"):
                scope = r.get("declared_scope") or r.get("declared_file") or "manifest"
                lines.append(f"Declared: {r['declared_version']} [{scope}]")
            if r.get("install_path"):
                lines.append(f"Location: {r['install_path']}")
            pats = r.get("import_patterns", [])
            if pats:
                lines.append("Import pattern:")
                for p in pats[:2]:
                    lines.append(f"  {p}")
            usage = r.get("usage", [])
            if usage:
                lines.append(f"Used in {len(usage)} file(s):")
                for u in usage[:3]:
                    lines.append(f"  {u['file']}:{u['line']}  {u['text']}")
        lines.append(f"LLM guidance: {guidance}")
        return "\n".join(lines)
