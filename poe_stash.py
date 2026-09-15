"""Explicit compatibility responses for unsupported private PoE2 stash tools.

GGG documents account/guild/public stashes as PoE1-only. This server performs
no credential discovery, authorization flow or private reads. Clipboard analysis is local.
"""

from mcp.types import Tool
from mcp_server_utils import Server, result, run_server
from rare_analysis import analyze_clipboard

app = Server("poe-stash")

TOOLS = [
    Tool(
        name="poe_auth",
        description=(
            "Run the OAuth 2.1 authorization flow for the PoE API. "
            "Optional upgrade from POESESSID — required for the newer official stash API. "
            "Opens a browser window for you to authorize, then saves tokens automatically. "
            "Requires POE_CLIENT_ID env var (register at pathofexile.com/developer). "
            "Once authorized, stash tools automatically use OAuth for better reliability."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "client_id": {
                    "type": "string",
                    "description": "Your PoE developer app client_id. Overrides POE_CLIENT_ID env var.",
                },
            },
        },
    ),
    Tool(
        name="poe_auth_status",
        description="Check the status of the current OAuth token (valid, expired, or not set up).",
        inputSchema={"type": "object", "properties": {}},
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
    elif tool.name == "poe_auth_status":
        tool.description = "Report audited official PoE2 OAuth capability and this port status without reading tokens or making requests."
    else:
        tool.description = "Not implemented in this bounded PoE2 port; see PORT_REPORT.md for preserved originals and exact API evidence."


@app.list_tools()
async def list_tools():
    return TOOLS


@app.call_tool()
async def call_tool(name, arguments):
    if name == "score_rare":
        return result(analyze_clipboard(arguments["item_text"]))
    if name == "poe_auth_status":
        return result(
            {
                "official_poe2_character_api": True,
                "character_url_template": "https://api.pathofexile.com/character/poe2/{name}",
                "list_characters_url": "https://api.pathofexile.com/character/poe2",
                "required_scope": "account:characters",
                "oauth_flow_in_this_port": "not_implemented",
                "token_status": "not_inspected",
                "official_poe2_stash_api_documented": False,
                "evidence": "https://www.pathofexile.com/developer/docs/reference#characters",
                "authorization_docs": "https://www.pathofexile.com/developer/docs/authorization",
                "note": "PoE2 OAuth character access is documented. No token presence, validity, scopes or endpoint access has been tested. Stash sections are labeled PoE1-only.",
            }
        )
    if name == "poe_auth":
        raise NotImplementedError(
            "PoE2 OAuth character access exists (account:characters; /character/poe2), but this bounded port has not implemented/validated the authorization flow. See preserved originals and PORT_REPORT.md; no credentials are required for public snapshots."
        )
    raise NotImplementedError(
        "Unavailable in this bounded port: GGG account/guild/public stash APIs are documented as PoE1-only. This does not mean PoE2 OAuth character access is unavailable. See PORT_REPORT.md."
    )


if __name__ == "__main__":
    run_server(app)
