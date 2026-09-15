"""Anonymous, read-only PoE2 trade search and metadata with explicit failures."""

import math
import re
import urllib.parse
import anyio
from mcp.types import Tool
from mcp_server_utils import Server, result, run_server
from poe_lib import resolve_league
from public_http import request_json, USER_AGENT

TRADE_BASE = "https://www.pathofexile.com/api/trade2"
TRADE_SITE = "https://www.pathofexile.com/trade2/search/poe2/"
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
    "Content-Type": "application/json",
}
NON_AFFILIATION_NOTICE = "Not affiliated with or endorsed by Grinding Gear Games."
app = Server("poe-trade")


def _load_headers():
    return dict(HEADERS)


def _get_json(url):
    return request_json(url, ttl=3600 if "/data/" in url else 0)[0]


def _post_json(url, payload):
    return request_json(url, payload=payload)[0]


def _metadata(kind):
    data = _get_json(TRADE_BASE + "/data/" + kind)
    if not isinstance(data, dict) or not isinstance(data.get("result"), list):
        raise ValueError(f"Invalid PoE2 {kind} metadata")
    return data["result"]


def _get_stats():
    return [
        {"id": e["id"], "text": e["text"], "type": g.get("label", "")}
        for g in _metadata("stats")
        for e in g.get("entries", [])
    ]


def _filter_options(group, name):
    for g in _metadata("filters"):
        if g["id"] == group:
            for f in g["filters"]:
                if f["id"] == name:
                    return f.get("option", {}).get("options", [])
    raise ValueError(f"PoE2 source no longer exposes {group}.{name}")


def _validate_search_metadata(args):
    league = resolve_league(args.get("league"))
    leagues = [l["id"] for l in _metadata("leagues") if l.get("realm") == "poe2"]
    if league not in leagues:
        raise ValueError(f"League {league!r} is not listed by the PoE2 trade source")
    for key, group, field in [
        ("category", "type_filters", "category"),
        ("rarity", "type_filters", "rarity"),
    ]:
        value = args.get(key)
        if (
            value
            and value != "any"
            and value not in [o["id"] for o in _filter_options(group, field)]
        ):
            raise ValueError(f"Unknown PoE2 {key}: {value}; use get_trade_filters")
    if args.get("stats"):
        known = {s["id"] for s in _get_stats()}
        unknown = [s["id"] for s in args["stats"] if s["id"] not in known]
        if unknown:
            raise ValueError(f"Unknown PoE2 stat IDs: {unknown}")


def _range(value, label):
    out = {}
    for key in ("min", "max"):
        if key in value:
            number = value[key]
            if (
                isinstance(number, bool)
                or not isinstance(number, (int, float))
                or not math.isfinite(number)
            ):
                raise ValueError(f"{label}.{key} must be a finite number")
            out[key] = number
    if "min" in out and "max" in out and out["min"] > out["max"]:
        raise ValueError(f"{label}: min exceeds max")
    return out


def _build_search_payload(arguments):
    if "min_links" in arguments:
        raise ValueError("PoE1 linked socket filters are unsupported in PoE2")
    stats = arguments.get("stats", [])
    if not isinstance(stats, list):
        raise ValueError("stats must be an array of PoE2 stat filters")
    instant = arguments.get("instant_buyout", False)
    status = (
        "securable"
        if instant
        else ("online" if arguments.get("online_only", True) else "any")
    )
    query = {
        "status": {"option": status},
        "stats": [{"type": "and", "filters": []}],
        "filters": {},
    }
    for key, target in [("name", "name"), ("base_type", "type")]:
        if arguments.get(key):
            query[target] = arguments[key]
    for stat in stats:
        if not isinstance(stat, dict) or not stat.get("id"):
            raise ValueError("Every stat requires an id")
        query["stats"][0]["filters"].append(
            {"id": stat["id"], "disabled": False, "value": _range(stat, "stat")}
        )
    type_filters = {
        key: {"option": arguments[key]}
        for key in ("category", "rarity")
        if arguments.get(key) and arguments[key] != "any"
    }
    if type_filters:
        query["filters"]["type_filters"] = {"filters": type_filters}
    if "max_level" in arguments:
        query["filters"]["req_filters"] = {
            "filters": {"lvl": {"max": arguments["max_level"]}}
        }
    price = _range(
        {
            key: arguments["%s_price" % key]
            for key in ("min", "max")
            if "%s_price" % key in arguments
        },
        "price",
    )
    if any(v < 0 for v in price.values()):
        raise ValueError("Price cannot be negative")
    # The metadata null option denotes omission; sending JSON null is HTTP 400.
    trade = {}
    if price:
        currency = arguments.get("price_currency", "divine")
        if currency not in ("divine", "exalted", "chaos"):
            raise ValueError("Supported price currencies: divine, exalted, chaos")
        trade["price"] = {**price, "option": currency}
    if arguments.get("account"):
        trade["account"] = {"input": arguments["account"]}
    query["filters"]["trade_filters"] = {"filters": trade}
    return {"query": query, "sort": {"price": "asc"}}


