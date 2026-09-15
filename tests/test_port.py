import asyncio
import importlib
import json
import sys
from pathlib import Path

import pytest
from mcp.types import CallToolResult

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def run(awaitable):
    return asyncio.run(awaitable)


def test_all_owned_tools_discovered():
    import poe_all

    modules = [importlib.import_module(name) for name in poe_all.OWNED_MODULES]
    expected = [t.name for m in modules for t in m.TOOLS]
    actual = [t.name for t in run(poe_all.list_tools())]
    assert actual == expected
    assert len(actual) == len(set(actual))
    assert {
        "get_price",
        "get_character",
        "ninja_lookup",
        "search_trade",
        "list_tabs",
        "get_filter_info",
    } <= set(actual)


def test_registry_fails_explicitly_on_missing_or_duplicate_module():
    import poe_all

    with pytest.raises(RuntimeError, match="required.*missing_owned"):
        poe_all.load_registry(["missing_owned"])
    with pytest.raises(RuntimeError, match="Duplicate"):
        poe_all.load_registry(["poe_trade", "poe_trade"])


def test_registry_error_results_and_schema():
    import poe_all

    for name, args in [
        ("unknown", {}),
        ("search_trade", {"stats": "broken"}),
        ("kf_check", {}),
    ]:
        result = run(poe_all.call_tool(name, args))
        assert isinstance(result, CallToolResult)
        assert result.isError


def test_poe2_query_currency_zero_bounds_and_online():
    import poe_trade

    payload = poe_trade._build_search_payload(
        {
            "online_only": False,
            "min_price": 0,
            "max_price": 2,
            "price_currency": "divine",
        }
    )
    assert payload["query"]["status"] == {"option": "any"}
    assert payload["query"]["filters"]["trade_filters"]["filters"]["price"] == {
        "min": 0,
        "max": 2,
        "option": "divine",
    }
    assert poe_trade.TRADE_BASE.endswith("/api/trade2")


@pytest.mark.parametrize(
    "args", [{"min_links": 6}, {"stats": "broken"}, {"min_price": 2, "max_price": 1}]
)
def test_poe1_or_invalid_filters_are_rejected(args):
    import poe_trade

    with pytest.raises(ValueError):
        poe_trade._build_search_payload(args)


def test_trade_search_has_poe2_realm_and_no_credentials(monkeypatch):
    import poe_trade

    seen = []
    monkeypatch.setattr(
        poe_trade,
        "_post_json",
        lambda url, payload: seen.append(url) or {"id": "abc123", "total": 2},
    )
    monkeypatch.setattr(poe_trade, "_validate_search_metadata", lambda args: None)
    result = run(poe_trade.call_tool("search_trade", {"league": "Test / HC"}))
    data = json.loads(result.content[0].text)
    assert seen == [
        "https://www.pathofexile.com/api/trade2/search/poe2/Test%20%2F%20HC"
    ]
    assert "/trade2/search/poe2/Test%20%2F%20HC/abc123" in data["trade_url"]
    assert "Cookie" not in poe_trade._load_headers()


def test_ninja_source_failure_is_error(monkeypatch):
    import poe_pricer

    monkeypatch.setattr(
        poe_pricer,
        "_ninja_lookup_live",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("HTTP 503")),
    )
    result = run(
        poe_pricer.call_tool(
            "ninja_lookup",
            {"name": "Divine Orb", "league": "Test", "category": "Currency"},
        )
    )
    assert result.isError
    assert "503" in result.content[0].text


def test_no_poe1_rare_price_estimates():
    import poe_pricer

    result = poe_pricer._price_single_api_item(
        {"frameType": 2, "name": "Test rare", "typeLine": "Wand"}, "Test"
    )
    assert result["price_estimate"] is None
    assert result["method"] == "unsupported"


