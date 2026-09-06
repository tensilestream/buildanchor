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


class OpenAIDialectTests(unittest.TestCase):
    """LiteLLM, OpenAI, LangChain and most gateways use the function-calling shape.

    Three things differ from the Messages API and only the first is obvious:
    the schema is wrapped in a ``function`` object, the model returns arguments
    as a **JSON string** rather than a dict, and results go back as a
    ``role: "tool"`` message. Verified against LiteLLM 1.100.0, whose completion
    path accepted these schemas and whose ``ChatCompletionMessageToolCall``
    dispatched through ``run_tool_call`` unchanged.
    """

    def test_definitions_are_wrapped_in_a_function_object(self) -> None:
        for tool in agent.tool_definitions(format="openai"):
            self.assertEqual(tool["type"], "function")
            self.assertIn("function", tool)
            self.assertIn("name", tool["function"])
            self.assertIn("parameters", tool["function"])
            self.assertNotIn("input_schema", tool["function"])

    def test_both_dialects_describe_the_same_tools(self) -> None:
        anthropic_tools = agent.tool_definitions()
        openai_tools = agent.tool_definitions(format="openai")
        self.assertEqual(
            [tool["name"] for tool in anthropic_tools],
            [tool["function"]["name"] for tool in openai_tools],
        )
        for native, wrapped in zip(anthropic_tools, openai_tools, strict=True):
            self.assertEqual(native["description"], wrapped["function"]["description"])
            self.assertEqual(native["input_schema"], wrapped["function"]["parameters"])

    def test_an_unknown_format_is_refused(self) -> None:
        from buildanchor import BuildAnchorError
        with self.assertRaises(BuildAnchorError) as caught:
            agent.tool_definitions(format="cohere")
        # The error names what is supported, so the fix is obvious.
        for supported in agent.FORMATS:
            self.assertIn(supported, str(caught.exception))

    def test_arguments_arriving_as_a_json_string_are_parsed(self) -> None:
        """The mistake this helper exists to prevent."""
        call = {"id": "call_1", "type": "function",
                "function": {"name": "get_test_command", "arguments": '{"phase": "test"}'}}
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "package.json").write_text(
                json.dumps({"name": "x", "scripts": {"test": "node --test"}}), encoding="utf-8")
            result = agent.run_tool_call(call, workspace=directory)
        self.assertEqual(result["command"], "npm test")

    def test_arguments_already_a_dict_also_work(self) -> None:
        call = {"id": "c", "function": {"name": "get_test_command", "arguments": {"phase": "test"}}}
        self.assertNotIn("error", agent.run_tool_call(call, workspace="."))

    def test_an_object_shaped_tool_call_works(self) -> None:
        """LiteLLM returns objects, not dicts."""
        class Function:
            name, arguments = "get_test_command", "{}"

        class ToolCall:
            id, function = "call_9", Function()

        result = agent.run_tool_call(ToolCall(), workspace=".")
        self.assertNotIn("error", result)
        self.assertEqual(agent.tool_message(ToolCall(), result)["tool_call_id"], "call_9")

    def test_malformed_arguments_return_an_error_not_an_exception(self) -> None:
        call = {"id": "c", "function": {"name": "get_test_command", "arguments": "{not json"}}
        result = agent.run_tool_call(call, workspace=".")
        self.assertIn("error", result)
        self.assertIn("not valid JSON", result["error"])

    def test_non_object_arguments_are_refused(self) -> None:
        call = {"id": "c", "function": {"name": "get_test_command", "arguments": "[1,2]"}}
        self.assertIn("error", agent.run_tool_call(call, workspace="."))

    def test_a_tool_call_without_a_name_is_refused(self) -> None:
        self.assertIn("error", agent.run_tool_call({"id": "c", "function": {}}, workspace="."))

    def test_tool_message_has_the_shape_the_api_expects(self) -> None:
        call = {"id": "call_7", "function": {"name": "get_test_command", "arguments": "{}"}}
        message = agent.tool_message(call, agent.run_tool_call(call, workspace="."))
        self.assertEqual(message["role"], "tool")
        self.assertEqual(message["tool_call_id"], "call_7")
        self.assertEqual(message["name"], "get_test_command")
        json.loads(message["content"])

    def test_litellm_is_the_openai_dialect(self) -> None:
        self.assertEqual(agent.LITELLM_FORMAT, "openai")


