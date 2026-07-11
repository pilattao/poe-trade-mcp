"""Rare item triage scorer for poe-trade-mcp.

Scores rare items by identifying and weighting valuable mods from the PoE API
item format. Does NOT produce price estimates — use the trade API for that.
The score is a relative triage signal: higher = more worth checking on trade.

`should_trade_check` is set True when total_score >= TRADE_CHECK_THRESHOLD,
indicating the item is likely worth a trade search.

Usage:
  result = score_item(item_dict)     # from PoE API stash/character data
  result = score_item_text(text)     # from in-game Ctrl+C clipboard text
  category = classify_item(base)     # "Helmet", "Body Armour", etc.
"""
import re
from dataclasses import dataclass, field

TRADE_CHECK_THRESHOLD = 15.0  # score above which we flag for trade lookup


# ── Item classification ───────────────────────────────────────────────────────

_SLOT_KEYWORDS: list[tuple[str, list[str]]] = [
    ("Weapon",      ["sword", "axe", "mace", "staff", "bow", "wand", "dagger",
                     "claw", "sceptre", "foil", "rapier", "maul", "warstaff"]),
    ("Off-hand",    ["shield", "buckler", "quiver"]),
    ("Helmet",      ["helmet", "cap", "hood", "helm", "crown", "circlet",
                     "cage", "mask", "bascinet", "burgonet", "sallet"]),
    ("Body Armour", ["plate", "armour", "vest", "regalia", "garb", "hide",
                     "chainmail", "brigandine", "lamellar", "raiment", "wrap",
                     "doublet", "coat", "jersey", "tunic", "robe", "silks",
                     "cardigan", "chestplate"]),
    ("Gloves",      ["gloves", "gauntlets", "mitts", "fists", "grip"]),
    ("Boots",       ["boots", "shoes", "greaves", "slippers", "sabatons",
                     "leggings", "treads"]),
    ("Ring",        ["ring"]),
    ("Amulet",      ["amulet", "talisman", "pendant", "necklace", "locket",
                     "clasp", "eye"]),
    ("Belt",        ["belt", "sash", "vise", "cord", "strap", "girdle",
                     "rustic sash", "heavy belt", "leather belt", "studded belt"]),
    ("Jewel",       ["jewel"]),
]


def classify_item(base: str) -> str:
    """Return the equipment slot category for a given base type string."""
    b = base.lower()
    for slot, keywords in _SLOT_KEYWORDS:
        if any(kw in b for kw in keywords):
            return slot
    return "Unknown"


# ── Mod rules ─────────────────────────────────────────────────────────────────
# Each rule: (compiled_regex, score_fn, label)
# score_fn receives the re.Match and returns a float score contribution.

def _v(m: re.Match, group: int = 1) -> float:
    try:
        return float(m.group(group))
    except (IndexError, TypeError, ValueError):
        return 0.0


