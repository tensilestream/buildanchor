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


def tool_definitions(
    *,
    include_all: bool = False,
    include_executing: bool = False,
) -> list[dict[str, Any]]:
    """Tool definitions for the Messages API, in ``input_schema`` form.

    Returns the three core tools by default — `get_build_truth`,
    `get_test_command`, `find_package` — which cover the whole surface and cost
    about 700 tokens of schema per request. `include_all` adds the extended
    `build.*` tools, which overlap heavily and cost roughly 2,300; you rarely
    want that.

    `include_executing` adds tools that run project-defined code. Off by
    default, because handing an agent a tool list should not be how it acquires
    the ability to execute your test suite.
    """
    selected = TOOLS if include_all else advertised_tools("core")
    return [
        _to_messages_api(tool)
        for tool in selected
        if include_executing or tool["name"] not in EXECUTING_TOOLS
    ]


def _to_messages_api(tool: dict[str, Any]) -> dict[str, Any]:
    """MCP names the field ``inputSchema``; the Messages API ``input_schema``."""
    return {
        "name": tool["name"],
        "description": tool["description"],
        "input_schema": tool.get("inputSchema") or {"type": "object", "properties": {}},
    }


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
    """Wrap a result as a ``tool_result`` content block.

    Return every block from one turn in a single user message: splitting them
    across messages teaches the model to stop making parallel calls.
    """
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": json.dumps(result, indent=2, sort_keys=True, default=str),
        "is_error": "error" in result,
    }


def system_prompt_block(workspace: str = ".", objective: str = "") -> str:
    """The build truth for a repository, ready to inject into a system prompt.

    Cheaper than a tool call when you already know the agent will need it: one
    compact block, no round trip. Put it in the cached prefix of your system
    prompt rather than in the per-request tail.
    """
    from .engine import BuildAnchor
    return BuildAnchor(workspace).llm_prompt(objective).content
