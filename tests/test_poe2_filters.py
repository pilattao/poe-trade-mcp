"""Synthetic definitions and explicit price fixtures; no active user filter is used."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import poe_filter


@pytest.fixture
def native(tmp_path):
    root = tmp_path / "PoB2"
    (root / "Data/Bases").mkdir(parents=True)
    (root / "GameVersions.lua").write_text('liveTargetVersion = "0_5"\n')
    (root / "Data/Bases/synthetic.lua").write_text('''return function(itemBases)
itemBases["Synthetic Focus"] = { type = "Focus", quality = 20,
 armour = { EnergyShield = 40, Ward = 20 }, req = { level = 20 }, }
itemBases["Synthetic Quarterstaff"] = { type = "Staff", subType = "Warstaff",
 tags = { warstaff = true }, req = { level = 35 }, }
itemBases["Synthetic Staff"] = { type = "Staff", req = { level = 12 }, }
itemBases["Synthetic Chest"] = { type = "Body Armour", armour = { EnergyShield = 120 }, req = { level = 50 }, }
itemBases["Hidden Legacy Base"] = { type = "Body Armour", hidden = true, req = { level = 80 }, }
end''')
    return root


def tool(name, arguments):
    result = asyncio.run(poe_filter.call_tool(name, arguments))
    return result, json.loads(result.content[0].text)


def test_continue_is_an_action_not_a_new_block():
    blocks = poe_filter._parse_blocks('Show\n Class "Body Armours"\n Continue\nHide\n Rarity Normal\n'.splitlines())
    assert [b["type"] for b in blocks] == ["Show", "Hide"]
    assert "Continue" in blocks[0]["body"]


def test_header_prefixes_and_comments_cannot_create_blocks():
    assert poe_filter._parse_blocks(['# Show', 'Showcase', '# Continue']) == []


def test_top_override_precedes_rules_and_imports(tmp_path):
    path = tmp_path / "test.filter"
    path.write_text('# Header\nImport "base.filter" Optional\nHide\n Rarity Normal\n')
    result = json.loads(poe_filter._tool_add_block({"filter_path": str(path), "block_text": 'Show\n BaseType == "Divine Orb"'}))
    assert result["ok"]
    text = path.read_text()
    assert text.index('BaseType') < text.index('Import') < text.index('Hide')


def test_replacement_keeps_following_section_comments_and_continue(tmp_path):
    path = tmp_path / "test.filter"
    path.write_bytes(b'\xef\xbb\xbfShow\r\n Class "Rings"\r\n Continue\r\n\r\n# Next section\r\nHide\r\n Rarity Normal\r\n')
    result = json.loads(poe_filter._tool_replace_block({"filter_path": str(path), "line": 1, "new_block_text": 'Show\n Class "Amulets"\n Continue'}))
    assert result["ok"]
    content = path.read_bytes()
    assert content.startswith(b'\xef\xbb\xbf')
    assert b'\r\n# Next section\r\nHide\r\n' in content
    assert b'Continue' in content
    assert b'\n' not in content.replace(b'\r\n', b'')


def test_basetype_override_does_not_replace_an_overlapping_multi_base_rule(tmp_path):
    path = tmp_path / "test.filter"
    path.write_text('Show # Bosch: preserve both\n BaseType == "Divine Orb" "Chaos Orb"\n')
    result = json.loads(poe_filter._tool_set_basetype_rule({"filter_path": str(path), "action": "Hide", "basetypes": ["Chaos Orb"]}))
    assert result["ok"]
    assert 'BaseType == "Divine Orb" "Chaos Orb"' in path.read_text()
    assert path.read_text().index('Hide') < path.read_text().index('Show')


@pytest.mark.parametrize("value", ['Divine Orb"\nHide\n#', 'Orb\\nHide', ''])
def test_basetype_input_cannot_inject_filter_commands(tmp_path, value):
    path = tmp_path / "test.filter"
    path.write_text('Show\n')
    result, _ = tool('set_basetype_rule', {"filter_path": str(path), "action": "Show", "basetypes": [value]})
    assert result.isError
    assert path.read_text() == 'Show\n'


def test_catalog_uses_native_poe2_bases_and_real_filter_class_names(native):
    result, data = tool('get_filter_catalog', {"pob_directory": str(native), "limit": 30})
    assert not result.isError
    rows = {row['name']: row for row in data['bases']}
    assert rows['Synthetic Focus']['item_class'] == 'Foci'
    assert rows['Synthetic Quarterstaff']['item_class'] == 'Quarterstaves'
    assert rows['Synthetic Staff']['item_class'] == 'Staves'
    assert 'Hidden Legacy Base' not in rows
    assert data['source']['game'] == 'poe2'
    assert 'Augment' in data['classes']
    assert 'Scarabs' not in data['classes']


def test_documented_classes_are_discoverable_without_local_native_data(monkeypatch):
    monkeypatch.delenv('POB_INSTALL_DIR', raising=False)
    monkeypatch.delenv('POE_MCP_SUITE_POB_DIR', raising=False)
    result, data = tool('get_filter_catalog', {'include_bases': False})
    assert not result.isError
    assert {'Foci', 'Quarterstaves', 'Augment', 'Waystones'} <= set(data['classes'])
    assert data['bases'] == []
    assert data['source']['kind'] == 'documented class mappings'


def test_catalog_rejects_poe1_instead_of_returning_old_crafting_bases(native):
    (native / 'GameVersions.lua').write_text('liveTargetVersion = "3_28"\n')
    result, data = tool('get_filter_catalog', {"pob_directory": str(native)})
    assert result.isError
    assert 'PoE2' in str(data)


def test_native_source_order_overrides_older_and_later_hidden_definitions(native):
    file = native / 'Data/Bases/synthetic.lua'
    text = file.read_text().rsplit('end', 1)[0]
    file.write_text(text + '''
itemBases["Synthetic Chest"] = { type = "Body Armour", armour = { EnergyShield = 150 }, req = { level = 60 }, }
itemBases["Synthetic Focus"] = { type = "Focus", hidden = true, }
end''')
    result, data = tool('get_filter_catalog', {'pob_directory': str(native)})
    assert not result.isError, data
    rows = {row['name']: row for row in data['bases']}
    assert rows['Synthetic Chest']['base_defenses']['EnergyShield'] == 150
    assert 'Synthetic Focus' not in rows


def test_negative_class_constraint_does_not_reverse_native_base_validation(native):
    result, data = tool('validate_filter', {'pob_directory': str(native), 'text': 'Show\n Class != "Body Armours"\n BaseType == "Synthetic Focus"\n'})
    assert not result.isError and data['valid'], data


@pytest.mark.parametrize('bad', [
    'Continue\n', 'Showcase\n Class "Rings"', 'Show\n Class == "Scarabs"',
    'Show\n LinkedSockets 6', 'Show\n Sockets >= 3RGB',
    'Show\n MapTier >= 10', 'Show\n SetTextColor 999 0 0', 'Show\n WaystoneTier => 10',
])
def test_validator_rejects_poe1_or_invalid_syntax(bad):
    result, data = tool('validate_filter', {'text': bad})
    assert not result.isError
    assert data['valid'] is False
    assert data['errors']


def test_validation_understands_import_continue_and_poe2_conditions():
    result, data = tool('validate_filter', {'text': '''Import "optional.filter" Optional
Show
 Class == "Waystones"
 WaystoneTier >= 10
 UnidentifiedItemTier >= 3
 SetFontSize 40
 Continue
Show
 Class == "Augment"
 SetTextColor 0 255 0 255
 MinimapIcon 1 Green Star
Show
'''})
    assert not result.isError
    assert data['valid'], data


def test_generator_creates_a_real_artifact_with_safe_fallback_and_verified_bases(tmp_path, native):
    output = tmp_path / 'private-artifact' / 'test.filter'
    result, data = tool('create_filter', {'output_path': str(output), 'pob_directory': str(native),
        'blocks': ['Show\n Class == "Foci"\n BaseType == "Synthetic Focus"\n BaseEnergyShield >= 30\n SetFontSize 40',
                   'Show\n Class == "Body Armours"\n BaseType == "Synthetic Chest"\n ItemLevel >= 80\n Sockets >= 2']})
    assert not result.isError, data
    assert output.is_file()
    assert output.read_text().rstrip().endswith('Show')
    checked, validation = tool('validate_filter', {'filter_path': str(output), 'pob_directory': str(native)})
    assert not checked.isError and validation['valid']
    assert data['path'] == str(output.resolve())
    before = output.read_bytes()
    rejected, _ = tool('create_filter', {'output_path': str(output), 'blocks': ['Hide']})
    assert rejected.isError and output.read_bytes() == before


def test_generator_rejects_unknown_equipment_bases_and_wrong_classes(tmp_path, native):
    for rule in ['Show\n Class == "Body Armours"\n BaseType == "Vaal Regalia"',
                 'Show\n Class == "Quarterstaves"\n BaseType == "Synthetic Focus"']:
        result, _ = tool('create_filter', {'output_path': str(tmp_path / 'bad.filter'), 'pob_directory': str(native), 'blocks': [rule]})
        assert result.isError
        assert not (tmp_path / 'bad.filter').exists()


def test_preview_evaluates_rule_order_continue_and_unknown_fields():
    text = 'Show\n Class == "Foci"\n SetFontSize 40\n Continue\nHide\n Rarity Normal\nShow\n'
    result, data = tool('test_filter', {'text': text, 'items': [
        {'class': 'Foci', 'rarity': 'Normal'}, {'class': 'Foci', 'rarity': 'Rare'}, {'class': 'Foci'},
    ]})
    assert not result.isError, data
    assert [r['decision'] for r in data['results']] == ['Hide', 'Show', 'unknown']
    assert data['results'][0]['actions']['SetFontSize'] == ['40']


def test_economy_generation_uses_reference_units_and_never_hides_unknown_or_unique_variants(tmp_path, monkeypatch):
    import filter_economy
    rows = [
        {'name': 'Synthetic Orb', 'category': 'Currency', 'exalted_value': 20, 'source_url': 'https://poe.ninja/synthetic', 'fetched_at': '2026-09-15T00:00:00Z'},
        {'name': 'Unpriced Orb', 'category': 'Currency', 'exalted_value': None},
        {'name': 'Cheap Orb', 'category': 'Currency', 'exalted_value': 0.1},
        {'name': 'Valuable Unique', 'base_type': 'Shared Belt', 'category': 'UniqueAccessories', 'exalted_value': 100},
        {'name': 'Cheap Unique', 'base_type': 'Shared Belt', 'category': 'UniqueAccessories', 'exalted_value': 0.01},
    ]
    monkeypatch.setattr(filter_economy, 'fetch_filter_prices', lambda league, categories: rows)
    output = tmp_path / 'economy.filter'
    result, data = tool('generate_economy_filter', {'output_path': str(output), 'league': 'Test League', 'categories': ['Currency', 'UniqueAccessories'],
        'currency': 'exalted', 'min_value': 1, 'high_value': 10, 'hide_below': True})
    assert not result.isError, data
    text = output.read_text()
    assert 'BaseType == "Synthetic Orb"' in text
    assert 'BaseType == "Valuable Unique"' not in text
    assert 'BaseType == "Shared Belt"' in text
    assert data['economy']['unpriced'] == ['Unpriced Orb']
    assert data['economy']['currency'] == 'exalted'
    preview, cases = tool('test_filter', {'text': text, 'items': [
        {'base_type': 'Unpriced Orb', 'rarity': 'Currency'}, {'base_type': 'Cheap Orb', 'rarity': 'Currency'},
        {'base_type': 'Shared Belt', 'rarity': 'Unique'}, {'base_type': 'Unknown New Drop', 'rarity': 'Rare'},
    ]})
    assert not preview.isError
    assert [r['decision'] for r in cases['results']] == ['Show', 'Hide', 'Show', 'Show']


def test_new_filter_tools_are_discovered_by_existing_aggregate():
    import poe_all
    registry = poe_all.load_registry()
    assert {'get_filter_catalog', 'validate_filter', 'create_filter', 'generate_economy_filter', 'test_filter', 'reload_filter'} <= registry.keys()


def test_unpriced_same_name_prevents_economy_hide():
    from filter_economy import economy_blocks
    blocks, data = economy_blocks([
        {'name': 'Synthetic Orb', 'category': 'Currency', 'exalted_value': 0.1},
        {'name': 'Synthetic Orb', 'category': 'Currency', 'exalted_value': None},
    ], league='Test League', hide_below=True)
    assert blocks[0].startswith('Show')
    assert data['unpriced'] == ['Synthetic Orb']


def test_unknown_classes_are_not_claimed_as_verified():
    result, data = tool('validate_filter', {'text': 'Show\n Class == "Future Class From A New Patch"\n'})
    assert not result.isError
    assert data['all_names_verified'] is False
    assert any('unverified' in w['message'] for w in data['warnings'])


def test_validate_resolves_imports_relative_to_the_actual_file(tmp_path):
    directory = tmp_path / 'filters'
    directory.mkdir()
    (directory / 'base.filter').write_text('Show\n')
    path = directory / 'test.filter'
    path.write_text('Import "base.filter"\nShow\n')
    result, data = tool('validate_filter', {'filter_path': str(path)})
    assert not result.isError and data['valid']


def test_new_filter_requires_a_real_filter_path_and_never_uses_active_default(tmp_path, monkeypatch):
    active = tmp_path / 'active.filter'
    active.write_text('Show # active must remain unchanged\n')
    monkeypatch.setenv('POE_FILTER_PATH', str(active))
    for args in [{'blocks': ['Show']}, {'output_path': str(tmp_path / 'not-a-filter.txt'), 'blocks': ['Show']}]:
        result, _ = tool('create_filter', args)
        assert result.isError
    assert active.read_text() == 'Show # active must remain unchanged\n'


def test_aggregate_stdio_creates_validates_and_previews_a_private_filter(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    active = tmp_path / 'active.filter'
    active.write_text('Show # untouched active filter\n')
    output = tmp_path / 'generated.filter'

    async def scenario():
        params = StdioServerParameters(command=sys.executable,
            args=[str(Path(__file__).resolve().parents[1] / 'poe_all.py')], cwd=str(tmp_path),
            env={'POE_FILTER_PATH': str(active), 'POE_PRICE_DB': str(tmp_path / 'unused.sqlite3')})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {'create_filter', 'test_filter', 'get_filter_catalog'} <= {t.name for t in tools.tools}
                created = await session.call_tool('create_filter', {'output_path': str(output), 'blocks': ['Show\n Class == "Augment"\n SetFontSize 38']})
                assert not created.isError
                checked = await session.call_tool('validate_filter', {'filter_path': str(output)})
                assert not checked.isError and json.loads(checked.content[0].text)['valid']
                override = await session.call_tool('set_basetype_rule', {'filter_path': str(output), 'action': 'Hide', 'basetypes': ['Chaos Orb']})
                assert not override.isError
                tested = await session.call_tool('test_filter', {'filter_path': str(output), 'items': [{'class': 'Stackable Currency', 'base_type': 'Chaos Orb'}]})
                assert not tested.isError
                assert json.loads(tested.content[0].text)['results'][0]['decision'] == 'Hide'

    asyncio.run(scenario())
    assert active.read_text() == 'Show # untouched active filter\n'
    assert output.is_file()


@pytest.mark.parametrize('value', ['True', 'False'])
def test_enchantment_boolean_and_accepted_drop_sound_decoration(value):
    result, data = tool('validate_filter', {'text': f'Show\n AnyEnchantment {value}\n DisableDropSound {value}\n'})
    assert not result.isError and data['valid'], data
    assert any(w.get('kind') == 'compatibility' for w in data['warnings'])
    preview, data = tool('test_filter', {'text': f'Show\n DisableDropSound {value}\n', 'items': [{}]})
    assert not preview.isError
    assert data['results'][0]['actions']['DisableDropSound'] == []


def test_named_alert_observed_in_accepted_cache_is_compatibility_not_numeric_error():
    result, data = tool('validate_filter', {'text': 'Show\n PlayAlertSound ShMirror 300\n'})
    assert not result.isError and data['valid'], data
    assert any(w.get('kind') == 'compatibility' and 'ShMirror' in w['message'] for w in data['warnings'])
    for text in ['Show\n PlayAlertSound ShMirror 301', 'Show\n PlayAlertSound NotAKnownSound 100', 'Show\n AnyEnchantment Maybe']:
        _, checked = tool('validate_filter', {'text': text})
        assert not checked['valid']


def test_native_base_list_can_contain_alternatives_outside_the_class(native, tmp_path):
    block = 'Show\n Class == "Foci"\n BaseType == "Synthetic Focus" "Synthetic Chest"\n'
    result, data = tool('validate_filter', {'text': block, 'pob_directory': str(native)})
    assert not result.isError and data['valid'], data
    assert data['errors'] == []
    created, _ = tool('create_filter', {'output_path': str(tmp_path / 'mixed.filter'), 'pob_directory': str(native), 'blocks': [block]})
    assert not created.isError


def test_catalog_absence_is_not_a_syntax_error_for_an_existing_filter(native):
    result, data = tool('validate_filter', {'text': 'Show\n Class == "Foci"\n BaseType == "Future Focus"\n', 'pob_directory': str(native)})
    assert not result.isError and data['valid'], data
    assert data['all_names_verified'] is False
    assert any('Future Focus' in w['message'] for w in data['warnings'])


def test_mod_count_preview_counts_matching_affixes_once_and_keeps_missing_evidence_unknown():
    text = 'Show\n HasExplicitMod >=2 "Test" "Test Alpha"\nHide\n'
    result, data = tool('test_filter', {'text': text, 'items': [
        {'explicit_mods': ['Test Alpha', 'Test Beta']}, {'explicit_mods': ['Test Alpha']}, {},
    ]})
    assert not result.isError
    assert [r['decision'] for r in data['results']] == ['Show', 'Hide', 'unknown']
    _, checked = tool('validate_filter', {'text': 'Show\n HasExplicitMod >=oops "Test Alpha"\n'})
    assert not checked['valid']


def test_null_item_fields_remain_unknown_instead_of_falling_through_to_hide():
    result, data = tool('test_filter', {'text': 'Show\n Class == "Foci"\nHide\n', 'items': [{'class': None}]})
    assert not result.isError
    assert data['results'][0]['decision'] == 'unknown'
