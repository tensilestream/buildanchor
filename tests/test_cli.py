import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from buildanchor.cli import main


class CLITests(unittest.TestCase):
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
            self.assertEqual(out.getvalue().strip(), "npm run test")

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
        import json
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
            self.assertTrue((root / "AGENT.md").is_file())
            self.assertIn("BuildAnchor Verified", out.getvalue())

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
