"""Opt-in GGG public-client PKCE flow. No secrets or network at import time.

Configuration uses only POE_OAUTH_* variables, with no legacy credential paths.
Default auth action is a local status/requirements report. A registered public
client, explicit enable setting and a confirmed action are required for a flow.
"""

import base64
from dataclasses import dataclass, replace
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
from pathlib import Path
import re
import secrets
import time
import urllib.parse
import webbrowser

from oauth_store import CredentialStore, SCOPE, TokenRecord
from poe_oauth_client import OAuthError, OAuthHTTP, TOKEN_URL

AUTH_URL = "https://www.pathofexile.com/oauth/authorize"
DOCS = "https://www.pathofexile.com/developer/docs/authorization"


@dataclass(frozen=True)
class OAuthConfig:
    client_id: str = ""
    contact: str = ""
    redirect_uri: str = ""
    token_file: Path | None = None
    enabled: bool = False
    allow_refresh: bool = False

    @classmethod
    def from_env(cls):
        path = os.environ.get("POE_OAUTH_TOKEN_FILE", "")
        return cls(
            client_id=os.environ.get("POE_OAUTH_CLIENT_ID", "").strip(),
            contact=os.environ.get("POE_OAUTH_CONTACT", "").strip(),
            redirect_uri=os.environ.get("POE_OAUTH_REDIRECT_URI", "").strip(),
            token_file=Path(path) if path else None,
            enabled=os.environ.get("POE_OAUTH_ENABLED") == "1",
            allow_refresh=os.environ.get("POE_OAUTH_ALLOW_REFRESH") == "1",
        )

    def missing(self):
        return [
            name
            for name, value in [
                ("POE_OAUTH_CLIENT_ID", self.client_id),
                ("POE_OAUTH_CONTACT", self.contact),
                ("POE_OAUTH_REDIRECT_URI", self.redirect_uri),
                ("POE_OAUTH_TOKEN_FILE", self.token_file),
            ]
            if not value
        ]

    def validate(self):
        if self.client_id and not re.fullmatch(
            r"[A-Za-z0-9_.-]{1,128}", self.client_id
        ):
            raise OAuthError("Invalid configured public client ID")
        if self.contact and not re.fullmatch(r"[A-Za-z0-9_.+@-]{3,200}", self.contact):
            raise OAuthError(
                "Invalid configured OAuth contact (header-safe email required)"
            )
        if self.redirect_uri:
            try:
                uri = urllib.parse.urlsplit(self.redirect_uri)
                valid = (
                    uri.scheme == "http"
                    and uri.hostname == "127.0.0.1"
                    and uri.username is None
                    and uri.password is None
                    and uri.port is not None
                    and 1024 <= uri.port <= 65535
                    and not uri.query
                    and not uri.fragment
                    and re.fullmatch(r"/[A-Za-z0-9_/-]+", uri.path)
                    and ".." not in uri.path.split("/")
                )
            except ValueError:
                valid = False
            if not valid:
                raise OAuthError(
                    "Public client requires its exact registered http://127.0.0.1:PORT/callback URI (port 1024–65535); confidential/remote redirects are not supported"
                )
        if self.token_file:
            CredentialStore(self.token_file, self.client_id)

    def require_action(self, confirm):
        if confirm is not True:
            raise OAuthError(
                "OAuth action requires explicit confirm=true after user authorization"
            )
        if not self.enabled:
            raise OAuthError(
                "Account access is disabled; configure the registered PoE2 client and enable POE_OAUTH_ENABLED=1 before authorization"
            )
        self.validate()
        if self.missing():
            raise OAuthError(
                "Missing OAuth configuration: " + ", ".join(self.missing())
            )

    def headers(self, *, form=False):
        self.validate()
        if not self.client_id or not self.contact:
            raise OAuthError("Configured public client ID and contact are required")
        headers = {
            "User-Agent": f"OAuth {self.client_id}/2.1.0 (contact: {self.contact}) poe-trade-mcp",
            "Accept": "application/json",
        }
        if form:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        return headers


def token_status(config=None, *, now=None):
    """Read only explicitly configured owned metadata; never introspect/refresh."""
    config = config or OAuthConfig.from_env()
    config.validate()
    now = time.time() if now is None else now
    status = {
        "official_poe2_character_api": True,
        "required_scope": SCOPE,
        "character_url_template": "https://api.pathofexile.com/character/poe2/{name}",
        "list_characters_url": "https://api.pathofexile.com/character/poe2",
        "official_poe2_stash_api_documented": False,
        "oauth_flow_in_this_port": "implemented_opt_in_public_pkce",
        "enabled": config.enabled,
        "configuration_missing": config.missing(),
        "token_status": "not_inspected",
        "server_validity": "not_checked",
        "refresh_enabled": config.allow_refresh,
        "evidence": "https://www.pathofexile.com/developer/docs/reference#characters",
        "authorization_docs": DOCS,
    }
    if config.token_file is None or not config.client_id:
        return status
    record = CredentialStore(config.token_file, config.client_id).load()
    if record is None:
        status["token_status"] = "not_authorized"
        return status
    remaining = max(0, int(record.expires_at - now))
    state = (
        "reauthorization_required"
        if record.reauthorization_required
        else (
            "expired"
            if record.expires_at <= now
            else ("expiring" if record.expires_at <= now + 60 else "unexpired_local")
        )
    )
    status.update(
        token_status=state,
        expires_in_seconds=remaining,
        scopes=[record.scope],
        refresh_available=bool(
            not record.reauthorization_required
            and record.refresh_token
            and record.refresh_expires_at > now
            and config.allow_refresh
        ),
        refresh_expires_in_seconds=max(0, int(record.refresh_expires_at - now))
        if record.refresh_token
        else None,
    )
    return status


