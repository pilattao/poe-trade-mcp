"""PoE Stash MCP Server — stash tab access and rare item scoring via MCP.

Wraps stash_cache.py (cached stash fetching) and rare_scorer.py (item pricing).
Requires poe_monitor config.json with poesessid, account, character.

Version: 1.0
"""
import asyncio
import json
import sys
from pathlib import Path

# Ensure this server's own directory is on the path so sibling modules are found
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from poe_lib import PoeApi, load_config
from stash_cache import StashCache
from rare_scorer import score_item, score_item_text, classify_item

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

app = Server("poe-stash")

# Lazy-initialized on first use
_api = None
_cache = None
_league = None
_last_sessid = None


def _detect_league(api: PoeApi, config: dict) -> str:
    """Detect current league from character data, falling back to env/config defaults."""
    import os
    if league := os.environ.get("POE_LEAGUE"):
        return league
    character = config.get("character", "")
    if character:
        try:
            data = api.get_items(character)
            return data.get("character", {}).get("league", "Mirage")
        except Exception:
            pass
    return config.get("league", "Mirage")


def _init():
    """Initialize API + cache from saved config. Reinitializes if SESSID changed."""
    global _api, _cache, _league, _last_sessid
    config = load_config()
    sessid = config["poesessid"]
    if _api is not None and sessid == _last_sessid:
        return
    _last_sessid = sessid
    _api = PoeApi(sessid, config["account"], config.get("character", ""),
                  config.get("contact_email", ""), config.get("client_id", ""))
    _league = _detect_league(_api, config)
    _cache = StashCache(_api, _league)


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


@app.list_tools()
async def list_tools():
    return TOOLS


def _item_summary(item):
    """Compact summary of an item dict."""
    name = item.get("name", "")
    base = item.get("typeLine", "")
    display = f"{name} {base}".strip() if name else base
    frame = item.get("frameType", 0)
    rarity = {0: "Normal", 1: "Magic", 2: "Rare", 3: "Unique"}.get(frame, "?")
    ilvl = item.get("ilvl", 0)
    mods = item.get("explicitMods", [])
    summary = {
        "name": display,
        "baseType": item.get("baseType", base),
        "rarity": rarity,
        "ilvl": ilvl,
        "category": classify_item(base),
        "mods": mods,
        "implicitMods": item.get("implicitMods", []),
        "craftedMods": item.get("craftedMods", []),
        "enchantMods": item.get("enchantMods", []),
        "sockets": item.get("sockets", []),
    }
    # Parse requirements (Level, Str, Dex, Int)
    reqs = item.get("requirements", [])
    if reqs:
        req_dict = {}
        for r in reqs:
            req_name = r.get("name", "")
            vals = r.get("values", [])
            if vals and vals[0]:
                req_dict[req_name] = int(vals[0][0])
        if req_dict:
            summary["requirements"] = req_dict
    # Include grid position if present (stash tab items)
    if "x" in item and "y" in item:
        summary["x"] = item["x"]
        summary["y"] = item["y"]
        summary["w"] = item.get("w", 1)
        summary["h"] = item.get("h", 1)
    return summary


