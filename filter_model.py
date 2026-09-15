"""PoE2 filter syntax checks and symbolic preview, based on GGG filter docs.

This is not the game client's compiler. Imports and missing item fields stay
explicitly unresolved in preview; unsupported directives are never ignored.
"""
from __future__ import annotations
import re
import shlex
from pathlib import Path
from filter_catalog import CLASSES, CLASS_MAP, SOURCES

HEADER = re.compile(r'^(Show|Hide|Minimal)(?:\s*(?:#.*)?)?$')
NUMERIC = {'AreaLevel', 'BaseArmour', 'BaseEnergyShield', 'BaseEvasion', 'BaseWard', 'DropLevel', 'GemLevel',
           'Height', 'ItemLevel', 'Quality', 'Sockets', 'StackSize', 'UnidentifiedItemTier', 'WaystoneTier', 'Width', 'CorruptedMods'}
BOOLEAN = {'AlwaysShow', 'AnyEnchantment', 'Corrupted', 'FracturedItem', 'HasImplicitMod', 'HasVaalUniqueMod', 'Identified', 'IsVaalUnique', 'Mirrored', 'TwiceCorrupted'}
STRINGS = {'BaseType', 'Class', 'Rarity', 'HasExplicitMod'}
DROP_SOUND_ACTIONS = {'DisableDropSound', 'EnableDropSound', 'DisableDropSoundIfAlertSound', 'EnableDropSoundIfAlertSound'}
NO_ARGS = {'Continue'} | DROP_SOUND_ACTIONS
# Observed in an accepted realm=poe2 online-filter cache. The public GGG table
# lists numeric IDs only, so named-ID compatibility stays an explicit warning.
COMPATIBLE_ALERT_IDS = {'ShMirror'}
COLORS = {'Red', 'Green', 'Blue', 'Brown', 'White', 'Yellow', 'Cyan', 'Grey', 'Orange', 'Pink', 'Purple'}
SHAPES = {'Circle', 'Diamond', 'Hexagon', 'Square', 'Star', 'Triangle', 'Cross', 'Moon', 'Raindrop', 'Kite', 'Pentagon', 'UpsideDownHouse'}
ACTIONS = NO_ARGS | {'SetTextColor', 'SetBorderColor', 'SetBackgroundColor', 'SetFontSize', 'PlayAlertSound',
                     'PlayAlertSoundPositional', 'CustomAlertSound', 'CustomAlertSoundOptional', 'MinimapIcon', 'PlayEffect'}
OPERATORS = {'=', '==', '!', '!=', '<', '<=', '>', '>='}
RARITY = {'Normal': 0, 'Magic': 1, 'Rare': 2, 'Unique': 3}
EQUIPMENT_CLASSES = set(CLASS_MAP.values()) | {'Quarterstaves', 'Life Flasks', 'Mana Flasks'}
LEGACY_CLASSES = {'Scarab', 'Scarabs', 'Maps', 'Divination Card', 'Divination Cards', 'Abyss Jewel', 'Abyss Jewels', 'Socketable'}


def tokens(line):
    return shlex.split(line.lstrip('\ufeff'), comments=True, posix=True)


def header(line):
    match = HEADER.fullmatch(line.lstrip('\ufeff').strip())
    return match[1] if match else None


def find_block_bounds(lines, start):
    end = len(lines) - 1
    for i in range(start + 1, len(lines)):
        if header(lines[i]) or re.match(r'^Import(?:\s|$)', lines[i].strip()):
            end = i - 1; break
    # Trailing section comments belong to the next section, not the edited rule.
    while end > start and (not lines[end].strip() or lines[end].lstrip().startswith('#')): end -= 1
    return start, end


def parse_blocks(lines):
    blocks, i = [], 0
    while i < len(lines):
        kind = header(lines[i])
        if kind:
            start, end = find_block_bounds(lines, i)
            body = lines[start:end + 1]
            conditions = {}
            for line in body[1:]:
                try: parts = tokens(line)
                except ValueError: continue
                if parts: conditions[parts[0]] = line.strip()[len(parts[0]):].strip()
            blocks.append({'line': start, 'end_line': end, 'type': kind, 'header': lines[i].strip(),
                'comment': lines[i].partition('#')[2].strip(), 'body': '\n'.join(body), 'conditions': conditions})
            i = end + 1
        else: i += 1
    return blocks


