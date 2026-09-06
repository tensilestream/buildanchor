# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from buildanchor import BuildAnchor
from buildanchor.cli import main
from buildanchor.transports import MCPServer


class MonorepoTests(unittest.TestCase):
    @staticmethod
    def _git(root: Path, *args: str) -> None:
        result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")

    def test_pnpm_workspace_discovery_and_scoping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pnpm-workspace.yaml").write_text("packages:\n  - 'apps/*'\n  - 'packages/*'\n", encoding="utf-8")
            (root / "package.json").write_text(json.dumps({"name": "root-repo", "scripts": {"test": "pnpm -r test"}}), encoding="utf-8")
            (root / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")

            # UI App
            web_dir = root / "apps" / "web"
            web_dir.mkdir(parents=True)
            (web_dir / "package.json").write_text(json.dumps({
                "name": "@acme/web",
                "dependencies": {"react": "^19.0.0", "next": "^15.0.0"},
                "scripts": {"test": "vitest run", "build": "next build"},
            }), encoding="utf-8")

            # Backend API App
            api_dir = root / "apps" / "api"
            api_dir.mkdir(parents=True)
            (api_dir / "package.json").write_text(json.dumps({
                "name": "@acme/api",
                "dependencies": {"express": "^4.19.0", "prisma": "^5.0.0"},
                "scripts": {"test": "jest", "build": "tsc"},
            }), encoding="utf-8")

            # Shared Package
            shared_dir = root / "packages" / "utils"
            shared_dir.mkdir(parents=True)
            (shared_dir / "package.json").write_text(json.dumps({
                "name": "@acme/utils",
                "scripts": {"test": "jest"},
            }), encoding="utf-8")

            engine = BuildAnchor(root)
            modules = engine.discover_modules()
            self.assertEqual(len(modules), 3)

            mod_by_name = {m.name: m for m in modules}
            self.assertEqual(mod_by_name["@acme/web"].category, "ui")
            self.assertEqual(mod_by_name["@acme/api"].category, "backend")
            self.assertEqual(mod_by_name["@acme/utils"].category, "shared")

            # Scope UI
            ui_cmd = engine.resolve_command("test", scope="ui")
            self.assertTrue(ui_cmd["is_monorepo"])
            self.assertEqual(ui_cmd["command"], "pnpm --filter @acme/web test")
            self.assertEqual(len(ui_cmd["targeted_modules"]), 1)
            self.assertEqual(ui_cmd["targeted_modules"][0]["name"], "@acme/web")

            # Scope Backend
            be_cmd = engine.resolve_command("test", scope="backend")
            self.assertEqual(be_cmd["command"], "pnpm --filter @acme/api test")
            self.assertEqual(len(be_cmd["targeted_modules"]), 1)

            # Scope specific module by name
            spec_cmd = engine.resolve_command("test", scope="@acme/utils")
            self.assertEqual(spec_cmd["command"], "pnpm --filter @acme/utils test")

            # Scope by relative path
            path_cmd = engine.resolve_command("build", scope="apps/web")
            self.assertEqual(path_cmd["command"], "pnpm --filter @acme/web run build")

            # Root command without scope
            root_cmd = engine.resolve_command("test")
            self.assertEqual(root_cmd["command"], "pnpm test")

    def test_turborepo_scoping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "turbo.json").write_text(json.dumps({"$schema": "https://turbo.build/schema.json"}), encoding="utf-8")
            (root / "package.json").write_text(json.dumps({"name": "turbo-repo", "workspaces": ["apps/*"]}), encoding="utf-8")

            web_dir = root / "apps" / "web"
            web_dir.mkdir(parents=True)
            (web_dir / "package.json").write_text(json.dumps({
                "name": "web",
                "dependencies": {"react": "^18.0.0"},
                "scripts": {"test": "vitest"},
            }), encoding="utf-8")

            api_dir = root / "apps" / "api"
            api_dir.mkdir(parents=True)
            (api_dir / "package.json").write_text(json.dumps({
                "name": "api",
                "dependencies": {"fastify": "^4.0.0"},
                "scripts": {"test": "jest"},
            }), encoding="utf-8")

            engine = BuildAnchor(root)
            cmd = engine.resolve_command("test", scope="ui")
            self.assertEqual(cmd["command"], "turbo run test --filter=web")
            self.assertEqual(cmd["source"], "turbo.json")

    def test_nx_scoping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "nx.json").write_text(json.dumps({"targetDefaults": {}}), encoding="utf-8")
            (root / "package.json").write_text(json.dumps({"name": "nx-repo", "workspaces": ["packages/*"]}), encoding="utf-8")

            client_dir = root / "packages" / "client"
            client_dir.mkdir(parents=True)
            (client_dir / "package.json").write_text(json.dumps({
                "name": "client",
                "dependencies": {"vue": "^3.0.0"},
                "scripts": {"test": "vitest"},
            }), encoding="utf-8")

            server_dir = root / "packages" / "server"
            server_dir.mkdir(parents=True)
            (server_dir / "package.json").write_text(json.dumps({
                "name": "server",
                "dependencies": {"nest": "^10.0.0"},
                "scripts": {"test": "jest"},
            }), encoding="utf-8")

            engine = BuildAnchor(root)
            cmd = engine.resolve_command("test", scope="ui")
            self.assertEqual(cmd["command"], "npx nx test client")

    def test_cargo_workspace_scoping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Cargo.toml").write_text('[workspace]\nmembers = ["crates/*"]\n', encoding="utf-8")

            web_crate = root / "crates" / "web_ui"
            web_crate.mkdir(parents=True)
            (web_crate / "Cargo.toml").write_text('[package]\nname = "web_ui"\nversion = "0.1.0"\nedition = "2021"\n', encoding="utf-8")

            core_crate = root / "crates" / "core_engine"
            core_crate.mkdir(parents=True)
            (core_crate / "Cargo.toml").write_text('[package]\nname = "core_engine"\nversion = "0.1.0"\nedition = "2021"\n', encoding="utf-8")

            engine = BuildAnchor(root)
            modules = engine.discover_modules()
            self.assertEqual(len(modules), 2)

            ui_cmd = engine.resolve_command("test", scope="ui")
            self.assertEqual(ui_cmd["command"], "cargo test -p web_ui")

            core_cmd = engine.resolve_command("test", scope="core_engine")
            self.assertEqual(core_cmd["command"], "cargo test -p core_engine")

    def test_maven_multi_module_scoping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pom.xml").write_text(
                "<project><modules><module>apps/web-app</module><module>services/auth-service</module></modules></project>",
                encoding="utf-8",
            )

            web_dir = root / "apps" / "web-app"
            web_dir.mkdir(parents=True)
            (web_dir / "pom.xml").write_text("<project><artifactId>web-app</artifactId></project>", encoding="utf-8")

            svc_dir = root / "services" / "auth-service"
            svc_dir.mkdir(parents=True)
            (svc_dir / "pom.xml").write_text("<project><artifactId>auth-service</artifactId></project>", encoding="utf-8")

            engine = BuildAnchor(root)
            modules = engine.discover_modules()
            self.assertEqual(len(modules), 2)

            ui_cmd = engine.resolve_command("test", scope="ui")
            self.assertEqual(ui_cmd["command"], "mvn test -pl apps/web-app")

            be_cmd = engine.resolve_command("test", scope="backend")
            self.assertEqual(be_cmd["command"], "mvn test -pl services/auth-service")

    def test_gradle_multi_project_scoping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "settings.gradle").write_text("include 'ui', 'api'\n", encoding="utf-8")

            (root / "ui").mkdir()
            (root / "api").mkdir()

            engine = BuildAnchor(root)
            modules = engine.discover_modules()
            self.assertEqual(len(modules), 2)

            ui_cmd = engine.resolve_command("test", scope="ui")
            self.assertEqual(ui_cmd["command"], "./gradlew :ui:test" if (root / "gradlew").is_file() else "gradle :ui:test")

            be_cmd = engine.resolve_command("test", scope="backend")
            self.assertEqual(be_cmd["command"], "./gradlew :api:test" if (root / "gradlew").is_file() else "gradle :api:test")

    def test_polyglot_monorepo_scoping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            # Frontend React app
            frontend = root / "frontend"
            frontend.mkdir()
            (frontend / "package.json").write_text(json.dumps({
                "name": "frontend-client",
                "dependencies": {"react": "^18.0.0"},
                "scripts": {"test": "npm test", "build": "npm run build"},
            }), encoding="utf-8")

            # Backend Python FastAPI service
            backend = root / "backend"
            backend.mkdir()
            (backend / "pyproject.toml").write_text('[project]\nname = "backend-api"\n', encoding="utf-8")

            engine = BuildAnchor(root)
            modules = engine.discover_modules()
            self.assertEqual(len(modules), 2)

            # Commands are relative to the module's own working directory.
            # `npm --prefix frontend test` and `python -m pytest backend` left
            # the working directory at the root, which is what broke them.
            ui_cmd = engine.resolve_command("test", scope="ui")
            self.assertEqual(ui_cmd["command"], "npm test")
            self.assertEqual(ui_cmd["working_directory"], "frontend")
            self.assertEqual(ui_cmd["command_shell"], "cd frontend && npm test")

            be_cmd = engine.resolve_command("test", scope="backend")
            self.assertEqual(be_cmd["working_directory"], "backend")
            self.assertNotIn("backend", be_cmd["command"].split()[1:])
            self.assertTrue(be_cmd["command"].endswith("pytest"), be_cmd["command"])

    def test_changed_files_scoping_in_git(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._git(root, "init")
            self._git(root, "config", "user.email", "agent@buildanchor.local")
            self._git(root, "config", "user.name", "BuildAnchor Agent")

            (root / "package.json").write_text(json.dumps({
                "name": "repo",
                "workspaces": ["apps/*"],
                "scripts": {"test": "npm test --workspaces"},
            }), encoding="utf-8")

            web_dir = root / "apps" / "web"
            web_dir.mkdir(parents=True)
            (web_dir / "package.json").write_text(json.dumps({
                "name": "web",
                "dependencies": {"react": "^18.0.0"},
                "scripts": {"test": "jest"},
            }), encoding="utf-8")
            web_src = web_dir / "src"
            web_src.mkdir()
            (web_src / "App.tsx").write_text("// initial\n", encoding="utf-8")

            api_dir = root / "apps" / "api"
            api_dir.mkdir(parents=True)
            (api_dir / "package.json").write_text(json.dumps({
                "name": "api",
                "dependencies": {"express": "^4.0.0"},
                "scripts": {"test": "jest"},
            }), encoding="utf-8")

            self._git(root, "add", ".")
            self._git(root, "commit", "-m", "Initial commit")

            # Now modify ONLY the web component
            (web_src / "App.tsx").write_text("// modified for new prescription flow\n", encoding="utf-8")

            engine = BuildAnchor(root)
            changed_cmd = engine.resolve_command("test", changed=True)

            self.assertTrue(changed_cmd["changed"])
            self.assertEqual(len(changed_cmd["targeted_modules"]), 1)
            self.assertEqual(changed_cmd["targeted_modules"][0]["name"], "web")
            self.assertEqual(changed_cmd["command"], "npm test --workspace web")
            self.assertIn("web", changed_cmd["reason"])

    def test_cli_modules_and_scoped_cmd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({
                "name": "my-mono",
                "workspaces": ["apps/*"],
            }), encoding="utf-8")

            web_dir = root / "apps" / "web"
            web_dir.mkdir(parents=True)
            (web_dir / "package.json").write_text(json.dumps({
                "name": "web",
                "dependencies": {"react": "^18.0.0"},
                "scripts": {"test": "jest"},
            }), encoding="utf-8")

            api_dir = root / "apps" / "api"
            api_dir.mkdir(parents=True)
            (api_dir / "package.json").write_text(json.dumps({
                "name": "api",
                "dependencies": {"express": "^4.0.0"},
                "scripts": {"test": "mocha"},
            }), encoding="utf-8")

            # Test buildanchor modules
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["modules", "--workspace", str(root)])
            self.assertEqual(code, 0)
            self.assertIn("Monorepo modules (2 found)", out.getvalue())
            self.assertIn("[UI]", out.getvalue())
            self.assertIn("[BACKEND]", out.getvalue())

            # Test buildanchor cmd test --scope ui
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["cmd", "test", "--scope", "ui", "--workspace", str(root)])
            self.assertEqual(code, 0)
            self.assertIn("npm test --workspace web", out.getvalue())

            # Test buildanchor cmd test --scope backend --explain
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["cmd", "test", "--scope", "backend", "--workspace", str(root), "--explain"])
            self.assertEqual(code, 0)
            text = out.getvalue()
            self.assertIn("command: npm test --workspace api", text)
            self.assertIn("scope: backend", text)
            self.assertIn("targeted: api (backend)", text)

            # Test buildanchor cmd --list
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["cmd", "--list", "--workspace", str(root)])
            self.assertEqual(code, 0)
            self.assertIn("Available scopes:", out.getvalue())
            self.assertIn("--scope ui", out.getvalue())

    def test_mcp_modules_and_cmd_tools(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({
                "name": "mono",
                "workspaces": ["apps/*"],
            }), encoding="utf-8")

            web_dir = root / "apps" / "web"
            web_dir.mkdir(parents=True)
            (web_dir / "package.json").write_text(json.dumps({
                "name": "web",
                "dependencies": {"react": "^18.0.0"},
                "scripts": {"test": "jest"},
            }), encoding="utf-8")

            server = MCPServer(str(root))
            modules_res = server.call_tool("build.modules", {"workspace": "."})
            self.assertTrue(modules_res["is_monorepo"])
            self.assertEqual(len(modules_res["modules"]), 1)
            self.assertEqual(modules_res["modules"][0]["name"], "web")

            cmd_res = server.call_tool("build.cmd", {"workspace": ".", "phase": "test", "scope": "ui"})
            self.assertEqual(cmd_res["command"], "npm test --workspace web")
            self.assertEqual(cmd_res["scope"], "ui")
            self.assertTrue(cmd_res["is_monorepo"])
