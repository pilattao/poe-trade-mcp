# Bounded PoE2 trade submodule port — historical checkpoint handoff

OAuth follow-up: [OAUTH_REPORT.md](OAUTH_REPORT.md) supersedes the OAuth gaps
below. The original slice and its test results are retained as history.

Date: 2026-09-15. All changes are directly in this submodule; no commits,
pushes, sibling edits, shared environment changes, or live-runtime changes.
This is a substantial working slice, **not complete restoration of the suite**.

## Changes delivered

- Deliberate runtime dependencies: `mcp>=1.26,<2`, anyio, jsonschema,
  defusedxml. Removed unused PyPoE/RePoE Git installs. Playwright is optional.
- One coherent registry for six owned modules and 39 tools. Imports/schema
  errors/duplicate names are fatal; no silent dropping and no ambiguous sibling
  `server`/`pob_vault_mcp` imports. Standalone and combined dispatch preserve MCP
  `isError`. The suite owns launching its separate PoB modules.
- PoE2 trade URLs/realm, current metadata validation, explicit league/currency,
  zero bounds, online/instant-buyout selection, exact mod mapping and explicit
  rejection of PoE1 linked-socket filters. The metadata default sale type must
  be **omitted**, not sent as null: the latter was live-tested and rejected.
- Real public PoE2 exchange and plural-category stash prices. Reference
  currencies and conversions are correctly modeled; volume is not listings;
  unique variants are retained instead of choosing the most expensive row.
- `price_db.py` is a real SQLite observation cache, created here because no
  source existed in the checked-out repository/project tree. Prices/history/
  movements separate exact leagues and category/variant/currency identities.
  Successful overview fetches and `refresh_prices` populate it. Cached/304
  reuse does not invent observations. Existing PoE1 price DBs are not loaded.
- HTTP pacing, response limits, JSON shape/error checks, ETags, hourly caching,
  and shared cooldowns after 429. No authenticated call, credential discovery,
  auto-retry loop, trade, purchase, or message is performed.
- Portable public browser export in `character_public.py`. Account/name, exact
  profile URL, source-age label and PoB2 XML are validated. Both uppercase page
  labels and original label casing are handled. Equipment sets, skills, trees,
  configuration and calculated stats are retained. Browser dependencies are
  optional and fail clearly only on a character request.
- Local `score_rare` provides PoE2 clipboard mod extraction and comparison
  criteria with unknown mods retained and no manufactured price. This is an
  analysis aid, not a calibrated economic score.
- `poe_auth_status` describes the actual official PoE2 OAuth capability and
  this port's state. It does not open or validate tokens. Filter tools retain
  local behavior and use the PoE2 default documents directory.

## Verification and live observations

Offline tests include a real MCP stdio client/subprocess launched from an
unrelated working directory. They cover discovery, missing/duplicate modules,
error propagation, invalid schemas, PoE2 currency/schema/URL construction,
variant ambiguity, league-isolated history, cache/ETag/cooldown behavior,
malformed/public-source errors, local clipboard analysis, OAuth capability
status without token reads, and public PoB2 export validation.

Final offline verification: **32 tests passed with MCP 1.30.0 and again with
MCP 1.26.0** in this submodule's isolated `.test-venv`. Dependency checks,
undefined/unused-name lint, Python parsing, and `git diff --check` passed.
Preserved baseline copies were compared byte-for-byte against Git.

Limited anonymous live probes on 2026-09-15:

| Probe | Observed result |
|---|---|
| `/poe2/api/economy/leagues` | HTTP 200; exact league IDs, including concurrent leagues. |
| Exchange `Currency`, Forbidden Rites | HTTP 200; `core.primary=divine`, proper chaos/exalted conversions. |
| Stash `UniqueAccessories`, same league | HTTP 200; `primaryValue`, `listingCount`, `baseType`, optional variant/corruption. Exact named unique lookup worked. |
| Official `/api/trade2/data/filters` and `/leagues` | HTTP 200; `armour.chest`, PoE2 realm, current sale/status options. |
| Official anonymous search with corrected omission of `sale_type` | HTTP 200; returned query ID/count and valid PoE2 search URL. |
| Public `get_character`, provided profile | Successful PoB2 export: 20 items, 16 skill groups, 1 tree. Source displayed “2 hours ago”; exact source timestamp unknown and left null. |