def quoted(value):
    if not isinstance(value, str) or not value.strip() or any(c in value for c in '\r\n\t\0"\\'):
        raise ValueError('Filter names must be nonempty single-line strings without quotes or backslashes')
    return '"' + value + '"'


def operand(args):
    if args and args[0] in OPERATORS: return args[0], args[1:]
    return '=', args


def numeric(args):
    op, values = operand(args)
    if len(values) == 1:
        match = re.fullmatch(r'(<=|>=|==|!=|!|=|<|>)?(-?\d+(?:\.\d+)?)', values[0])
        if match: return match[1] or op, float(match[2])
    raise ValueError('Expected a comparison and a finite numeric value')


def _integer(value, minimum, maximum):
    if not re.fullmatch(r'\d+', value) or not minimum <= int(value) <= maximum:
        raise ValueError(f'Expected integer from {minimum} to {maximum}')


def mod_condition(args):
    """GGG's HasExplicitMod [comparison/count] mod-name list."""
    if not args or not args[0]: raise ValueError('HasExplicitMod needs modifier names')
    op, count, names = '>=', 1, args
    attached = re.fullmatch(r'(<=|>=|==|!=|!|=|<|>)(\d+)', args[0])
    if attached:
        op, count, names = attached[1], int(attached[2]), args[1:]
    elif args[0] in OPERATORS:
        if len(args) < 3 or not re.fullmatch(r'\d+', args[1]): raise ValueError('Modifier count must be a non-negative integer followed by names')
        op, count, names = args[0], int(args[1]), args[2:]
    elif re.fullmatch(r'\d+', args[0]):
        op, count, names = '=', int(args[0]), args[1:]
    elif args[0][0] in '<>!=':
        raise ValueError('Invalid modifier-count comparison')
    if not names or any(not name for name in names): raise ValueError('HasExplicitMod needs modifier names after its count')
    return op, count, names


def _directive(key, args):
    if key in NUMERIC:
        _, value = numeric(args)
        if value < 0: raise ValueError('Numeric filter thresholds must be non-negative')
    elif key in BOOLEAN:
        if args not in (['True'], ['False']): raise ValueError('Expected True or False')
    elif key == 'HasExplicitMod':
        mod_condition(args)
    elif key in STRINGS:
        op, values = operand(args)
        if not values: raise ValueError('Expected at least one quoted name')
        if key == 'Rarity' and any(v not in RARITY for v in values): raise ValueError('Unknown rarity')
        if key == 'Class':
            if op not in ('=', '==', '!', '!='): raise ValueError('Class only supports string comparisons')
            for value in values:
                if value in LEGACY_CLASSES:
                    raise ValueError(f'Obsolete or PoE1-only filter class {value!r}; use get_filter_catalog (socketables use Augment)')
        if key == 'BaseType' and op not in ('=', '==', '!', '!='): raise ValueError('BaseType only supports string comparisons')
    elif key in NO_ARGS:
        if args and not (key in DROP_SOUND_ACTIONS and args in (['True'], ['False'])):
            raise ValueError(f'{key} does not accept these values')
    elif key in ('SetTextColor', 'SetBorderColor', 'SetBackgroundColor'):
        if len(args) not in (3, 4): raise ValueError('Expected RGB or RGBA values')
        for value in args: _integer(value, 0, 255)
    elif key == 'SetFontSize':
        if len(args) != 1: raise ValueError('Expected one font size')
        _integer(args[0], 1, 45)
    elif key in ('PlayAlertSound', 'PlayAlertSoundPositional'):
        if args == ['None']: return
        if not 1 <= len(args) <= 2: raise ValueError('Expected sound ID and optional volume')
        if args[0] not in COMPATIBLE_ALERT_IDS: _integer(args[0], 1, 16)
        if len(args) == 2: _integer(args[1], 0, 300)
    elif key in ('CustomAlertSound', 'CustomAlertSoundOptional'):
        if not 1 <= len(args) <= 2: raise ValueError('Expected sound filename and optional volume')
        if len(args) == 2: _integer(args[1], 0, 300)
    elif key == 'MinimapIcon':
        if args == ['-1']: return
        if len(args) != 3 or args[1] not in COLORS or args[2] not in SHAPES: raise ValueError('Expected size, color and shape')
        _integer(args[0], 0, 2)
    elif key == 'PlayEffect':
        if args == ['None']: return
        if len(args) not in (1, 2) or args[0] not in COLORS or len(args) == 2 and args[1] != 'Temp': raise ValueError('Expected effect color and optional Temp')
    else:
        raise ValueError(f'Unknown or unsupported PoE2 directive {key!r}; no PoE1 syntax fallback is used')


