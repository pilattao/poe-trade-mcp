"""Snapshot-based PoE2 filter highlights from existing anonymous economy readers.

No orders, accounts, price DB writes, fabricated rates or rare-item valuations.
"""
import math
import urllib.parse
from datetime import datetime, timezone
from filter_model import quoted
from poe_lib import resolve_league
from poe_pricer import (CATEGORIES, NINJA_EXCHANGE_URL, NINJA_STASH_URL, _EXCHANGE_TYPES,
                        list_economy_leagues, normalize_overview)
from public_http import request_json


def fetch_filter_prices(league, categories):
    league = resolve_league(league)
    if not isinstance(categories, list) or not 1 <= len(categories) <= 5 or len(set(categories)) != len(categories):
        raise ValueError('Provide 1 to 5 distinct PoE2 economy categories')
    if any(category not in CATEGORIES for category in categories):
        raise ValueError('Unsupported PoE2 economy category; no PoE1 scarab or base-price fallback')
    if league not in [r['id'] for r in list_economy_leagues()['leagues']]:
        raise ValueError('Exact league is not present in the public PoE2 economy source')
    rows = []
    for category in categories:
        base = NINJA_EXCHANGE_URL if category in _EXCHANGE_TYPES else NINJA_STASH_URL
        url = base + '?' + urllib.parse.urlencode({'league': league, 'type': category})
        data, meta = request_json(url, ttl=300)
        rows.extend(normalize_overview(data, category, league, meta))
    if not rows: raise ValueError('The requested public categories contained no price rows; no economy filter was generated')
    return rows


def _positive(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def economy_blocks(rows, *, league, currency='exalted', min_value=1, high_value=10, hide_below=False):
    if currency not in ('chaos', 'divine', 'exalted'): raise ValueError('Use chaos, divine or exalted as the reference comparison unit')
    if not _positive(min_value) or not _positive(high_value) or high_value < min_value:
        raise ValueError('Value thresholds must be positive and high_value must be at least min_value')
    key = currency + '_value'
    grouped, unpriced, skipped, sources = {}, [], [], []
    for row in rows:
        if row.get('game', 'poe2') != 'poe2' or row.get('league', league) != league:
            raise ValueError('Economy rows belong to a different game or league')
        name, category, value = row.get('name'), row.get('category'), row.get(key)
        if not isinstance(name, str) or not name.strip(): raise ValueError('Economy row has no name')
        provenance = {k: row[k] for k in ('source_url', 'fetched_at', 'source_timestamp', 'freshness_note') if k in row}
        if provenance and provenance not in sources: sources.append(provenance)
        if not _positive(value): unpriced.append(name); continue
        unique = isinstance(category, str) and category.startswith('Unique')
        if unique:
            base_type = row.get('base_type')
            if not isinstance(base_type, str) or not base_type:
                skipped.append({'name': name, 'reason': 'Unique needs its actual drop base; unique display names cannot identify unopened drops'}); continue
        elif category in _EXCHANGE_TYPES:
            # Uncut gem tier names and lineage variants may not equal their drop
            # BaseType. Keep them shown unless a verified native mapping exists.
            if category in ('UncutGems', 'LineageSupportGems'):
                skipped.append({'name': name, 'reason': 'Gem level/variant needs a verified filter condition mapping'}); continue
            base_type = name
        elif category == 'PrecursorTablets': base_type = row.get('base_type') or name
        else:
            skipped.append({'name': name, 'reason': 'No verified drop-base mapping for this economy category'}); continue
        quoted(base_type)
        group = grouped.setdefault((base_type, unique), {'base_type': base_type, 'unique': unique, 'values': [], 'names': []})
        group['values'].append(value); group['names'].append(name)
    blocks, groups = [], []
    for group in sorted(grouped.values(), key=lambda g: max(g['values']), reverse=True):
        maximum = max(group['values'])
        action = 'Hide' if hide_below and not group['unique'] and group['base_type'] not in unpriced and maximum < min_value else 'Show'
        if group['unique']:
            note = 'Highest observed unique variant reference; identity/rolls are unknown on the ground'
        else: note = 'Reference snapshot; not an executable offer'
        lines = [f'{action} # {note}; {maximum:g} {currency}']
        if group['unique']: lines.append('    Rarity Unique')
        lines.append('    BaseType == ' + quoted(group['base_type']))
        if action == 'Show' and maximum >= min_value:
            lines += ['    SetFontSize ' + ('45' if maximum >= high_value else '38'),
                      '    SetBorderColor 255 210 80', '    SetBackgroundColor 25 25 25 230']
            if maximum >= high_value: lines += ['    PlayAlertSound 1 200', '    MinimapIcon 1 Yellow Star', '    PlayEffect Yellow Temp']
        blocks.append('\n'.join(lines))
        groups.append({**group, 'reference_value': maximum, 'action': action})
    return blocks, {'league': league, 'currency': currency, 'min_value': min_value, 'high_value': high_value,
        'created_at': datetime.now(timezone.utc).isoformat(), 'sources': sources, 'unpriced': sorted(set(unpriced)), 'skipped': skipped,
        'groups': groups, 'price_kind': 'Reference valuations; unique bases group variants conservatively',
        'notes': ['Unknown prices never create Hide rules. Unmatched drops remain visible; caller-supplied rules keep priority.',
                  'This is a static snapshot, not a live in-game price lookup or a promise of sale value.']}