The live character check printed only counts/age, and kept the decoded export
in process memory. No browser login state was reused. Pricing probes wrote only
a separate ignored `.cache/smoke-prices.sqlite3`, not the default runtime cache.
No claim is made that every category or every inherited feature was live-tested.
`fetch_listing` has schema/URL support but was not live-verified against a listing.

## Exact remaining capabilities

**Eight discovered tools currently return explicit not-implemented errors:**

`poe_auth`, `get_tab`, `list_tabs`, `price_tab`, `find_items`, `cache_status`,
`scan_stash_tabs`, `kf_check`.

`poe_auth_status` is implemented as a capability/state report; `token_status`
remains `not_inspected`. It does not claim tokens are absent/valid/expired.
`score_rare` is implemented as local mod/comparable analysis; numeric valuation
and affix-tier calibration remain unavailable. `get_character_pob` returns
actual `pob_xml`/`pob_code` plus provenance in JSON, not a fabricated GGG import.
The old raw-XML return contract therefore requires callers to select `pob_xml`.

### Official OAuth character API is supported in PoE2

[GGG account-character reference](https://www.pathofexile.com/developer/docs/reference#characters)
explicitly supports `poe2` for:

- `GET https://api.pathofexile.com/character/poe2`
- `GET https://api.pathofexile.com/character/poe2/{name}`

Required scope: `account:characters`. The detail endpoint returns equipment,
inventory, and passive information for the authorized account. See also
[GGG authorization](https://www.pathofexile.com/developer/docs/authorization).
The remaining gap is implementing and validating an appropriately scoped OAuth
flow/client and PoE2 character normalization. It is **not** absence of a PoE2
OAuth API. No authorized HTTP calls or credential inspection were performed.

### Stash evidence differs from character evidence

The same [GGG reference](https://www.pathofexile.com/developer/docs/reference)
labels Account Stashes, Guild Stashes, and Public Stashes **PoE1 only** and does
not list `poe2` for those routes. This establishes the documented API gap only;
it is not a claim that every website/internal/user-export mechanism is
impossible. The legacy cookie endpoints are not a verified PoE2 implementation.
Do not request a session cookie as a substitute for an audited supported route.

### Public ninja boundary

[poe.ninja's API documentation](https://poe.ninja/docs/api) permits the economy
surface and disallows third-party use of internal builds/profiles endpoints.
The character adapter reads the ordinary public page and its visible export in
an anonymous browser, following the pre-existing user-approved approach. It
never calls the internal APIs directly. Page/UI changes and public visibility
can still prevent exports; those failures are surfaced.

### Preserved originals

Exact `.py.txt` copies of baseline `poe_lib.py`, `poe_char.py`, and `poe_stash.py`
are in [legacy/](legacy/README.md), from commit
`544387f8d57bd69cd916022ee8e2f2e2c1174b76`. They are not imported by discovery.
Root `poe_oauth.py`, `stash_cache.py`, and `rare_scorer.py` are unchanged.
This retains the old authorization, stash normalization, character formatting,
scan and breakpoint implementations as concrete reference for the next port.
None should be re-enabled with its PoE1 assumptions unchecked.

## Next work for the main agent

1. Integrate this checkout using the suite's chosen MCP 1.x interpreter; install
   optional character browser requirements only in the environment you own.
2. Restore audited PoE2 OAuth character support separately, retaining the public
   snapshot option. Clarify exact scope/setup only after implementing a reviewable
   client; no credentials were needed for this delivered slice.
3. Investigate supported PoE2 stash/user-export routes before reviving stash
   tools. Restore local cache inspection only with explicit cache origin and
   account/league isolation; do not read inherited private files implicitly.
4. Add actual PoE2 affix-tier/comparable pricing models and PoB2 calculations if
   needed. The local mod extractor currently deliberately does not price rares.
