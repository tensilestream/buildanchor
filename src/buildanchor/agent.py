# Copyright 2026 Tensilestream and BuildAnchor contributors
# SPDX-License-Identifier: Apache-2.0

"""Wiring BuildAnchor into an agent you are building.

Existing agents reach BuildAnchor over MCP. If you are *writing* an agent, MCP
is a lot of machinery for something that runs in your own process — you want the
tool definitions and a function that executes a tool call, and nothing else.

That is this module. The schemas are the same ones the MCP server advertises,
so an agent built this way and an agent using the MCP server see the identical
tool surface; there is no second definition to drift.

    import anthropic
    from buildanchor import agent

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-opus-5",
        max_tokens=16000,
        thinking={"type": "adaptive"},
        tools=agent.tool_definitions(),
        messages=[{"role": "user", "content": "Run this project's tests."}],
    )

    for block in response.content:
        if block.type == "tool_use":
            result = agent.run_tool(block.name, block.input, workspace=".")

`run_tool` returns a plain dict, ready to serialise into a ``tool_result``.
BuildAnchor has no runtime dependencies and this module adds none — it does not
import any model provider's SDK, and works with whichever one you use.
"""

from __future__ import annotations

import json
from typing import Any

from .build_truth.core.errors import BuildAnchorError
from .transports import TOOLS, MCPServer, advertised_tools

#: Tools that execute project-defined code. Excluded by default: an agent
#: deciding on its own to run your test suite should be a decision you opted
#: into, not a side effect of handing it a tool list.
EXECUTING_TOOLS: frozenset[str] = frozenset({"build.validate_change"})


#: Tool-schema dialects. The same three tools, shaped for whichever client you
#: use. Each shape was checked against the library that consumes it rather than
#: written from memory — the conversions and their sources:
#:
#: ``anthropic``  Messages API: ``name`` / ``description`` / ``input_schema``.
#: ``openai``     Function calling: a ``function`` object under a ``type`` tag.
#:                Verified through LiteLLM 1.100.0's completion path.
#: ``gemini``     ``function_declarations`` using ``parameters_json_schema``,
#:                which takes JSON Schema directly — verified by constructing a
#:                ``google.genai.types.Tool`` from it. The older ``parameters``
#:                field wants an OpenAPI 3.0 subset and silently drops keywords
#:                it does not know, so this avoids it.
#: ``bedrock``    Converse ``toolSpec`` with ``inputSchema: {"json": ...}``,
#:                read from botocore's own ``ToolSpecification`` service model.
#: ``mcp``        The Model Context Protocol shape, ``inputSchema``, unchanged.
FORMATS: tuple[str, ...] = ("anthropic", "openai", "gemini", "bedrock", "mcp")

#: Gateways and frameworks that normalise to the OpenAI function-calling shape.
#: Named because these are what people search for, not because they differ.
LITELLM_FORMAT = "openai"
LANGCHAIN_FORMAT = "openai"
OPENROUTER_FORMAT = "openai"


def tool_definitions(
    *,
    include_all: bool = False,
    include_executing: bool = False,
    format: str = "anthropic",
) -> list[dict[str, Any]]:
    """Tool definitions for a model client, in the dialect it expects.

    Returns the three core tools by default — `get_build_truth`,
    `get_test_command`, `find_package` — which cover the whole surface and cost
    about 700 tokens of schema per request. `include_all` adds the extended
    `build.*` tools, which overlap heavily and cost roughly 2,300; you rarely
    want that.

    `include_executing` adds tools that run project-defined code. Off by
    default, because handing an agent a tool list should not be how it acquires
    the ability to execute your test suite.

    `format` selects the dialect: ``"anthropic"`` for the Messages API,
    ``"openai"`` for the function-calling shape used by OpenAI, LiteLLM,
    LangChain and most gateways.
    """
    if format not in FORMATS:
        raise BuildAnchorError(
            f"unknown tool format '{format}'; supported: {', '.join(FORMATS)}"
        )
    selected = [
        tool for tool in (TOOLS if include_all else advertised_tools("core"))
        if include_executing or tool["name"] not in EXECUTING_TOOLS
    ]
    if format == "gemini":
        # Gemini groups declarations under one Tool rather than listing tools.
        return [{"function_declarations": [_to_gemini(tool) for tool in selected]}]
    shape = _SHAPES[format]
    return [shape(tool) for tool in selected]


