# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""Wiring BuildAnchor into an agent someone is building.

Existing agents reach it over MCP. Someone *writing* an agent wants the tool
definitions and a function that runs a tool call — MCP over a pipe is a lot of
machinery for something in your own process. These helpers are that, and these
tests pin the two properties that matter: the schemas are the same ones MCP
advertises, and a failing tool call comes back as a result the model can read
rather than an exception that kills the loop.
"""

import json
import tempfile
import unittest
from pathlib import Path

from buildanchor import agent


class ToolDefinitionTests(unittest.TestCase):
    def test_definitions_use_the_messages_api_field_name(self) -> None:
        for tool in agent.tool_definitions():
            self.assertIn("input_schema", tool, "MCP's inputSchema was not translated")
            self.assertNotIn("inputSchema", tool)
            self.assertEqual(tool["input_schema"]["type"], "object")
            self.assertTrue(tool["description"].strip())

    def test_the_default_set_is_the_three_core_tools(self) -> None:
        names = [tool["name"] for tool in agent.tool_definitions()]
        self.assertEqual(names, ["get_build_truth", "get_test_command", "find_package"])

    def test_definitions_come_from_the_same_place_mcp_advertises(self) -> None:
        """One definition, so an agent and an MCP client cannot see different tools."""
        from buildanchor.transports import advertised_tools
        advertised = {tool["name"]: tool for tool in advertised_tools("core")}
        for tool in agent.tool_definitions():
            self.assertIn(tool["name"], advertised)
            self.assertEqual(tool["description"], advertised[tool["name"]]["description"])
            self.assertEqual(tool["input_schema"], advertised[tool["name"]]["inputSchema"])

    def test_executing_tools_are_excluded_by_default(self) -> None:
        """Handing an agent a tool list should not grant it the ability to run code."""
        names = {tool["name"] for tool in agent.tool_definitions(include_all=True)}
        self.assertFalse(names & agent.EXECUTING_TOOLS)
        with_executing = {
            tool["name"] for tool in agent.tool_definitions(include_all=True, include_executing=True)
        }
        self.assertTrue(with_executing & agent.EXECUTING_TOOLS)

    def test_the_default_set_is_small_enough_to_send_every_turn(self) -> None:
        payload = json.dumps(agent.tool_definitions())
        self.assertLess(len(payload) // 4, 1200, "the default tool surface has grown expensive")


class RunToolTests(unittest.TestCase):
    def _project(self) -> str:
        directory = tempfile.mkdtemp()
        Path(directory, "package.json").write_text(
            json.dumps({"name": "x", "scripts": {"test": "node --test"}}), encoding="utf-8")
        return directory

    def test_a_tool_call_returns_a_plain_dict(self) -> None:
        result = agent.run_tool("get_test_command", {}, workspace=self._project())
        self.assertIsInstance(result, dict)
        self.assertEqual(result["command"], "npm test")
        self.assertIn("working_directory", result)

    def test_an_unknown_tool_returns_an_error_rather_than_raising(self) -> None:
        """An exception kills the loop; an error the model can read does not."""
        result = agent.run_tool("no_such_tool", {}, workspace=self._project())
        self.assertIn("error", result)
        self.assertIn("no_such_tool", result["error"])

    def test_a_refused_workspace_returns_an_error(self) -> None:
        result = agent.run_tool("get_test_command", {"workspace": "/etc"}, workspace=self._project())
        self.assertIn("error", result)

    def test_results_serialise_for_a_tool_result_block(self) -> None:
        result = agent.run_tool("get_test_command", {}, workspace=self._project())
        block = agent.tool_result_block("toolu_abc", result)
        self.assertEqual(block["type"], "tool_result")
        self.assertEqual(block["tool_use_id"], "toolu_abc")
        self.assertFalse(block["is_error"])
        json.loads(block["content"])  # must be valid JSON for the model to read

    def test_an_error_result_is_marked_is_error(self) -> None:
        block = agent.tool_result_block("toolu_abc", {"error": "boom"})
        self.assertTrue(block["is_error"])

    def test_system_prompt_block_is_injectable(self) -> None:
        block = agent.system_prompt_block(workspace=self._project())
        self.assertIn("BuildAnchor Build Truth", block)
        self.assertIn("npm test", block)


class CliBridgeTests(unittest.TestCase):
    """The Node and Java SDKs reach these helpers through the CLI."""

    def _run(self, *args: str) -> str:
        import contextlib
        import io

        from buildanchor.cli import main
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            main(list(args))
        return out.getvalue()

    def test_list_tools_emits_the_same_schemas(self) -> None:
        from buildanchor.transports import advertised_tools
        listed = json.loads(self._run("mcp", "--list-tools"))
        self.assertEqual([t["name"] for t in listed],
                         [t["name"] for t in advertised_tools("core")])

    def test_call_tool_executes_one_tool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "package.json").write_text(
                json.dumps({"name": "x", "scripts": {"test": "node --test"}}), encoding="utf-8")
            result = json.loads(self._run(
                "mcp", "--call-tool", "get_test_command", "--workspace", directory))
            self.assertEqual(result["command"], "npm test")

    def test_malformed_tool_input_is_refused_clearly(self) -> None:
        """The CLI reports a refusal as exit 4 with a readable message."""
        import contextlib
        import io

        from buildanchor.cli import main
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = main(["mcp", "--call-tool", "get_test_command", "--tool-input", "{not json"])
        self.assertEqual(code, 4)
        self.assertIn("not valid JSON", err.getvalue())


if __name__ == "__main__":
    unittest.main()
