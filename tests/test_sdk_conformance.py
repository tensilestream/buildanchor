# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""Every SDK must offer the same product.

There are four client surfaces and nothing previously connected them, so they
drifted: the Java client was missing six operations the other two had, ``verify``
existed only in Python, and ``doctor`` existed nowhere but the CLI. A user who
picks the wrong language finds a smaller product and has no way to know that is
what happened.

These tests read the SDK sources and fail when one falls behind
``buildanchor.operations``. They are deliberately source-level: the Node and Java
clients cannot be imported from Python, and a check that only runs where a
toolchain happens to be installed is a check that silently stops running.
"""

import re
import unittest
from pathlib import Path

from buildanchor import operations
from buildanchor.sdk import AsyncBuildAnchorClient, BuildAnchorClient

REPO_ROOT = Path(__file__).resolve().parent.parent
NODE_SDK = REPO_ROOT / "sdk" / "node" / "src" / "index.js"
NODE_TYPES = REPO_ROOT / "sdk" / "node" / "src" / "index.d.ts"
JAVA_SDK = REPO_ROOT / "sdk" / "java" / "src" / "main" / "java" / "com" / "buildanchor" / "BuildAnchorClient.java"


def _stub_for(operation, language: str) -> str:
    """The code to add, so a conformance failure is a checklist and not a puzzle.

    Three SDKs are only a liability if keeping them in step is manual work.
    Telling a contributor exactly what to paste is cheaper than dropping a
    client that somebody depends on.
    """
    summary = operation.summary
    if language == "python":
        return (f'    def {operation.name}(self) -> dict[str, Any]:\n'
                f'        """{summary}"""\n'
                f'        return self._call("{operation.name}", {{}}, '
                f'lambda: self._engine.{operation.name}())')
    if language == "node":
        return (f'  /** {summary} */\n'
                f'  {operation.camel_case}() {{ return this.#call("{operation.name}", {{}}); }}')
    return (f'    /** {summary} */\n'
            f'    public BuildAnchorResponse {operation.camel_case}() '
            f'throws IOException, InterruptedException {{\n'
            f'        return call("{operation.name}", "{{}}", '
            f'"{operation.http_path or ""}", "{operation.name}");\n'
            f'    }}')


def _missing_report(missing_names: list[str], language: str) -> str:
    """A failure message a contributor can act on without reading this file."""
    if not missing_names:
        return ""
    wanted = [o for o in operations.OPERATIONS
              if (o.name in missing_names or o.camel_case in missing_names)]
    stubs = "\n\n".join(_stub_for(operation, language) for operation in wanted)
    return (f"\n\nThe {language} SDK is missing {len(wanted)} operation(s). "
            f"Add these, adjusting arguments to match the operation:\n\n{stubs}\n")


class OperationRegistryTests(unittest.TestCase):
    def test_names_are_unique(self) -> None:
        self.assertEqual(len(operations.OPERATION_NAMES), len(set(operations.OPERATION_NAMES)))
        self.assertEqual(len(operations.CAMEL_CASE_NAMES), len(set(operations.CAMEL_CASE_NAMES)))

    def test_local_only_operations_have_no_http_route(self) -> None:
        """An operation that executes project code must not be reachable remotely."""
        for operation in operations.OPERATIONS:
            if operation.local_only:
                self.assertIsNone(operation.http_path, f"{operation.name} is local-only but routed")

    def test_every_http_route_is_served(self) -> None:
        from buildanchor.transports import HTTP_ENDPOINTS
        for route in operations.http_routes():
            self.assertIn(route, HTTP_ENDPOINTS, f"{route} is declared but the transport does not serve it")


class PythonSDKConformanceTests(unittest.TestCase):
    def test_sync_client_implements_every_operation(self) -> None:
        missing = [name for name in operations.OPERATION_NAMES if not hasattr(BuildAnchorClient, name)]
        self.assertEqual([], missing, _missing_report(missing, "python"))

    def test_async_client_matches_the_sync_one(self) -> None:
        missing = [name for name in operations.OPERATION_NAMES if not hasattr(AsyncBuildAnchorClient, name)]
        self.assertEqual([], missing, "the async client has fallen behind the sync one")

    def test_local_only_operations_refuse_a_remote_client(self) -> None:
        from buildanchor.build_truth.core.errors import BuildAnchorError
        client = BuildAnchorClient(workspace=str(REPO_ROOT), endpoint="http://example.invalid")
        for name in operations.LOCAL_ONLY:
            with self.assertRaises(BuildAnchorError, msg=f"{name} did not refuse a remote client"):
                getattr(client, name)()


class NodeSDKConformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        if not NODE_SDK.is_file():
            self.skipTest("Node SDK source not present")
        self.source = NODE_SDK.read_text(encoding="utf-8")

    def _declared_methods(self) -> set[str]:
        # Class methods are declared as `name(` or `async name(` at two-space
        # indentation inside the client class.
        return set(re.findall(r"(?m)^  (?:async )?([a-zA-Z][a-zA-Z0-9]*)\s*\(", self.source))

    def test_implements_every_operation(self) -> None:
        declared = self._declared_methods()
        missing = [name for name in operations.CAMEL_CASE_NAMES if name not in declared]
        self.assertEqual([], missing, _missing_report(missing, "node"))

    def test_local_only_operations_refuse_an_endpoint(self) -> None:
        self.assertIn("LOCAL_ONLY", self.source,
                      "the Node SDK does not guard local-only operations against remote use")

    def test_type_declarations_match_the_implementation(self) -> None:
        if not NODE_TYPES.is_file():
            self.skipTest("Node type declarations not present")
        types = NODE_TYPES.read_text(encoding="utf-8")
        missing = [name for name in operations.CAMEL_CASE_NAMES if name not in types]
        self.assertEqual([], missing, "index.d.ts has fallen behind index.js")


class JavaSDKConformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        if not JAVA_SDK.is_file():
            self.skipTest("Java SDK source not present")
        self.source = JAVA_SDK.read_text(encoding="utf-8")

    def test_implements_every_operation(self) -> None:
        declared = set(re.findall(r"public\s+[A-Za-z<>]+\s+([a-zA-Z][a-zA-Z0-9]*)\s*\(", self.source))
        missing = [name for name in operations.CAMEL_CASE_NAMES if name not in declared]
        self.assertEqual([], missing, _missing_report(missing, "java"))

    def test_local_only_operations_refuse_an_endpoint(self) -> None:
        self.assertIn("local-only", self.source,
                      "the Java SDK does not guard local-only operations against remote use")


class TransportConformanceTests(unittest.TestCase):
    def test_every_sdk_route_is_reachable_over_http(self) -> None:
        """A route an SDK calls must exist, or that SDK is broken in HTTP mode."""
        from buildanchor.transports import HTTP_ENDPOINTS
        node_source = NODE_SDK.read_text(encoding="utf-8") if NODE_SDK.is_file() else ""
        java_source = JAVA_SDK.read_text(encoding="utf-8") if JAVA_SDK.is_file() else ""
        for source, label in ((node_source, "Node"), (java_source, "Java")):
            for route in set(re.findall(r'"(/v1/[a-z-]+)"', source)):
                self.assertIn(route, HTTP_ENDPOINTS, f"the {label} SDK calls {route}, which is not served")



class ResponseShapeTests(unittest.TestCase):
    """The same operation must return the same shape on every transport.

    ``modules`` returned a bare array from the CLI and an envelope from HTTP,
    MCP and the Python SDK — so an SDK saw a different contract in local mode
    than over the wire, for the same call against the same repository.
    """

    def setUp(self) -> None:
        import json
        import tempfile

        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        root = Path(self.tempdir.name)
        (root / "web").mkdir()
        (root / "web" / "package.json").write_text(
            json.dumps({"name": "web", "scripts": {"test": "node --test"}}), encoding="utf-8")
        (root / "api").mkdir()
        (root / "api" / "pyproject.toml").write_text(
            '[project]\nname = "api"\nversion = "1"\n', encoding="utf-8")
        self.root = root

    def _cli_json(self, *args: str):
        import contextlib
        import io
        import json

        from buildanchor.cli import main
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            main([*args, "--workspace", str(self.root), "--format", "json"])
        return json.loads(out.getvalue())

    def test_modules_has_one_shape_everywhere(self) -> None:
        from buildanchor.sdk import BuildAnchorClient
        from buildanchor.transports import MCPServer

        cli = self._cli_json("modules")
        sdk = BuildAnchorClient(workspace=str(self.root)).modules()
        mcp = {k: v for k, v in MCPServer(str(self.root)).call_tool("build.modules", {}).items()
               if k != "_deprecation"}

        self.assertEqual(set(cli), set(sdk), "CLI and Python SDK disagree about modules")
        self.assertEqual(set(sdk), set(mcp), "Python SDK and MCP disagree about modules")
        self.assertIsInstance(cli["modules"], list)
        self.assertEqual(cli["is_monorepo"], sdk["is_monorepo"])

    def test_resolve_command_has_one_shape_everywhere(self) -> None:
        from buildanchor.sdk import BuildAnchorClient
        from buildanchor.transports import MCPServer

        cli = self._cli_json("cmd", "test")
        sdk = BuildAnchorClient(workspace=str(self.root)).resolve_command("test")
        mcp = MCPServer(str(self.root)).call_tool("get_test_command", {})
        for key in ("command", "working_directory", "command_status", "phase"):
            self.assertIn(key, cli, f"CLI is missing {key}")
            self.assertIn(key, sdk, f"the Python SDK is missing {key}")
            self.assertIn(key, mcp, f"MCP is missing {key}")

    def test_is_monorepo_agrees_with_the_report(self) -> None:
        from buildanchor import BuildAnchor
        from buildanchor.sdk import BuildAnchorClient
        engine = BuildAnchor(str(self.root))
        self.assertEqual(
            BuildAnchorClient(workspace=str(self.root)).modules()["is_monorepo"],
            engine.inspect().repository["is_monorepo"],
            "the module listing and the report disagree about repository shape",
        )




def operations_schema_versions() -> tuple[str, ...]:
    from buildanchor import schema
    return schema.SUPPORTED_SCHEMAS


class SDKTestSuiteTests(unittest.TestCase):
    """The SDK suites must track the schema, not a literal that goes stale."""

    def test_node_tests_do_not_pin_an_outdated_schema(self) -> None:
        from buildanchor import schema
        path = REPO_ROOT / "sdk" / "node" / "test" / "index.test.js"
        if not path.is_file():
            self.skipTest("Node SDK tests not present")
        source = path.read_text(encoding="utf-8")
        for stale in {v for v in operations_schema_versions() if v != schema.CURRENT_SCHEMA}:
            self.assertNotIn(
                f'schema_version, "{stale}"', source,
                f"the Node SDK suite pins schema {stale}, which is no longer current",
            )
        self.assertIn(f'"{schema.CURRENT_SCHEMA}"', source,
                      "the Node SDK suite does not reference the current schema")


if __name__ == "__main__":
    unittest.main()
