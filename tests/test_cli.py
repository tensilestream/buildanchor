import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

from buildanchor.cli import (
    _claude_config_path,
    _collect_interactive_inputs,
    _render_cli_banner,
    _select_mcp_clients_keyboard,
    _setup_mcp_clients,
    _should_render_cli_banner,
    main,
)


class CLITests(unittest.TestCase):
    @staticmethod
    def _set_pty_size(descriptor: int, rows: int = 40, columns: int = 120) -> None:
        """Give PTY integration tests a stable, readable terminal viewport."""
        import fcntl
        import struct
        import termios

        fcntl.ioctl(descriptor, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))

    def test_interactive_plan_collects_a_required_objective_before_execution(self) -> None:
        class TTYInput(io.StringIO):
            def isatty(self) -> bool:
                return True

        class TTYOutput(io.StringIO):
            def isatty(self) -> bool:
                return True

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({"name": "sample"}), encoding="utf-8")
            output = TTYOutput()
            with (
                mock.patch("buildanchor.cli.sys.stdin", TTYInput("Add an audit endpoint\n")),
                mock.patch("buildanchor.cli.sys.stdout", output),
            ):
                code = main(["plan", "-i", "--workspace", str(root)])

        self.assertIn(code, {0, 2})  # A fixture without Git may be inconclusive.
        self.assertIn("PLAN  /  INTERACTIVE MODE", output.getvalue())
        self.assertIn("Objective", output.getvalue())
        self.assertIn("Add an audit endpoint", output.getvalue())

    def test_interactive_fields_require_objective_and_support_defaults(self) -> None:
        args = Namespace(command="plan", objective="", token_budget=2500)
        answers = iter(["", "Add a health endpoint"])
        output = io.StringIO()

        _collect_interactive_inputs(args, Path("/workspace"), lambda: next(answers), output)

        self.assertEqual(args.objective, "Add a health endpoint")
        self.assertIn("An answer is required", output.getvalue())

    def test_cli_banner_is_human_facing_and_safe_for_machine_interfaces(self) -> None:
        class TTYOutput(io.StringIO):
            def isatty(self) -> bool:
                return True

        output = TTYOutput()
        with mock.patch("buildanchor.cli.sys.stdout", output):
            self.assertTrue(_should_render_cli_banner(Namespace(
                command="inspect", format="text", agent=False, ci=False,
            )))
            self.assertFalse(_should_render_cli_banner(Namespace(
                command="inspect", format="json", agent=False, ci=False,
            )))
            self.assertFalse(_should_render_cli_banner(Namespace(
                command="mcp", format="text", agent=False, ci=False,
            )))
            _render_cli_banner()

        self.assertIn("BuildAnchor", output.getvalue())
        self.assertIn("Build Truth for AI coding agents", output.getvalue())

    def test_validate_change_returns_inconclusive_exit_code_without_git(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pyproject.toml").write_text("[project]\nname = 'fixture'\n", encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(["validate-change", "--workspace", str(root)])
            self.assertEqual(exit_code, 2)
            self.assertIn("No Git repository was detected", output.getvalue())


    def test_installer_script_help(self) -> None:
        import subprocess
        script = Path(__file__).resolve().parents[1] / "scripts" / "install.sh"
        if script.exists():
            result = subprocess.run(["bash", str(script), "--help"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            self.assertIn("BuildAnchor installer", result.stdout)
            self.assertIn("--local", result.stdout)

    def test_find_command_found_and_not_found(self) -> None:
        import json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({"dependencies": {"express": "^4.19.0"}}), encoding="utf-8")

            # Found: exit 0
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["find", "--package", "express", "--workspace", str(root)])
            self.assertEqual(code, 0)
            self.assertIn("express", out.getvalue())
            self.assertIn("declared", out.getvalue())

            # Not found: exit 1
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["find", "--package", "missing-pkg-123", "--workspace", str(root)])
            self.assertEqual(code, 1)
            self.assertIn("NOT FOUND", out.getvalue())

            # installed-only on declared package: exit 1
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["find", "--package", "express", "--workspace", str(root), "--installed-only"])
            self.assertEqual(code, 1)
            self.assertIn("NOT FOUND", out.getvalue())

            # format llm
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["find", "--package", "express", "--workspace", str(root), "--format", "llm"])
            self.assertEqual(code, 0)
            self.assertIn("# BuildAnchor package: express", out.getvalue())

    def test_cmd_command(self) -> None:
        import json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({
                "scripts": {"test": "npm test", "build": "npm run build"}
            }), encoding="utf-8")

            # Positional cmd test: exit 0
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["cmd", "test", "--workspace", str(root)])
            self.assertEqual(code, 0)
            self.assertEqual(out.getvalue().strip(), "npm test")

            # Positional cmd build: exit 0
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["cmd", "build", "--workspace", str(root)])
            self.assertEqual(code, 0)
            self.assertEqual(out.getvalue().strip(), "npm run build")

            # Missing phase: exit 2
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["cmd", "clean", "--workspace", str(root)])
            self.assertEqual(code, 2)

    def test_init_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({
                "name": "sample-project",
                "scripts": {"test": "jest"}
            }), encoding="utf-8")

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["init", "--workspace", str(root)])
            self.assertEqual(code, 0)
            self.assertTrue((root / ".buildanchor.json").is_file())
            # AGENTS.md is the cross-agent convention; AGENT.md was read by
            # nothing. An existing CLAUDE.md or AGENT.md is still preferred.
            rules = root / "AGENTS.md"
            self.assertTrue(rules.is_file())
            written = rules.read_text(encoding="utf-8")
            self.assertIn("npm test", written)
            self.assertIn("buildanchor verify", written)
            self.assertIn("Single-project repository", written)
            self.assertIn("BuildAnchor Verified", out.getvalue())

            # Re-running refreshes the block in place rather than appending a
            # second, divergent copy.
            with contextlib.redirect_stdout(io.StringIO()):
                main(["init", "--workspace", str(root)])
            self.assertEqual(rules.read_text(encoding="utf-8").count("<!-- BuildAnchor Rules Block -->"), 1)

    def test_init_updates_every_agent_file_not_just_one(self) -> None:
        """A file left holding an older answer is worse than no file."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({"scripts": {"test": "node --test"}}), encoding="utf-8")
            (root / "CLAUDE.md").write_text("# House rules\n", encoding="utf-8")
            (root / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                main(["init", "--workspace", str(root)])
            for name in ("CLAUDE.md", "AGENTS.md"):
                written = (root / name).read_text(encoding="utf-8")
                self.assertIn("BuildAnchor Rules Block", written, name)
                self.assertIn("npm test", written, name)

    def test_init_check_reports_drift_and_changes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({"scripts": {"test": "node --test"}}), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                main(["init", "--workspace", str(root)])
            rules = root / "AGENTS.md"
            before = rules.read_text(encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["init", "--workspace", str(root), "--check"]), 0)

            # The repository becomes a workspace: shape and commands change.
            (root / "package.json").write_text(
                json.dumps({"workspaces": ["packages/*"], "scripts": {"test": "node --test"}}), encoding="utf-8")
            package = root / "packages" / "api"
            package.mkdir(parents=True)
            (package / "package.json").write_text(
                json.dumps({"name": "api", "scripts": {"test": "node --test"}}), encoding="utf-8")

            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = main(["init", "--workspace", str(root), "--check"])
            self.assertEqual(code, 1)
            self.assertIn("stale", err.getvalue())
            self.assertEqual(rules.read_text(encoding="utf-8"), before, "--check must not write")

    def test_init_rules_file_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({"scripts": {"test": "node --test"}}), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                main(["init", "--workspace", str(root), "--rules-file", "docs/AGENT_NOTES.md"])
            self.assertTrue((root / "docs" / "AGENT_NOTES.md").is_file())
            self.assertFalse((root / "AGENTS.md").is_file())

    def test_init_prefers_an_existing_claude_md(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({"scripts": {"test": "jest"}}), encoding="utf-8")
            (root / "CLAUDE.md").write_text("# House rules\n\nBe careful.\n", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                main(["init", "--workspace", str(root)])
            written = (root / "CLAUDE.md").read_text(encoding="utf-8")
            self.assertIn("House rules", written)
            self.assertIn("BuildAnchor Rules Block", written)
            self.assertFalse((root / "AGENTS.md").is_file())

    def test_setup_copilot_creates_and_safely_updates_workspace_mcp_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({"name": "sample"}), encoding="utf-8")
            config_path = root / ".vscode" / "mcp.json"
            config_path.parent.mkdir()
            config_path.write_text(json.dumps({"servers": {"other": {"command": "other-server"}}}), encoding="utf-8")

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["setup-copilot", "--workspace", str(root)])
            self.assertEqual(code, 0)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(config["servers"]["other"]["command"], "other-server")
            self.assertEqual(config["servers"]["buildanchor"]["args"][-1], "${workspaceFolder}")

            config["servers"]["buildanchor"] = {"command": "different-server"}
            config_path.write_text(json.dumps(config), encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["setup-copilot", "--workspace", str(root)]), 4)
            self.assertEqual(main(["setup-copilot", "--workspace", str(root), "--force"]), 0)

    def test_setup_mcp_configures_repository_and_global_clients_in_their_own_scopes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            home = Path(directory) / "home"
            root.mkdir()
            outcomes = _setup_mcp_clients(root, "cursor,claude-code,claude-desktop,gpt", home=home)

            self.assertEqual(
                {item["client"] for item in outcomes},
                {"cursor", "claude-code", "claude-desktop", "codex"},
            )
            cursor = json.loads((root / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
            self.assertEqual(cursor["mcpServers"]["buildanchor"]["args"][-1], str(root))
            claude_code = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
            self.assertEqual(claude_code["mcpServers"]["buildanchor"]["args"][-1], str(root))
            claude_desktop_path = next(
                Path(item["config_file"])
                for item in outcomes
                if item["client"] == "claude-desktop"
            )
            self.assertIn(
                "buildanchor",
                json.loads(claude_desktop_path.read_text(encoding="utf-8"))["mcpServers"],
            )
            codex = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
            self.assertIn("[mcp_servers.buildanchor]", codex)
            self.assertEqual(_setup_mcp_clients(root, "codex", home=home)[0]["status"], "already configured")

    def test_claude_alias_configures_claude_code_at_the_repository_scope(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            home = Path(directory) / "home"
            root.mkdir()

            outcome = _setup_mcp_clients(root, "claude", home=home)

            self.assertEqual(outcome[0]["client"], "claude-code")
            self.assertTrue((root / ".mcp.json").is_file())
            self.assertFalse((_claude_config_path(home)).exists())

    def test_keyboard_mcp_selector_uses_arrows_space_and_enter(self) -> None:
        keys = iter(["\x1b[B", " ", "\x1b[B", " ", "\r"])
        output = io.StringIO()
        selection = _select_mcp_clients_keyboard(lambda: next(keys), output)
        self.assertEqual(selection, "cursor,claude-code")
        self.assertIn("BUILDANCHOR", output.getvalue())
        self.assertIn("[x] Cursor  Repository", output.getvalue())
        self.assertIn("> [ ] GitHub Copilot  Repository", output.getvalue())
        self.assertIn("[Space] Toggle", output.getvalue())
        self.assertNotIn("❯", output.getvalue())  # noqa: RUF001 — the point is that this character is absent

    @unittest.skipIf(os.name == "nt", "PTY integration is POSIX-specific")
    def test_interactive_mcp_selector_runs_correctly_in_a_real_tty(self) -> None:
        import pty
        import select

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            master, slave = pty.openpty()
            self._set_pty_size(slave)
            process = subprocess.Popen(
                [sys.executable, "-m", "buildanchor", "setup-mcp", "--workspace", str(workspace), "--interactive"],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=Path(__file__).resolve().parents[1],
            )
            os.close(slave)
            output = bytearray()
            deadline = time.monotonic() + 5
            try:
                while b"SELECT MCP CLIENTS" not in output and time.monotonic() < deadline:
                    ready, _, _ = select.select([master], [], [], 0.1)
                    if ready:
                        output.extend(os.read(master, 4096))
                self.assertIn(b"BUILDANCHOR", output)
                self.assertIn(b"SELECT MCP CLIENTS", output)
                os.write(master, b"\x1b[B \r")  # Down, Space, Enter: select Cursor.
                while process.poll() is None and time.monotonic() < deadline:
                    ready, _, _ = select.select([master], [], [], 0.1)
                    if ready:
                        try:
                            output.extend(os.read(master, 4096))
                        except OSError:
                            break
                self.assertEqual(process.wait(timeout=1), 0)
            finally:
                if process.poll() is None:
                    process.kill()
                os.close(master)

            self.assertTrue((workspace / ".cursor" / "mcp.json").is_file())
            self.assertIn(b"\x1b[?1049h", output)

    @unittest.skipIf(os.name == "nt", "PTY integration is POSIX-specific")
    def test_interactive_plan_prompts_for_objective_in_a_real_tty(self) -> None:
        import pty
        import select

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "package.json").write_text('{"name":"sample"}\n', encoding="utf-8")
            master, slave = pty.openpty()
            self._set_pty_size(slave)
            process = subprocess.Popen(
                [sys.executable, "-m", "buildanchor", "plan", "-i", "--workspace", str(workspace)],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=Path(__file__).resolve().parents[1],
            )
            os.close(slave)
            output = bytearray()
            deadline = time.monotonic() + 5
            try:
                while b"Objective" not in output and time.monotonic() < deadline:
                    ready, _, _ = select.select([master], [], [], 0.1)
                    if ready:
                        output.extend(os.read(master, 4096))
                self.assertIn(b"PLAN  /  INTERACTIVE MODE", output)
                self.assertIn(b"Objective", output)
                os.write(master, b"Add a health endpoint\r")
                while process.poll() is None and time.monotonic() < deadline:
                    ready, _, _ = select.select([master], [], [], 0.1)
                    if ready:
                        try:
                            output.extend(os.read(master, 4096))
                        except OSError:
                            break
                self.assertIn(process.wait(timeout=1), {0, 2})
            finally:
                if process.poll() is None:
                    process.kill()
                os.close(master)

    def test_exit_on_mismatch(self) -> None:
        import json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({"name": "node-app"}), encoding="utf-8")

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["plan", "--workspace", str(root), "--objective", "Add a JPA entity", "--exit-on-mismatch"])
            self.assertEqual(code, 3)

    def test_assert_ecosystem(self) -> None:
        import json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({"name": "node-app"}), encoding="utf-8")

            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = main(["preflight", "--workspace", str(root), "--assert-ecosystem", "maven"])
            self.assertEqual(code, 3)
            self.assertIn("assert: FAIL", err.getvalue())

    def test_agent_flag(self) -> None:
        import json
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({"name": "node-app"}), encoding="utf-8")

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["preflight", "--workspace", str(root), "--agent"])
            self.assertEqual(code, 0)
            self.assertTrue(out.getvalue().startswith("# BuildAnchor Build Truth"))


if __name__ == "__main__":
    unittest.main()
