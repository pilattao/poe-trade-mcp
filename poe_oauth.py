"""OAuth 2.1 with PKCE for the Path of Exile API.

This is an optional upgrade from POESESSID authentication. The default
POESESSID path works for most API calls. OAuth is required for the newer
official stash API endpoints and provides longer-lived access.

Setup (one-time):
  1. Register a developer app at https://www.pathofexile.com/developer
     - Set redirect URI to http://localhost:7878/callback
     - Note your client_id
  2. Add POE_CLIENT_ID to your .mcp.json poe server env block
  3. Ask Claude to run the `poe_auth` tool — it opens your browser,
     you authorize, and tokens are saved automatically
  4. Tokens are stored at ~/.cache/poe-trade-mcp/tokens.json
     and auto-refreshed before they expire

GGG OAuth endpoints:
  Authorization: https://www.pathofexile.com/oauth/authorize
  Token:         https://www.pathofexile.com/oauth/token

Scopes used: account:stashes account:characters account:profile
"""
import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Event

_AUTH_URL     = "https://www.pathofexile.com/oauth/authorize"
_TOKEN_URL    = "https://www.pathofexile.com/oauth/token"
_REDIRECT_URI = "http://localhost:7878/callback"
_SCOPES       = "account:stashes account:characters account:profile"
_TOKENS_PATH  = Path.home() / ".cache" / "poe-trade-mcp" / "tokens.json"
_CALLBACK_TIMEOUT = 120  # seconds to wait for browser auth


def _pkce_pair() -> tuple[str, str]:
    """Generate a PKCE (code_verifier, code_challenge) pair."""
    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def load_tokens() -> dict:
    """Load saved tokens from disk. Returns empty dict if none saved."""
    if _TOKENS_PATH.exists():
        try:
            return json.loads(_TOKENS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_tokens(tokens: dict) -> None:
    _TOKENS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _TOKENS_PATH.write_text(json.dumps(tokens, indent=2), encoding="utf-8")


def clear_tokens() -> None:
    """Remove saved tokens (forces re-auth on next use)."""
    if _TOKENS_PATH.exists():
        _TOKENS_PATH.unlink()


def get_valid_token(client_id: str, user_agent: str) -> str | None:
    """Return a valid access token, refreshing if needed.

    Returns None if no tokens are saved (OAuth not set up).
    Raises RuntimeError if refresh fails.
    """
    tokens = load_tokens()
    if not tokens.get("access_token"):
        return None

    # Refresh if expiring within 60 seconds
    if tokens.get("expires_at", 0) - time.time() < 60:
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            return None
        tokens = _exchange_token({
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }, user_agent)
        save_tokens(tokens)

    return tokens.get("access_token")


def token_status() -> dict:
    """Return a summary of the current token state for display."""
    tokens = load_tokens()
    if not tokens.get("access_token"):
        return {"status": "not_authenticated", "message": "No OAuth tokens saved. Run poe_auth to set up."}
    expires_at = tokens.get("expires_at", 0)
    remaining = int(expires_at - time.time())
    if remaining < 0:
        return {"status": "expired", "message": "Token expired. Run poe_auth to re-authorize."}
    return {
        "status": "valid",
        "expires_in_seconds": remaining,
        "expires_in_minutes": remaining // 60,
        "scopes": tokens.get("scope", _SCOPES),
    }


def _exchange_token(data: dict, user_agent: str) -> dict:
    """POST to the token endpoint and return the response with expires_at set."""
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        _TOKEN_URL,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": user_agent,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Token exchange failed HTTP {e.code}: {body_text}") from e

    result["expires_at"] = time.time() + result.get("expires_in", 3600)
    return result


def run_auth_flow(client_id: str, user_agent: str) -> dict:
    """Run the full OAuth 2.1 PKCE authorization flow.

    Opens a browser window for the user to authorize the app.
    Spins up a temporary local server on port 7878 to catch the callback.
    Saves tokens to disk and returns the token dict.

    Raises RuntimeError if auth fails or times out.
    """
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)

    params = {
        "client_id": client_id,
        "response_type": "code",
        "scope": _SCOPES,
        "state": state,
        "redirect_uri": _REDIRECT_URI,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{_AUTH_URL}?{urllib.parse.urlencode(params)}"

    result: dict = {}
    done = Event()

    class _CallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass  # suppress access logs

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            query = dict(urllib.parse.parse_qsl(parsed.query))

            if query.get("state") != state:
                self._respond(400, b"State mismatch. Possible CSRF. Close this tab and try again.")
                return

            if "error" in query:
                result["error"] = query["error"]
                result["error_description"] = query.get("error_description", "")
                self._respond(400, f"Authorization error: {query['error']}. Close this tab.".encode())
            else:
                result["code"] = query.get("code", "")
                self._respond(200, b"Authorization complete! You can close this tab and return to Claude.")
            done.set()

        def _respond(self, code: int, body: bytes):
            self.send_response(code)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(body)

    server = HTTPServer(("127.0.0.1", 7878), _CallbackHandler)
    server.timeout = 2  # poll every 2 s so we can check done

    print(f"[poe_oauth] Opening browser for authorization...")
    print(f"[poe_oauth] If the browser doesn't open, visit: {auth_url}")
    webbrowser.open(auth_url)

    elapsed = 0
    while not done.is_set() and elapsed < _CALLBACK_TIMEOUT:
        server.handle_request()
        elapsed += 2
    server.server_close()

    if not done.is_set():
        raise RuntimeError(f"Authorization timed out after {_CALLBACK_TIMEOUT}s. Try again.")
    if "error" in result:
        raise RuntimeError(f"Authorization error: {result['error']} — {result.get('error_description', '')}")
    if not result.get("code"):
        raise RuntimeError("No authorization code received.")

    tokens = _exchange_token({
        "client_id": client_id,
        "grant_type": "authorization_code",
        "code": result["code"],
        "redirect_uri": _REDIRECT_URI,
        "code_verifier": verifier,
    }, user_agent)

    save_tokens(tokens)
    return tokens
