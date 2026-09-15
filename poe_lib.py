"""PoE2 public-only configuration and explicit legacy capability boundaries.

This module never reads session cookies, config.json, OAuth tokens, or sibling
projects. Public character snapshots are implemented in character_public.py.
"""

import os


def resolve_league(league=None):
    value = league if league is not None else os.environ.get("POE_LEAGUE")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            "Supply an exact PoE2 league or set POE_LEAGUE; no league is guessed"
        )
    return value.strip()


def load_config():
    return {
        key: os.environ.get(env, "")
        for key, env in {
            "account": "POE_ACCOUNT_NAME",
            "character": "POE_CHARACTER_NAME",
            "league": "POE_LEAGUE",
            "contact_email": "POE_CONTACT_EMAIL",
            "league_slug": "POE_NINJA_LEAGUE_SLUG",
        }.items()
    }


class PoeApi:
    """Compatibility boundary: private GGG APIs are outside this public port."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "Authenticated character/stash access is unavailable in this public-only PoE2 port. Use get_character with a public poe.ninja profile."
        )


def build_pob_xml(*args, **kwargs):
    raise NotImplementedError("Use get_character_pob for the actual public PoB2 export")


class PobAnalyzer:
    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "Headless analysis belongs to the separate PoB2 server"
        )