class AuthorizationAttempt:
    def __init__(self, config):
        config.validate()
        if config.missing():
            raise OAuthError(
                "Configure the registered public client and dedicated store first"
            )
        self.config = config
        self.verifier = secrets.token_urlsafe(32)
        self.state = secrets.token_urlsafe(32)
        self.code = None
        self.received_at = None
        self.consumed = False
        self.redeemed = False
        self.denied = False

    @property
    def authorization_url(self):
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        return (
            AUTH_URL
            + "?"
            + urllib.parse.urlencode(
                {
                    "client_id": self.config.client_id,
                    "response_type": "code",
                    "scope": SCOPE,
                    "state": self.state,
                    "redirect_uri": self.config.redirect_uri,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                }
            )
        )

    def accept_callback(self, target):
        if self.consumed:
            raise ValueError("Authorization callback already consumed")
        uri = urllib.parse.urlsplit(target)
        if (
            uri.scheme
            or uri.netloc
            or uri.fragment
            or uri.path != urllib.parse.urlsplit(self.config.redirect_uri).path
        ):
            raise ValueError("Unexpected callback path")
        values = urllib.parse.parse_qs(
            uri.query, keep_blank_values=True, max_num_fields=20
        )
        if any(len(v) != 1 for v in values.values()):
            raise ValueError("Duplicate callback parameter")
        state = values.get("state", [""])[0]
        if not state.isascii() or not secrets.compare_digest(state, self.state):
            raise ValueError("Invalid OAuth callback state")
        if "error" in values:
            if "code" in values:
                raise ValueError("Ambiguous OAuth callback result")
            self.consumed = True
            self.denied = True
            return
        code = values.get("code", [""])[0]
        if (
            not code
            or len(code) > 4096
            or not code.isascii()
            or any(ord(c) <= 32 or ord(c) >= 127 for c in code)
        ):
            raise ValueError("Missing or invalid authorization code")
        self.code = code
        self.received_at = time.monotonic()
        self.consumed = True


def exchange_code(config, attempt, *, http=None, now=time.time, confirm=False):
    config.require_action(confirm)
    if (
        attempt.config != config
        or attempt.redeemed
        or not attempt.consumed
        or not attempt.code
        or attempt.denied
    ):
        raise OAuthError("Authorization attempt is not ready or was already redeemed")
    if time.monotonic() - attempt.received_at >= 25:
        raise OAuthError(
            "Authorization code is too old; start a new user-authorized flow"
        )
    attempt.redeemed = True
    store = CredentialStore(config.token_file, config.client_id)
    with store.lock():
        store.load()  # Reject foreign/insecure files before sending a code.
        data = (http or OAuthHTTP()).request(
            "POST",
            TOKEN_URL,
            headers=config.headers(form=True),
            form={
                "client_id": config.client_id,
                "grant_type": "authorization_code",
                "code": attempt.code,
                "redirect_uri": config.redirect_uri,
                "scope": SCOPE,
                "code_verifier": attempt.verifier,
            },
        )
        record = TokenRecord.from_response(data, client_id=config.client_id, now=now())
        if not config.allow_refresh:
            record = replace(record, refresh_token=None, refresh_expires_at=None)
        store.save(record)
    return token_status(config, now=now())


def run_auth_flow(
    config=None, *, confirm=False, timeout=120, browser_open=None, http=None
):
    """Only an explicit reviewed/user-confirmed call may open the browser."""
    config = config or OAuthConfig.from_env()
    config.require_action(confirm)
    if (
        not isinstance(timeout, int)
        or isinstance(timeout, bool)
        or not 10 <= timeout <= 180
    ):
        raise OAuthError("Authorization timeout must be between 10 and 180 seconds")
    attempt = AuthorizationAttempt(config)
    uri = urllib.parse.urlsplit(config.redirect_uri)
    store = CredentialStore(config.token_file, config.client_id)
    # Validate the explicit destination before opening a browser. The callback
    # port is exclusive; exchange/refresh operations share the store file lock.
    with store.lock():
        store.load()
    expected_host = f"127.0.0.1:{uri.port}"

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            valid = False
            if (
                self.headers.get("Host") == expected_host
                and self.client_address[0] == "127.0.0.1"
                and len(self.path) <= 8192
            ):
                try:
                    attempt.accept_callback(self.path)
                    valid = True
                except ValueError:
                    pass
            self.send_response(200 if valid else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            try:
                self.wfile.write(
                    b"Authorization response received. You may close this tab."
                    if valid
                    else b"Invalid authorization response."
                )
            except OSError:
                pass

        def setup(self):
            super().setup()
            self.connection.settimeout(2)

    try:
        server = HTTPServer(("127.0.0.1", uri.port), Callback)
    except OSError:
        raise OAuthError(
            "Registered loopback callback port is unavailable; no browser was opened"
        ) from None
    with server:
        server.timeout = 0.5
        try:
            opened = (browser_open or webbrowser.open)(attempt.authorization_url)
        except Exception:
            raise OAuthError("Could not open the authorization browser") from None
        if not opened:
            raise OAuthError("Could not open the authorization browser")
        deadline = time.monotonic() + timeout
        while not attempt.consumed and time.monotonic() < deadline:
            server.handle_request()
    if attempt.denied:
        raise OAuthError("User denied authorization; no token was saved")
    if not attempt.consumed:
        raise OAuthError("Authorization timed out; no token was saved")
    return exchange_code(config, attempt, http=http, confirm=True)
