# poe-trade-mcp — Tool Reference

Multi-server MCP bundle for Path of Exile. All tools are prefixed `mcp__poe__` in the Claude context.
Entry point: `poe_all.py`.

---

## poe-market — Price History

Local price history database built from scraping poe.ninja snapshots.

| Tool | Description |
|------|-------------|
| `get_price` | Latest price for a specific item (chaos value + category) |
| `get_price_history` | Full price trajectory for an item across all snapshots |
| `search_items` | Search items by name substring; returns latest price for each match |
| `get_risers` | Items with the biggest positive price increase (% change) |
| `get_fallers` | Items with the biggest negative price drop (% change) |
| `get_movers` | Items with the biggest absolute price movement (up or down) |
| `snapshot_status` | Database info: total snapshots, latest fetch time, total items tracked |

---

## poe-stash — Stash Tab Management

> ⚠️ **`list_tabs`, `get_tab`, `find_items`, and `scan_stash_tabs` are currently blocked.** GGG disabled the legacy `character-window/get-stash-items` endpoint; calls return HTTP 403. OAuth developer registration is required for full stash access (designed for public apps, not personal tools). **`score_rare` and `price_tab` still work.** For bulk stash scanning, use [WealthyExile](https://www.wealthyexile.com). See [`playbooks/stash-scanning.md`](../playbooks/stash-scanning.md).

| Tool | Description |
|------|-------------|
| `list_tabs` | ⛔ Blocked — List all stash tab names and indices |
| `get_tab` | ⛔ Blocked — Get all items from a stash tab by name or index (5-min cache) |
| `find_items` | ⛔ Blocked — Search stash tabs for items matching a query (name, base, or mod text) |
| `score_rare` | ✅ Score a rare item from PoE clipboard text; returns price estimate + mod breakdown |
| `price_tab` | ✅ Score and price all rare items in a stash tab, sorted by value (cache-only; populate with `get_tab` first) |
| `cache_status` | Show cache freshness for stash tabs |

---

## poe-trade — Trade Site Search

Queries the official Path of Exile trade API. **`search_trade` and `search_by_item_mods` return a clickable trade URL + total count only (ExileExchange pattern) — no listing details fetched. User opens the URL in their browser.**

| Tool | Description |
|------|-------------|
| `search_trade` | Search trade for items with filters; returns trade URL + total count |
| `get_stat_ids` | Look up trade filter stat IDs by keyword |
| `search_by_item_mods` | Search trade by mod text without needing stat IDs; returns trade URL + total count |
| `fetch_listing` | Fetch full details for specific listing IDs from a previous search (use sparingly) |

---

## poe-char — Character Data

Fetches live character data from the PoE API.

| Tool | Description |
|------|-------------|
| `get_character` | Fetch live gear and passive tree for the configured character |
| `get_socketed_gems` | Exact socket layout + gem placement per equipped item from the PoE API (colours, links, which gem in each socket, empty sockets) — the authoritative binding PoB discards |
| `get_character_pob` | Fetch character data and return a PoB-ready XML build |
| `scan_stash_tabs` | Price all stash tabs whose name starts with `_` |
| `kf_check` | Kinetic Fusillade breakpoint analysis via headless PoB (attack rate vs max effective APS) |

---

## poe-pricer — Item Pricing

Prices individual items using poe.ninja and the rare scorer.

| Tool | Description |
|------|-------------|
| `ninja_lookup` | poe.ninja price for any named item (cached 15 min) |
| `price_item` | Price a single item from API dict or clipboard text |
| `price_items` | Price a batch of items, sorted by price (highest first) |

---

## poe-filter — Loot Filter Editing

Read and edit a local `.filter` file in place.

| Tool | Description |
|------|-------------|
| `get_filter_info` | Summary: file path, total lines, block count, section headers |
| `find_blocks` | Search filter blocks by keyword; returns block type, comment, and line numbers |
| `get_block` | Get the full text of a block by starting line number |
| `add_block` | Insert a new filter block at top/bottom/after a pattern or line |
| `remove_block` | Remove a block by starting line number |
| `replace_block` | Replace a block entirely with new text |
| `set_basetype_rule` | Add a top-priority Show/Hide rule for one or more BaseTypes |