def test_price_cache_league_variants_and_real_history(tmp_path):
    import price_db

    db = price_db.PriceCache(tmp_path / "prices.sqlite3")
    row = {
        "id": "a",
        "name": "Test Orb",
        "category": "Currency",
        "price": 2,
        "currency": "chaos",
        "source_url": "https://poe.ninja/test",
    }
    db.record("League A", "Currency", [row], fetched_at="2026-09-01T00:00:00+00:00")
    db.record(
        "League B",
        "Currency",
        [{**row, "price": 99}],
        fetched_at="2026-09-01T00:00:00+00:00",
    )
    db.record(
        "League A",
        "Currency",
        [{**row, "price": 4}],
        fetched_at="2026-09-02T00:00:00+00:00",
    )
    assert db.search("League A", "Test")[0]["price"] == 4
    assert db.search("League B", "Test")[0]["price"] == 99
    assert [r["price"] for r in db.history("League A", "Test Orb")] == [2, 4]
    assert db.movers("League A", min_snapshots=2)[0]["change_pct"] == 100
    assert db.status("Never")["total_snapshots"] == 0


def test_league_required_without_silent_default(monkeypatch):
    from poe_lib import resolve_league

    monkeypatch.delenv("POE_LEAGUE", raising=False)
    with pytest.raises(ValueError, match="league"):
        resolve_league(None)
    monkeypatch.setenv("POE_LEAGUE", "Test")
    assert resolve_league(None) == "Test"
    assert resolve_league("HC") == "HC"


def economy_payload():
    return {
        "core": {
            "primary": "divine",
            "rates": {"chaos": 9.1, "exalted": 410},
            "items": [{"id": "divine", "name": "Divine Orb"}],
        },
        "items": [{"id": "exalted", "name": "Exalted Orb"}],
        "lines": [
            {"id": "exalted", "primaryValue": 1 / 410, "volumePrimaryValue": 100}
        ],
    }


def test_exchange_units_and_volume_are_not_listings():
    from poe_pricer import normalize_overview

    rows = normalize_overview(
        economy_payload(),
        "Currency",
        "Test",
        {
            "source_url": "https://poe.ninja/test",
            "fetched_at": "2026-09-15T00:00:00+00:00",
        },
    )
    exalt = next(r for r in rows if r["name"] == "Exalted Orb")
    assert exalt["currency"] == "divine"
    assert exalt["price"] == pytest.approx(1 / 410)
    assert exalt["chaos_value"] == pytest.approx(9.1 / 410)
    assert exalt["exalted_value"] == pytest.approx(1)
    assert exalt["volume_primary_value"] == 100
    assert "listing_count" not in exalt
    assert next(r for r in rows if r["name"] == "Divine Orb")["price"] == 1


def test_stash_variants_kept_and_no_missing_price_zero():
    from poe_pricer import normalize_overview

    data = economy_payload()
    data["lines"] = [
        {
            "id": 1,
            "name": "Test Unique",
            "baseType": "Gold Ring",
            "variant": "A",
            "primaryValue": 2,
            "listingCount": 5,
        },
        {
            "id": 2,
            "name": "Test Unique",
            "baseType": "Gold Ring",
            "variant": "B",
            "primaryValue": 3,
            "listingCount": 1,
        },
    ]
    rows = normalize_overview(data, "UniqueAccessories", "Test", {})
    assert len(rows) == 2
    assert rows[0]["chaos_value"] == pytest.approx(18.2)
    del data["lines"][0]["primaryValue"]
    with pytest.raises(ValueError, match="primaryValue"):
        normalize_overview(data, "UniqueAccessories", "Test", {})


def test_lookup_ambiguous_unique_is_not_highest_price(monkeypatch):
    import poe_pricer

    monkeypatch.setattr(
        poe_pricer,
        "_ninja_lookup_live",
        lambda *a, **kw: [
            {"name": "Unique", "variant": "a", "chaos_value": 1},
            {"name": "Unique", "variant": "b", "chaos_value": 999},
        ],
    )
    with pytest.raises(ValueError, match="variant"):
        poe_pricer._best_ninja_price("Unique", "Test")


def test_trade_sale_type_uses_poe2_metadata_default():
    import poe_trade

    trade = poe_trade._build_search_payload({})["query"]["filters"]["trade_filters"][
        "filters"
    ]
    assert "sale_type" not in trade


