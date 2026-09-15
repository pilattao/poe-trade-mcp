"""Offline regression evidence for the bounded public PoE2 tool audit."""

import asyncio
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import poe_market
import poe_pricer
import poe_trade
from price_db import PriceCache


def call(module, name, args):
    response = asyncio.run(module.call_tool(name, args))
    return response, json.loads(response.content[0].text)


def test_search_ids_can_be_used_directly_for_listing_fetch(monkeypatch):
    monkeypatch.setattr(poe_trade, "_validate_search_metadata", lambda args: None)
    monkeypatch.setattr(
        poe_trade, "_post_json",
        lambda *args: {"id": "query-a", "total": 87, "result": ["listing-a", "listing-b"]},
    )
    response, search = call(poe_trade, "search_trade", {"league": "Test League"})
    assert not response.isError
    assert search["listing_ids"] == ["listing-a", "listing-b"]
    assert search["total"] == 87  # Total matches need not equal returned IDs.
    urls = []

    def fetch(url):
        urls.append(url)
        return {"result": [{"id": "listing-a", "item": {"name": "Test Unique"},
                            "listing": {"price": {"amount": 2, "currency": "exalted"}}}, None]}

    monkeypatch.setattr(poe_trade, "_get_json", fetch)
    response, result = call(poe_trade, "fetch_listing", {
        "query_id": search["query_id"], "listing_ids": search["listing_ids"],
    })
    assert not response.isError
    assert urls == ["https://www.pathofexile.com/api/trade2/fetch/listing-a,listing-b?query=query-a&realm=poe2"]
    assert result["unavailable_listings"] == 1
    assert result["results"][0]["price_amount"] == 2
    assert result["results"][0]["price_currency"] == "exalted"


@pytest.mark.parametrize("result", [None, "listing-a", ["bad/id"], [None], [42]])
def test_search_rejects_unusable_source_listing_ids(monkeypatch, result):
    monkeypatch.setattr(poe_trade, "_validate_search_metadata", lambda args: None)
    monkeypatch.setattr(poe_trade, "_post_json", lambda *args: {"id": "q", "total": 1, "result": result})
    response, data = call(poe_trade, "search_trade", {"league": "Test"})
    assert response.isError
    assert "listing" in data["detail"]


def test_search_empty_result_is_success(monkeypatch):
    monkeypatch.setattr(poe_trade, "_validate_search_metadata", lambda args: None)
    monkeypatch.setattr(poe_trade, "_post_json", lambda *args: {"id": "q", "total": 0, "result": []})
    response, data = call(poe_trade, "search_trade", {"league": "Test"})
    assert not response.isError
    assert data["listing_ids"] == []


def test_mod_text_matches_source_template_with_literal_plus(monkeypatch):
    monkeypatch.setattr(poe_trade, "_get_stats", lambda: [
        {"id": "explicit.test", "text": "+# to Test Stat", "type": "Explicit"},
    ])
    assert poe_trade.mod_text_to_stat_id("+5 to Test Stat") == ("explicit.test", 5)


def test_mod_search_preserves_minimum_and_returns_listing_ids(monkeypatch):
    monkeypatch.setattr(poe_trade, "_get_stats", lambda: [
        {"id": "explicit.test", "text": "#% increased Test Speed", "type": "Explicit"},
    ])
    seen = []
    monkeypatch.setattr(poe_trade, "_validate_search_metadata", lambda args: None)
    monkeypatch.setattr(poe_trade, "_post_json", lambda url, payload:
                        seen.append(payload) or {"id": "query", "total": 1, "result": ["listing"]})
    response, data = call(poe_trade, "search_by_item_mods", {
        "league": "Test", "item_category": "armour.boots", "online_only": False,
        "mods": [{"text": "20% increased Test Speed", "min_pct": 0.75}],
    })
    assert not response.isError
    assert data["listing_ids"] == ["listing"]
    query = seen[0]["query"]
    assert query["stats"][0]["filters"] == [{"id": "explicit.test", "disabled": False, "value": {"min": 15}}]
    assert query["filters"]["type_filters"]["filters"] == {"category": {"option": "armour.boots"}, "rarity": {"option": "rare"}}
    assert query["status"] == {"option": "any"}


def test_mod_mapping_keeps_ambiguity_explicit(monkeypatch):
    monkeypatch.setattr(poe_trade, "_get_stats", lambda: [
        {"id": "explicit.a", "text": "# to Test Stat"},
        {"id": "explicit.b", "text": "+# to Test Stat"},
    ])
    with pytest.raises(ValueError, match="no unique"):
        poe_trade.mod_text_to_stat_id("+5 to Test Stat")


def test_unique_mod_search_uses_name_without_inventing_stats(monkeypatch):
    seen = []
    monkeypatch.setattr(poe_trade, "_validate_search_metadata", lambda args: None)
    monkeypatch.setattr(poe_trade, "_post_json", lambda url, payload:
                        seen.append(payload) or {"id": "query", "total": 0, "result": []})
    response, _ = call(poe_trade, "search_by_item_mods", {
        "league": "Test", "unique_name": "Test Unique", "item_category": "accessory.amulet",
    })
    assert not response.isError
    assert seen[0]["query"]["name"] == "Test Unique"
    assert seen[0]["query"]["stats"][0]["filters"] == []