_MOD_RULES: list[tuple[re.Pattern, object, str]] = [
    # Life / ES
    (re.compile(r'\+(\d+) to maximum Life'),
     lambda m: _v(m) * 0.25,                        "Max Life"),
    (re.compile(r'\+(\d+) to maximum Energy Shield'),
     lambda m: _v(m) * 0.12,                        "Energy Shield"),
    (re.compile(r'(\d+)% increased maximum Energy Shield'),
     lambda m: _v(m) * 0.25,                        "ES%"),
    (re.compile(r'Regenerate (\d+\.?\d*) Life per second'),
     lambda m: _v(m) * 0.4,                         "Life Regen"),
    (re.compile(r'(\d+\.?\d*) Life Regenerated per second'),
     lambda m: _v(m) * 0.4,                         "Life Regen"),

    # Resistances
    (re.compile(r'\+(\d+)% to all Elemental Resistances'),
     lambda m: _v(m) * 1.5,                         "All Res"),
    (re.compile(r'\+(\d+)% to (?:Fire|Cold|Lightning) Resistance'),
     lambda m: _v(m) * 0.4,                         "Elemental Res"),
    (re.compile(r'\+(\d+)% to Chaos Resistance'),
     lambda m: _v(m) * 0.8,                         "Chaos Res"),

    # Offense — crit
    (re.compile(r'(\d+)% increased Critical Strike Multiplier'),
     lambda m: _v(m) * 0.5,                         "Crit Multi"),
    (re.compile(r'\+(\d+)% to (?:Global )?Critical Strike Multiplier'),
     lambda m: _v(m) * 0.5,                         "Crit Multi"),
    (re.compile(r'(\d+)% increased (?:Global )?Critical Strike Chance'),
     lambda m: _v(m) * 0.25,                        "Crit Chance"),

    # Offense — speed
    (re.compile(r'(\d+)% increased Attack Speed'),
     lambda m: _v(m) * 0.8,                         "Attack Speed"),
    (re.compile(r'(\d+)% increased Cast Speed'),
     lambda m: _v(m) * 0.7,                         "Cast Speed"),

    # Offense — damage
    (re.compile(r'(\d+)% increased Spell Damage'),
     lambda m: _v(m) * 0.35,                        "Spell Damage"),
    (re.compile(r'(\d+)% increased Physical Damage'),
     lambda m: _v(m) * 0.4,                         "Phys Damage%"),
    (re.compile(r'adds (\d+) to (\d+) Physical Damage'),
     lambda m: (_v(m, 1) + _v(m, 2)) / 2 * 0.3,    "Flat Phys"),
    (re.compile(r'adds (\d+) to (\d+) (?:Fire|Cold|Lightning) Damage to Attacks'),
     lambda m: (_v(m, 1) + _v(m, 2)) / 2 * 0.2,    "Flat Ele Atk"),
    (re.compile(r'adds (\d+) to (\d+) (?:Fire|Cold|Lightning) Damage to Spells'),
     lambda m: (_v(m, 1) + _v(m, 2)) / 2 * 0.25,   "Flat Ele Spell"),

    # Movement (highly valuable on boots)
    (re.compile(r'(\d+)% increased Movement Speed'),
     lambda m: _v(m) * 1.2,                         "Move Speed"),

    # Attributes (minor value, mostly useful as thresholds)
    (re.compile(r'\+(\d+) to (?:all )?Strength'),
     lambda m: _v(m) * 0.06,                        "Strength"),
    (re.compile(r'\+(\d+) to (?:all )?Dexterity'),
     lambda m: _v(m) * 0.06,                        "Dexterity"),
    (re.compile(r'\+(\d+) to (?:all )?Intelligence'),
     lambda m: _v(m) * 0.06,                        "Intelligence"),
    (re.compile(r'\+(\d+) to all Attributes'),
     lambda m: _v(m) * 0.15,                        "All Attributes"),

    # Mana (minor)
    (re.compile(r'\+(\d+) to maximum Mana'),
     lambda m: _v(m) * 0.04,                        "Max Mana"),

    # Leech
    (re.compile(r'(\d+\.?\d*)% of (?:Physical )?Attack Damage Leeched as Life'),
     lambda m: _v(m) * 4.0,                         "Life Leech"),
    (re.compile(r'(\d+\.?\d*)% of Spell Damage Leeched as Life'),
     lambda m: _v(m) * 4.0,                         "Spell Life Leech"),

    # Open prefix/suffix (crafting value)
    (re.compile(r'Has \d+ (?:Prefix|Suffix) Modifier'),
     lambda m: 5.0,                                  "Open Affix"),
]

# Mods below this per-mod score contribution are flagged as junk
_JUNK_THRESHOLD = 1.0


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class ScoreResult:
    name: str
    category: str
    ilvl: int
    total_score: float
    price_estimate: float       # always 0 — use trade API for real prices
    affix_count: int
    good_mod_count: int
    junk_count: int
    breakdown: list[dict]
    is_fractured: bool = False
    should_trade_check: bool = False


# ── Core scoring logic ────────────────────────────────────────────────────────

