"""PoE2 public poe.ninja overviews with currency-aware, variant-aware pricing."""

import math
import urllib.parse
import anyio
from mcp.types import Tool
from mcp_server_utils import Server, result, run_server
from poe_lib import resolve_league
from price_db import PriceCache
from public_http import request_json

NINJA_EXCHANGE_URL = "https://poe.ninja/poe2/api/economy/exchange/current/overview"
NINJA_STASH_URL = "https://poe.ninja/poe2/api/economy/stash/current/item/overview"
NINJA_LEAGUES_URL = "https://poe.ninja/poe2/api/economy/leagues"
_EXCHANGE_TYPES = [
    "Currency",
    "Fragments",
    "Abyss",
    "UncutGems",
    "LineageSupportGems",
    "Essences",
    "SoulCores",
    "Idols",
    "Runes",
    "Ritual",
    "Expedition",
    "Delirium",
    "Breach",
    "Verisium",
]
_STASH_TYPES = [
    "UniqueWeapons",
    "UniqueArmours",
    "UniqueAccessories",
    "UniqueFlasks",
    "UniqueCharms",
    "UniqueJewels",
    "UniqueSanctumRelics",
    "UniqueTablets",
    "PrecursorTablets",
]
CATEGORIES = _EXCHANGE_TYPES + _STASH_TYPES
_NINJA_TTL = 3600
app = Server("poe-pricer")


def _number(value, field):
    if (
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"Invalid or missing {field} in PoE2 overview")
    return value


def normalize_overview(data, category, league, meta):
    if not isinstance(data, dict) or not isinstance(data.get("lines"), list):
        raise ValueError("Invalid PoE2 overview: expected lines array")
    core = data.get("core", {})
    primary = core.get("primary")
    if not isinstance(primary, str) or not primary:
        raise ValueError("PoE2 overview has no core.primary currency")
    rates = dict(core.get("rates", {}))
    rates[primary] = 1
    rates = {k: _number(v, f"core.rates.{k}") for k, v in rates.items()}
    item_map = {
        str(item["id"]): item for item in data.get("items", []) + core.get("items", [])
    }
    rows = []
    exchange = category in _EXCHANGE_TYPES
    for line in data["lines"]:
        price = _number(line.get("primaryValue"), "primaryValue")
        item = item_map.get(str(line.get("id")), {}) if exchange else line
        name = item.get("name")
        if not name or "id" not in line:
            raise ValueError("PoE2 overview line lacks item id/name metadata")
        row = {
            "id": str(line["id"]),
            "name": name,
            "game": "poe2",
            "league": league,
            "category": category,
            "price": price,
            "currency": primary,
            "primary_value": price,
            "chaos_value": price * rates["chaos"] if rates.get("chaos") else None,
            "divine_value": price * rates["divine"] if rates.get("divine") else None,
            "exalted_value": price * rates["exalted"] if rates.get("exalted") else None,
            "source": "poe.ninja exchange" if exchange else "poe.ninja stash estimate",
            **meta,
        }
        if exchange:
            row["volume_primary_value"] = line.get("volumePrimaryValue")
            row["volume_currency"] = primary
        else:
            row.update(
                base_type=line.get("baseType"),
                variant=line.get("variant"),
                corrupted=line.get("corrupted"),
                listing_count=line.get("listingCount"),
                details_id=line.get("detailsId"),
            )
        rows.append(row)
    # Primary currency can be absent from the exchange lines. Its unit price is
    # exactly 1; include only for the Currency category, with explicit provenance.
    if (
        category == "Currency"
        and data["lines"]
        and primary in item_map
        and not any(r["id"] == primary for r in rows)
    ):
        rows.append(
            {
                "id": primary,
                "name": item_map[primary]["name"],
                "game": "poe2",
                "league": league,
                "category": category,
                "price": 1,
                "currency": primary,
                "primary_value": 1,
                "chaos_value": rates.get("chaos"),
                "divine_value": rates.get("divine"),
                "exalted_value": rates.get("exalted"),
                "source": "poe.ninja core reference currency",
                **meta,
            }
        )
    return rows


