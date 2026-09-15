# PoE2 loot-filter workflows

The `poe_filter` server now reads, edits, validates, previews and generates actual
`.filter` files. `poe_all.py` discovers all thirteen filter tools automatically.
Nothing here selects a filter in the game, publishes it, or reloads a game client.

## Sources and scope

- [GGG syntax reference](https://www.pathofexile.com/item-filter/about).
- [GGG PoE2 Third Edict classes and bases](https://www.pathofexile.com/forum/view-thread/3828542).
- [GGG PoE2 Last of the Druids changes](https://www.pathofexile.com/forum/view-thread/3884730): socketables use class `Augment`.
- Base names and structural properties come from the selected local PoB2
  `Data/Bases/*.lua`. The reader verifies `GameVersions.lua`, handles native
  assignment order, and excludes hidden definitions by default. It executes no Lua.
  Definitions establish identity, not current drop availability or crafting value.
- Economy generation uses the existing anonymous PoE2 poe.ninja normalizer and
  HTTP rate limiter, with no price-database writes or new data-source fallback.

`validate_filter` checks document syntax and, when a native catalog is supplied,
equipment base/class identities. It is **not the game-client compiler**. Unknown
class names are explicitly unverified; known obsolete/PoE1-only classes and
directives are rejected. Imported contents are not recursively validated.

Existing-filter validation treats missing native names as reference warnings;
PoB's visible base catalog is not the game's complete name registry. A shared
`BaseType` list may include alternatives excluded by `Class`: the remaining
intersection is still a valid rule. Creation with a supplied catalog keeps
strict checks against missing equipment names and wholly incompatible rules.

`AnyEnchantment` is supported as the documented boolean condition. Legacy
boolean decoration on drop-sound commands and the observed named sound ID
`ShMirror` are accepted with compatibility warnings. Drop-sound commands act by
presence; `DisableDropSound False` is not interpreted as enabling the sound.
Sound playback is not tested. New generated rules use the documented numeric IDs.

For `HasExplicitMod` previews, supply `explicit_mods` as a list of affix names.
The count includes each matching affix once, even if several patterns match it.
Missing affix data remains unknown rather than being treated as an empty list.

## Tools

| Tool | Purpose |
|---|---|
| `get_filter_info` | Read counts and section headers; `Continue` is an action within a block. |
| `find_blocks`, `get_block` | Locate and read rules using 1-based line numbers. |
| `add_block` | Insert a validated block; `top` precedes rules and imports. |
| `remove_block`, `replace_block` | Edit one rule while preserving adjacent sections, UTF-8 BOM and newline convention. |
| `set_basetype_rule` | Add an exact base override; replace only an equivalent managed rule. |
| `reload_filter` | Read the file again; never reload the game. |
| `get_filter_catalog` | Search native PoE2 bases and their actual filter classes, such as `Foci` and `Quarterstaves`. |
| `validate_filter` | Validate `text` or an explicit `filter_path`. |
| `test_filter` | Symbolically test ordered rules and `Continue` on supplied item fields. Missing fields and imports produce unknown results. |
| `create_filter` | Create a filter from supplied text blocks with a final `Show` for unmatched drops. |
| `generate_economy_filter` | Create a static, currency-aware reference-price snapshot with optional caller rules. |

New files require an **absolute `output_path` ending in `.filter`** and refuse to
overwrite existing files by default. Use a review directory first. Existing-file
tools accept `filter_path`, or the explicit `POE_FILTER_PATH` setting. The legacy
read/edit default remains `~/Documents/My Games/Path of Exile 2/Starting.filter`;
it is not used for generation. Windows paths are translated only to an available
WSL drive when running on Linux. No automatic Windows profile discovery occurs.

## Native base discovery and generation

Pass `pob_directory` or set `POB_INSTALL_DIR` to a complete PoB2 installation or
source checkout. No PoE1 crafting-base list is substituted when data is missing.
Catalog results include native requirements, base defenses, socket limits, source
files and unmapped native types. `include_hidden` is an explicit opt-in.
Set `include_bases=false` to discover documented class mappings without requiring
a local installation; the response marks the native base catalog as unloaded.

Call `create_filter` with `output_path`, optional `pob_directory`, and `blocks`,
for example a block using a base returned by the catalog:

```text
Show
    Class == "Body Armours"
    BaseType == "Runeforged Vile Robe"
    Rarity <= Rare
    ItemLevel >= 80
    SetFontSize 40
    SetBorderColor 120 190 255
```

This highlights a particular item base and item level. It does not value the
item's rolls or predict crafting results. `BaseEnergyShield` and `BaseWard` are
base-stat conditions, not guarantees about rolled item defenses or build effects.

## Economy generation

Supply an exact `league`, explicit `output_path`, up to five supported `categories`,
and the comparison `currency` (`exalted`, `chaos` or `divine`). `min_value` and
`high_value` are configurable highlighting thresholds in that currency, not price
estimates. Values come from the source's own conversion fields.

- No rate is invented when a conversion is unavailable. Such rows stay unpriced.
- `hide_below` defaults to false and can hide only known low-value fungible bases.
  An unpriced same-name observation prevents that generated Hide rule.
- Unique names are never substituted for drop base names. Unique variants sharing
  a base receive the highest observed reference highlight and are never hidden by
  a cheap variant. This does not establish the value of an unidentified drop.
- Gem economy rows without a verified drop-name/level mapping are reported in
  `skipped` and create no hiding rules. Explicit gem/class rules remain available.
- Caller `blocks` take precedence. Otherwise unmatched and unknown drops reach
  the final `Show`. Prices are a static snapshot; no monitoring runs in the game.
- Output includes source URLs, fetch timestamps, units, unpriced/skipped rows and
  the artifact hash. Fetch time is not the underlying market snapshot timestamp.

## Verification

Offline tests use synthetic native definitions and explicit price fixtures:

```sh
python -m pytest tests/test_poe2_filters.py tests/test_port.py tests/test_transport.py
```

The original active filter is not needed for testing. Generate into a new private
temporary directory, run `validate_filter`, and use `test_filter` with representative
items before separately selecting a filter in the game.