def _to_messages_api(tool: dict[str, Any]) -> dict[str, Any]:
    """MCP names the field ``inputSchema``; the Messages API ``input_schema``."""
    return {
        "name": tool["name"],
        "description": tool["description"],
        "input_schema": _schema_of(tool),
    }


def _to_openai(tool: dict[str, Any]) -> dict[str, Any]:
    """The function-calling shape: a `function` object under a `type` tag."""
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": _schema_of(tool),
        },
    }


def _to_gemini(tool: dict[str, Any]) -> dict[str, Any]:
    """A function declaration carrying JSON Schema, not the OpenAPI subset.

    ``parameters_json_schema`` accepts JSON Schema as written.
    ``parameters`` expects Gemini's OpenAPI 3.0 subset and quietly ignores
    keywords outside it, which loses argument descriptions without any error.
    """
    return {
        "name": tool["name"],
        "description": tool["description"],
        "parameters_json_schema": _schema_of(tool),
    }


def _to_bedrock(tool: dict[str, Any]) -> dict[str, Any]:
    """The Converse API's ``toolSpec``, whose schema nests under ``json``."""
    return {
        "toolSpec": {
            "name": tool["name"],
            "description": tool["description"],
            "inputSchema": {"json": _schema_of(tool)},
        }
    }


def _to_mcp(tool: dict[str, Any]) -> dict[str, Any]:
    """The Model Context Protocol shape, which is what these already are."""
    return {
        "name": tool["name"],
        "description": tool["description"],
        "inputSchema": _schema_of(tool),
    }


_SHAPES: dict[str, Any] = {
    "anthropic": _to_messages_api,
    "openai": _to_openai,
    "bedrock": _to_bedrock,
    "mcp": _to_mcp,
}


def _schema_of(tool: dict[str, Any]) -> dict[str, Any]:
    return tool.get("inputSchema") or {"type": "object", "properties": {}}


def run_tool(name: str, arguments: dict[str, Any] | None = None,
             workspace: str = ".") -> dict[str, Any]:
    """Execute one tool call and return its result as a dict.

    Errors are returned rather than raised, shaped so they can go straight back
    to the model as a ``tool_result`` with ``is_error``. A model that is told
    what went wrong can correct itself; one that gets an exception in your
    process cannot.
    """
    try:
        return MCPServer(workspace).call_tool(name, arguments or {})
    except BuildAnchorError as exc:
        return {"error": str(exc), "tool": name}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "tool": name}


