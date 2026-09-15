"""Explicit compatibility responses for unsupported private PoE2 stash tools.

GGG documents account/guild/public stashes as PoE1-only. This server performs
no credential discovery. OAuth character authorization is an explicit opt-in; stash
access remains outside the documented PoE2 API. Clipboard analysis is local.
"""

import anyio
from mcp.types import Tool
from mcp_server_utils import Server, result, run_server
from rare_analysis import analyze_clipboard

app = Server("poe-stash")

TOOLS = [
    Tool(
        name="poe_auth",
        description="Default: local OAuth status/configuration requirements. action=authorize starts the registered-public-client PKCE flow only with confirm=true and POE_OAUTH_ENABLED=1. Never supply tokens in tool arguments.",
        inputSchema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "authorize"],
                    "default": "status",
                },
                "confirm": {"type": "boolean", "default": False},
                "timeout": {
                    "type": "integer",
                    "minimum": 10,
                    "maximum": 180,
                    "default": 120,
                },
            },
            "additionalProperties": False,
        },
    ),
    Tool(
        name="poe_auth_status",
        description="Inspect only the explicitly configured owned OAuth store. Report local expiry/scope/state without token values, refresh, introspection, or any network request.",
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    Tool(
        name="get_tab",
        description="Get all items from a stash tab by name or index. Uses 5-minute cache.",
        inputSchema={
            "type": "object",
            "properties": {
                "tab_name": {
                    "type": "string",
                    "description": "Tab name (case-insensitive). Use this OR tab_index.",
                },
                "tab_index": {
                    "type": "integer",
                    "description": "Tab index (0-based). Use this OR tab_name.",
                },
                "force": {
                    "type": "boolean",
                    "description": "Force refresh, bypassing cache (default false).",
                },
            },
        },
    ),
    Tool(
        name="list_tabs",
        description="List all stash tab names and indices.",
        inputSchema={
            "type": "object",
            "properties": {
                "force": {
                    "type": "boolean",
                    "description": "Force refresh tab list (default false).",
                },
            },
        },
    ),
    Tool(
        name="score_rare",
        description="Score a rare item from PoE clipboard text (Ctrl+C format). Returns price estimate and mod breakdown.",
        inputSchema={
            "type": "object",
            "properties": {
                "item_text": {
                    "type": "string",
                    "description": "Raw item text as copied from PoE (Ctrl+C).",
                },
            },
            "required": ["item_text"],
        },
    ),
    Tool(
        name="price_tab",
        description="Score and price all rare items in a stash tab. Returns sorted list with price estimates.",
        inputSchema={
            "type": "object",
            "properties": {
                "tab_name": {
                    "type": "string",
                    "description": "Tab name (case-insensitive). Use this OR tab_index.",
                },
                "tab_index": {
                    "type": "integer",
                    "description": "Tab index (0-based).",
                },
                "min_price": {
                    "type": "integer",
                    "description": "Only show items worth at least this many chaos (default 1).",
                },
                "force": {
                    "type": "boolean",
                    "description": "Force refresh stash data (default false).",
                },
            },
        },
    ),
    Tool(
        name="find_items",
        description="Search stash tabs for items matching a query (name, base type, or mod text).",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term — matches item name, base type, or mod text.",
                },
                "tab_name": {
                    "type": "string",
                    "description": "Search only this tab (optional — searches first 10 tabs if omitted).",
                },
                "force": {
                    "type": "boolean",
                    "description": "Force refresh (default false).",
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="cache_status",
        description="Show cache freshness for stash tabs.",
        inputSchema={"type": "object", "properties": {}},
    ),
]


for tool in TOOLS:
    if tool.name == "score_rare":
        tool.description = "Analyze supplied PoE2 Rare/Magic clipboard mods locally and suggest comparison criteria; no calibrated price or network request."
    elif tool.name not in ("poe_auth", "poe_auth_status"):
        tool.description = "Not implemented in this bounded PoE2 port; see PORT_REPORT.md for preserved originals and exact API evidence."


@app.list_tools()
async def list_tools():
    return TOOLS


@app.call_tool()
async def call_tool(name, arguments):
    if name == "score_rare":
        return result(analyze_clipboard(arguments["item_text"]))
    if name in ("poe_auth", "poe_auth_status"):
        from poe_oauth import token_status, run_auth_flow

        if name == "poe_auth_status" or arguments.get("action", "status") == "status":
            return result(await anyio.to_thread.run_sync(token_status))
        from functools import partial

        return result(
            await anyio.to_thread.run_sync(
                partial(
                    run_auth_flow,
                    confirm=arguments.get("confirm", False),
                    timeout=arguments.get("timeout", 120),
                )
            )
        )
    raise NotImplementedError(
        "Unavailable in this bounded port: GGG account/guild/public stash APIs are documented as PoE1-only. This does not mean PoE2 OAuth character access is unavailable. See PORT_REPORT.md."
    )


if __name__ == "__main__":
    run_server(app)