def validate(text, *, catalog=None, path=None, strict_native_names=False):
    if not isinstance(text, str): raise ValueError('Filter text must be a string')
    if len(text.encode('utf-8')) > 8_000_000: raise ValueError('Filter exceeds the 8 MB analysis bound')
    errors, warnings, imports, current = [], [], [], None
    lines = text.lstrip('\ufeff').splitlines()
    for index, line in enumerate(lines):
        try:
            parts = tokens(line)
            if not parts: continue
            kind = header(line)
            if kind:
                if kind == 'Minimal': raise ValueError('Minimal is a Ruthless directive, not a PoE2 filter block')
                current = kind; continue
            key, args = parts[0], parts[1:]
            if key == 'Import':
                if not 1 <= len(args) <= 2 or len(args) == 2 and args[1] != 'Optional': raise ValueError('Import needs a filename and optional Optional keyword')
                if not args[0].lower().endswith('.filter'): raise ValueError('Imported filename must end in .filter')
                imports.append({'line': index + 1, 'file': args[0], 'optional': len(args) == 2})
                if path and len(args) == 1 and not (Path(path).parent / args[0]).is_file():
                    raise ValueError(f'Required imported filter is missing: {args[0]}')
                current = None; continue
            if current is None: raise ValueError(f'{key} appears outside a Show/Hide block')
            _directive(key, args)
            if key in DROP_SOUND_ACTIONS and args:
                warnings.append({'line': index + 1, 'kind': 'compatibility',
                    'message': f'{key} {args[0]}: legacy boolean decoration is accepted; the command acts by presence, not as a boolean switch. GGG documents the bare command.'})
            if key in ('PlayAlertSound', 'PlayAlertSoundPositional') and args[0] in COMPATIBLE_ALERT_IDS:
                warnings.append({'line': index + 1, 'kind': 'compatibility',
                    'message': f'Named alert ID {args[0]} is accepted for observed PoE2 cache compatibility; the current public GGG numeric-ID table does not enumerate it. Playback is unverified.'})
            if key == 'Class':
                op, names = operand(args)
                for name in names:
                    if not any(name == c if op == '==' else name in c for c in CLASSES):
                        warnings.append({'line': index + 1, 'message': f'Class name is outside the verified catalog: {name}; game recognition is unverified'})
        except ValueError as exc: errors.append({'line': index + 1, 'message': str(exc)})
    blocks = parse_blocks(lines)
    names = {r['name']: r for r in catalog['bases']} if catalog else {}
    for block in blocks:
        directives = []
        for line in block['body'].splitlines()[1:]:
            try:
                p = tokens(line)
                if p: directives.append(p)
            except ValueError: pass
        conditions = [p for p in directives if p[0] not in ACTIONS]
        if block['type'] == 'Hide' and not conditions:
            warnings.append({'line': block['line'] + 1, 'message': 'Unconditional Hide matches every item that reaches it'})
        has_class_filter = any(p[0] == 'Class' for p in directives)
        selected_classes = set(CLASSES) if has_class_filter else set()
        for p in directives:
            if p[0] == 'Class':
                op, values = operand(p[1:])
                matching = {c for c in CLASSES if any(v == c if op == '==' else v in c for v in values)}
                if op in ('!', '!='): selected_classes.difference_update(matching)
                else: selected_classes.intersection_update(matching)
        for p in directives:
            if p[0] != 'BaseType': continue
            op, values = operand(p[1:])
            if op != '==': continue
            incompatible = [v for v in values if v in names and has_class_filter and names[v]['item_class'] not in selected_classes]
            unknown = [v for v in values if v not in names]
            compatible = [v for v in values if v in names and (not has_class_filter or names[v]['item_class'] in selected_classes)]
            if incompatible:
                message = f'{len(incompatible)} BaseType alternatives are excluded by Class; multiple alternatives are OR-ed within the BaseType condition'
                if strict_native_names and not compatible and not unknown:
                    errors.append({'line': block['line'] + 1, 'message': 'No native BaseType alternative can match the selected Class'})
                else:
                    warnings.append({'line': block['line'] + 1, 'kind': 'reference', 'message': message})
            for value in values:
                if catalog and selected_classes and selected_classes <= EQUIPMENT_CLASSES and value not in names:
                    issue = {'line': block['line'] + 1, 'message': f'Base {value!r} is absent from the selected visible PoE2 equipment definitions; game recognition is unverified'}
                    (errors if strict_native_names else warnings).append(issue)
                elif value not in names:
                    warnings.append({'line': block['line'] + 1, 'message': f'Base identity not checked against native equipment data: {value}'})
    if imports: warnings.append({'message': 'Imported contents are not recursively validated or simulated'})
    return {'valid': not errors, 'game': 'poe2', 'errors': errors, 'warnings': warnings,
        'all_names_verified': not any('not checked' in w['message'] or 'unverified' in w['message'] for w in warnings),
        'block_count': len(blocks), 'imports': imports, 'scope': 'Document syntax and supplied native base identities; not the game-client compiler', 'sources': SOURCES}