@app.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        if name == "poe_auth":
            from poe_oauth import run_auth_flow
            config = load_config()
            client_id = arguments.get("client_id") or config.get("client_id", "")
            if not client_id:
                return [TextContent(type="text", text=(
                    "POE_CLIENT_ID is not set. Register a developer app at "
                    "https://www.pathofexile.com/developer, set redirect URI to "
                    "http://localhost:7878/callback, then add POE_CLIENT_ID to your "
                    ".mcp.json poe server env block and restart MCP servers."
                ))]
            _init()
            tokens = run_auth_flow(client_id, _api.user_agent if _api else "poe-trade-mcp/1.0")
            mins = int(tokens.get("expires_in", 3600)) // 60
            return [TextContent(type="text", text=(
                f"OAuth authorization successful! Token expires in {mins} minutes. "
                f"Stash tools will now use the OAuth API automatically."
            ))]

        if name == "poe_auth_status":
            from poe_oauth import token_status
            status = token_status()
            return [TextContent(type="text", text=json.dumps(status, indent=2))]

        _init()

        if name == "get_tab":
            force = arguments.get("force", False)
            tab_name = arguments.get("tab_name")
            tab_index = arguments.get("tab_index")
            if tab_name:
                items = _cache.get_tab_by_name(tab_name, force=force)
            elif tab_index is not None:
                items = _cache.get_tab(tab_index, force=force)
            else:
                return [TextContent(type="text", text="Error: provide tab_name or tab_index")]
            summaries = [_item_summary(i) for i in items]
            result = {"count": len(items), "items": summaries}
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "list_tabs":
            force = arguments.get("force", False)
            tabs = _cache.get_tab_list(force=force)
            tab_list = [{"index": t["i"], "name": t["n"], "type": t.get("type", "?")} for t in tabs]
            return [TextContent(type="text", text=json.dumps(tab_list, indent=2))]

        elif name == "score_rare":
            result = score_item_text(arguments["item_text"])
            if result is None:
                return [TextContent(type="text", text="Not a rare item or couldn't parse.")]
            out = {
                "name": result.name,
                "category": result.category,
                "ilvl": result.ilvl,
                "price_estimate": result.price_estimate,
                "total_score": result.total_score,
                "affix_count": result.affix_count,
                "good_mods": result.good_mod_count,
                "junk_mods": result.junk_count,
                "breakdown": result.breakdown,
            }
            return [TextContent(type="text", text=json.dumps(out, indent=2))]

        elif name == "price_tab":
            min_price = arguments.get("min_price", 1)
            tab_name = arguments.get("tab_name")
            tab_index = arguments.get("tab_index")

            # Cache-only: read directly from disk cache, never hit remote API
            from stash_cache import _cache_path, _tab_list_path
            if tab_name:
                tab_list_path = _tab_list_path(_cache.league)
                if not tab_list_path.exists():
                    return [TextContent(type="text", text="Error: no cached tab list. Run list_tabs first to populate cache.")]
                tabs = json.loads(tab_list_path.read_text())
                name_upper = tab_name.upper().replace(" ", "")
                matched_idx = None
                for t in tabs:
                    if t.get("n", "").upper().replace(" ", "") == name_upper:
                        matched_idx = t["i"]
                        break
                if matched_idx is None:
                    return [TextContent(type="text", text=f"Error: tab '{tab_name}' not found in cached tab list. Available: {[t['n'] for t in tabs]}")]
                tab_index = matched_idx

            if tab_index is None:
                return [TextContent(type="text", text="Error: provide tab_name or tab_index")]

            cache_file = _cache_path(_cache.league, tab_index)
            if not cache_file.exists():
                return [TextContent(type="text", text=f"Error: tab {tab_index} not cached. Run get_tab first to populate cache.")]
            items = json.loads(cache_file.read_text())

            rares = [i for i in items if i.get("frameType") == 2]
            scored = []
            for item in rares:
                result = score_item(item)
                if result.price_estimate >= min_price:
                    scored.append({
                        "name": result.name,
                        "category": result.category,
                        "price": result.price_estimate,
                        "score": result.total_score,
                        "good_mods": result.good_mod_count,
                        "junk_mods": result.junk_count,
                        "breakdown": result.breakdown,
                    })

            scored.sort(key=lambda x: x["score"], reverse=True)
            total = sum(s["price"] for s in scored)
            out = {
                "total_rares": len(rares),
                "priced_items": len(scored),
                "total_value": total,
                "items": scored,
            }
            return [TextContent(type="text", text=json.dumps(out, indent=2))]

        elif name == "find_items":
            force = arguments.get("force", False)
            query = arguments["query"].lower()
            tab_name = arguments.get("tab_name")

            if tab_name:
                items = _cache.get_tab_by_name(tab_name, force=force)
            else:
                items = _cache.get_tabs(range(10), force=force)

            matches = []
            for item in items:
                searchable = " ".join([
                    item.get("name", ""),
                    item.get("typeLine", ""),
                    " ".join(item.get("explicitMods", [])),
                    " ".join(item.get("implicitMods", [])),
                    " ".join(item.get("craftedMods", [])),
                ]).lower()
                if query in searchable:
                    summary = _item_summary(item)
                    # Add score if rare
                    if item.get("frameType") == 2:
                        result = score_item(item)
                        summary["price_estimate"] = result.price_estimate
                        summary["score"] = result.total_score
                    matches.append(summary)

            return [TextContent(type="text", text=json.dumps({"matches": len(matches), "items": matches}, indent=2))]

        elif name == "cache_status":
            tabs = _cache.get_tab_list()
            statuses = []
            for t in tabs[:15]:
                age = _cache.cache_age(t["i"])
                fresh = age is not None and age < 300
                statuses.append({
                    "index": t["i"],
                    "name": t["n"],
                    "cached": age is not None,
                    "age_seconds": round(age) if age else None,
                    "fresh": fresh,
                })
            return [TextContent(type="text", text=json.dumps({"league": _league, "tabs": statuses}, indent=2))]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        return [TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]


from mcp_server_utils import run_server

if __name__ == "__main__":
    run_server(app, port=8482, name="poe-stash")