def _search(args):
    payload = _build_search_payload(args)
    _validate_search_metadata(args)
    league = resolve_league(args.get("league"))
    encoded = urllib.parse.quote(league, safe="")
    data = _post_json(TRADE_BASE + "/search/poe2/" + encoded, payload)
    query_id = data.get("id")
    if (
        not isinstance(query_id, str)
        or not re.fullmatch(r"[A-Za-z0-9_-]+", query_id)
        or not isinstance(data.get("total"), int)
    ):
        raise ValueError("PoE2 trade search response lacks a valid query id/total")
    return {
        "game": "poe2",
        "league": league,
        "total": data["total"],
        "query_id": query_id,
        "trade_url": TRADE_SITE + encoded + "/" + query_id,
        "notice": NON_AFFILIATION_NOTICE,
    }


def _normalize_stat(text):
    return re.sub(r"\s+", " ", re.sub(r"\+?-?\d+(?:\.\d+)?", "#", text.lower())).strip()


def mod_text_to_stat_id(text, is_local=False):
    numbers = re.findall(r"[-+]?\d+(?:\.\d+)?", text)
    if len(numbers) != 1:
        raise ValueError(
            "Mod text must contain one numeric value; use explicit stat IDs for ranges/multiple values"
        )
    pattern = _normalize_stat(text)
    if is_local:
        pattern += " (local)"
    matches = [
        s
        for s in _get_stats()
        if s["id"].startswith("explicit.") and _normalize_stat(s["text"]) == pattern
    ]
    if len(matches) != 1:
        raise ValueError(
            "Mod text has no unique PoE2 stat mapping; use get_stat_ids and search_trade"
        )
    return matches[0]["id"], float(numbers[0])


def _parse_listing(row):
    item, listing = row.get("item", {}), row.get("listing", {})
    price = listing.get("price", {})
    return {
        "id": row.get("id"),
        "name": item.get("name"),
        "base_type": item.get("typeLine"),
        "ilvl": item.get("ilvl"),
        "price_amount": price.get("amount"),
        "price_currency": price.get("currency"),
        "implicit_mods": item.get("implicitMods", []),
        "explicit_mods": item.get("explicitMods", []),
        "corrupted": item.get("corrupted", False),
    }


STRING = {"type": "string", "minLength": 1}
LIMIT = {"type": "integer", "minimum": 1, "maximum": 100}
SEARCH = {
    "league": {
        **STRING,
        "description": "Exact PoE2 league or POE_LEAGUE; never guessed.",
    },
    "category": {**STRING, "description": "PoE2 category id from get_trade_filters."},
    "rarity": {**STRING, "description": "Rarity id from get_trade_filters."},
    "name": STRING,
    "base_type": STRING,
    "stats": {
        "type": "array",
        "maxItems": 100,
        "items": {
            "type": "object",
            "properties": {
                "id": STRING,
                "min": {"type": "number"},
                "max": {"type": "number"},
            },
            "required": ["id"],
            "additionalProperties": False,
        },
    },
    "min_price": {"type": "number", "minimum": 0},
    "max_price": {"type": "number", "minimum": 0},
    "price_currency": {
        "type": "string",
        "enum": ["divine", "exalted", "chaos"],
        "default": "divine",
    },
    "max_level": {"type": "integer", "minimum": 1, "maximum": 100},
    "online_only": {"type": "boolean", "default": True},
    "instant_buyout": {
        "type": "boolean",
        "default": False,
        "description": "Filter to securable listings; performs no purchase.",
    },
    "account": STRING,
}


