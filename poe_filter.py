"""Read, validate, preview and generate PoE2 loot filters, without game writes.

Authoritative syntax: https://www.pathofexile.com/item-filter/about
Native base names are read from an explicitly selected PoB2 installation.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

import anyio
from mcp.types import Tool
from mcp_server_utils import Server, result, run_server
from filter_catalog import CLASSES, CLASS_MAP, SOURCES, load_catalog, local_path
from filter_model import (header, tokens, parse_blocks, find_block_bounds, validate, preview, quoted, operand)
import filter_economy

DEFAULT_FILTER = Path.home() / 'Documents/My Games/Path of Exile 2/Starting.filter'
app = Server('poe-filter')


def _get_filter_path(arguments, *, output=False):
    value = arguments.get('output_path' if output else 'filter_path')
    if value is None and not output: value = os.environ.get('POE_FILTER_PATH') or DEFAULT_FILTER
    if value is None: raise ValueError('An explicit output_path is required; no active filter is chosen')
    if os.name != 'nt' and re.match(r'^[A-Za-z]:[\\/]', str(value)) and not (Path('/mnt') / str(value)[0].lower()).is_dir():
        raise ValueError('Windows paths need their mounted WSL drive; otherwise provide a native absolute path')
    path = local_path(value)
    if not path.is_absolute(): raise ValueError('Supply an absolute local filter path')
    if path.suffix.lower() != '.filter': raise ValueError('Filter files must have the .filter extension')
    if path.is_symlink(): raise ValueError('Use the actual filter path, not a symlink')
    return path.resolve()


def _load_filter(path):
    if path.stat().st_size > 8_000_000: raise ValueError('Filter exceeds the 8 MB analysis bound')
    return path.read_text(encoding='utf-8-sig').splitlines()


def _write_filter(path, lines, *, create=False, overwrite=False):
    previous = path.read_bytes() if path.is_file() else b''
    if create and path.exists() and not overwrite: raise FileExistsError('Output already exists; no existing filter was replaced')
    ending = '\r\n' if b'\r\n' in previous else '\n'
    text = ending.join('\n'.join(lines).splitlines()).rstrip('\r\n') + ending
    encoded = (b'\xef\xbb\xbf' if previous.startswith(b'\xef\xbb\xbf') else b'') + text.encode('utf-8')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.poe2-filter-', delete=False) as stream:
            temporary = Path(stream.name); stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
        if create and not overwrite:
            os.link(temporary, path)  # Exclusive creation: another writer cannot be overwritten.
        else: os.replace(temporary, path)
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)
    return {'path': str(path), 'bytes': len(encoded), 'sha256': hashlib.sha256(encoded).hexdigest()}


_parse_blocks = parse_blocks
_find_block_bounds = find_block_bounds


def _extract_conditions(lines):
    return {p[0]: ' '.join(p[1:]) for line in lines if (p := tokens(line))}


def _validate_block(block_text):
    checked = validate(block_text)
    if not checked['valid']: return str(checked['errors'])
    if len(parse_blocks(block_text.splitlines())) != 1 or checked['imports']:
        return 'Provide exactly one Show/Hide block; Continue is an action within it'
    return None


def _tool_get_filter_info(arguments):
    path = _get_filter_path(arguments); lines = _load_filter(path); blocks = parse_blocks(lines)
    return json.dumps({'path': str(path), 'game': 'poe2', 'total_lines': len(lines),
        'blocks': {**{k: sum(b['type'] == k for b in blocks) for k in ('Show', 'Hide', 'Minimal')}, 'total': len(blocks)},
        'continue_actions': sum('Continue' in b['conditions'] for b in blocks),
        'sections': [f'L{i + 1}: {line.strip()}' for i, line in enumerate(lines) if re.match(r'#\s*(?:\[\[|##)', line.strip())][:100]})


def _tool_find_blocks(arguments):
    path = _get_filter_path(arguments); query = arguments['query'].casefold(); limit = arguments.get('limit', 20)
    matches = [b for b in parse_blocks(_load_filter(path)) if query in b['body'].casefold()]
    return json.dumps({'total_matches': len(matches), 'blocks': [{'line': b['line'] + 1, 'end_line': b['end_line'] + 1,
        'type': b['type'], 'comment': b['comment'], 'conditions': b['conditions'], 'preview': b['body'][:500]} for b in matches[:limit]]})


def _block_at(lines, line):
    if isinstance(line, bool) or not isinstance(line, int) or not 1 <= line <= len(lines): raise ValueError('Block line is out of range')
    if not header(lines[line - 1]): raise ValueError('Line is not a Show/Hide block header')
    return find_block_bounds(lines, line - 1)


def _tool_get_block(arguments):
    lines = _load_filter(_get_filter_path(arguments)); start, end = _block_at(lines, arguments['line'])
    return json.dumps({'line': start + 1, 'end_line': end + 1, 'text': '\n'.join(lines[start:end + 1])})


def _tool_add_block(arguments):
    path = _get_filter_path(arguments); text = arguments['block_text'].strip(); position = arguments.get('position', 'top')
    error = _validate_block(text)
    if error: return json.dumps({'error': f'Invalid block: {error}'})
    lines = _load_filter(path); blocks = parse_blocks(lines)
    if position == 'top':
        index = next((i for i, line in enumerate(lines) if tokens(line)), len(lines))
    elif position == 'bottom': index = len(lines)
    elif position.startswith('after_line:'):
        index = int(position.split(':', 1)[1])
        if not 0 <= index <= len(lines): raise ValueError('Insertion line is out of range')
        if any(b['line'] < index <= b['end_line'] for b in blocks): raise ValueError('Insertion would split an existing block; use its final line')
    elif position.startswith('after_pattern:'):
        pattern = position.split(':', 1)[1].casefold()
        found = next((i for i, line in enumerate(lines) if pattern in line.casefold()), None)
        if found is None: raise ValueError('Insertion pattern not found')
        block = next((b for b in blocks if b['line'] <= found <= b['end_line']), None)
        index = block['end_line'] + 1 if block else found + 1
    else: raise ValueError('Unknown insertion position')
    _write_filter(path, lines[:index] + text.splitlines() + [''] + lines[index:])
    return json.dumps({'ok': True, 'inserted_after_line': index, 'new_block': text, 'path': str(path)})


def _tool_remove_block(arguments):
    path = _get_filter_path(arguments); lines = _load_filter(path); start, end = _block_at(lines, arguments['line'])
    removed = '\n'.join(lines[start:end + 1]); _write_filter(path, lines[:start] + lines[end + 1:])
    return json.dumps({'ok': True, 'removed_lines': f'{start + 1}-{end + 1}', 'removed_text': removed})


def _tool_replace_block(arguments):
    path = _get_filter_path(arguments); lines = _load_filter(path); start, end = _block_at(lines, arguments['line'])
    text = arguments['new_block_text'].strip(); error = _validate_block(text)
    if error: return json.dumps({'error': f'Invalid block: {error}'})
    old = '\n'.join(lines[start:end + 1]); _write_filter(path, lines[:start] + text.splitlines() + lines[end + 1:])
    return json.dumps({'ok': True, 'replaced_lines': f'{start + 1}-{end + 1}', 'old': old, 'new': text})


def _tool_set_basetype_rule(arguments):
    path = _get_filter_path(arguments); action = arguments['action']; bases = arguments['basetypes']; exact = arguments.get('exact_match', True)
    if action not in ('Show', 'Hide'): raise ValueError('action must be Show or Hide')
    if not bases or len(set(bases)) != len(bases): raise ValueError('Provide distinct nonempty base names')
    names = ' '.join(quoted(b) for b in bases)
    comment = arguments.get('comment', '')
    if '\n' in comment or '\r' in comment: raise ValueError('Comment must occupy one line')
    extra = arguments.get('extra_conditions', '').strip()
    marker = hashlib.sha256(json.dumps([sorted(bases), exact, extra]).encode()).hexdigest()[:12]
    text = f'{action} # poe2-mcp:basetype:{marker}' + (f' {comment}' if comment else '')
    text += '\n    BaseType ' + ('== ' if exact else '') + names
    if extra: text += '\n' + '\n'.join('    ' + line.strip() for line in extra.splitlines())
    error = _validate_block(text)
    if error: raise ValueError(error)
    for block in parse_blocks(_load_filter(path)):
        if not block['comment'].startswith(('poe2-mcp:', 'Bosch:')): continue
        base_directives = [tokens(line) for line in block['body'].splitlines()[1:] if tokens(line) and tokens(line)[0] == 'BaseType']
        if len(base_directives) != 1: continue
        op, values = operand(base_directives[0][1:])
        if set(values) == set(bases) and (op == '==') == exact and (marker in block['comment'] or not extra and block['comment'].startswith('Bosch:')):
            return _tool_replace_block({'filter_path': str(path), 'line': block['line'] + 1, 'new_block_text': text})
    return _tool_add_block({'filter_path': str(path), 'block_text': text, 'position': 'top'})


def _catalog(arguments):
    return load_catalog(arguments.get('pob_directory'), include_hidden=arguments.get('include_hidden', False))


def _text(arguments):
    return arguments['text'] if 'text' in arguments else '\n'.join(_load_filter(_get_filter_path(arguments)))


def _create(arguments, *, blocks=None, economy=None):
    path = _get_filter_path(arguments, output=True)
    blocks = arguments['blocks'] if blocks is None else blocks
    if not isinstance(blocks, list) or any(not isinstance(b, str) for b in blocks): raise ValueError('blocks must be filter text sections')
    heading = '# PoE2 filter generated by poe-mcp-suite\n# Syntax: ' + SOURCES[0]
    if economy:
        heading += '\n# Economy: ' + economy['league'] + '; reference unit ' + economy['currency'] + '; snapshot ' + economy['created_at']
        for source in economy['sources']:
            heading += '\n# Price source: ' + source.get('source_url', 'unknown') + '; fetched ' + source.get('fetched_at', 'unknown')
    text = heading + '\n\n' + '\n\n'.join(block.strip() for block in blocks) + '\n\nShow\n'
    catalog = _catalog(arguments) if arguments.get('pob_directory') else None
    checked = validate(text, catalog=catalog, path=path, strict_native_names=True)
    if not checked['valid']: raise ValueError(f'Filter validation failed: {checked["errors"]}')
    output = _write_filter(path, text.splitlines(), create=True, overwrite=arguments.get('overwrite', False))
    return {'ok': True, **output, 'validation': checked, 'economy': economy, 'activation': 'File only; no active filter was selected or reloaded'}


def _dispatch(name, arguments):
    old = {'get_filter_info': _tool_get_filter_info, 'find_blocks': _tool_find_blocks, 'get_block': _tool_get_block,
        'add_block': _tool_add_block, 'remove_block': _tool_remove_block, 'replace_block': _tool_replace_block, 'set_basetype_rule': _tool_set_basetype_rule}
    if name in old: return json.loads(old[name](arguments))
    if name == 'reload_filter': return {'reloaded_from_disk': True, **json.loads(_tool_get_filter_info(arguments))}
    if name == 'get_filter_catalog':
        data = _catalog(arguments) if arguments.get('include_bases', True) else {
            'classes': CLASSES, 'bases': [], 'source': {'game': 'poe2', 'kind': 'documented class mappings', 'filter_references': SOURCES},
            'notes': ['Native bases were not requested; enable include_bases and provide a PoB2 directory to discover them.']}
        query = arguments.get('query', '').casefold(); requested = arguments.get('item_class')
        item_class = CLASS_MAP.get(requested, requested)
        if item_class is not None and item_class not in CLASSES: raise ValueError('Unknown PoE2 item class')
        rows = [row for row in data['bases'] if query in row['name'].casefold() and (item_class is None or row['item_class'] == item_class)]
        return {**data, 'bases': rows[:arguments.get('limit', 50)], 'matched_bases': len(rows), 'total_bases': len(data['bases']),
            'base_catalog_loaded': arguments.get('include_bases', True)}
    if name == 'validate_filter': return validate(_text(arguments), catalog=_catalog(arguments) if arguments.get('pob_directory') else None,
        path=_get_filter_path(arguments) if arguments.get('filter_path') else None)
    if name == 'test_filter': return preview(_text(arguments), arguments['items'])
    if name == 'create_filter': return _create(arguments)
    if name == 'generate_economy_filter':
        league = arguments['league']; categories = arguments.get('categories', ['Currency'])
        rows = filter_economy.fetch_filter_prices(league, categories)
        blocks, economy = filter_economy.economy_blocks(rows, league=league, currency=arguments.get('currency', 'exalted'),
            min_value=arguments.get('min_value', 1), high_value=arguments.get('high_value', 10), hide_below=arguments.get('hide_below', False))
        # Caller rules go first; price-derived highlights cannot override explicit choices.
        return _create(arguments, blocks=arguments.get('blocks', []) + blocks, economy=economy)
    raise ValueError(f'Unknown filter tool: {name}')


PATH = {'filter_path': {'type': 'string', 'description': 'Absolute local .filter path; POE_FILTER_PATH may configure the default'}}
NATIVE = {'pob_directory': {'type': 'string', 'description': 'Verified local PoB2 install or source directory; read only'}}
OUTPUT = {'output_path': {'type': 'string', 'description': 'Explicit absolute .filter output path; never activates it'},
          'overwrite': {'type': 'boolean', 'default': False}, **NATIVE}
TEXT = {**PATH, 'text': {'type': 'string'}}

def _tool(name, description, properties, required=(), **extra):
    return Tool(name=name, description=description, inputSchema={'type': 'object', 'properties': properties,
        'required': list(required), 'additionalProperties': False, **extra})


TOOLS = [
    _tool('get_filter_info', 'Read local filter structure and section headers. Continue is an action, not a block.', PATH),
    _tool('find_blocks', 'Find local filter blocks by text; returns 1-based line numbers and a bounded preview.', {**PATH, 'query': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 200}}, ['query']),
    _tool('get_block', 'Read a block at its 1-based starting line.', {**PATH, 'line': {'type': 'integer', 'minimum': 1}}, ['line']),
    _tool('add_block', 'Insert one validated PoE2 block. top precedes all rules/imports; after_pattern appends after its matching block.', {**PATH, 'block_text': {'type': 'string'}, 'position': {'type': 'string'}}, ['block_text']),
    _tool('remove_block', 'Remove one block, preserving adjoining section comments.', {**PATH, 'line': {'type': 'integer', 'minimum': 1}}, ['line']),
    _tool('replace_block', 'Replace one block; preserves UTF-8 BOM and newline convention.', {**PATH, 'line': {'type': 'integer', 'minimum': 1}, 'new_block_text': {'type': 'string'}}, ['line', 'new_block_text']),
    _tool('set_basetype_rule', 'Create a highest-priority exact base rule; only an equivalent managed rule is replaced.', {**PATH,
        'action': {'enum': ['Show', 'Hide']}, 'basetypes': {'type': 'array', 'minItems': 1, 'maxItems': 200, 'uniqueItems': True, 'items': {'type': 'string', 'minLength': 1}},
        'exact_match': {'type': 'boolean'}, 'comment': {'type': 'string'}, 'extra_conditions': {'type': 'string'}}, ['action', 'basetypes']),
    _tool('reload_filter', 'Re-read the local file; does not reload or alter the game client.', PATH),
    _tool('get_filter_catalog', 'Discover real PoE2 filter classes and local native base definitions. No PoE1 crafting-base fallback or drop/value assumptions.', {**NATIVE,
        'query': {'type': 'string'}, 'item_class': {'type': 'string'}, 'include_bases': {'type': 'boolean', 'default': True},
        'include_hidden': {'type': 'boolean', 'default': False}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 500}}),
    _tool('validate_filter', 'Check PoE2 syntax and optional native equipment base identities. This is not the game-client compiler.', {**TEXT, **NATIVE}, oneOf=[{'required': ['text']}, {'required': ['filter_path']}]),
    _tool('test_filter', 'Preview ordered rules and Continue on supplied item fields; missing fields/imports remain unknown.', {**TEXT,
        'items': {'type': 'array', 'maxItems': 200, 'items': {'type': 'object'}}}, ['items'], oneOf=[{'required': ['text']}, {'required': ['filter_path']}]),
    _tool('create_filter', 'Create an actual PoE2 filter from validated blocks, with a final Show for unmatched drops. Explicit output path, no overwrite by default.', {**OUTPUT,
        'blocks': {'type': 'array', 'maxItems': 1000, 'items': {'type': 'string'}}}, ['output_path', 'blocks']),
    _tool('generate_economy_filter', 'Generate a static PoE2 filter from public reference prices. Keeps unknown prices visible; unique bases never hide cheaper/unknown variants.', {**OUTPUT,
        'league': {'type': 'string', 'minLength': 1}, 'categories': {'type': 'array', 'minItems': 1, 'maxItems': 5, 'uniqueItems': True, 'items': {'enum': filter_economy.CATEGORIES}},
        'currency': {'enum': ['chaos', 'divine', 'exalted']}, 'min_value': {'type': 'number', 'exclusiveMinimum': 0}, 'high_value': {'type': 'number', 'exclusiveMinimum': 0},
        'hide_below': {'type': 'boolean', 'default': False}, 'blocks': {'type': 'array', 'maxItems': 1000, 'items': {'type': 'string'}}}, ['output_path', 'league']),
]


@app.list_tools()
async def list_tools(): return TOOLS


@app.call_tool()
async def call_tool(name, arguments):
    return result(await anyio.to_thread.run_sync(lambda: _dispatch(name, arguments)))


if __name__ == '__main__': run_server(app, port=8487, name='poe-filter')