def _condition(key, args, item):
    if key == 'HasExplicitMod':
        mods = item.get('explicit_mods')
        if not isinstance(mods, list) or any(not isinstance(mod, str) for mod in mods): return None
        op, expected, names = mod_condition(args)
        # A single affix matching overlapping patterns counts once.
        count = sum(any(name in mod for name in names) for mod in mods)
        return _compare(count, expected, op)
    field = {'Class': 'class', 'BaseType': 'base_type', 'Rarity': 'rarity'}.get(key, re.sub(r'(?<!^)(?=[A-Z])', '_', key).lower())
    if field not in item or item[field] is None: return None
    actual = item[field]
    if key in BOOLEAN: return actual == (args[0] == 'True') if isinstance(actual, bool) else None
    if key in NUMERIC:
        if isinstance(actual, bool) or not isinstance(actual, (int, float)): return None
        op, expected = numeric(args)
    elif key in ('Class', 'BaseType', 'Rarity'):
        if not isinstance(actual, str): return None
        op, values = operand(args)
        if key == 'Rarity' and op in ('<', '<=', '>', '>='):
            if actual not in RARITY or len(values) != 1: return None
            actual, expected = RARITY[actual], RARITY[values[0]]
        else:
            matches = any(actual == v if op == '==' or key == 'Rarity' else v in str(actual) for v in values)
            return not matches if op in ('!', '!=') else matches
    else: return None
    return _compare(actual, expected, op)


def _compare(actual, expected, op):
    return {'=': lambda: actual == expected, '==': lambda: actual == expected, '!': lambda: actual != expected,
        '!=': lambda: actual != expected, '<': lambda: actual < expected, '<=': lambda: actual <= expected,
        '>': lambda: actual > expected, '>=': lambda: actual >= expected}[op]()


def preview(text, items):
    checked = validate(text)
    if not checked['valid']: raise ValueError(f'Cannot preview an invalid filter: {checked["errors"]}')
    blocks = parse_blocks(text.splitlines())
    results = []
    for item in items:
        actions, matched, decision = {}, [], 'Show'
        if checked['imports']:
            results.append({'decision': 'unknown', 'reason': 'Imported filter contents are unresolved', 'actions': {}, 'matched_lines': []}); continue
        for block in blocks:
            lines = [tokens(line) for line in block['body'].splitlines()[1:]]
            lines = [p for p in lines if p]
            checks = [_condition(p[0], p[1:], item) for p in lines if p[0] not in ACTIONS]
            if False in checks: continue
            if None in checks: decision = 'unknown'; break
            decision = block['type']; matched.append(block['line'] + 1)
            actions.update({p[0]: [] if p[0] in DROP_SOUND_ACTIONS else p[1:] for p in lines if p[0] in ACTIONS and p[0] != 'Continue'})
            if not any(p[0] == 'Continue' for p in lines): break
        results.append({'decision': decision, 'actions': actions, 'matched_lines': matched})
    return {'results': results, 'compatibility_warnings': [w for w in checked['warnings'] if w.get('kind') == 'compatibility'],
        'scope': 'Symbolic evaluation of supplied item fields; not an in-game drop or audio-playback test'}