def _score_mod_list(mods: list[str]) -> tuple[float, list[dict], int, int]:
    """Score a list of mod text strings. Returns (total, breakdown, good, junk)."""
    total = 0.0
    breakdown = []
    good = 0
    junk = 0

    for mod_text in mods:
        mod_score = 0.0
        matched_label = None

        for pattern, score_fn, label in _MOD_RULES:
            m = pattern.search(mod_text)
            if m:
                contrib = score_fn(m)
                if contrib > mod_score:
                    mod_score = contrib
                    matched_label = label

        breakdown.append({
            "mod": mod_text,
            "label": matched_label or "—",
            "score": round(mod_score, 2),
        })
        total += mod_score
        if mod_score >= _JUNK_THRESHOLD:
            good += 1
        else:
            junk += 1

    return total, breakdown, good, junk


def score_item(item: dict) -> ScoreResult | None:
    """Score a PoE API item dict. Returns None if not a rare/magic item."""
    frame = item.get("frameType", 0)
    if frame not in (1, 2):  # 1=magic, 2=rare
        return None

    name_raw = item.get("name", "").strip().strip('"')
    type_line = item.get("typeLine", "").strip()
    name = f"{name_raw} {type_line}".strip() if name_raw else type_line
    ilvl = item.get("ilvl", 0)
    category = classify_item(type_line)
    is_fractured = bool(item.get("fractured", False))

    all_mods: list[str] = []
    all_mods.extend(item.get("explicitMods", []))
    all_mods.extend(item.get("implicitMods", []))
    all_mods.extend(item.get("craftedMods", []))
    all_mods.extend(item.get("fracturedMods", []))
    affix_count = len(item.get("explicitMods", [])) + len(item.get("craftedMods", []))

    total, breakdown, good, junk = _score_mod_list(all_mods)

    return ScoreResult(
        name=name,
        category=category,
        ilvl=ilvl,
        total_score=round(total, 2),
        price_estimate=0.0,
        affix_count=affix_count,
        good_mod_count=good,
        junk_count=junk,
        breakdown=breakdown,
        is_fractured=is_fractured,
        should_trade_check=total >= TRADE_CHECK_THRESHOLD,
    )


# ── Clipboard text parser ─────────────────────────────────────────────────────

def score_item_text(text: str) -> ScoreResult | None:
    """Score a rare/magic item from in-game Ctrl+C clipboard text.

    Returns None if the text doesn't describe a scoreable item.
    """
    lines = [ln.strip() for ln in text.strip().splitlines()]
    if not lines:
        return None

    # Find Rarity line
    rarity = None
    name_lines: list[str] = []
    item_level = 0
    mods: list[str] = []
    type_line = ""

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("Rarity:"):
            rarity = line.split(":", 1)[1].strip().lower()
            # Next 1–2 non-separator lines are the item name / base
            j = i + 1
            while j < len(lines) and lines[j] and not lines[j].startswith("---"):
                name_lines.append(lines[j])
                j += 1
            i = j
            continue
        if line.startswith("Item Level:"):
            try:
                item_level = int(line.split(":", 1)[1].strip())
            except ValueError:
                pass
        i += 1

    if rarity not in ("rare", "magic"):
        return None

    # Heuristic: last name_line is the base type
    if len(name_lines) >= 2:
        name = name_lines[0]
        type_line = name_lines[-1]
    elif name_lines:
        name = name_lines[0]
        type_line = name_lines[0]
    else:
        name = type_line = ""

    # Collect mod blocks (sections after the first "---" separator)
    in_mod_section = False
    section_count = 0
    for line in lines:
        if line.startswith("---"):
            section_count += 1
            in_mod_section = section_count >= 2
            continue
        if in_mod_section and line and not line.startswith("("):
            # Skip lines that look like headers (no numbers)
            if any(c.isdigit() for c in line) or line.startswith("+") or line.startswith("adds"):
                mods.append(line)

    total, breakdown, good, junk = _score_mod_list(mods)
    category = classify_item(type_line)

    return ScoreResult(
        name=name,
        category=category,
        ilvl=item_level,
        total_score=round(total, 2),
        price_estimate=0.0,
        affix_count=len(mods),
        good_mod_count=good,
        junk_count=junk,
        breakdown=breakdown,
        is_fractured=False,
        should_trade_check=total >= TRADE_CHECK_THRESHOLD,
    )
