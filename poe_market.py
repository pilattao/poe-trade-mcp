"""Query real local PoE2 price observations; explicit refresh uses public ninja."""

import anyio
from mcp.types import Tool
from mcp_server_utils import Server, result, run_server
from poe_lib import resolve_league
from price_db import PriceCache
from poe_pricer import CATEGORIES, fetch_category

app = Server("poe-market")
LEAGUE = {
    "type": "string",
    "minLength": 1,
    "description": "Exact PoE2 league or POE_LEAGUE; no auto-selection.",
}
NAME = {"type": "string", "minLength": 1}
LIMIT = {"type": "integer", "minimum": 1, "maximum": 200}


def _tool(name, description, properties=None, required=()):
    return Tool(
        name=name,
        description=description,
        inputSchema={
            "type": "object",
            "properties": {"league": LEAGUE, **(properties or {})},
            "required": list(required),
            "additionalProperties": False,
        },
    )


TOOLS = [
    _tool(
        "get_price",
        "Get exact-name prices, preserving all variants, from the local PoE2 cache.",
        {"name": NAME},
        ["name"],
    ),
    _tool(
        "get_price_history",
        "Local observations for an exact name; includes category, currency, variant and fetch times.",
        {"name": NAME},
        ["name"],
    ),
    _tool(
        "search_items",
        "Search latest cached PoE2 category snapshots by literal name substring.",
        {"query": NAME, "limit": LIMIT},
        ["query"],
    ),
    *[
        _tool(
            name,
            description,
            {
                "min_snapshots": {"type": "integer", "minimum": 2},
                "limit": LIMIT,
                "min_price": {
                    "type": "number",
                    "minimum": 0,
                    "description": "Minimum price in each result’s reference currency.",
                },
            },
        )
        for name, description in [
            (
                "get_risers",
                "Positive changes across real observations of the same league/item/variant/currency.",
            ),
            (
                "get_fallers",
                "Negative changes across real observations of the same league/item/variant/currency.",
            ),
            (
                "get_movers",
                "Largest absolute percentage changes; mixed currencies are never summed.",
            ),
        ]
    ],
    _tool(
        "snapshot_status",
        "Show cache coverage and observation times for the exact PoE2 league.",
    ),
    _tool(
        "refresh_prices",
        "Fetch one public PoE2 category and persist a real snapshot; respects hourly HTTP cache.",
        {"category": {"type": "string", "enum": CATEGORIES}},
        ["category"],
    ),
]


def _dispatch(name, args):
    league = resolve_league(args.get("league"))
    db = PriceCache()
    if name == "snapshot_status":
        return db.status(league)
    if name == "refresh_prices":
        rows = fetch_category(league, args["category"])
        return {
            "league": league,
            "category": args["category"],
            "items": len(rows),
            "fetched_at": rows[0]["fetched_at"] if rows else None,
        }
    if db.status(league)["total_snapshots"] == 0:
        raise ValueError(
            "No PoE2 price observations for this league. Call refresh_prices with an explicit category first."
        )
    if name == "get_price":
        rows = [
            r
            for r in db.search(league, args["name"], limit=100000)
            if r["name"].casefold() == args["name"].casefold()
        ]
        if not rows:
            raise ValueError(
                "Exact item name is absent from the cached categories; refresh the relevant category"
            )
        return {"results": rows}
    if name == "get_price_history":
        return {"results": db.history(league, args["name"])}
    if name == "search_items":
        return {"results": db.search(league, args["query"], args.get("limit", 20))}
    return {
        "results": db.movers(
            league,
            args.get("min_snapshots", 3),
            args.get("limit", 25),
            {"get_risers": "up", "get_fallers": "down", "get_movers": "both"}[name],
            args.get("min_price", 0),
        )
    }


@app.list_tools()
async def list_tools():
    return TOOLS


@app.call_tool()
async def call_tool(name, arguments):
    return result(await anyio.to_thread.run_sync(_dispatch, name, arguments))


if __name__ == "__main__":
    run_server(app)
