"""Portable public-page PoB2 snapshots; never call internal ninja APIs directly."""

import base64
import copy
from datetime import datetime, timezone
import os
import re
import threading
import time
import urllib.parse
import zlib
from defusedxml import ElementTree as ET

_MAX_XML = 10_000_000
_CACHE = {}
_LOCK = threading.Lock()
_LAST_ATTEMPT = 0.0


def validate_profile(url):
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "poe.ninja"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Expected an exact public https://poe.ninja/poe2/profile/... URL"
        )
    match = re.fullmatch(
        r"/poe2/profile/([^/]+)/([^/]+)/character/([^/]+)/?", parsed.path
    )
    if not match:
        raise ValueError("Expected a public PoE2 profile/account/league/character URL")
    account, slug, character = [urllib.parse.unquote(s) for s in match.groups()]
    for segment in (account, slug, character):
        if (
            segment in (".", "..")
            or any(c in segment for c in "/\\?#")
            or any(ord(c) < 32 for c in segment)
        ):
            raise ValueError("Invalid public profile path segment")
    return {
        "url": url.rstrip("/"),
        "account": account,
        "league_slug": slug,
        "character": character,
    }


def strip_page_label(text, label):
    return re.sub(r"^" + re.escape(label) + r"\s*", "", text, flags=re.I).strip()


def validate_page(page, profile):
    if page.get("name") != profile["character"]:
        raise ValueError("Public page has a different character")
    if (
        page.get("account", "").replace("#", "-").casefold()
        != profile["account"].replace("#", "-").casefold()
    ):
        raise ValueError("Public page has a different account")
    if not page.get("last_fetched_text", "").strip():
        raise ValueError("Public source freshness is unavailable")


def decode_export(code):
    try:
        if (
            not isinstance(code, str)
            or len(code) > _MAX_XML
            or not re.fullmatch(r"[A-Za-z0-9_+=/\-]+", code.strip())
        ):
            raise ValueError("Invalid Path of Building export")
        compressed = base64.urlsafe_b64decode(
            code.strip() + "=" * (-len(code.strip()) % 4)
        )
        decoder = zlib.decompressobj()
        xml = decoder.decompress(compressed, _MAX_XML + 1)
        if len(xml) > _MAX_XML or not decoder.eof or decoder.unused_data:
            raise ValueError("Incomplete or oversized PoB2 export")
        root = ET.fromstring(
            xml, forbid_dtd=True, forbid_entities=True, forbid_external=True
        )
        build = root.find("Build")
        if root.tag != "PathOfBuilding2" or build is None:
            raise ValueError("Expected a PoE2 PathOfBuilding2 export")
        items = {
            node.attrib["id"]: {"text": (node.text or "").strip()}
            for node in root.findall("./Items/Item")
        }
        sets = []
        for node in root.findall("./Items/ItemSet"):
            slots = {s.get("name"): s.get("itemId") for s in node.findall("Slot")}
            if any(v != "0" and v not in items for v in slots.values()):
                raise ValueError("PoB2 slot references a missing item")
            sets.append({**node.attrib, "slots": slots})
        skills = [
            {
                **s.attrib,
                "set_id": ss.get("id"),
                "gems": [g.attrib for g in s.findall("Gem")],
            }
            for ss in root.findall("./Skills/SkillSet")
            for s in ss.findall("Skill")
        ]
        trees = [
            {
                **s.attrib,
                "nodes": [n for n in s.get("nodes", "").split(",") if n],
                "xml": ET.tostring(s, encoding="unicode"),
            }
            for s in root.findall("./Tree/Spec")
        ]
        if (
            not items
            or not sets
            or not any(s["gems"] for s in skills)
            or not any(t["nodes"] for t in trees)
        ):
            raise ValueError(
                "Expected complete PoB2 equipment, skill sets and passive tree"
            )
        config = root.find("Config")
        return {
            "build": build.attrib,
            "items": items,
            "item_sets": sets,
            "skills": skills,
            "trees": trees,
            "calculated_stats": {
                s.get("stat"): s.get("value") for s in build.findall("PlayerStat")
            },
            "calculated_stats_notice": "Exported poe.ninja/PoB calculations, not measurements in game.",
            "configuration_xml": ET.tostring(config, encoding="unicode")
            if config is not None
            else None,
        }, xml.decode("utf-8")
    except (ValueError, KeyError, zlib.error, ET.ParseError) as exc:
        raise ValueError(f"Invalid PoB2 export: {exc}") from exc


def _capture(profile):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Public character snapshots need optional requirements-character.txt and Chromium; see README.md"
        ) from exc
    with sync_playwright() as playwright:
        options = {"headless": True}
        if os.environ.get("POE2_CHROME_PATH"):
            options["executable_path"] = os.environ["POE2_CHROME_PATH"]
        browser = playwright.chromium.launch(**options)
        try:
            # An ephemeral context starts without cookies, login state or a user profile.
            context = browser.new_context(
                locale="en-US", viewport={"width": 1440, "height": 1000}
            )
            page = context.new_page()
            response = page.goto(
                profile["url"], wait_until="domcontentloaded", timeout=30000
            )
            if response is None or not response.ok:
                raise RuntimeError(
                    f"Public profile HTTP {response.status if response else 'unavailable'}"
                )
            page.wait_for_function(
                "() => document.querySelector('input[aria-label=\"Import code for Path of Building\"]')?.value?.length > 100",
                timeout=45000,
            )
            if page.url.rstrip("/") != profile["url"]:
                raise ValueError("Unexpected public profile redirect")
            name = page.locator("h1").inner_text().strip()
            account = (
                page.get_by_role("heading", name="Account", exact=True)
                .locator("..")
                .inner_text()
            )
            age = (
                page.get_by_role("heading", name="Last fetched", exact=True)
                .locator("..")
                .inner_text()
            )
            return {
                "name": name,
                "account": strip_page_label(account, "Account"),
                "last_fetched_text": strip_page_label(age, "Last fetched"),
                "pob_code": page.get_by_role(
                    "textbox", name="Import code for Path of Building", exact=True
                ).input_value(),
                "captured_at": datetime.now(timezone.utc).isoformat(),
            }
        finally:
            browser.close()


def get_snapshot(url):
    global _LAST_ATTEMPT
    profile = validate_profile(url)
    key = profile["url"]
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < 900:
            data = copy.deepcopy(cached[1])
            data["source"]["cache_age_seconds"] = round(time.monotonic() - cached[0])
            return data
        remaining = 60 - (time.monotonic() - _LAST_ATTEMPT)
        if remaining > 0:
            raise RuntimeError(
                f"Public profile fetch cooldown; retry in {remaining:.0f}s"
            )
        _LAST_ATTEMPT = time.monotonic()
        page = _capture(profile)
        validate_page(page, profile)
        data, xml = decode_export(page["pob_code"])
        data.update(
            pob_xml=xml,
            pob_code=page["pob_code"],
            source={
                "url": key,
                "account": page["account"],
                "character": page["name"],
                "league_slug": profile["league_slug"],
                "captured_at": page["captured_at"],
                "source_age_text_at_capture": page["last_fetched_text"],
                "source_timestamp": None,
                "cache_age_seconds": 0,
                "access_method": "public_browser_pob_export",
                "notice": "Source age is the displayed age at capture; add cache_age_seconds. This is not live GGG character data.",
            },
        )
        _CACHE[key] = (time.monotonic(), copy.deepcopy(data))
        return data
