"""PoE API client and shared utilities for poe-trade-mcp.

Authentication:
  Primary:  POESESSID cookie — works for character-window endpoints and
            the legacy stash API. Set POE_SESSION_ID in .mcp.json.
  Optional: OAuth 2.1 — required for the newer api.pathofexile.com stash
            endpoints. Set POE_CLIENT_ID and run the `poe_auth` tool once.
            When OAuth tokens are present they are used for stash calls;
            everything else continues to use POESESSID.

Env vars (set in .mcp.json):
  POE_SESSION_ID     — POESESSID cookie value (required)
  POE_ACCOUNT_NAME   — account name with or without discriminator (required)
  POE_CONTACT_EMAIL  — included in User-Agent as required by GGG (required)
  POE_LEAGUE         — current league name, e.g. Mirage (required for stash)
  POE_CHARACTER_NAME — default character for get_character calls (optional)
  POE_CLIENT_ID      — developer app client_id for OAuth upgrade (optional)
"""
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

_BASE_URL   = "https://www.pathofexile.com/character-window"
_OAUTH_BASE = "https://api.pathofexile.com"
_HEADERS = {
    "Accept": "application/json",
}
_REQUEST_DELAY = 1.5  # minimum seconds between API calls (PoE rate limit)
_TIMEOUT = 30


def load_config() -> dict:
    """Load PoE credentials. Env vars take precedence over config.json."""
    cfg: dict = {}

    cfg_path = Path(__file__).parent / "config.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    if sessid := os.environ.get("POE_SESSION_ID"):
        cfg["poesessid"] = sessid
    if account := os.environ.get("POE_ACCOUNT_NAME"):
        cfg["account"] = account
    if char := os.environ.get("POE_CHARACTER_NAME"):
        cfg["character"] = char
    if email := os.environ.get("POE_CONTACT_EMAIL"):
        cfg["contact_email"] = email
    if league := os.environ.get("POE_LEAGUE"):
        cfg["league"] = league
    if client_id := os.environ.get("POE_CLIENT_ID"):
        cfg["client_id"] = client_id

    if not cfg.get("poesessid"):
        raise RuntimeError(
            "PoE session ID not found. Set POE_SESSION_ID env var "
            "or add 'poesessid' to poe-trade-mcp/config.json."
        )
    if not cfg.get("account"):
        raise RuntimeError(
            "PoE account name not found. Set POE_ACCOUNT_NAME env var "
            "or add 'account' to poe-trade-mcp/config.json."
        )

    return cfg


class PoeApi:
    """HTTP client for the PoE API.

    Uses POESESSID cookie auth by default. When a client_id is provided and
    OAuth tokens are saved (via the poe_auth tool), stash calls automatically
    upgrade to the newer api.pathofexile.com endpoints with Bearer auth.
    All other calls (character, passives, trade) continue to use POESESSID.
    """

    _last_request_time: float = 0.0

    def __init__(self, sessid: str, account: str, character: str = "",
                 contact_email: str = "", client_id: str = ""):
        self.sessid = sessid
        self.account = account  # kept with discriminator; urlencode encodes # as %23
        self.character = character
        self.client_id = client_id
        contact = f"; contact: {contact_email}" if contact_email else ""
        self.user_agent = f"poe-trade-mcp/1.0 (account: {self.account}{contact})"

    def _rate_limit(self):
        elapsed = time.monotonic() - PoeApi._last_request_time
        if elapsed < _REQUEST_DELAY:
            time.sleep(_REQUEST_DELAY - elapsed)

    def _get(self, endpoint: str, params: dict) -> dict:
        """POESESSID-authenticated GET against the character-window API."""
        self._rate_limit()
        url = f"{_BASE_URL}/{endpoint}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={
            **_HEADERS,
            "Cookie": f"POESESSID={self.sessid}",
            "User-Agent": self.user_agent,
            "Referer": "https://www.pathofexile.com/",
            "Origin": "https://www.pathofexile.com",
        })
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                PoeApi._last_request_time = time.monotonic()
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"PoE API {endpoint} HTTP {e.code}: {body}") from e

    def _oauth_get(self, path: str, token: str) -> dict:
        """OAuth Bearer-authenticated GET against api.pathofexile.com."""
        self._rate_limit()
        url = f"{_OAUTH_BASE}{path}"
        req = urllib.request.Request(url, headers={
            **_HEADERS,
            "Authorization": f"Bearer {token}",
            "User-Agent": self.user_agent,
        })
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
                PoeApi._last_request_time = time.monotonic()
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"PoE OAuth API {path} HTTP {e.code}: {body}") from e

    def _oauth_token(self) -> str | None:
        """Return a valid OAuth token if configured, None otherwise."""
        if not self.client_id:
            return None
        try:
            from poe_oauth import get_valid_token
            return get_valid_token(self.client_id, self.user_agent)
        except Exception:
            return None

    # ── Character endpoints (POESESSID) ──────────────────────────────────────

    def get_items(self, character_name: str = "") -> dict:
        """Fetch equipped items and character metadata."""
        return self._get("get-items", {
            "character": character_name or self.character,
            "accountName": self.account,
        })

    def get_passives(self, character_name: str = "") -> dict:
        """Fetch allocated passive tree nodes and mastery effects."""
        return self._get("get-passive-skills", {
            "character": character_name or self.character,
            "accountName": self.account,
        })

    # ── Stash endpoints (OAuth if available, POESESSID fallback) ─────────────

    def get_stash_tabs(self, league: str) -> dict:
        """Fetch stash tab list.

        Uses OAuth + api.pathofexile.com if client_id and tokens are set up.
        Falls back to POESESSID + character-window API otherwise.

        Response is normalized by stash_cache to a common format.
        """
        token = self._oauth_token()
        if token:
            return self._oauth_get(
                f"/stash/{urllib.parse.quote(league)}", token
            )
        return self._get("get-stash-items", {
            "accountName": self.account,
            "league": league,
            "tabs": 1,
            "tabIndex": 0,
            "realm": "pc",
        })

    def get_stash_tab(self, league: str, tab_index: int,
                      stash_id: str = "") -> dict:
        """Fetch items from one stash tab.

        Uses OAuth if available and stash_id is known (from get_stash_tabs).
        Falls back to POESESSID + tabIndex otherwise.
        """
        token = self._oauth_token()
        if token and stash_id:
            return self._oauth_get(
                f"/stash/{urllib.parse.quote(league)}/{stash_id}", token
            )
        return self._get("get-stash-items", {
            "accountName": self.account,
            "league": league,
            "tabs": 0,
            "tabIndex": tab_index,
            "realm": "pc",
        })


# ── Stubs for features handled by pob-mcp ────────────────────────────────────

def build_pob_xml(*args, **kwargs) -> str:
    raise NotImplementedError(
        "build_pob_xml is not implemented here. "
        "Use pob-mcp's lua_import_character tool instead."
    )


class PobAnalyzer:
    """Stub — headless PoB analysis is handled by pob-mcp."""
    def start(self): raise NotImplementedError("Use pob-mcp instead.")
    def stop(self): pass
    def load_build(self, *a, **kw): raise NotImplementedError("Use pob-mcp instead.")
    def eval_lua(self, *a, **kw): raise NotImplementedError("Use pob-mcp instead.")
