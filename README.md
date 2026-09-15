# PoE2 trade MCP

A standalone MCP stdio server for **public PoE2 trade metadata/search, poe.ninja
prices, local price history, public character snapshots, and local filter files**.
This is the trade submodule of the suite; it does not import sibling PoB servers.
An optional official PoE2 OAuth character client is now available after explicit
configuration and user consent. Public ninja remains the default; see
[OAuth setup, contract and live-check requirements](OAUTH_REPORT.md).

## Install and run

Python 3.10+ and the supported upstream MCP 1.x API are required:

```sh
python -m pip install -r requirements.txt
python poe_all.py
```

The dependency range is deliberately `mcp>=1.26,<2`. MCP 2.x removed the
`Server.list_tools` interface used here. PyPoE and RePoE are not runtime
dependencies. Individual `poe_trade.py`, `poe_pricer.py`, `poe_market.py`,
`poe_char.py`, `poe_stash.py`, and `poe_filter.py` entry points also use stdio.
SSE is not implemented. Choose the interpreter in the suite configuration;
this submodule does not install into or reconfigure a running suite.

`poe_all.py` loads all six owned servers as one registry. Missing imports,
invalid schemas, and duplicate tool names fail startup with the module named.
All 40 declared tools are discoverable. Seven retained compatibility tools return
explicit unsupported errors; they are documented below. Sibling `pob_vault_mcp`
and `server` modules are intentionally not part of this registry: launch those
through their own suite configuration.

## Configuration

| Setting | Behavior |
|---|---|
| `POE_LEAGUE` | Exact league fallback for trade/pricing/cache tools. A tool's explicit `league` wins. No auto-selection or hardcoded season. |
| `POE_PRICE_DB` | Optional SQLite path; default `.cache/poe2-prices.sqlite3` inside this submodule. No legacy PoE1 DB is loaded. |
| `POE_FILTER_PATH` | Local `.filter` path; default `~/Documents/My Games/Path of Exile 2/Starting.filter`. |
| `POE_ACCOUNT_NAME`, `POE_CHARACTER_NAME`, `POE_NINJA_LEAGUE_SLUG` | Optional defaults for public character URLs. Prefer an explicit `profile_url`. |
| `POE2_CHROME_PATH` | Optional existing Chromium/Chrome executable for character snapshots. |

Public tools never read `config.json`, session cookies, browser login profiles,
or sibling credentials. OAuth uses only explicitly configured `POE_OAUTH_*`
inputs and a component-owned private store; there is no global token default.
The historical OAuth source is preserved as `legacy/poe_oauth.py.txt`.
`stash_cache.py` and `rare_scorer.py` remain inactive reference source.

## Trade

`get_trade_leagues`, `get_trade_filters`, and `get_stat_ids` read official
`/api/trade2/data/*` metadata. `search_trade` and `search_by_item_mods` use
`/api/trade2/search/poe2/{league}` and return
`/trade2/search/poe2/{league}/{query_id}`. Search is read-only; no message,
purchase, whisper, or seller-contact action exists.

- Categories and stat IDs are checked against PoE2 metadata. For example,
  Body Armour uses `armour.chest`; use discovery instead of PoE1 IDs.
- Prices have an explicit `price_currency`: `divine` (default), `exalted`, or
  `chaos`. Zero bounds are preserved; inverted bounds are rejected.
- `online_only` defaults to true. `instant_buyout=true` selects `securable`;
  it filters listings and does not buy anything. The metadata default for
  buyout/fixed-price is represented by **omitting** `sale_type`; sending a JSON
  null option is rejected by the live API with HTTP 400.
- PoE1 `min_links`, malformed stat arrays, and unmatched/ambiguous textual
  mods fail explicitly. Multi-number or negative mods require explicit stat
  IDs and bounds rather than an inferred range.
- `fetch_listing` accepts an array of 1–10 explicitly supplied IDs and a query
  ID. It returns item/price details without contact information or whisper text.
- Requests are anonymous. HTTP denial/rate limits are errors; there is no
  credential fallback, Cloudflare bypass, silent broadening, or automatic retry.

## Prices and local history