def list_economy_leagues():
    data, meta = request_json(NINJA_LEAGUES_URL, ttl=3600)
    if not isinstance(data, list) or any(
        not isinstance(v, dict) or not v.get("id") for v in data
    ):
        raise ValueError("Invalid PoE2 economy league response")
    return {"leagues": data, **meta}


def fetch_category(league, category):
    league = resolve_league(league)
    if category not in CATEGORIES:
        raise ValueError(
            f"Unsupported PoE2 category: {category}; use get_economy_categories"
        )
    if league not in [v["id"] for v in list_economy_leagues()["leagues"]]:
        raise ValueError(
            f"League {league!r} is absent from the public PoE2 economy source"
        )
    base = NINJA_EXCHANGE_URL if category in _EXCHANGE_TYPES else NINJA_STASH_URL
    url = base + "?" + urllib.parse.urlencode({"league": league, "type": category})
    data, meta = request_json(url, ttl=_NINJA_TTL)
    rows = normalize_overview(data, category, league, meta)
    PriceCache().record(league, category, rows, fetched_at=meta["fetched_at"])
    return rows


def _ninja_lookup_live(query, league, category=None):
    if not query.strip():
        raise ValueError("Supply a nonempty item name")
    # Without a category, search all supported categories sequentially. An exact
    # match finishes the lookup after collecting every variant in that category.
    results = []
    for selected in [category] if category else CATEGORIES:
        rows = fetch_category(league, selected)
        matches = [r for r in rows if query.casefold() in r["name"].casefold()]
        results.extend(matches)
        if any(r["name"].casefold() == query.casefold() for r in matches):
            break
    return results


def _best_ninja_price(
    name, league, category=None, variant=None, base_type=None, corrupted=None
):
    rows = [
        r
        for r in _ninja_lookup_live(name, league, category)
        if r["name"].casefold() == name.casefold()
    ]
    for key, value in [
        ("variant", variant),
        ("base_type", base_type),
        ("corrupted", corrupted),
    ]:
        if value is not None:
            rows = [r for r in rows if r.get(key) == value]
    if len(rows) > 1:
        raise ValueError(
            "Multiple price variants match; supply category, variant, base_type and/or corrupted using ninja_lookup results"
        )
    return rows[0] if rows else None


def _price_single_api_item(item, league=None, category=None, variant=None):
    league = resolve_league(league)
    name = item.get("name", "").strip() or item.get("typeLine", "").strip()
    if not name:
        raise ValueError("Item must have name or typeLine")
    if item.get("frameType") in (0, 1, 2):
        return {
            "name": name,
            "method": "unsupported",
            "price_estimate": None,
            "league": league,
            "note": "Normal/magic/rare equipment valuation is not calibrated for PoE2. Use a comparable trade search.",
        }
    price = _best_ninja_price(
        name, league, category, variant, item.get("baseType"), item.get("corrupted")
    )
    if price is None:
        return {
            "name": name,
            "method": "not_found",
            "price_estimate": None,
            "league": league,
            "note": "No exact named item/variant found in the queried public categories.",
        }
    # price_estimate remains chaos for callers of the old API, with an explicit
    # currency tag. Missing conversion stays null instead of becoming zero.
    return {
        **price,
        "method": "ninja_public",
        "price_estimate": price["chaos_value"],
        "price_estimate_currency": "chaos",
        "note": "Category/variant estimate, not an appraisal of these exact item rolls.",
    }


