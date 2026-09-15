# Owned PoE2 tools

All names below are registered by `poe_all.py`. Errors have MCP `isError=true`.
Read [README.md](README.md) for source semantics and configuration.

| Server | Tools | Capability |
|---|---|---|
| trade | `search_trade`, `search_by_item_mods`, `fetch_listing`, `get_stat_ids`, `get_trade_filters`, `get_trade_leagues` | Anonymous read-only PoE2 trade website requests. Availability depends on public access. |
| pricer | `ninja_lookup`, `price_item`, `price_items`, `get_economy_leagues`, `get_economy_categories` | Public PoE2 overviews; exact units, variant-aware prices, hourly cache. |
| market | `get_price`, `get_price_history`, `search_items`, `get_risers`, `get_fallers`, `get_movers`, `snapshot_status`, `refresh_prices` | Real local SQLite observations, separated by league/category/variant. |
| char | `get_character`, `get_socketed_gems`, `get_character_pob` | Visible public ninja profile/PoB2 export with age. Optional Playwright + Chromium. |
| char | `scan_stash_tabs`, `kf_check` | Explicit unsupported errors: private stash / unverified PoE1 breakpoint model. |
| stash | `score_rare`, `poe_auth_status` | Local PoE2 clipboard comparison analysis; audited OAuth capability status without reading tokens. |
| stash | `poe_auth`, `get_tab`, `list_tabs`, `price_tab`, `find_items`, `cache_status` | Explicit not-implemented errors; preserved source and exact API gaps in PORT_REPORT.md. |
| filter | `get_filter_info`, `find_blocks`, `get_block`, `add_block`, `remove_block`, `replace_block`, `set_basetype_rule` | Existing local file operations with PoE2 default directory and protocol error status. |

**39 tools total: 31 implemented, 8 explicit compatibility errors.**

## Economy categories

| Source | Accepted PoE2 category types |
|---|---|
| Exchange | `Currency`, `Fragments`, `Abyss`, `UncutGems`, `LineageSupportGems`, `Essences`, `SoulCores`, `Idols`, `Runes`, `Ritual`, `Expedition`, `Delirium`, `Breach`, `Verisium` |
| Stash overview | `UniqueWeapons`, `UniqueArmours`, `UniqueAccessories`, `UniqueFlasks`, `UniqueCharms`, `UniqueJewels`, `UniqueSanctumRelics`, `UniqueTablets`, `PrecursorTablets` |

The exchange `core.primary` currency governs `primaryValue`; `core.rates`
converts it. Stash overview is aggregate public pricing, not access to a user's
private stash. Exchange volume is not a count of listings. These types are from
[poe.ninja's documented PoE2 public endpoints](https://poe.ninja/docs/api).
