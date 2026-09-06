# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""What this tool is allowed to execute.

BuildAnchor runs build commands. That is the point, and it is also the reason a
careful person hesitates to install it. The honest answer to "what will this run
on my machine?" should not be a paragraph of reassurance — it should be an
inventory that a test keeps true.

Every subprocess call site in the package is enumerated below. Adding one makes
this test fail, which forces the addition to be deliberate and documented rather
than arriving quietly in a refactor.

The shape of the guarantee:

* Most execution is **read-only git**, used to learn what changed and which
  files are tracked. It cannot modify a repository.
* Exactly **two** call sites run project-defined code, both reached only from a
  command the user typed: ``verify``, and ``validate-change --execute``.
* Nothing uses a shell. Every call passes a fixed argument vector, so there is
  no string for a repository's contents to be interpolated into.
"""

import ast
import unittest
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "src" / "buildanchor"

#: Read-only git invocations: they observe a repository and cannot change one.
GIT_READ_ONLY = {
    ("build_truth/features/inspection.py", "_git_tracked_files"),
    ("build_truth/features/inspection.py", "_git_info"),
    ("build_truth/features/validation.py", "_git_changed_files"),
}

#: The only places project-defined code is ever executed. Both are reached only
#: from an explicit user command, and both are absent from the MCP and HTTP
#: surfaces, because a remote caller cannot consent to running local code.
EXECUTES_PROJECT_CODE = {
    ("build_truth/features/verification.py", "_run_probe"),
    ("build_truth/features/validation.py", "_execute_validation"),
}

ALLOWED = GIT_READ_ONLY | EXECUTES_PROJECT_CODE


def _call_sites() -> list[tuple[str, str, ast.Call]]:
    """Every ``subprocess`` call in the package, with its enclosing function."""
    sites: list[tuple[str, str, ast.Call]] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(SOURCE_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Call):
                    continue
                function = inner.func
                if (isinstance(function, ast.Attribute)
                        and isinstance(function.value, ast.Name)
                        and function.value.id == "subprocess"
                        and function.attr in {"run", "Popen", "call", "check_output", "check_call"}):
                    sites.append((relative, node.name, inner))
    return sites


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    return next((kw.value for kw in call.keywords if kw.arg == name), None)


class ExecutionSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sites = _call_sites()

    def test_every_call_site_is_accounted_for(self) -> None:
        """A new subprocess call must be a deliberate, documented decision."""
        found = {(path, function) for path, function, _call in self.sites}
        unexpected = sorted(found - ALLOWED)
        self.assertEqual(
            [], unexpected,
            "new subprocess call sites appeared; add them to the inventory in this "
            "test and to docs/EXECUTION.md, or remove them",
        )

    def test_no_call_site_has_disappeared(self) -> None:
        """Keeps the inventory honest in the other direction."""
        found = {(path, function) for path, function, _call in self.sites}
        missing = sorted(ALLOWED - found)
        self.assertEqual([], missing, "the inventory lists call sites that no longer exist")

    def test_nothing_uses_a_shell(self) -> None:
        """A shell would let repository contents become part of a command."""
        for path, function, call in self.sites:
            shell = _keyword(call, "shell")
            if shell is None:
                continue  # subprocess defaults to shell=False
            self.assertIsInstance(shell, ast.Constant, f"{path}:{function} passes a computed shell=")
            self.assertFalse(shell.value, f"{path}:{function} uses a shell")

    def test_every_call_is_bounded_by_a_timeout(self) -> None:
        """A build tool that hangs must not hang the agent that called it."""
        for path, function, call in self.sites:
            self.assertIsNotNone(
                _keyword(call, "timeout"), f"{path}:{function} can block forever",
            )

    def test_every_command_is_a_fixed_argument_vector(self) -> None:
        """The command is a list, never a string a repository can influence."""
        for path, function, call in self.sites:
            self.assertTrue(call.args, f"{path}:{function} passes no command")
            command = call.args[0]
            self.assertNotIsInstance(
                command, (ast.JoinedStr, ast.Constant),
                f"{path}:{function} builds its command as a string",
            )

    def test_only_two_places_run_project_code(self) -> None:
        """The claim the README makes, asserted rather than promised."""
        running_project_code = {
            (path, function) for path, function, _call in self.sites
        } - GIT_READ_ONLY
        self.assertEqual(
            running_project_code, EXECUTES_PROJECT_CODE,
            "the set of places that execute project-defined code has changed",
        )

    def test_project_code_execution_is_absent_from_remote_surfaces(self) -> None:
        """Neither MCP nor HTTP may reach an operation that executes code."""
        from buildanchor import operations
        from buildanchor.transports import HTTP_ENDPOINTS, advertised_tools
        for name in operations.LOCAL_ONLY:
            operation = next(o for o in operations.OPERATIONS if o.name == name)
            self.assertIsNone(operation.http_path)
            self.assertNotIn(operation.camel_case, [t["name"] for t in advertised_tools("full")])
        self.assertNotIn("/v1/verify", HTTP_ENDPOINTS)


class DocumentationTests(unittest.TestCase):
    """The inventory and the document that describes it must agree."""

    def test_execution_document_lists_every_call_site(self) -> None:
        document = SOURCE_ROOT.parent.parent / "docs" / "EXECUTION.md"
        if not document.is_file():
            self.fail("docs/EXECUTION.md is missing; the execution surface must be documented")
        text = document.read_text(encoding="utf-8")
        for path, function in sorted(ALLOWED):
            self.assertIn(function, text, f"{path}:{function} is not described in docs/EXECUTION.md")



class NetworkSurfaceTests(unittest.TestCase):
    """The engine must not reach the network, whatever the SDK does."""

    #: The SDK talks to an HTTP endpoint the *user* configures. Nothing else may.
    NETWORK_ALLOWED: frozenset[str] = frozenset({"sdk.py"})

    def test_only_the_sdk_can_reach_the_network(self) -> None:
        offenders = []
        for path in sorted(SOURCE_ROOT.rglob("*.py")):
            relative = path.relative_to(SOURCE_ROOT).as_posix()
            if relative in self.NETWORK_ALLOWED:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    root = name.split(".")[0]
                    if root in {"urllib", "socket", "requests", "httpx", "aiohttp", "ftplib", "telnetlib"}:
                        offenders.append(f"{relative} imports {name}")
        self.assertEqual([], offenders, "the engine gained a network dependency")

    def test_the_http_server_is_opt_in(self) -> None:
        """`serve` binds a socket, but only when the user runs that command."""
        transports = (SOURCE_ROOT / "transports.py").read_text(encoding="utf-8")
        self.assertIn("def serve_http", transports)
        self.assertIn('"127.0.0.1', (SOURCE_ROOT / "cli.py").read_text(encoding="utf-8"),
                      "the HTTP server should default to loopback, not a public interface")


if __name__ == "__main__":
    unittest.main()
