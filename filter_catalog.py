"""PoE2 filter names from verified local PoB2 literal data, never PoE1 fallbacks.

The loader reads structural fields only. It does not execute Lua, inspect builds,
read credentials, or copy the native catalog into the repository.
"""
from __future__ import annotations
import os
import re
from pathlib import Path

SOURCES = [
    "https://www.pathofexile.com/item-filter/about",
    "https://www.pathofexile.com/forum/view-thread/3828542",
    "https://www.pathofexile.com/forum/view-thread/3884730",
]
CLASS_MAP = {
    'Body Armour': 'Body Armours', 'Helmet': 'Helmets', 'Gloves': 'Gloves', 'Boots': 'Boots',
    'Shield': 'Shields', 'Buckler': 'Bucklers', 'Focus': 'Foci', 'Quiver': 'Quivers',
    'Ring': 'Rings', 'Amulet': 'Amulets', 'Belt': 'Belts', 'Jewel': 'Jewels', 'Charm': 'Charms',
    'Bow': 'Bows', 'Crossbow': 'Crossbows', 'Wand': 'Wands', 'Sceptre': 'Sceptres',
    'Staff': 'Staves', 'Warstaff': 'Quarterstaves', 'Spear': 'Spears', 'Talisman': 'Talismans',
    'Claw': 'Claws', 'Dagger': 'Daggers', 'One Hand Sword': 'One Hand Swords',
    'Two Hand Sword': 'Two Hand Swords', 'One Hand Axe': 'One Hand Axes', 'Two Hand Axe': 'Two Hand Axes',
    'One Hand Mace': 'One Hand Maces', 'Two Hand Mace': 'Two Hand Maces', 'Flail': 'Flails',
}
CLASSES = sorted(set(CLASS_MAP.values()) | {
    'Quarterstaves', 'Life Flasks', 'Mana Flasks', 'Stackable Currency', 'Quest Items',
    'Waystones', 'Map Fragments', 'Pinnacle Keys', 'Omen', 'Augment', 'Incubators',
    'Uncut Skill Gems', 'Uncut Spirit Gems', 'Uncut Support Gems', 'Skill Gems', 'Support Gems',
    'Tablet',
})


def local_path(value: str | Path) -> Path:
    value = str(value)
    if os.name != 'nt' and re.match(r'^[A-Za-z]:[\\/]', value):
        value = '/mnt/' + value[0].lower() + '/' + value[3:].replace('\\', '/')
    return Path(value).expanduser()


def _tokens(text: str):
    """Tokenize Lua literals/comments; code is never evaluated."""
    result, i = [], 0
    while i < len(text):
        if text[i].isspace(): i += 1; continue
        comment = text.startswith('--', i)
        start = i + 2 if comment else i
        bracket = re.match(r'\[(=*)\[', text[start:])
        if bracket:
            end_marker = ']' + bracket[1] + ']'
            end = text.find(end_marker, start + len(bracket[0]))
            if end < 0: raise ValueError('Unclosed native Lua long string/comment')
            if not comment: result.append(('string', text[start + len(bracket[0]):end]))
            i = end + len(end_marker); continue
        if comment:
            end = text.find('\n', i); i = len(text) if end < 0 else end + 1; continue
        if text[i] in '\"\'':
            quote, parts = text[i], []; i += 1
            while i < len(text) and text[i] != quote:
                if text[i] == '\\':
                    i += 1
                    if i == len(text): raise ValueError('Unclosed native Lua string')
                    escape = {'n': '\n', 'r': '\r', 't': '\t', '\\': '\\', '"': '"', "'": "'"}
                    if text[i] in escape: parts.append(escape[text[i]]); i += 1
                    elif text[i].isdigit():
                        match = re.match(r'\d{1,3}', text[i:]); parts.append(chr(int(match[0]))); i += len(match[0])
                    else: raise ValueError('Unsupported escape in native literal data')
                else: parts.append(text[i]); i += 1
            if i == len(text): raise ValueError('Unclosed native Lua string')
            result.append(('string', ''.join(parts))); i += 1; continue
        number = re.match(r'-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?', text[i:])
        if number:
            result.append(('number', float(number[0]) if any(c in number[0] for c in '.eE') else int(number[0]))); i += len(number[0]); continue
        name = re.match(r'[A-Za-z_][A-Za-z_0-9]*', text[i:])
        if name: result.append(('name', name[0])); i += len(name[0]); continue
        result.append((text[i], text[i])); i += 1
    return result