@pytest.mark.parametrize("include_unpriced", [False, True])
def test_batch_totals_and_visibility_keep_unpriced_and_quantity_basis(monkeypatch, include_unpriced):
    monkeypatch.setattr(poe_pricer, "_ninja_lookup_live", lambda *args: [
        {"name": "Test Orb", "chaos_value": 2},
        {"name": "Valuable Orb", "chaos_value": 10},
    ])
    response, data = call(poe_pricer, "price_items", {
        "league": "Test", "category": "Currency", "min_price": 5,
        "include_unpriced": include_unpriced,
        "items": [{"name": "Test Orb", "frameType": 5, "stackSize": 20},
                  {"name": "Valuable Orb", "frameType": 5},
                  {"name": "Unpriced rare", "frameType": 2}],
    })
    assert not response.isError
    assert data["total_items"] == 3
    assert data["priced_count"] == 2
    assert data["unpriced_count"] == 1
    assert data["total_value_chaos"] == 12
    assert "one unit per supplied item" in data["quantity_basis"]
    assert [r["name"] for r in data["items"]] == (
        ["Valuable Orb", "Unpriced rare"] if include_unpriced else ["Valuable Orb"]
    )


@pytest.mark.parametrize("corrupted", [False, True])
def test_unique_clipboard_preserves_base_and_corruption(monkeypatch, corrupted):
    rows = [
        {"name": "Test Unique", "base_type": "Test Amulet", "corrupted": False, "chaos_value": 3},
        {"name": "Test Unique", "base_type": "Test Amulet", "corrupted": True, "chaos_value": 1},
        {"name": "Test Unique", "base_type": "Other Base", "corrupted": corrupted, "chaos_value": 99},
    ]
    monkeypatch.setattr(poe_pricer, "_ninja_lookup_live", lambda *args: rows)
    text = "Item Class: Amulets\nRarity: Unique\nTest Unique\nTest Amulet\n--------\nItem Level: 80\n--------\n"
    if corrupted:
        text += "Corrupted\n"
    response, data = call(poe_pricer, "price_item", {"league": "Test", "category": "UniqueAccessories", "item_text": text})
    assert not response.isError
    assert data["price_estimate"] == (1 if corrupted else 3)
    assert data["corrupted"] is corrupted
    assert data["base_type"] == "Test Amulet"


def test_corrupted_clipboard_never_uses_uncorrupted_only_reference(monkeypatch):
    monkeypatch.setattr(poe_pricer, "_ninja_lookup_live", lambda *args: [
        {"name": "Test Unique", "base_type": "Test Amulet", "corrupted": False, "chaos_value": 3},
    ])
    response, data = call(poe_pricer, "price_item", {
        "league": "Test", "category": "UniqueAccessories",
        "item_text": "Rarity: Unique\nTest Unique\nTest Amulet\n--------\nCorrupted",
    })
    assert not response.isError
    assert data["method"] == "not_found"
    assert data["price_estimate"] is None


def test_currency_clipboard_does_not_require_equipment_variant(monkeypatch):
    monkeypatch.setattr(poe_pricer, "_ninja_lookup_live", lambda *args: [
        {"name": "Test Orb", "chaos_value": 2},
    ])
    response, data = call(poe_pricer, "price_item", {
        "league": "Test", "category": "Currency",
        "item_text": "Rarity: Currency\nTest Orb\n--------\nStack Size: 20/20",
    })
    assert not response.isError
    assert data["price_estimate"] == 2


def test_history_and_movement_use_same_league_variant_currency(tmp_path, monkeypatch):
    """Synthetic snapshots test signs/bounds separately from live observations."""
    monkeypatch.setenv("POE_PRICE_DB", str(tmp_path / "audit.sqlite3"))
    db = PriceCache()
    def row(item, price, variant=None, currency="divine"):
        return {"id": item, "name": item, "price": price, "currency": currency, "variant": variant}
    before = [row("Up", 1), row("Down", 4), row("Same", 2), row("Variant", 1, "A"),
              row("Variant", 10, "B"), row("Changed unit", 1), row("Gone", 2), row("Zero", 0)]
    after = [row("Up", 3), row("Down", 2), row("Same", 2), row("Variant", 2, "A"),
             row("Variant", 9, "B"), row("Changed unit", 100, currency="chaos"), row("Zero", 1)]
    for timestamp, rows in [("2026-01-01T00:00:00Z", before), ("2026-01-02T00:00:00Z", after)]:
        db.record("Test", "Currency", rows, timestamp)
    assert db.record("Test", "Currency", after, "2026-01-02T00:00:00Z") == 0
    db.record("Other League", "Currency", [row("Up", 999)])
    for tool, expected in [("get_risers", [200, 100]), ("get_fallers", [-50, -10]),
                           ("get_movers", [200, 100, -50, -10, 0])]:
        response, data = call(poe_market, tool, {"league": "Test", "min_snapshots": 2})
        assert not response.isError
        assert [r["change_pct"] for r in data["results"]] == pytest.approx(expected)
        assert all(r["snapshots"] == 2 for r in data["results"])
    _, limited = call(poe_market, "get_risers", {"league": "Test", "min_snapshots": 2, "limit": 1, "min_price": 3})
    assert [r["name"] for r in limited["results"]] == ["Up"]
    _, insufficient = call(poe_market, "get_movers", {"league": "Test", "min_snapshots": 3})
    assert insufficient["results"] == []
    _, history = call(poe_market, "get_price_history", {"league": "Test", "name": "Up"})
    assert [r["price"] for r in history["results"]] == [1, 3]
    _, status = call(poe_market, "snapshot_status", {"league": "Test"})
    assert status["total_snapshots"] == 2
    assert status["items_tracked"] == len(after)
    _, other = call(poe_market, "snapshot_status", {"league": "Empty"})
    assert other["total_snapshots"] == 0
