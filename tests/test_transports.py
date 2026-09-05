import io
import json
import tempfile
import unittest
from pathlib import Path

from buildanchor.transports import MCPServer


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
            self.assertTrue(any(tool["name"] == "build.context" for tool in payload["result"]["tools"]))

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
            self.assertEqual(content["command"], "npm run test")
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
            self.assertEqual(cmd["result"]["structuredContent"]["command"], "npm run test")


if __name__ == "__main__":
    unittest.main()

