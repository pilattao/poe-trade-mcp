"""Public ninja snapshots by default; explicit opt-in official OAuth reads."""

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


# Only get_character can switch sources. PoB exports and skill-group reads keep
# their established public-source contract; official JSON is not fabricated XML.
TOOLS[0].inputSchema["properties"] = {
    **PROFILE,
    "source": {"type": "string", "enum": ["public", "oauth"], "default": "public"},
    "confirm_oauth": {"type": "boolean", "default": False},
}
TOOLS[
    0
].description += " source=oauth instead returns official character JSON, only with explicit confirm_oauth and configured enablement."
TOOLS.append(
    Tool(
        name="list_oauth_characters",
        description="Explicit private read of the authorized account's PoE2 character list. Requires confirm_oauth=true and POE_OAUTH_ENABLED=1. No private response is saved.",
        inputSchema={
            "type": "object",
            "properties": {"confirm_oauth": {"type": "boolean", "default": False}},
            "additionalProperties": False,
        },
    )
)


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
    if name == "list_oauth_characters" or args.get("source") == "oauth":
        from functools import partial
        from poe_oauth import OAuthConfig
        from poe_oauth_client import CharacterClient

        config = OAuthConfig.from_env()
        config.require_action(args.get("confirm_oauth", False))
        if any(key in args for key in ("profile_url", "account", "league_slug")):
            raise ValueError("Public profile fields cannot select the OAuth account")
        client = CharacterClient(config)
        operation = (
            partial(client.list_characters, confirm=True)
            if name == "list_oauth_characters"
            else partial(client.get_character, args.get("character_name"), confirm=True)
        )
        return result(await anyio.to_thread.run_sync(operation))
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