def _tool(name, description, properties, required=()):
    return Tool(
        name=name,
        description=description,
        inputSchema={
            "type": "object",
            "properties": properties,
            "required": list(required),
            "additionalProperties": False,
        },
    )


TOOLS = [
    _tool(
        "search_trade",
        "Anonymous PoE2 read-only trade search; returns a URL and count. Public API access may be denied.",
        SEARCH,
    ),
    _tool(
        "get_stat_ids",
        "Find current PoE2 stat ids; cached metadata, no league guessing.",
        {"query": STRING, "limit": LIMIT},
        ["query"],
    ),
    _tool(
        "get_trade_filters",
        "Get current PoE2 categories, rarity and other supported filters.",
        {},
    ),
    _tool(
        "get_trade_leagues",
        "Get current official trade leagues for the poe2 realm.",
        {},
    ),
    _tool(
        "search_by_item_mods",
        "Map unambiguous single-number mod texts to PoE2 stat ids. Unmatched mods are errors.",
        {
            **{
                k: v
                for k, v in SEARCH.items()
                if k not in ("stats", "name", "category")
            },
            "unique_name": STRING,
            "item_category": STRING,
            "mods": {
                "type": "array",
                "minItems": 1,
                "maxItems": 30,
                "items": {
                    "type": "object",
                    "properties": {
                        "text": STRING,
                        "is_local": {"type": "boolean"},
                        "min_pct": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": ["text"],
                    "additionalProperties": False,
                },
            },
        },
    ),
    _tool(
        "fetch_listing",
        "Read up to 10 explicitly supplied PoE2 listing IDs. No seller contact or purchase.",
        {
            "query_id": {**STRING, "pattern": "^[A-Za-z0-9_-]+$"},
            "listing_ids": {
                "type": "array",
                "minItems": 1,
                "maxItems": 10,
                "items": {**STRING, "pattern": "^[A-Za-z0-9_-]+$"},
            },
        },
        ["query_id", "listing_ids"],
    ),
]


def _dispatch(name, args):
    if name == "get_trade_filters":
        return {"game": "poe2", "filters": _metadata("filters")}
    if name == "get_trade_leagues":
        return {
            "game": "poe2",
            "leagues": [l for l in _metadata("leagues") if l.get("realm") == "poe2"],
        }
    if name == "get_stat_ids":
        q = args["query"].casefold()
        return [
            s
            for s in _get_stats()
            if q in s["text"].casefold() or q in s["id"].casefold()
        ][: args.get("limit", 10)]
    if name == "search_by_item_mods":
        args = dict(args)
        if args.get("unique_name") and args.get("mods"):
            raise ValueError("Supply unique_name or mods, not both")
        if args.get("unique_name"):
            args["name"] = args.pop("unique_name")
        else:
            if not args.get("mods"):
                raise ValueError("Supply mods or unique_name")
            stats = []
            for mod in args.pop("mods"):
                sid, value = mod_text_to_stat_id(
                    mod["text"], mod.get("is_local", False)
                )
                if value < 0:
                    raise ValueError(
                        "Negative mods need an explicit min/max stat filter"
                    )
                if any(s["id"] == sid for s in stats):
                    raise ValueError("Duplicate mod stat id; use search_trade")
                stats.append({"id": sid, "min": value * mod.get("min_pct", 0.7)})
            args["stats"] = stats
            args.setdefault("rarity", "rare")
        if args.get("item_category"):
            args["category"] = args.pop("item_category")
        return _search(args)
    if name == "search_trade":
        return _search(args)
    url = (
        TRADE_BASE
        + "/fetch/"
        + ",".join(args["listing_ids"])
        + "?"
        + urllib.parse.urlencode({"query": args["query_id"], "realm": "poe2"})
    )
    data = _get_json(url)
    if not isinstance(data.get("result"), list):
        raise ValueError("Invalid PoE2 listing response")
    return {
        "results": [_parse_listing(r) for r in data["result"] if r is not None],
        "unavailable_listings": sum(r is None for r in data["result"]),
        "notice": NON_AFFILIATION_NOTICE,
    }


@app.list_tools()
async def list_tools():
    return TOOLS


@app.call_tool()
async def call_tool(name, arguments):
    return result(await anyio.to_thread.run_sync(_dispatch, name, arguments))


if __name__ == "__main__":
    run_server(app)
