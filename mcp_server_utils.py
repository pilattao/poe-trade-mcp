"""MCP 1.x protocol adapter shared by owned servers."""

import functools
import json
import anyio
import jsonschema
from mcp.server import Server as SDKServer
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent

if not hasattr(SDKServer, "list_tools"):
    raise RuntimeError(
        "poe-trade-mcp requires mcp>=1.26,<2; install requirements.txt with a compatible interpreter"
    )


def result(data, *, error=False):
    text = (
        data
        if isinstance(data, str)
        else json.dumps(data, ensure_ascii=False, allow_nan=False)
    )
    return CallToolResult(content=[TextContent(type="text", text=text)], isError=error)


class Server(SDKServer):
    """Public decorators; keep errors intact in both direct and MCP dispatch."""

    def list_tools(self):
        parent = super().list_tools()

        def decorate(fn):
            self.tool_provider = fn
            return parent(fn)

        return decorate

    def call_tool(self, **kwargs):
        parent = super().call_tool(**kwargs)

        def decorate(fn):
            @functools.wraps(fn)
            async def guarded(name, arguments):
                try:
                    tools = await self.tool_provider()
                    tool = next((t for t in tools if t.name == name), None)
                    if tool is None:
                        raise ValueError(f"Unknown tool: {name}")
                    jsonschema.validate(arguments, tool.inputSchema)
                    value = await fn(name, arguments)
                    if isinstance(value, CallToolResult):
                        return value
                    # Retained local filter handlers return JSON text; preserve their failures.
                    failed = False
                    for content in value:
                        if isinstance(content, TextContent):
                            try:
                                data = json.loads(content.text)
                                failed |= isinstance(data, dict) and bool(
                                    data.get("error")
                                )
                            except ValueError:
                                failed |= content.text.startswith(
                                    ("Error:", "Error in ", "Unknown tool:", "HTTP ")
                                )
                    return CallToolResult(content=value, isError=failed)
                except jsonschema.ValidationError as exc:
                    return result(
                        {"error": "Invalid arguments", "detail": exc.message},
                        error=True,
                    )
                except Exception as exc:
                    return result(
                        {"error": type(exc).__name__, "detail": str(exc)}, error=True
                    )

            parent(guarded)
            return guarded

        return decorate


def run_server(app, **_kwargs):
    async def main():
        async with stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream, write_stream, app.create_initialization_options()
            )

    anyio.run(main)
