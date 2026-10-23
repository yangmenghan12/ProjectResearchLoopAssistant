"""MCP transport only. Tool registration, authorization and audit live in ToolGateway."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

if TYPE_CHECKING:
    from app.tools.gateway import ToolContext, ToolDefinition


async def call_bundled_mcp(name: str, args: dict, env: dict[str, str]) -> dict:
    params = StdioServerParameters(command=sys.executable,
        args=["-m", "app.assistant.mcp_server"],
        env={**os.environ, **env}, cwd=str(Path(__file__).resolve().parents[2]))
    async with asyncio.timeout(55):
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                result = await client.call_tool(name, args)
    if result.isError:
        raise ValueError("; ".join(c.text for c in result.content if c.type == "text"))
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(next(c.text for c in result.content if c.type == "text"))


def execute_mcp(tool: ToolDefinition, args: dict, context: ToolContext) -> dict:
    """Connection configuration comes exclusively from the host-bound context."""
    source = context.source
    env = {"RESEARCH_MCP_ROOT": str(context.root), "RESEARCH_MCP_ONLY_FILE": "",
           "RESEARCH_MCP_GITHUB_URL": "", "RESEARCH_MCP_WORKSPACE": "",
           "RESEARCH_MCP_SNAPSHOT_ID": ""}
    if tool.remote_name == "prepare_github":
        if source.source_type != "github_repo":
            raise ValueError("No GitHub source was attached to this task")
        env.update(RESEARCH_MCP_GITHUB_URL=source.normalized_uri,
                   RESEARCH_MCP_WORKSPACE=str(context.root), RESEARCH_MCP_SNAPSHOT_ID=str(uuid4()))
    elif source.source_type == "local_file":
        env["RESEARCH_MCP_ONLY_FILE"] = source.normalized_uri
    return asyncio.run(call_bundled_mcp(tool.remote_name, args, env))