The implementation follows [poe.ninja's public API reference](https://poe.ninja/docs/api).
Use `get_economy_leagues` and `get_economy_categories` to select an exact league
and category. Prefer an explicit category to keep each lookup small.

```json
{"name":"Divine Orb","league":"Forbidden Rites","category":"Currency"}
```

```json
{"name":"Choir of the Storm","league":"Forbidden Rites","category":"UniqueAccessories"}
```

Currency-like categories use `/poe2/api/economy/exchange/current/overview`.
Equipment uses `/poe2/api/economy/stash/current/item/overview` with **plural**
category names such as `UniqueAccessories` and `UniqueWeapons`.
The complete category list is in [TOOLS.md](TOOLS.md).

Every price retains the source's `price` and `currency` (`core.primary`).
`core.rates` expresses units of a target currency per primary currency, so a
price of 2 divine with a chaos rate of 9.1 becomes 18.2 chaos. Exchange volume
is `volume_primary_value`, not a listing count. Stash estimates retain
`listing_count`, `base_type`, `variant`, and `corrupted`.

`ninja_lookup` retains all matching variants. Single-item pricing refuses an
ambiguous exact name; it never selects the highest-priced variant. Supply a
category/variant and, in `item_dict`, base type/corruption to disambiguate.
`price_estimate` is the compatibility chaos value, explicitly tagged with its
currency; it is null when conversion is unavailable. A named unique estimate
is not an appraisal of its exact rolls. Normal/magic/rare equipment gets an
explicit unsupported result with a null estimate, not a PoE1 heuristic price.
Batch totals are per supplied unit; stack sizes are not multiplied.

Successful overview fetches populate a real SQLite history automatically.
`refresh_prices` fetches one category explicitly. `get_price`, `search_items`,
`get_price_history`, and movement tools only query this local history. Cache
identity includes league, category, item ID, variant, base type, corruption and
currency for trend comparison. No cross-league prices are merged. A latest
category snapshot replaces that category's visible coverage; delisted items do
not survive as current prices. No observations means a useful error directing
the caller to refresh; `snapshot_status` can inspect an empty cache.

Public economy responses are cached for an hour, with ETag revalidation and
cache headers respected. Trade metadata uses an hour cache. Requests are
serialized and spaced by at least 1.5 seconds per host, widened by GGG rate
headers. HTTP 429 establishes a shared cooldown from `Retry-After` and returns
an error. No polling or background collector is started. Reusing a cached or
304 response does not invent another historical observation.

`fetched_at` is **our fetch time**, not the age of game data. `source_timestamp`
is null when the source does not provide it. HTTP `Date`/`Age` are reported
separately. Source errors and invalid schemas are never cached as empty results.
A category omitted from local history is not evidence that an item is worthless.

## Public character snapshots

Install the optional browser dependency and either install Playwright Chromium
or point to an existing browser:

```sh
python -m pip install -r requirements-character.txt
python -m playwright install chromium
```

Call `get_character`, `get_socketed_gems`, or `get_character_pob` with:

```json
{"profile_url":"https://poe.ninja/poe2/profile/ACCOUNT/LEAGUE_SLUG/character/CHARACTER"}
```

The exact league slug comes from the source URL: for example `forbiddenrites`,
not a guessed `forbidden-rites`. A fresh anonymous browser context reads the
visible public page and its **Import code for Path of Building** field. This
code does not call ninja's internal builds/profile APIs directly. Missing,
private, redirected, or incomplete exports are errors, with no authenticated
fallback. Optional browser dependencies do not prevent server startup.

The local adapter validates account/name, requires displayed source freshness,
and decodes only a complete `PathOfBuilding2` export with bounded decompression
and XML entity/DTD protection. Equipment sets, skill/support groups, tree specs,
configuration and calculated stats remain distinct. Calculated stats are PoB
outputs, not in-game measurements. The return shape is:

- `get_character`: build/items/item_sets/skills/trees/configuration and source.
- `get_socketed_gems`: PoE2 skill groups and source; no PoE1 socket-color model.
- `get_character_pob`: `pob_xml`, `pob_code`, and source in JSON. Pass `pob_xml`
  to the separate PoB2 server.

Source includes `source_age_text_at_capture`, `captured_at`,
`cache_age_seconds`, exact URL, and a null exact source timestamp when unknown.
The displayed age is retained as text, not presented as live character data.
Snapshots are kept only in process memory for 15 minutes. A global one-minute
profile-fetch cooldown limits uncached requests. No private data is published.

## Remaining work after this bounded slice

- `list_tabs`, `get_tab`, `price_tab`, `find_items`, `cache_status`,
  and `scan_stash_tabs` are not implemented in this
  slice. GGG's [reference](https://www.pathofexile.com/developer/docs/reference)
  labels account/guild/public stashes PoE1-only; official character access needs
  OAuth. No credentials are requested or harvested.
- `score_rare` works locally: it extracts PoE2 clipboard mods, identifies
  comparison criteria (including Spirit, skill levels, Critical Damage Bonus),
  preserves unknown text, and returns a null price. It does not call a source
  or pretend that arbitrary mod weights are market prices.
- `poe_auth_status` reports the audited OAuth capability and implementation
  state, **not remote token validity**. Without an explicit owned store, token
  status is `not_inspected`; with one, only local expiry/scope/state is inspected. Official
  `GET https://api.pathofexile.com/character/poe2` and
  `GET https://api.pathofexile.com/character/poe2/{name}` support PoE2 with
  `account:characters`. The client and opt-in PKCE flow are implemented with
  offline contracts; authenticated live verification remains. See OAUTH_REPORT.md.
- `kf_check` returns an unsupported error: its inherited PoE1 breakpoint model
  has no verified PoE2 basis.
- Exact baseline copies of replaced character/stash/library modules are in
  [legacy/](legacy/README.md). Their APIs and unported functionality remain
  available for the main agent to audit; this is not a complete suite port.
- Browser/profile page changes can break public exports. Missing optional
  dependencies, browser binaries, blocked pages, or missing source age are
  surfaced; there is no hidden fallback to private APIs.
- There is no historical backfill, background polling, appraisal of exact rolls,
  automatic purchase, or headless PoB engine in this submodule.
- Filter tools edit only local files; they do not publish filters or validate
  all game-version syntax. Existing filter parser behavior is otherwise retained.

## Verification

```sh
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
```

Tests use synthetic fixtures and mocked HTTP, plus a real MCP subprocess and
stdio client from an unrelated working directory. They cover discovery,
missing/duplicate module failures, schema errors, PoE2 URLs/currency units,
variant ambiguity, league-separated history, source failures, ETags/cooldowns,
and public export validation. Live probes are separate from the offline suite;
see [PORT_REPORT.md](PORT_REPORT.md) for observations and limits.

This product is not affiliated with or endorsed by Grinding Gear Games.