def test_character_export_is_poe2_and_preserves_sets_and_age():
    import base64, zlib
    from character_public import decode_export, validate_profile, validate_page

    xml = b'<PathOfBuilding2><Build level="93"/><Items><Item id="1">Rarity: Unique\nTest Item\nBase</Item><ItemSet id="1"><Slot name="Weapon 1" itemId="1"/></ItemSet></Items><Skills><SkillSet id="1"><Skill><Gem nameSpec="Spark"/></Skill></SkillSet></Skills><Tree><Spec nodes="1,2"/></Tree></PathOfBuilding2>'
    code = base64.urlsafe_b64encode(zlib.compress(xml)).decode()
    data, decoded = decode_export(code)
    assert decoded == xml.decode()
    assert data["skills"][0]["gems"][0]["nameSpec"] == "Spark"
    assert data["item_sets"][0]["slots"] == {"Weapon 1": "1"}
    profile = validate_profile(
        "https://poe.ninja/poe2/profile/Test-1234/forbiddenrites/character/TestChar"
    )
    page = {
        "name": "TestChar",
        "account": "Test#1234",
        "last_fetched_text": "2 hours ago",
    }
    validate_page(page, profile)
    with pytest.raises(ValueError, match="freshness"):
        validate_page({**page, "last_fetched_text": ""}, profile)
    with pytest.raises(ValueError):
        decode_export(
            base64.urlsafe_b64encode(
                zlib.compress(xml.replace(b"PathOfBuilding2", b"PathOfBuilding"))
            ).decode()
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://other.test/poe2/profile/a/b/character/c",
        "https://poe.ninja/poe1/profile/a/b/character/c",
        "https://user:secret@poe.ninja/poe2/profile/a/b/character/c",
        "https://poe.ninja/poe2/profile/a/../character/c",
    ],
)
def test_character_profile_url_boundaries(url):
    from character_public import validate_profile

    with pytest.raises(ValueError):
        validate_profile(url)


def test_legacy_private_capabilities_are_explicit_without_config_reads(monkeypatch):
    import poe_stash, poe_char

    monkeypatch.setattr(
        Path,
        "read_text",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("unexpected credential/config read")
        ),
    )
    for name in (
        "list_tabs",
        "get_tab",
        "price_tab",
        "find_items",
        "cache_status",
    ):
        tool = next(t for t in poe_stash.TOOLS if t.name == name)
        result = run(
            poe_stash.call_tool(
                name, {k: "x" for k in tool.inputSchema.get("required", [])}
            )
        )
        assert result.isError
        assert json.loads(result.content[0].text)["error"] == "NotImplementedError"
    assert run(poe_char.call_tool("scan_stash_tabs", {})).isError


def test_public_page_uppercase_headers_are_removed():
    from character_public import strip_page_label

    assert strip_page_label("ACCOUNT\nTest#1234", "Account") == "Test#1234"
    assert (
        strip_page_label("LAST FETCHED\n2 hours ago", "Last fetched") == "2 hours ago"
    )


def test_local_poe2_rare_analysis_does_not_price_or_fetch(monkeypatch):
    import poe_stash

    text = """Item Class: Sceptres
Rarity: Rare
Test Branch
Rattling Sceptre
--------
Spirit: 100
--------
Requirements:
Level: 70
Int: 100
--------
Item Level: 82
--------
+40 to Spirit
+3 to Level of all Minion Skills
+30% to Fire Resistance
17% increased Cast Speed
An unknown mod without numeric values
"""
    response = run(poe_stash.call_tool("score_rare", {"item_text": text}))
    assert not response.isError
    data = json.loads(response.content[0].text)
    assert data["category"] == "weapon.sceptre"
    assert data["price_estimate"] is None
    assert data["item_level"] == 82
    assert len(data["mods"]) == 5
    assert any(r["kind"] == "spirit" for r in data["comparison_candidates"])
    assert any(r["kind"] == "skill_levels" for r in data["comparison_candidates"])
    assert data["unclassified_mods"] == ["An unknown mod without numeric values"]
    assert not any("Level: 70" in m for m in data["mods"])


def test_oauth_status_reports_actual_poe2_support_without_inspecting_tokens(
    monkeypatch,
):
    import poe_stash

    monkeypatch.setattr(
        Path,
        "read_text",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("credential read")),
    )
    response = run(poe_stash.call_tool("poe_auth_status", {}))
    assert not response.isError
    data = json.loads(response.content[0].text)
    assert data["official_poe2_character_api"] is True
    assert data["token_status"] == "not_inspected"
    assert data["required_scope"] == "account:characters"
    assert (
        data["character_url_template"]
        == "https://api.pathofexile.com/character/poe2/{name}"
    )
