# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""Canonical build-command resolution for detected workspaces and modules."""

from __future__ import annotations

import json
from typing import Any

from ...models import ModuleInfo
from ..core import conventions, toolchain, vocabulary


class CommandResolutionMixin:
    def resolve_command(
        self,
        phase: str = "test",
        scope: str | None = None,
        changed: bool = False,
        report: Any = None,
    ) -> dict[str, Any]:
        """Resolve the verified shell command for a build phase (test, build, lint, format, clean).

        A task runner the repository declares — justfile, Taskfile, Makefile,
        mise, noxfile, tox — answers first, because that is what a person on the
        team actually types. Otherwise the ecosystem's own manifest decides:
        package.json scripts, pyproject.toml, pom.xml, build.gradle, go.mod,
        Cargo.toml.

        Supports monorepo scoping via `scope` ('ui', 'backend', package name/path)
        and git change-impact targeting via `changed=True`.
        """
        workspace = self.workspace
        # One report per call. Asking twice — once for the modules, once for the
        # shape — walked and digested the whole repository a second time to
        # validate a cache that then hit.
        # Reuse the caller's report when it has one: validating the cache costs
        # a full walk of the repository, so doing it twice per request is the
        # single largest avoidable cost in the MCP path.
        report = report if report is not None else self._inspect_cached()
        modules = [self._module_info(detail) for detail in report.module_details]
        # Shape comes from the report so `cmd` and `inspect` cannot disagree
        # about whether this repository is a monorepo. A root project with one
        # SDK subdirectory is not one, and advertising scoping there is noise.
        repository = report.repository or {}
        shape = repository.get("shape", "unknown")
        is_monorepo = bool(repository.get("is_monorepo", False))

        aliases = vocabulary.aliases_for(phase)

        targeted_modules: list[ModuleInfo] = []
        reason: str | None = None

        if changed:
            changed_files, _ = self._git_changed_files("HEAD")
            if changed_files:
                changed_paths = [item["path"] for item in changed_files]
                matched: list[ModuleInfo] = []
                for mod in modules:
                    mod_p = mod.path
                    if mod_p == ".":
                        continue
                    if any(cp == mod_p or cp.startswith(mod_p + "/") for cp in changed_paths):
                        if mod not in matched:
                            matched.append(mod)
                if matched:
                    targeted_modules = matched
                    reason = f"Targeted {len(targeted_modules)} module(s) affected by git changes: {', '.join(m.name for m in targeted_modules)}"
                else:
                    reason = "No changed files detected inside sub-modules; falling back to root command."
            else:
                reason = "No git modifications detected compared to baseline; falling back to root command."

        elif scope:
            scope_clean = scope.strip().lower()
            cat_map = {
                "ui": "ui", "frontend": "ui", "front-end": "ui", "web": "ui", "client": "ui",
                "backend": "backend", "be": "backend", "api": "backend", "server": "backend", "service": "backend",
                "shared": "shared", "common": "shared", "lib": "shared", "utils": "shared",
                "unknown": "unknown", "unclassified": "unknown",
            }
            target_cat = cat_map.get(scope_clean)
            if target_cat:
                matched = [m for m in modules if m.category == target_cat]
                if matched:
                    targeted_modules = matched
                    reason = f"Targeted {len(targeted_modules)} module(s) matching '{scope}' ({target_cat}) category"

            if not targeted_modules:
                matched = [m for m in modules if m.name.lower() == scope_clean or m.path.lower() == scope_clean]
                if matched:
                    targeted_modules = matched
                    reason = f"Targeted module '{targeted_modules[0].name}' matching exact scope '{scope}'"

            if not targeted_modules:
                matched = [m for m in modules if scope_clean in m.name.lower() or scope_clean in m.path.lower()]
                if matched:
                    targeted_modules = matched
                    reason = f"Targeted {len(targeted_modules)} module(s) fuzzy-matching scope '{scope}'"

            if not targeted_modules:
                reason = f"No module matched scope '{scope}'; falling back to root command."

        def _wrap(res: dict[str, Any], mods: list[ModuleInfo] | None = None) -> dict[str, Any]:
            used_mods = mods if mods is not None else targeted_modules
            res["scope"] = scope
            res["changed"] = changed
            res["is_monorepo"] = is_monorepo
            res["repository_shape"] = shape
            res["targeted_modules"] = [m.to_dict() for m in used_mods]
            # A command is only unambiguous alongside the directory it runs in.
            # A single target contributes its own; a fan-out over several
            # modules is already root-relative by construction.
            res["working_directory"] = used_mods[0].working_directory if len(used_mods) == 1 else "."
            res["command_duration_ms"] = (
                used_mods[0].test_command_duration_ms
                if len(used_mods) == 1 and phase == "test" else None
            )
            res["command_status"] = (
                used_mods[0].test_command_status
                if len(used_mods) == 1 and phase == "test"
                else self._verified_status_for(res["working_directory"], phase, res.get("command"))
            )
            res["reason"] = reason or ("Targeted module command" if used_mods else ("Root workspace command" if is_monorepo else "Single-project command"))
            return res

        # If we have targeted modules, construct the scoped command
        if targeted_modules:
            # Check Turborepo
            if (workspace / "turbo.json").is_file():
                filters = " ".join(f"--filter={m.name}" for m in targeted_modules)
                runner = "turbo run"
                if (workspace / "pnpm-lock.yaml").is_file():
                    runner = "pnpm turbo run"
                elif (workspace / "bun.lockb").is_file() or (workspace / "bun.lock").is_file():
                    runner = "bun x turbo run"
                return _wrap({
                    "phase": phase,
                    "command": f"{runner} {phase} {filters}",
                    "source": "turbo.json",
                    "ecosystem": "node",
                })

            # Check Nx
            if (workspace / "nx.json").is_file():
                proj_list = ",".join(m.name for m in targeted_modules)
                cmd = f"npx nx run-many --target={phase} --projects={proj_list}" if len(targeted_modules) > 1 else f"npx nx {phase} {targeted_modules[0].name}"
                return _wrap({
                    "phase": phase,
                    "command": cmd,
                    "source": "nx.json",
                    "ecosystem": "node",
                })

            # Check pnpm workspace
            if (workspace / "pnpm-workspace.yaml").is_file() or (workspace / "pnpm-lock.yaml").is_file():
                filters = " ".join(f"--filter {m.name}" for m in targeted_modules)
                pnpm_action = "test" if phase == "test" else f"run {phase}"
                return _wrap({
                    "phase": phase,
                    "command": f"pnpm {filters} {pnpm_action}",
                    "source": "pnpm-workspace.yaml" if (workspace / "pnpm-workspace.yaml").is_file() else "pnpm-lock.yaml",
                    "ecosystem": "node",
                })

            # Check npm workspace
            root_pkg = workspace / "package.json"
            if root_pkg.is_file():
                try:
                    r_data = json.loads(self._read(root_pkg))
                    if r_data.get("workspaces"):
                        ws_flags = " ".join(f"--workspace {m.name}" for m in targeted_modules)
                        npm_action = "test" if phase == "test" else f"run {phase}"
                        return _wrap({
                            "phase": phase,
                            "command": f"npm {npm_action} {ws_flags}",
                            "source": "package.json [workspaces]",
                            "ecosystem": "node",
                        })
                except Exception:
                    pass

            # Check Cargo workspace
            if (workspace / "Cargo.toml").is_file() and all(m.ecosystem == "rust" for m in targeted_modules):
                p_flags = " ".join(f"-p {m.name}" for m in targeted_modules)
                cargo_phase = "test" if phase == "test" else ("build" if phase == "build" else "clippy" if phase == "lint" else "fmt")
                return _wrap({
                    "phase": phase,
                    "command": f"cargo {cargo_phase} {p_flags}",
                    "source": "Cargo.toml [workspace]",
                    "ecosystem": "rust",
                })

            # Check Maven multi-module
            if (workspace / "pom.xml").is_file() and all(m.ecosystem == "maven" for m in targeted_modules):
                wrapper = "./mvnw" if (workspace / "mvnw").is_file() else "mvn"
                mvn_phase = "test" if phase == "test" else ("package" if phase == "build" else "clean")
                pl_arg = ",".join(m.path for m in targeted_modules)
                return _wrap({
                    "phase": phase,
                    "command": f"{wrapper} {mvn_phase} -pl {pl_arg}",
                    "source": "pom.xml [modules]",
                    "ecosystem": "maven",
                })

            # Check Gradle multi-project
            if any((workspace / g).is_file() for g in ("settings.gradle", "settings.gradle.kts")) and all(m.ecosystem == "gradle" for m in targeted_modules):
                wrapper = "./gradlew" if (workspace / "gradlew").is_file() else "gradle"
                tasks = " ".join(f":{m.name.replace('/', ':')}:{phase}" for m in targeted_modules)
                return _wrap({
                    "phase": phase,
                    "command": f"{wrapper} {tasks}",
                    "source": "settings.gradle",
                    "ecosystem": "gradle",
                })

            # Check Go workspace
            if (workspace / "go.work").is_file() and all(m.ecosystem == "go" for m in targeted_modules):
                cmds = [f"go {phase} ./{m.path}/..." for m in targeted_modules]
                return _wrap({
                    "phase": phase,
                    "command": " && ".join(cmds),
                    "source": "go.work",
                    "ecosystem": "go",
                })

            # Fallback / polyglot targeted execution. Each module already
            # carries the command its own toolchain needs and the directory it
            # must run in; re-deriving a second, root-relative form here is how
            # `cmd` and `modules` came to disagree.
            if len(targeted_modules) == 1:
                only = targeted_modules[0]
                command = only.test_command if phase == "test" else only.build_command
                if command:
                    return _wrap({
                        "phase": phase,
                        "command": command,
                        "command_shell": (
                            only.test_command_shell if phase == "test" else only.build_command_shell
                        ),
                        "source": "module toolchain",
                        "ecosystem": only.ecosystem,
                    })

            # Several modules: chain their root-safe forms, each of which
            # carries its own `cd`, and report the root as the directory.
            cmds = []
            for module in targeted_modules:
                shell = module.test_command_shell if phase == "test" else module.build_command_shell
                direct = module.test_command if phase == "test" else module.build_command
                chosen = shell or direct
                if chosen:
                    cmds.append(chosen)
            if not cmds:
                return _wrap({
                    "phase": phase,
                    "command": None,
                    "source": "monorepo module convention",
                    "ecosystem": targeted_modules[0].ecosystem,
                })

            return _wrap({
                "phase": phase,
                "command": " && ".join(cmds),
                "source": "monorepo module convention",
                "ecosystem": targeted_modules[0].ecosystem if len(targeted_modules) == 1 else "polyglot",
            })

        # Fallback to root workspace resolution
        result = {"phase": phase, "command": None, "source": None, "ecosystem": None}

        # A declared task runner comes first. A repository with a justfile
        # reading `test: cargo nextest run` has said how it wants to be tested;
        # answering `cargo test` there would be the tool overriding the team.
        declared = conventions.declared_command(workspace, phase)
        if declared:
            result["command"] = " ".join(declared["command"])
            result["source"] = f"{declared['source']} [{declared['runner']} {declared['target']}]"
            result["ecosystem"] = declared["runner"]
            return _wrap(result, [])

        # Node.js: check package.json scripts
        pkg_json = workspace / "package.json"
        if pkg_json.is_file():
            try:
                pkg = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
                scripts = pkg.get("scripts", {})
                # Same resolver the module path uses, so the root and a
                # sub-package cannot disagree about how a script is invoked.
                runner, _lock_source = toolchain.node_runner(workspace)
                for alias in aliases:
                    if alias in scripts:
                        result["command"] = " ".join(toolchain.node_script_command(runner, alias))
                        result["source"] = "package.json"
                        result["ecosystem"] = "node"
                        return _wrap(result, [])
            except (json.JSONDecodeError, OSError):
                pass

        # Python: check pyproject.toml for test/build commands
        pyproject = workspace / "pyproject.toml"
        if pyproject.is_file():
            text = pyproject.read_text(encoding="utf-8", errors="replace")
            if phase == "test":
                if "[tool.pytest" in text:
                    argv, env_source = toolchain.python_test_command(workspace)
                    result["command"] = " ".join(argv)
                    result["toolchain_source"] = env_source
                    result["source"] = "pyproject.toml [tool.pytest]"
                    result["ecosystem"] = "python"
                    return _wrap(result, [])
            if phase == "build":
                result["command"] = "python -m build"
                result["source"] = "pyproject.toml"
                result["ecosystem"] = "python"
                return _wrap(result, [])
            if phase == "lint":
                if "ruff" in text:
                    result["command"] = "ruff check ."
                    result["source"] = "pyproject.toml [ruff]"
                    result["ecosystem"] = "python"
                    return _wrap(result, [])
            if phase == "format":
                if "ruff" in text:
                    result["command"] = "ruff format ."
                    result["source"] = "pyproject.toml [ruff]"
                    result["ecosystem"] = "python"
                    return _wrap(result, [])

        # Java/Maven
        pom = workspace / "pom.xml"
        if pom.is_file():
            maven_phases = {"test": "mvn test", "build": "mvn package", "clean": "mvn clean", "lint": "mvn checkstyle:check"}
            if phase in maven_phases:
                result["command"] = maven_phases[phase]
                result["source"] = "pom.xml"
                result["ecosystem"] = "maven"
                return _wrap(result, [])

        # Java/Gradle
        for gradle_file in ("build.gradle", "build.gradle.kts"):
            if (workspace / gradle_file).is_file():
                wrapper = "./gradlew" if (workspace / "gradlew").is_file() else "gradle"
                gradle_phases = {"test": f"{wrapper} test", "build": f"{wrapper} build", "clean": f"{wrapper} clean", "lint": f"{wrapper} check"}
                if phase in gradle_phases:
                    result["command"] = gradle_phases[phase]
                    result["source"] = gradle_file
                    result["ecosystem"] = "gradle"
                    return _wrap(result, [])

        # Go
        go_mod = workspace / "go.mod"
        if go_mod.is_file():
            go_phases = {"test": "go test ./...", "build": "go build ./...", "lint": "golangci-lint run", "format": "gofmt -w .", "clean": "go clean"}
            if phase in go_phases:
                result["command"] = go_phases[phase]
                result["source"] = "go.mod"
                result["ecosystem"] = "go"
                return _wrap(result, [])

        # Rust
        cargo_toml = workspace / "Cargo.toml"
        if cargo_toml.is_file():
            cargo_phases = {"test": "cargo test", "build": "cargo build", "lint": "cargo clippy", "format": "cargo fmt", "clean": "cargo clean"}
            if phase in cargo_phases:
                result["command"] = cargo_phases[phase]
                result["source"] = "Cargo.toml"
                result["ecosystem"] = "rust"
                return _wrap(result, [])

        # Root fallbacks for monorepo orchestrators if no explicit script matched
        if (workspace / "turbo.json").is_file():
            result["command"] = f"turbo run {phase}"
            result["source"] = "turbo.json"
            result["ecosystem"] = "node"
        elif (workspace / "pnpm-workspace.yaml").is_file():
            result["command"] = f"pnpm -r run {phase}" if phase != "test" else "pnpm -r test"
            result["source"] = "pnpm-workspace.yaml"
            result["ecosystem"] = "node"

        return _wrap(result, [])
