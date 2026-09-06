# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""Command verification — turning a declared command into a checked one.

Static inspection can show that a command was declared. It cannot show that the
command runs: a project's tests may fail to import, its runner may not be
installed, its suite may collect nothing at all. This module closes that gap by
climbing the ladder in ``core.verification_levels`` one rung at a time, in the
module's own working directory, and recording the result against a digest of the
files that determined it.

Verification executes project code and is therefore always opt-in. It is not
reachable from the MCP transport, whose default posture stays static.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core import toolchain
from ..core.errors import BuildAnchorError
from ..core.verification_levels import LEVELS, at_least, rank

CACHE_RELATIVE_PATH = Path(".buildanchor") / "verified.json"
CACHE_SCHEMA = "v1"

#: The cache is designed to be committed. It records which commands are proven
#: and for which manifest digest — a fact about the repository, not about the
#: machine that happened to run the probe. Committing it means a developer who
#: clones already knows, and CI, which runs the real suite on every push, stops
#: throwing its evidence away. Nothing in it is machine-specific: no absolute
#: paths, no hostnames, nothing about the machine that ran the probe, and a
#: re-run that changes nothing produces no diff — wherever it runs.

# A probe should be cheap. Anything slower than this is a sign that the "cheap"
# probe is really running the suite, so the ceiling is deliberately low.
DEFAULT_PROBE_TIMEOUT = 120
DEFAULT_FULL_TIMEOUT = 900
OUTPUT_TAIL_LIMIT = 2000


def _without_timestamp(entry: dict[str, Any]) -> dict[str, Any]:
    """Compare cache entries by result, ignoring when they were recorded."""
    return {key: value for key, value in entry.items() if key != "verified_at"}


