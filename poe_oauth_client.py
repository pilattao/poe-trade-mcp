"""Official PoE2 OAuth character reads and bounded, redacted HTTP transport."""

from dataclasses import replace
from datetime import datetime, timezone
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from oauth_store import CredentialStore, TokenRecord

TOKEN_URL = "https://www.pathofexile.com/oauth/token"
CHARACTER_URL = "https://api.pathofexile.com/character/poe2"


class OAuthError(RuntimeError):
    def __init__(self, message, *, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise OAuthError("OAuth HTTP redirects are refused")


class OAuthHTTP:
    _lock = threading.Lock()
    _next = {}

    def __init__(self, opener=None):
        self.opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect()
        )

    @staticmethod
    def _delay(headers):
        delay = 1.5
        for kind in ("Client", "Ip", "Account"):
            rules = headers.get("X-Rate-Limit-" + kind, "").split(",")
            states = headers.get("X-Rate-Limit-" + kind + "-State", "").split(",")
            for rule in rules:
                try:
                    hits, period, _ = map(float, rule.split(":"))
                    if hits > 0:
                        delay = max(delay, period / hits * 1.25)
                except ValueError:
                    continue
            for state in states:
                try:
                    _, _, restricted = map(float, state.split(":"))
                    delay = max(delay, restricted)
                except ValueError:
                    continue
        if headers.get("Retry-After"):
            from public_http import retry_seconds

            delay = max(delay, retry_seconds(headers["Retry-After"]))
        return delay

    def request(self, method, url, *, headers, form=None):
        parsed = urllib.parse.urlsplit(url)
        if method == "POST":
            if url != TOKEN_URL or not isinstance(form, dict):
                raise OAuthError("Only the official OAuth token POST is allowed")
        elif method == "GET":
            suffix = parsed.path.removeprefix("/character/poe2/")
            decoded = urllib.parse.unquote(suffix)
            detail_route = (
                parsed.path.startswith("/character/poe2/")
                and bool(decoded)
                and decoded not in (".", "..")
                and not any(c in decoded for c in "/\\%?#")
                and not any(ord(c) < 32 for c in decoded)
                and urllib.parse.quote(decoded, safe="") == suffix
            )
            if (
                parsed.scheme != "https"
                or parsed.netloc != "api.pathofexile.com"
                or parsed.query
                or parsed.fragment
                or not (parsed.path == "/character/poe2" or detail_route)
            ):
                raise OAuthError("Only official PoE2 character GET routes are allowed")
        else:
            raise OAuthError("Unsupported OAuth HTTP method")
        body = urllib.parse.urlencode(form).encode() if form is not None else None
        request = urllib.request.Request(url, data=body, method=method, headers=headers)
        with self._lock:
            delay = self._next.get(parsed.hostname, 0) - time.monotonic()
            if delay > 10:
                raise OAuthError(
                    f"OAuth rate-limit cooldown; retry in {delay:.0f}s", status=429
                )
            if delay > 0:
                time.sleep(delay)
            response_headers = {}
            try:
                with self.opener.open(request, timeout=30) as response:
                    response_headers = response.headers
                    if response.status != 200:
                        raise OAuthError(
                            f"OAuth HTTP {response.status}; response was not complete",
                            status=response.status,
                        )
                    raw = response.read(8_000_001)
                    if len(raw) > 8_000_000:
                        raise OAuthError("OAuth response exceeds size limit")
                    try:
                        data = json.loads(raw)
                    except (ValueError, UnicodeError):
                        raise OAuthError("OAuth source returned invalid JSON") from None
                    if not isinstance(data, dict) or "error" in data:
                        raise OAuthError("OAuth source returned an invalid response")
                    return data
            except urllib.error.HTTPError as exc:
                response_headers = exc.headers
                # Never echo server descriptions/bodies: they may contain tokens,
                # authorization codes, or private character data.
                code = None
                try:
                    error = json.loads(exc.read(65536)).get("error")
                    if error in (
                        "invalid_grant",
                        "invalid_client",
                        "invalid_scope",
                        "access_denied",
                        "temporarily_unavailable",
                    ):
                        code = error
                except (ValueError, AttributeError, TypeError):
                    pass
                exc.close()
                raise OAuthError(
                    f"Official OAuth HTTP {exc.code}" + (f" ({code})" if code else ""),
                    status=exc.code,
                    code=code,
                ) from None
            except (urllib.error.URLError, OSError):
                raise OAuthError(
                    "Official OAuth connection failed; request was not retried"
                ) from None
            finally:
                self._next[parsed.hostname] = time.monotonic() + self._delay(
                    response_headers
                )