COMMON = {
    "league": {
        "type": "string",
        "minLength": 1,
        "description": "Exact PoE2 league; defaults only to POE_LEAGUE.",
    },
    "category": {
        "type": "string",
        "enum": CATEGORIES,
        "description": "Prefer an explicit category to bound public requests.",
    },
    "variant": {"type": "string"},
}
TOOLS = [
    Tool(
        name="get_economy_leagues",
        description="List public PoE2 economy leagues without guessing a default.",
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    Tool(
        name="get_economy_categories",
        description="List supported PoE2 public price categories and currency semantics.",
        inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
    ),
    Tool(
        name="ninja_lookup",
        description="Public PoE2 prices with reference currency, conversions, variants, source and fetch time; cached hourly.",
        inputSchema={
            "type": "object",
            "properties": {**COMMON, "name": {"type": "string", "minLength": 1}},
            "required": ["name"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="price_item",
        description="Estimate a named PoE2 item from public category/variant prices. Rare valuation is unsupported.",
        inputSchema={
            "type": "object",
            "properties": {
                **COMMON,
                "item_dict": {"type": "object"},
                "item_text": {"type": "string", "minLength": 1},
            },
            "oneOf": [{"required": ["item_dict"]}, {"required": ["item_text"]}],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="price_items",
        description="Price up to 100 supplied items using public PoE2 overviews. Totals report unpriced counts.",
        inputSchema={
            "type": "object",
            "properties": {
                **COMMON,
                "items": {
                    "type": "array",
                    "items": {"type": "object"},
                    "maxItems": 100,
                },
                "min_price": {"type": "number", "minimum": 0},
                "include_unpriced": {"type": "boolean"},
            },
            "required": ["items"],
            "additionalProperties": False,
        },
    ),
]


def _parse_clipboard(text):
    lines = [s.strip() for s in text.strip().splitlines() if s.strip()]
    for index, line in enumerate(lines):
        if line.startswith("Rarity:") and index + 1 < len(lines):
            rarity = line.split(":", 1)[1].strip()
            frame = {
                "Normal": 0,
                "Magic": 1,
                "Rare": 2,
                "Unique": 3,
                "Currency": 5,
                "Gem": 4,
            }.get(rarity)
            if frame is None:
                raise ValueError(f"Unsupported clipboard rarity: {rarity}")
            item = {"name": lines[index + 1], "frameType": frame}
            if frame == 3:
                # Clipboard input must select the same base/corruption variant
                # as API-shaped input, never an uncorrupted price for a
                # corrupted item merely because its unique name matches.
                if index + 2 < len(lines) and not lines[index + 2].startswith("---"):
                    item["baseType"] = lines[index + 2]
                item["corrupted"] = "Corrupted" in lines
            return item
    raise ValueError("Expected English PoE clipboard text with Rarity and item name")


def _dispatch(name, arguments):
    if name == "get_economy_leagues":
        return list_economy_leagues()
    if name == "get_economy_categories":
        return {
            "exchange": _EXCHANGE_TYPES,
            "stash": _STASH_TYPES,
            "currency": "Read core.primary; rates convert one primary into target currency.",
            "source": "https://poe.ninja/docs/api",
        }
    league = resolve_league(arguments.get("league"))
    category, variant = arguments.get("category"), arguments.get("variant")
    if name == "ninja_lookup":
        rows = _ninja_lookup_live(arguments["name"], league, category)
        if variant is not None:
            rows = [r for r in rows if r.get("variant") == variant]
        return {"league": league, "results": rows, "match_count": len(rows)}
    if name == "price_item":
        item = arguments.get("item_dict") or _parse_clipboard(arguments["item_text"])
        return _price_single_api_item(item, league, category, variant)
    rows = [
        _price_single_api_item(i, league, category, variant) for i in arguments["items"]
    ]
    priced = [r for r in rows if r.get("price_estimate") is not None]
    visible = [
        r
        for r in rows
        if (r.get("price_estimate") is None and arguments.get("include_unpriced"))
        or (
            r.get("price_estimate") is not None
            and r["price_estimate"] >= arguments.get("min_price", 0)
        )
    ]
    return {
        "league": league,
        "total_items": len(rows),
        "priced_count": len(priced),
        "unpriced_count": len(rows) - len(priced),
        "total_value_chaos": sum(r["price_estimate"] for r in priced),
        "quantity_basis": "one unit per supplied item; stack sizes are not multiplied",
        "items": sorted(
            visible, key=lambda r: r.get("price_estimate") or 0, reverse=True
        ),
    }


@app.list_tools()
async def list_tools():
    return TOOLS


@app.call_tool()
async def call_tool(name, arguments):
    return result(await anyio.to_thread.run_sync(_dispatch, name, arguments))


if __name__ == "__main__":
    run_server(app)