class VerificationMixin:
    # -- cache ------------------------------------------------------------

    def _verification_cache_path(self) -> Path:
        return self.workspace / CACHE_RELATIVE_PATH

    def _load_verification_cache(self) -> dict[str, Any]:
        path = self._verification_cache_path()
        if not path.is_file():
            return {"schema_version": CACHE_SCHEMA, "entries": {}}
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": CACHE_SCHEMA, "entries": {}}
        if not isinstance(data, dict) or data.get("schema_version") != CACHE_SCHEMA:
            return {"schema_version": CACHE_SCHEMA, "entries": {}}
        entries = data.get("entries")
        return {"schema_version": CACHE_SCHEMA, "entries": entries if isinstance(entries, dict) else {}}

    def _save_verification_cache(self, cache: dict[str, Any]) -> None:
        path = self._verification_cache_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cache, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        except OSError as exc:
            raise BuildAnchorError(f"could not write verification cache: {exc}") from exc

    def _manifest_digest(self, directory: Path) -> str:
        """Digest the files that decide how this module builds and tests.

        A verification result is valid exactly as long as this digest holds. It
        deliberately ignores source files: editing a test does not invalidate
        the knowledge that the runner resolves and the suite collects.
        """
        digest = hashlib.sha256()
        for name in toolchain.MANIFEST_FILES:
            candidate = directory / name
            if candidate.is_file():
                digest.update(name.encode())
                digest.update(candidate.read_bytes())
        return f"sha256:{digest.hexdigest()}"

    def _apply_verification_cache(self, modules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Stamp each module with any still-valid verification result.

        Called on every ``inspect``. One ``buildanchor verify`` run therefore
        upgrades the status reported by every later ``inspect``, ``modules`` and
        ``cmd`` call, at no execution cost, until a manifest changes.
        """
        cache = self._load_verification_cache()
        if not cache["entries"]:
            return modules
        for module in modules:
            entry = cache["entries"].get(self._cache_key(module.get("path", "."), "test"))
            if not entry:
                continue
            directory = self.workspace / module.get("working_directory", ".")
            if entry.get("manifest_digest") != self._manifest_digest(directory):
                # Stale: the toolchain changed under the recorded result. Say
                # nothing rather than reporting a result that no longer holds.
                continue
            if entry.get("command") != module.get("test_command"):
                continue
            module["test_command_status"] = entry.get("level_reached", "declared")
            module["test_command_outcome"] = entry.get("outcome", entry.get("level_reached", "declared"))
            module["test_command_duration_ms"] = entry.get("full_run_duration_ms")
            module["verified_at"] = entry.get("verified_at")
        return modules

    @staticmethod
    def _default_jobs() -> int:
        """Bounded concurrency: enough to hide latency, not enough to thrash."""
        return max(1, min(8, (os.cpu_count() or 2)))

    @staticmethod
    def _cache_key(module_path: str, phase: str) -> str:
        return f"{module_path}::{phase}"

    def _verified_status_for(self, module_path: str, phase: str, command: str | None) -> str:
        """Return the recorded rung for a command, or ``declared`` if unproven."""
        if phase != "test" or not command:
            return "declared"
        entry = self._load_verification_cache()["entries"].get(self._cache_key(module_path, "test"))
        if not entry or entry.get("command") != command:
            return "declared"
        directory = self.workspace / entry.get("working_directory", ".")
        if entry.get("manifest_digest") != self._manifest_digest(directory):
            return "declared"
        return entry.get("level_reached", "declared")

    # -- execution --------------------------------------------------------

    def _run_probe(self, argv: list[str], directory: Path, timeout_seconds: int) -> dict[str, Any]:
        """Run one probe with a fixed argument vector and no shell."""
        self._assert_inside(directory.resolve())
        started = time.monotonic()
        try:
            completed = subprocess.run(
                argv, cwd=directory, capture_output=True, text=True,
                timeout=max(1, timeout_seconds), check=False, shell=False,
            )
        except FileNotFoundError:
            return {"exit_code": None, "duration_ms": 0, "output_tail": "", "error": f"{argv[0]}: not found"}
        except subprocess.TimeoutExpired:
            elapsed = int((time.monotonic() - started) * 1000)
            return {"exit_code": None, "duration_ms": elapsed, "output_tail": "", "error": f"timed out after {timeout_seconds}s"}
        except OSError as exc:
            return {"exit_code": None, "duration_ms": 0, "output_tail": "", "error": str(exc)}
        elapsed = int((time.monotonic() - started) * 1000)
        tail = ((completed.stdout or "") + (completed.stderr or ""))[-OUTPUT_TAIL_LIMIT:]
        return {"exit_code": completed.returncode, "duration_ms": elapsed, "output_tail": tail, "error": None}

    def _collect_probe_for(self, module: dict[str, Any], directory: Path, argv: list[str]) -> tuple[list[str] | None, str]:
        """Return the discovery-only probe for a module, or ``None`` with a reason.

        ``argv`` is the command under verification. The probe is derived from it
        wherever possible, so that the rung proves *that* command rather than a
        separately resolved one that may differ.
        """
        ecosystem = module.get("ecosystem", "generic")
        if ecosystem == "python":
            return toolchain.python_collect_probe_for(argv)
        if ecosystem == "node":
            manifest = directory / "package.json"
            if not manifest.is_file():
                return None, "package.json is not readable"
            try:
                scripts = json.loads(self._read(manifest)).get("scripts", {})
            except (json.JSONDecodeError, OSError):
                return None, "package.json is not valid JSON"
            body = str(scripts.get("test", ""))
            if not body:
                return None, "no test script is declared"
            return toolchain.node_collect_probe(directory, body)
        probe = toolchain.COMPILED_COLLECT_PROBES.get(ecosystem)
        if probe is None:
            return None, f"no discovery-only probe is defined for the {ecosystem} ecosystem"
        return toolchain.wrapper_aware(ecosystem, probe, directory, self.workspace), f"{ecosystem} compile-only probe"

    # -- public API -------------------------------------------------------

    def verify_commands(
        self,
        level: str = "collects",
        scope: str | None = None,
        timeout_seconds: int | None = None,
        use_cache: bool = True,
        write_cache: bool = True,
        jobs: int | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Climb the verification ladder for each module's test command.

        Returns one result per module recording the highest rung reached, why it
        stopped there, and the manifest digest the result is valid for. Rungs
        are attempted in order and the climb stops at the first failure, so a
        module that cannot resolve its runner is never asked to run its suite.

        Modules are verified concurrently; ``jobs`` bounds that, defaulting to
        the machine's parallelism capped at 8.
        """
        if level not in LEVELS:
            raise BuildAnchorError(f"level must be one of {', '.join(LEVELS)}; got '{level}'")

        report = self._inspect_cached()
        modules = list(report.module_details)

        # `module_details` holds sub-projects, so in a repository whose root is
        # itself a project the most important command — the root one — was never
        # verified. Add it explicitly whenever the root is a project.
        shape = (report.repository or {}).get("shape")
        if modules and shape in ("single-project", "root-plus-satellites"):
            root_resolved = self.resolve_command("test", report=report) or {}
            root_command = root_resolved.get("command")
            if root_command and root_resolved.get("working_directory", ".") == ".":
                modules.insert(0, {
                    "name": self.workspace.name or ".",
                    "path": ".",
                    "ecosystem": root_resolved.get("ecosystem") or "generic",
                    "category": "unknown",
                    "working_directory": ".",
                    "test_command": root_command,
                    "test_command_shell": root_command,
                    "test_command_status": "declared",
                })

        if not modules:
            # Single-project repository. Take both the command and its ecosystem
            # from the resolver: `build_systems` is ordered by marker precedence,
            # not by what actually runs the tests here.
            resolved = self.resolve_command("test") or {}
            command = resolved.get("command")
            modules = [{
                "name": self.workspace.name,
                "path": ".",
                "ecosystem": resolved.get("ecosystem") or "generic",
                "category": "shared",
                "working_directory": ".",
                "test_command": command,
                "test_command_shell": command,
                "test_command_status": "declared",
            }]
        if scope:
            needle = scope.strip().lower()
            modules = [m for m in modules if needle in m.get("name", "").lower() or needle in m.get("path", "").lower()]
            if not modules:
                raise BuildAnchorError(f"no module matched scope '{scope}'")

        if dry_run:
            # "What will this run on my machine?" should be answerable without
            # running it. Verification executes project-defined code, so the
            # honest thing is to show the exact argument vectors first.
            return self._verification_plan(modules, level)

        cache = self._load_verification_cache()
        active_cache = cache if use_cache else {"entries": {}}

        # Probes are independent subprocesses that spend their time waiting, so
        # a monorepo should pay for its slowest module rather than the sum of
        # all of them. Order is preserved regardless of completion order.
        workers = max(1, min(jobs or self._default_jobs(), len(modules)))
        if workers == 1 or len(modules) == 1:
            results = [
                self._verify_module(module, level, timeout_seconds, active_cache)
                for module in modules
            ]
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(
                    lambda module: self._verify_module(module, level, timeout_seconds, active_cache),
                    modules,
                ))

        if write_cache:
            for result in results:
                if result["level_reached"] == "declared" and result["outcome"] == "skipped":
                    continue
                key = self._cache_key(result["path"], "test")
                entry = {
                    "command": result["command"],
                    "working_directory": result["working_directory"],
                    "level_reached": result["level_reached"],
                    "outcome": result["outcome"],
                    "manifest_digest": result["manifest_digest"],
                    "full_run_duration_ms": result["full_run_duration_ms"],
                    # Deliberately no record of which machine ran the probe. The
                    # manifest digest is what makes the result true, and a field
                    # that flips between "local" and "ci" would rewrite the file
                    # on every run — churn is how a committed file gets ignored.
                    "verified_at": result["verified_at"],
                }
                # The file is meant to be committed, so re-running with no
                # change must produce no diff. Only a changed result earns a
                # new timestamp.
                previous = cache["entries"].get(key)
                if previous and _without_timestamp(previous) == _without_timestamp(entry):
                    entry["verified_at"] = previous.get("verified_at", entry["verified_at"])
                cache["entries"][key] = entry

            known = {self._cache_key(module.get("path", "."), "test") for module in modules}
            if not scope:
                # A scoped run only knows about its own modules, so it must not
                # prune the rest. A full run can: an entry naming a module that
                # no longer exists is dead weight in a file people review.
                live = {self._cache_key(detail.get("path", "."), "test") for detail in report.module_details}
                live |= known
                for stale_key in [key for key in cache["entries"] if key not in live]:
                    del cache["entries"][stale_key]
            self._save_verification_cache(cache)

        proven = sum(1 for r in results if at_least(r["level_reached"], "collects"))
        executed = [r for r in results if not r.get("cached") and r.get("duration_ms") is not None]
        return {
            "schema_version": "v1",
            "session_id": report.session_id,
            "workspace": str(self.workspace),
            "requested_level": level,
            "modules_verified": len(results),
            "modules_at_collects_or_better": proven,
            "status": "valid" if results and all(r["outcome"] != "failed" for r in results) else ("invalid" if any(r["outcome"] == "failed" for r in results) else "inconclusive"),
            "results": results,
            "cache_path": str(CACHE_RELATIVE_PATH),
            "workers": workers,
            "wall_clock_saved_ms": max(
                0, sum(r["duration_ms"] for r in executed) - max((r["duration_ms"] for r in executed), default=0)
            ) if workers > 1 else 0,
            "limitations": [
                "Verification executes project-defined code with shell=False and a timeout.",
                "A 'collects' result proves discovery and imports, not that any test passes.",
            ],
        }

    def _verification_plan(self, modules: list[dict[str, Any]], level: str) -> dict[str, Any]:
        """Describe exactly what would be executed, executing nothing."""
        planned = []
        for module in modules:
            directory = (self.workspace / module.get("working_directory", ".")).resolve()
            command = module.get("test_command")
            entry: dict[str, Any] = {
                "name": module.get("name"),
                "path": module.get("path"),
                "working_directory": module.get("working_directory", "."),
                "would_run": [],
            }
            if not command:
                entry["would_run"] = []
                entry["note"] = "no test command declared; nothing would run"
                planned.append(entry)
                continue
            argv = command.split()
            if at_least(level, "collects"):
                probe, reason = self._collect_probe_for(module, directory, argv)
                if probe:
                    entry["would_run"].append({"command": " ".join(probe), "rung": "collects", "why": reason})
                else:
                    entry["note"] = reason
            if level == "passes":
                entry["would_run"].append({"command": command, "rung": "passes", "why": "the full test command"})
            if not entry["would_run"] and not entry.get("note"):
                entry["note"] = "only the entrypoint would be checked; nothing would be executed"
            planned.append(entry)
        return {
            "schema_version": "v1",
            "workspace": str(self.workspace),
            "requested_level": level,
            "dry_run": True,
            "status": "inconclusive",
            "modules_verified": 0,
            "modules_at_collects_or_better": 0,
            "plan": planned,
            "results": [],
            "cache_path": str(CACHE_RELATIVE_PATH),
            "limitations": ["Nothing was executed. Re-run without --dry-run to verify."],
        }

    def _verify_module(
        self,
        module: dict[str, Any],
        target_level: str,
        timeout_seconds: int | None,
        cache: dict[str, Any],
    ) -> dict[str, Any]:
        path = module.get("path", ".")
        working_directory = module.get("working_directory", ".")
        directory = (self.workspace / working_directory).resolve()
        command = module.get("test_command")
        manifest_digest = self._manifest_digest(directory)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

        result: dict[str, Any] = {
            "name": module.get("name", path),
            "path": path,
            "ecosystem": module.get("ecosystem", "generic"),
            "working_directory": working_directory,
            "command": command,
            "command_shell": module.get("test_command_shell"),
            "level_reached": "declared",
            "outcome": "declared",
            "manifest_digest": manifest_digest,
            "verified_at": now,
            "rungs": [],
            "cached": False,
            # Observed cost of the rungs actually executed. A caller deciding
            # between a probe and the full suite has no way to guess this, and
            # we measure it anyway.
            "duration_ms": 0,
            "full_run_duration_ms": None,
        }

        if not command:
            result["outcome"] = "skipped"
            result["reason"] = "no test command is declared for this module"
            return result

        cached = cache.get("entries", {}).get(self._cache_key(path, "test"))
        if cached and cached.get("manifest_digest") == manifest_digest and cached.get("command") == command:
            if rank(cached.get("level_reached", "declared")) >= rank(target_level):
                result.update({
                    "level_reached": cached["level_reached"],
                    "outcome": cached.get("outcome", cached["level_reached"]),
                    "verified_at": cached.get("verified_at", now),
                    "full_run_duration_ms": cached.get("full_run_duration_ms"),
                    "cached": True,
                    "reason": "cached result is still valid for the current manifest digest",
                })
                return result

        argv = command.split()

        # Rung 1 — resolvable.
        resolved, detail = toolchain.entrypoint_exists(argv, directory)
        if resolved and module.get("ecosystem") == "python":
            # An interpreter on PATH says nothing about whether pytest is
            # installed in the environment that will actually run.
            installed, tool_detail = toolchain.python_tool_available(directory, "pytest")
            if installed is False:
                resolved, detail = False, tool_detail
            elif installed is True:
                detail = f"{detail}; {tool_detail}"
        result["rungs"].append({"level": "resolvable", "passed": resolved, "detail": detail})
        if not resolved:
            result["outcome"] = "failed"
            result["reason"] = detail
            return result
        result["level_reached"] = "resolvable"
        result["outcome"] = "resolvable"
        if target_level == "resolvable":
            return result

        # Rung 2 — collects.
        probe_argv, probe_reason = self._collect_probe_for(module, directory, argv)
        if probe_argv is None:
            result["rungs"].append({"level": "collects", "passed": None, "detail": probe_reason})
            # The rung was not attempted, so the result is neither a pass nor a
            # failure. Recording that distinctly stops "resolvable" from reading
            # as an endorsement it has not earned.
            result["outcome"] = "skipped"
            result["reason"] = f"stopped at 'resolvable': {probe_reason}"
            return result
        probe = self._run_probe(probe_argv, directory, timeout_seconds or DEFAULT_PROBE_TIMEOUT)
        passed = probe["exit_code"] == 0
        result["rungs"].append({
            "level": "collects", "passed": passed, "detail": probe_reason,
            "command": " ".join(probe_argv), "exit_code": probe["exit_code"],
            "duration_ms": probe["duration_ms"],
            "output_tail": "" if passed else probe["output_tail"],
            "error": probe["error"],
        })
        result["duration_ms"] += probe["duration_ms"]
        if not passed:
            result["outcome"] = "failed"
            result["reason"] = probe["error"] or f"discovery probe exited {probe['exit_code']}"
            return result
        result["level_reached"] = "collects"
        result["outcome"] = "collects"
        if target_level == "collects":
            return result

        # Rung 3 — passes.
        full = self._run_probe(argv, directory, timeout_seconds or DEFAULT_FULL_TIMEOUT)
        passed = full["exit_code"] == 0
        result["rungs"].append({
            "level": "passes", "passed": passed, "detail": "full test command",
            "command": command, "exit_code": full["exit_code"],
            "duration_ms": full["duration_ms"],
            "output_tail": "" if passed else full["output_tail"],
            "error": full["error"],
        })
        result["duration_ms"] += full["duration_ms"]
        result["full_run_duration_ms"] = full["duration_ms"]
        if passed:
            result["level_reached"] = "passes"
            result["outcome"] = "passes"
        else:
            result["outcome"] = "failed"
            result["reason"] = full["error"] or f"test command exited {full['exit_code']}"
        return result
