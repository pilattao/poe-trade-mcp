"""Public PoE2 character snapshots from the visible poe.ninja PoB2 export."""

import urllib.parse
import anyio
from mcp.types import Tool
from mcp_server_utils import Server, result, run_server
from poe_lib import load_config
from character_public import get_snapshot, validate_profile

app = Server("poe-char")
STRING = {"type": "string", "minLength": 1}
PROFILE = {
    "profile_url": {
        **STRING,
        "description": "Exact public /poe2/profile/account/league-slug/character/name URL.",
    },
    "account": STRING,
    "character_name": STRING,
    "league_slug": {
        **STRING,
        "description": "Exact source slug, e.g. forbiddenrites. Never inferred from a league name.",
    },
}
TOOLS = [
    Tool(
        name=name,
        description=description,
        inputSchema={
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        },
    )
    for name, description, properties in [
        (
            "get_character",
            "Fetch a public PoB2 snapshot: equipment sets, skills, passives, configuration, calculated stats and source age. Requires optional browser support.",
            PROFILE,
        ),
        (
            "get_socketed_gems",
            "Return PoE2 skill/support groups from a public export, retaining skill set and source age.",
            PROFILE,
        ),
        (
            "get_character_pob",
            "Return the actual public PoB2 XML/import code and source age; use pob_xml with the separate PoB2 server.",
            PROFILE,
        ),
        (
            "scan_stash_tabs",
            "Unsupported: private stash scans are outside the public PoE2 source.",
            {},
        ),
        (
            "kf_check",
            "Unsupported: inherited PoE1 Kinetic Fusillade breakpoint model has no verified PoE2 equivalent.",
            {},
        ),
    ]
]


def _profile_url(args):
    cfg = load_config()
    if args.get("profile_url"):
        profile = validate_profile(args["profile_url"])
        for field, target in [
            ("account", "account"),
            ("character_name", "character"),
            ("league_slug", "league_slug"),
        ]:
            if args.get(field) and args[field].replace("#", "-") != profile[target]:
                raise ValueError(f"{field} conflicts with profile_url")
        return profile["url"]
    account = args.get("account") or cfg["account"]
    character = args.get("character_name") or cfg["character"]
    slug = args.get("league_slug") or cfg["league_slug"]
    if not account or not character or not slug:
        raise ValueError(
            "Supply profile_url, or account + character_name + exact league_slug (or POE_ACCOUNT_NAME/POE_CHARACTER_NAME/POE_NINJA_LEAGUE_SLUG)"
        )
    segments = [
        urllib.parse.quote(s, safe="")
        for s in (account.replace("#", "-"), slug, character)
    ]
    return f"https://poe.ninja/poe2/profile/{segments[0]}/{segments[1]}/character/{segments[2]}"


@app.list_tools()
async def list_tools():
    return TOOLS


@app.call_tool()
async def call_tool(name, args):
    if name in ("scan_stash_tabs", "kf_check"):
        raise NotImplementedError(next(t.description for t in TOOLS if t.name == name))
    data = await anyio.to_thread.run_sync(get_snapshot, _profile_url(args))
    if name == "get_socketed_gems":
        return result({"skills": data["skills"], "source": data["source"]})
    if name == "get_character_pob":
        return result(
            {
                "pob_xml": data["pob_xml"],
                "pob_code": data["pob_code"],
                "source": data["source"],
            }
        )
    return result({k: v for k, v in data.items() if k not in ("pob_xml", "pob_code")})


if __name__ == "__main__":
    run_server(app)
