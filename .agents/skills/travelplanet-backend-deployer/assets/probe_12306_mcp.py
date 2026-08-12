"""Bounded smoke test for the vendored 12306 MCP server inside the image."""

from __future__ import annotations

import asyncio

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = "/app/vendor/12306-mcp/node_modules/12306-mcp/build/index.js"


async def main() -> None:
    params = StdioServerParameters(command="node", args=[SERVER])
    async with asyncio.timeout(60):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()
                names = [tool.name for tool in tools.tools]
                print(f"server={initialized.serverInfo.name} version={initialized.serverInfo.version}")
                print("tools=" + ",".join(names))
                if "get-current-date" not in names:
                    raise RuntimeError("get-current-date tool is missing")
                result = await session.call_tool("get-current-date", {})
                if result.isError or not result.content:
                    raise RuntimeError("get-current-date failed")
                print("current-date=" + result.content[0].text)


if __name__ == "__main__":
    asyncio.run(main())