class CharacterClient:
    def __init__(self, config, *, http=None, now=time.time):
        self.config = config
        self.http = http or OAuthHTTP()
        self.now = now

    def _request(self, url, confirm):
        self.config.require_action(confirm)
        store = CredentialStore(self.config.token_file, self.config.client_id)
        with store.lock():
            record = store.load()
            if record is None:
                raise OAuthError(
                    "No credentials in the explicitly configured owned store; authorize this client first"
                )
            if record.reauthorization_required:
                raise OAuthError(
                    "Reauthorization is required; stored token was rejected or refresh outcome is uncertain"
                )
            if record.expires_at <= self.now() + 60:
                if (
                    not self.config.allow_refresh
                    or not record.refresh_token
                    or record.refresh_expires_at <= self.now()
                ):
                    raise OAuthError(
                        "Access token expired or expiring; explicit authorization is required"
                    )
                # Refresh tokens are single-use. Persist uncertainty *before*
                # attempting a refresh so a crash/timeout cannot silently replay it.
                store.save(replace(record, reauthorization_required=True))
                data = self.http.request(
                    "POST",
                    TOKEN_URL,
                    headers=self.config.headers(form=True),
                    form={
                        "client_id": self.config.client_id,
                        "grant_type": "refresh_token",
                        "refresh_token": record.refresh_token,
                    },
                )
                new = TokenRecord.from_response(
                    data,
                    client_id=self.config.client_id,
                    now=self.now(),
                    previous=record,
                )
                store.save(new)
                record = new
            try:
                return self.http.request(
                    "GET",
                    url,
                    headers={
                        **self.config.headers(),
                        "Authorization": "Bearer " + record.access_token,
                    },
                )
            except OAuthError as exc:
                if exc.status in (401, 403):
                    store.save(replace(record, reauthorization_required=True))
                raise

    @staticmethod
    def _validate_character(character, expected_name=None):
        if not isinstance(character, dict) or not isinstance(
            character.get("name"), str
        ):
            raise OAuthError("Official character response has invalid schema")
        if character.get("realm") != "poe2":
            raise OAuthError(
                "Official character response did not confirm the poe2 realm"
            )
        if expected_name is not None and character["name"] != expected_name:
            raise OAuthError("Official response contains a different character")
        for key in ("equipment", "skills", "jewels"):
            if (
                key in character
                and character[key] is not None
                and not isinstance(character[key], list)
            ):
                raise OAuthError("Official character item fields have invalid schema")
        if (
            "passives" in character
            and character["passives"] is not None
            and not isinstance(character["passives"], dict)
        ):
            raise OAuthError("Official character passives have invalid schema")
        return character

    def _source(self, url):
        return {
            "access_method": "official_oauth",
            "realm": "poe2",
            "source_url": url,
            "fetched_at": datetime.fromtimestamp(self.now(), timezone.utc).isoformat(),
            "source_timestamp": None,
            "visibility": "authorized_account_private",
            "note": "On-demand official response, not persisted here; fetched_at is not an upstream snapshot timestamp.",
        }

    def list_characters(self, *, confirm=False):
        data = self._request(CHARACTER_URL, confirm)
        if not isinstance(data.get("characters"), list):
            raise OAuthError("Official character list response has invalid schema")
        return {
            "characters": [self._validate_character(c) for c in data["characters"]],
            "source": self._source(CHARACTER_URL),
        }

    def get_character(self, name, *, confirm=False):
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 128
            or name in (".", "..")
            or any(c in name for c in "/\\%?#")
            or any(ord(c) < 32 for c in name)
        ):
            raise OAuthError("Supply a valid exact character name")
        url = CHARACTER_URL + "/" + urllib.parse.quote(name, safe="")
        data = self._request(url, confirm)
        if data.get("character") is None:
            raise OAuthError("Character is absent from the authorized account")
        return {
            "character": self._validate_character(data["character"], name),
            "source": self._source(url),
        }
