import io
import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from buildanchor.transports import MCPServer, advertised_tools


class TransportTests(unittest.TestCase):
    def test_mcp_initialize_and_inspect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({"scripts": {"test": "pytest"}}), encoding="utf-8")
            server = MCPServer(str(root))
            initialized = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
            self.assertEqual(initialized["result"]["serverInfo"]["name"], "buildanchor")
            response = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "build.inspect", "arguments": {}}})
            value = response["result"]["structuredContent"]
            self.assertEqual(value["build_systems"], ["node"])

    def test_mcp_preflight_returns_agent_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({"engines": {"node": ">=22"}}), encoding="utf-8")
            response = MCPServer(str(root)).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "build.preflight", "arguments": {"objective": "Add a feature"}}})
            value = response["result"]["structuredContent"]
            self.assertEqual(value["phase"], "preflight")
            self.assertIn("agent_context", value)

    def test_mcp_preflight_serializes_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "go.mod").write_text("module example.com/app\ngo 1.23\n", encoding="utf-8")
            response = MCPServer(str(root)).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "build.preflight", "arguments": {}}})
            self.assertIsInstance(response["result"]["structuredContent"]["evidence"][0], dict)

    def test_mcp_plan_returns_gates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({"scripts": {"test": "pytest"}}), encoding="utf-8")
            response = MCPServer(str(root)).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "build.plan", "arguments": {"objective": "Add a feature"}}})
            value = response["result"]["structuredContent"]
            self.assertEqual(value["status"], "ready")
            self.assertIn("validate", value["validation_gates"])

    def test_mcp_stdio_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "go.mod").write_text("module example.com/app\ngo 1.23\n", encoding="utf-8")
            input_stream = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n")
            output_stream = io.StringIO()
            MCPServer(str(root)).run(input_stream, output_stream)
            payload = json.loads(output_stream.getvalue())
            listed = [tool["name"] for tool in payload["result"]["tools"]]
            # tools/list advertises the three core tools by default: every
            # advertised schema is resident in the agent's context on every turn.
            self.assertEqual(listed, ["get_build_truth", "get_test_command", "find_package"])
            self.assertNotIn("build.context", listed)

    def test_mcp_tools_list_full_mode_restores_extended_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "go.mod").write_text("module example.com/app\ngo 1.23\n", encoding="utf-8")
            with unittest.mock.patch.dict(os.environ, {"BUILDANCHOR_MCP_TOOLS": "full"}):
                response = MCPServer(str(root)).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
            listed = [tool["name"] for tool in response["result"]["tools"]]
            self.assertIn("build.context", listed)
            self.assertGreater(len(listed), 3)

    def test_extended_tools_stay_dispatchable_when_unadvertised(self) -> None:
        """Trimming the listing must not break a caller that knows the name."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "go.mod").write_text("module example.com/app\ngo 1.23\n", encoding="utf-8")
            server = MCPServer(str(root))
            self.assertNotIn("build.context", [t["name"] for t in advertised_tools()])
            response = server.handle({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "build.context", "arguments": {}},
            })
            self.assertIn("result", response)

    def test_mcp_rejects_workspace_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            root.mkdir()
            server = MCPServer(str(root))
            response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "build.inspect", "arguments": {"workspace": ".."}}})
            self.assertEqual(response["error"]["code"], -32001)

    def test_mcp_find_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({"dependencies": {"lodash": "^4.17.21"}}), encoding="utf-8")
            server = MCPServer(str(root))
            response = server.handle({
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {"name": "build.find_package", "arguments": {"package": "lodash"}}
            })
            content = response["result"]["structuredContent"]
            self.assertTrue(content["found"])
            self.assertEqual(content["package"], "lodash")
            self.assertEqual(content["results"][0]["declared_version"], "^4.17.21")

    def test_mcp_cmd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({"scripts": {"test": "mocha", "build": "webpack"}}), encoding="utf-8")
            server = MCPServer(str(root))
            response = server.handle({
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {"name": "build.cmd", "arguments": {"phase": "test"}}
            })
            content = response["result"]["structuredContent"]
            self.assertEqual(content["command"], "npm test")
            self.assertEqual(content["phase"], "test")

    def test_mcp_core_tool_aliases_and_resources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text(json.dumps({"scripts": {"test": "mocha"}, "dependencies": {"express": "^4.18.2"}}), encoding="utf-8")
            server = MCPServer(str(root))

            # Test resources/list
            res = server.handle({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})
            self.assertEqual(res["result"]["resources"], [])

            # Test get_build_truth alias
            truth = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "get_build_truth", "arguments": {}}})
            self.assertIn("content", truth["result"]["structuredContent"])

            # Test find_package alias
            pkg = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "find_package", "arguments": {"package": "express"}}})
            self.assertTrue(pkg["result"]["structuredContent"]["found"])

            # Test get_test_command alias
            cmd = server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "get_test_command", "arguments": {}}})
            self.assertEqual(cmd["result"]["structuredContent"]["command"], "npm test")


if __name__ == "__main__":
    unittest.main()

