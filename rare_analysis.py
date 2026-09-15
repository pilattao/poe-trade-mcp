"""Local PoE2 clipboard extraction for comparable-price research, without values.

This deliberately reports matching criteria, not uncalibrated mod weights or a
market price. Unknown text is preserved rather than classified as worthless.
"""

import re

# Exact clipboard Item Class labels to currently observed PoE2 trade categories.
CLASSES = {
    "Wands": "weapon.wand",
    "Staves": "weapon.staff",
    "Quarterstaves": "weapon.warstaff",
    "Sceptres": "weapon.sceptre",
    "Bows": "weapon.bow",
    "Crossbows": "weapon.crossbow",
    "Spears": "weapon.spear",
    "One Hand Maces": "weapon.onemace",
    "Two Hand Maces": "weapon.twomace",
    "Body Armours": "armour.chest",
    "Helmets": "armour.helmet",
    "Gloves": "armour.gloves",
    "Boots": "armour.boots",
    "Shields": "armour.shield",
    "Foci": "armour.focus",
    "Quivers": "armour.quiver",
    "Bucklers": "armour.buckler",
    "Rings": "accessory.ring",
    "Amulets": "accessory.amulet",
    "Belts": "accessory.belt",
    "Jewels": "jewel",
}
PATTERNS = [
    ("spirit", r"\bSpirit\b"),
    ("skill_levels", r"\bLevel of all .*Skills\b"),
    ("critical_damage_bonus", r"\bCritical Damage Bonus\b"),
    ("resistance", r"\bResistance[s]?\b"),
    ("life", r"\b(?:maximum Life|Life per second)\b"),
    ("mana", r"\b(?:maximum Mana|Mana per second)\b"),
    ("energy_shield", r"\bEnergy Shield\b"),
    ("armour", r"\bArmour\b"),
    ("evasion", r"\bEvasion(?: Rating)?\b"),
    ("speed", r"\b(?:Movement|Cast|Attack) Speed\b"),
    ("damage", r"\b(?:Spell|Physical|Fire|Cold|Lightning|Chaos|Minion) Damage\b"),
    ("attributes", r"\b(?:Strength|Dexterity|Intelligence|all Attributes)\b"),
]


def analyze_clipboard(text):
    if not isinstance(text, str) or len(text) > 100000:
        raise ValueError("Supply bounded English PoE2 clipboard text")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    rarity_index = next(
        (i for i, s in enumerate(lines) if s.startswith("Rarity:")), None
    )
    if rarity_index is None or lines[rarity_index].split(":", 1)[1].strip() not in (
        "Rare",
        "Magic",
    ):
        raise ValueError("score_rare expects Rare or Magic PoE2 clipboard text")
    item_class = next(
        (s.split(":", 1)[1].strip() for s in lines if s.startswith("Item Class:")), None
    )
    ilvl_index = next(
        (i for i, s in enumerate(lines) if s.startswith("Item Level:")), None
    )
    if ilvl_index is None:
        raise ValueError("Expected Item Level section to separate properties from mods")
    names = []
    for line in lines[rarity_index + 1 :]:
        if line.startswith("---"):
            break
        names.append(line)
    if not names:
        raise ValueError("Expected item name/base")
    mods = []
    for line in lines[ilvl_index + 1 :]:
        if line.startswith("---") or line in ("Corrupted", "Mirrored", "Unidentified"):
            continue
        if re.match(
            r"^(?:Sockets|Quality|Requirements|Level|Str|Dex|Int|Item Level):", line
        ):
            continue
        # Advanced clipboard metadata is not a mod itself.
        if line.startswith("{") and line.endswith("}"):
            continue
        mods.append(line)
    if not mods:
        raise ValueError("No modifier text found after Item Level")
    candidates = []
    unknown = []
    for mod in mods:
        kind = next(
            (kind for kind, pattern in PATTERNS if re.search(pattern, mod, re.I)), None
        )
        if kind is None:
            unknown.append(mod)
        else:
            candidates.append(
                {
                    "text": mod,
                    "kind": kind,
                    "numbers": [
                        float(n) for n in re.findall(r"[-+]?\d+(?:\.\d+)?", mod)
                    ],
                    "next_step": "Resolve the exact PoE2 stat ID and verify local/global meaning before applying numeric bounds.",
                }
            )
    return {
        "game": "poe2",
        "method": "local_mod_analysis",
        "name": names[0],
        "base_type": names[-1] if len(names) > 1 else None,
        "item_class": item_class,
        "category": CLASSES.get(item_class),
        "item_level": int(lines[ilvl_index].split(":", 1)[1]),
        "mods": mods,
        "comparison_candidates": candidates,
        "unclassified_mods": unknown,
        "price_estimate": None,
        "note": "Candidate comparison criteria only; no affix-tier, build-value, or market-price calibration. Unknown mods are not junk.",
        "trade_reference": "https://www.pathofexile.com/api/trade2/data/filters",
    }
