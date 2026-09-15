"""Aggregate only the six servers shipped in this submodule, over MCP stdio.

Sibling PoB servers run through the suite's own configuration. Missing owned
modules, invalid tool schemas, and duplicate names are fatal startup errors.
"""

import importlib
from pathlib import Path
import jsonschema
from mcp_server_utils import Server, run_server

OWNED_MODULES = (
    "poe_market",
    "poe_stash",
    "poe_trade",
    "poe_char",
    "poe_pricer",
    "poe_filter",
)


def load_registry(module_names=OWNED_MODULES):
    registry = {}
    for module_name in module_names:
        try:
            module = importlib.import_module(module_name)
            if (
                Path(module.__file__).resolve().parent
                != Path(__file__).resolve().parent
            ):
                raise ValueError("Module resolved outside this submodule")
            if (
                not module.TOOLS
                or not callable(module.call_tool)
                or not callable(module.list_tools)
            ):
                raise ValueError("Expected TOOLS, list_tools and call_tool")
            for tool in module.TOOLS:
                jsonschema.Draft202012Validator.check_schema(tool.inputSchema)
                if tool.name in registry:
                    raise ValueError(f"Duplicate tool: {tool.name}")
                registry[tool.name] = (tool, module.call_tool)
        except Exception as exc:
            raise RuntimeError(
                f"Cannot load required module {module_name}: {exc}"
            ) from exc
    return registry


_tool_registry = load_registry()
_ALL_TOOLS = [entry[0] for entry in _tool_registry.values()]
combined = Server("poe-trade-mcp")


@combined.list_tools()
async def list_tools():
    return list(_ALL_TOOLS)


@combined.call_tool()
async def call_tool(name: str, arguments: dict):
    return await _tool_registry[name][1](name, arguments)


if __name__ == "__main__":
    run_server(combined)