def _literal(tokens, index):
    kind, value = tokens[index]
    if kind in ('string', 'number'): return value, index + 1
    if kind == 'name' and value in ('true', 'false', 'nil'):
        return {'true': True, 'false': False, 'nil': None}[value], index + 1
    if kind != '{': raise ValueError(f'Non-literal native base value: {value}')
    result, index, position = {}, index + 1, 1
    while index < len(tokens) and tokens[index][0] != '}':
        if tokens[index][0] in (',', ';'): index += 1; continue
        if tokens[index][0] == '[':
            key, index = _literal(tokens, index + 1)
            if tokens[index][0] != ']' or tokens[index + 1][0] != '=': raise ValueError('Malformed native table key')
            index += 2
        elif tokens[index][0] == 'name' and tokens[index + 1][0] == '=':
            key = tokens[index][1]; index += 2
        else: key = position; position += 1
        result[key], index = _literal(tokens, index)
    if index >= len(tokens): raise ValueError('Unclosed native table')
    return result, index + 1


def _bases(text):
    tokens, index = _tokens(text), 0
    while index + 5 < len(tokens):
        if tokens[index] == ('name', 'itemBases') and tokens[index + 1][0] == '[' and tokens[index + 2][0] == 'string' and tokens[index + 3][0] == ']' and tokens[index + 4][0] == '=':
            name = tokens[index + 2][1]
            data, index = _literal(tokens, index + 5)
            if not isinstance(data, dict): raise ValueError('Native base is not a literal table')
            yield name, data
        else: index += 1


def load_catalog(pob_directory=None, *, include_hidden=False):
    configured = pob_directory or os.environ.get('POB_INSTALL_DIR') or os.environ.get('POE_MCP_SUITE_POB_DIR')
    if not configured: raise ValueError('Set POB_INSTALL_DIR or provide pob_directory for verified PoE2 bases; no legacy base list is used')
    root = local_path(configured).resolve()
    if not (root / 'Data/Bases').is_dir() and (root / 'src/Data/Bases').is_dir(): root /= 'src'
    version_file = root / 'GameVersions.lua'
    if not version_file.is_file(): raise ValueError('Cannot verify PoE2 GameVersions.lua in the selected installation')
    tokens = _tokens(version_file.read_text(encoding='utf-8-sig'))
    versions = [tokens[i + 2][1] for i in range(len(tokens) - 2) if tokens[i] == ('name', 'liveTargetVersion') and tokens[i + 1][0] == '=' and tokens[i + 2][0] == 'string']
    if len(versions) != 1 or not re.fullmatch(r'0_\d+(?:_\d+)*', versions[0]): raise ValueError('Selected native data is not a verified PoE2 installation')
    files = sorted((root / 'Data/Bases').glob('*.lua'))
    if not files: raise ValueError('No native PoE2 base definitions found')
    bases, skipped, definitions, overrides = [], set(), {}, 0
    for file in files:
        if file.stat().st_size > 4_000_000: raise ValueError('Native base file exceeds the analysis bound')
        for name, data in _bases(file.read_text(encoding='utf-8-sig')):
            if name in definitions:
                if definitions[name][1] != file: raise ValueError(f'Ambiguous base load order across files: {name}')
                overrides += 1
            definitions[name] = (data, file)
    for name, (data, file) in definitions.items():
        if data.get('hidden') and not include_hidden: continue
        kind, subtype, tags = data.get('type'), data.get('subType'), data.get('tags', {})
        item_class = CLASS_MAP.get(kind)
        if kind == 'Staff' and (subtype == 'Warstaff' or tags.get('warstaff')): item_class = 'Quarterstaves'
        if kind == 'Flask': item_class = {'Life': 'Life Flasks', 'Mana': 'Mana Flasks'}.get(subtype)
        if kind == 'Shield' and (tags.get('buckler') or subtype == 'Buckler'): item_class = 'Bucklers'
        if not item_class: skipped.add(str(kind)); continue
        bases.append({'name': name, 'item_class': item_class, 'native_type': kind, 'subtype': subtype,
            'requirements': data.get('req', {}), 'base_defenses': data.get('armour', {}),
            'socket_limit': data.get('socketLimit'), 'hidden': bool(data.get('hidden')),
            'source_file': str(file.relative_to(root))})
    return {'classes': CLASSES, 'bases': sorted(bases, key=lambda row: row['name']),
        'source': {'game': 'poe2', 'kind': 'native PoB2 definitions', 'directory': str(root), 'target_version': versions[0], 'filter_references': SOURCES},
        'notes': ['Native definitions establish base identity, not current drop availability or crafting value.',
                  'Class mappings cover documented and mapped native types; unmapped types are reported explicitly.'],
        'unmapped_native_types': sorted(skipped), 'source_order_overrides': overrides}