def tool_result_block(tool_use_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """Wrap a result as an Anthropic ``tool_result`` content block.

    Return every block from one turn in a single user message: splitting them
    across messages teaches the model to stop making parallel calls.
    """
    return tool_result({"id": tool_use_id}, result, format="anthropic")


def system_prompt_block(workspace: str = ".", objective: str = "") -> str:
    """The build truth for a repository, ready to inject into a system prompt.

    Cheaper than a tool call when you already know the agent will need it: one
    compact block, no round trip. Put it in the cached prefix of your system
    prompt rather than in the per-request tail.
    """
    from .engine import BuildAnchor
    return BuildAnchor(workspace).llm_prompt(objective).content


# ---------------------------------------------------------------------------
# OpenAI-shaped clients (LiteLLM, OpenAI, LangChain, most gateways)
#
# Three things differ from the Messages API, and only the first is obvious:
#   1. the tool schema is wrapped in a `function` object;
#   2. the model returns arguments as a **JSON string**, not a dict;
#   3. results go back as a `role: "tool"` message, not a content block.
# Getting (2) wrong produces a confusing TypeError deep in a tool; these
# helpers handle it.
# ---------------------------------------------------------------------------

def run_tool_call(tool_call: Any, workspace: str = ".") -> dict[str, Any]:
    """Execute one OpenAI-shaped tool call.

    Accepts the object LiteLLM and the OpenAI SDK return, or the equivalent
    dict. Malformed arguments come back as an error result rather than an
    exception, for the same reason `run_tool` does.
    """
    name, raw_arguments, _identifier = _unpack_tool_call(tool_call)
    if not name:
        return {"error": "tool call had no function name", "tool": None}

    if isinstance(raw_arguments, dict):
        arguments = raw_arguments
    else:
        try:
            arguments = json.loads(raw_arguments or "{}")
        except (TypeError, json.JSONDecodeError) as exc:
            return {"error": f"arguments were not valid JSON: {exc}", "tool": name}
        if not isinstance(arguments, dict):
            return {"error": "arguments must be a JSON object", "tool": name}

    return run_tool(name, arguments, workspace=workspace)


def _get(value: Any, *names: str) -> Any:
    """Read an attribute or a key, whichever the object happens to use.

    Providers return objects; their wire forms are dicts; tests use both.
    """
    for name in names:
        found = getattr(value, name, None)
        if found is None and isinstance(value, dict):
            found = value.get(name)
        if found is not None:
            return found
    return None


def _unpack_tool_call(tool_call: Any) -> tuple[str | None, Any, str | None]:
    """Read ``(name, arguments, id)`` from any provider's tool-call shape.

    Four shapes, all seen in the wild and all verified against the library that
    produces them: OpenAI's ``function``, Anthropic's ``tool_use``, Gemini's
    ``functionCall`` / ``function_call``, and Bedrock's ``toolUse``.
    """
    # Gemini: {"functionCall": {"name", "args"}}
    call = _get(tool_call, "functionCall", "function_call")
    if call is not None:
        return _get(call, "name"), _get(call, "args") or {}, _get(call, "id")

    # Bedrock Converse: {"toolUse": {"toolUseId", "name", "input"}}
    use = _get(tool_call, "toolUse", "tool_use")
    if use is not None:
        return _get(use, "name"), _get(use, "input") or {}, _get(use, "toolUseId", "tool_use_id")

    # OpenAI / LiteLLM: {"function": {"name", "arguments"}}
    function = _get(tool_call, "function")
    if function is not None:
        return (_get(function, "name"), _get(function, "arguments") or "{}",
                _get(tool_call, "id"))

    # Anthropic: a tool_use block carries name and input directly.
    name = _get(tool_call, "name")
    if name is not None:
        arguments = _get(tool_call, "input")
        if arguments is None:
            arguments = _get(tool_call, "arguments") or {}
        return name, arguments, _get(tool_call, "id")

    # No recognisable name — still surface the id, so a result can be addressed
    # to the call it answers even when the shape is only partly filled in.
    return None, "{}", _get(tool_call, "id", "tool_use_id", "toolUseId")


def tool_result(tool_call: Any, result: dict[str, Any],
                format: str = "openai") -> dict[str, Any]:
    """The result, shaped the way ``format``'s API expects to receive it.

    Each shape was read from the library that consumes it: ``role: "tool"`` for
    OpenAI-compatible clients, a ``tool_result`` content block for Anthropic,
    ``functionResponse`` for Gemini, and Converse's ``toolResult`` — which is
    the only one with an explicit ``status``, so a failure can be reported as a
    failure rather than as text that happens to say "error".
    """
    if format not in FORMATS:
        raise BuildAnchorError(
            f"unknown tool format '{format}'; supported: {', '.join(FORMATS)}"
        )
    name, _arguments, identifier = _unpack_tool_call(tool_call)
    failed = "error" in result
    payload = json.dumps(result, indent=2, sort_keys=True, default=str)

    if format == "anthropic":
        return {
            "type": "tool_result",
            "tool_use_id": identifier,
            "content": payload,
            "is_error": failed,
        }
    if format == "gemini":
        return {"functionResponse": {"name": name, "response": result}}
    if format == "bedrock":
        block: dict[str, Any] = {
            "toolResult": {
                "toolUseId": identifier,
                "content": [{"json": result}],
            }
        }
        if failed:
            block["toolResult"]["status"] = "error"
        return block
    return {
        "role": "tool",
        "tool_call_id": identifier,
        "name": name,
        "content": payload,
    }


def tool_message(tool_call: Any, result: dict[str, Any]) -> dict[str, Any]:
    """The ``role: "tool"`` message an OpenAI-shaped client expects back.

    One message per tool call, each carrying the ``tool_call_id`` it answers.
    Kept as the name people already use; ``tool_result(..., format=...)`` is the
    general form.
    """
    return tool_result(tool_call, result, format="openai")