class ProviderDialectTests(unittest.TestCase):
    """One tool surface, five dialects.

    Each shape was verified against the library that consumes it, not written
    from memory: ``google.genai.types.Tool`` accepted the Gemini declarations
    and ``FunctionResponse`` the results; botocore validated the Bedrock
    ``ToolConfiguration`` and ``ToolResultBlock`` against its own service model;
    LiteLLM 1.100.0's completion path accepted the OpenAI schemas. Those
    libraries are not test dependencies — these tests pin the shapes they
    approved so a change here fails locally.
    """

    def _project(self) -> str:
        directory = tempfile.mkdtemp()
        Path(directory, "package.json").write_text(
            json.dumps({"name": "x", "scripts": {"test": "node --test"}}), encoding="utf-8")
        return directory

    def test_every_dialect_describes_the_same_tools(self) -> None:
        expected = [tool["name"] for tool in agent.tool_definitions()]
        named = {
            "openai": lambda d: [t["function"]["name"] for t in d],
            "gemini": lambda d: [f["name"] for f in d[0]["function_declarations"]],
            "bedrock": lambda d: [t["toolSpec"]["name"] for t in d],
            "mcp": lambda d: [t["name"] for t in d],
        }
        for dialect, extract in named.items():
            self.assertEqual(extract(agent.tool_definitions(format=dialect)), expected, dialect)

    def test_every_dialect_carries_the_same_schema(self) -> None:
        native = {t["name"]: t["input_schema"] for t in agent.tool_definitions()}
        for tool in agent.tool_definitions(format="openai"):
            self.assertEqual(tool["function"]["parameters"], native[tool["function"]["name"]])
        for declaration in agent.tool_definitions(format="gemini")[0]["function_declarations"]:
            self.assertEqual(declaration["parameters_json_schema"], native[declaration["name"]])
        for tool in agent.tool_definitions(format="bedrock"):
            spec = tool["toolSpec"]
            self.assertEqual(spec["inputSchema"]["json"], native[spec["name"]])

    def test_gemini_uses_json_schema_not_the_openapi_subset(self) -> None:
        """`parameters` silently drops keywords it does not know; this does not."""
        declaration = agent.tool_definitions(format="gemini")[0]["function_declarations"][0]
        self.assertIn("parameters_json_schema", declaration)
        self.assertNotIn("parameters", declaration)

    def test_gemini_groups_declarations_under_one_tool(self) -> None:
        definitions = agent.tool_definitions(format="gemini")
        self.assertEqual(len(definitions), 1)
        self.assertEqual(len(definitions[0]["function_declarations"]), 3)

    def test_every_provider_call_shape_dispatches(self) -> None:
        workspace = self._project()
        calls = {
            "openai": {"id": "c1", "function": {"name": "get_test_command", "arguments": "{}"}},
            "anthropic": {"type": "tool_use", "id": "toolu_1",
                          "name": "get_test_command", "input": {}},
            "gemini": {"functionCall": {"name": "get_test_command", "args": {}}},
            "bedrock": {"toolUse": {"toolUseId": "tu1", "name": "get_test_command", "input": {}}},
        }
        for dialect, call in calls.items():
            result = agent.run_tool_call(call, workspace=workspace)
            self.assertNotIn("error", result, f"{dialect} tool call did not dispatch")
            self.assertEqual(result["command"], "npm test", dialect)

    def test_snake_case_call_shapes_also_work(self) -> None:
        """SDK objects and wire JSON disagree about casing; both are accepted."""
        call = {"function_call": {"name": "get_test_command", "args": {}}}
        self.assertNotIn("error", agent.run_tool_call(call, workspace=self._project()))

    def test_result_shapes_match_each_api(self) -> None:
        workspace = self._project()
        call = {"id": "c1", "function": {"name": "get_test_command", "arguments": "{}"}}
        result = agent.run_tool_call(call, workspace=workspace)

        self.assertEqual(agent.tool_result(call, result, format="openai")["role"], "tool")
        self.assertEqual(
            agent.tool_result({"id": "toolu_1", "name": "x", "input": {}}, result,
                              format="anthropic")["type"], "tool_result")
        gemini = agent.tool_result({"functionCall": {"name": "get_test_command", "args": {}}},
                                   result, format="gemini")
        self.assertEqual(gemini["functionResponse"]["name"], "get_test_command")
        self.assertIsInstance(gemini["functionResponse"]["response"], dict)
        bedrock = agent.tool_result(
            {"toolUse": {"toolUseId": "tu1", "name": "get_test_command", "input": {}}},
            result, format="bedrock")
        self.assertEqual(bedrock["toolResult"]["toolUseId"], "tu1")
        self.assertEqual(bedrock["toolResult"]["content"], [{"json": result}])

    def test_bedrock_reports_failure_as_a_status(self) -> None:
        """Converse has an explicit status; a failure should not just be text."""
        call = {"toolUse": {"toolUseId": "tu1", "name": "nope", "input": {}}}
        failed = agent.run_tool_call(call, workspace=self._project())
        block = agent.tool_result(call, failed, format="bedrock")
        self.assertEqual(block["toolResult"]["status"], "error")
        ok = agent.tool_result(call, {"command": "npm test"}, format="bedrock")
        self.assertNotIn("status", ok["toolResult"])

    def test_anthropic_results_mark_errors(self) -> None:
        block = agent.tool_result({"id": "t"}, {"error": "boom"}, format="anthropic")
        self.assertTrue(block["is_error"])

    def test_an_unknown_result_format_is_refused(self) -> None:
        from buildanchor import BuildAnchorError
        with self.assertRaises(BuildAnchorError):
            agent.tool_result({"id": "t"}, {}, format="cohere")

    def test_the_named_gateway_aliases_are_the_openai_dialect(self) -> None:
        for alias in (agent.LITELLM_FORMAT, agent.LANGCHAIN_FORMAT, agent.OPENROUTER_FORMAT):
            self.assertEqual(alias, "openai")

    def test_legacy_helpers_still_work(self) -> None:
        self.assertEqual(agent.tool_message({"id": "c"}, {})["role"], "tool")
        self.assertEqual(agent.tool_result_block("t", {})["type"], "tool_result")
