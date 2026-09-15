"""Synthetic OAuth contracts only: never read real credentials or contact GGG."""

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest


@pytest.fixture
def config(tmp_path):
    from poe_oauth import OAuthConfig

    return OAuthConfig(
        client_id="synthetic-client",
        contact="test@example.invalid",
        redirect_uri="http://127.0.0.1:18787/callback",
        token_file=tmp_path / "private" / "poe2-oauth.json",
        enabled=True,
    )


def token_response(**changes):
    return {
        "access_token": "synthetic-access",
        "token_type": "bearer",
        "scope": "account:characters",
        "expires_in": 36000,
        **changes,
    }


class FakeHTTP:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, *, headers, form=None):
        self.requests.append((method, url, headers, form))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def seed(config, response=None, now=100):
    from oauth_store import CredentialStore, TokenRecord

    record = TokenRecord.from_response(
        response or token_response(), client_id=config.client_id, now=now
    )
    CredentialStore(config.token_file, config.client_id).save(record)
    return record


def test_status_no_config_does_not_inspect_existing_store(monkeypatch):
    import poe_oauth

    for key in list(os.environ):
        if key.startswith("POE_OAUTH_"):
            monkeypatch.delenv(key)
    monkeypatch.setattr(
        Path,
        "open",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("unexpected file access")
        ),
    )
    status = poe_oauth.token_status()
    assert status["token_status"] == "not_inspected"
    assert status["official_poe2_character_api"] is True


def test_pkce_and_authorization_scope_are_exact(config):
    from poe_oauth import AuthorizationAttempt

    attempt = AuthorizationAttempt(config)
    query = parse_qs(urlsplit(attempt.authorization_url).query)
    assert query["scope"] == ["account:characters"]
    assert query["code_challenge_method"] == ["S256"]
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(attempt.verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert query["code_challenge"] == [expected]
    assert len(attempt.verifier) >= 43
    assert attempt.verifier not in attempt.authorization_url
    assert attempt.verifier not in repr(attempt)
    assert "client_secret" not in query
    assert query["redirect_uri"] == [config.redirect_uri]


def test_callback_checks_path_state_duplicates_and_one_use(config):
    from poe_oauth import AuthorizationAttempt

    attempt = AuthorizationAttempt(config)
    with pytest.raises(ValueError, match="state"):
        attempt.accept_callback("/callback?state=wrong&code=synthetic-code")
    with pytest.raises(ValueError, match="path"):
        attempt.accept_callback(
            "/other?state=" + attempt.state + "&code=synthetic-code"
        )
    with pytest.raises(ValueError, match="Duplicate"):
        attempt.accept_callback(
            "/callback?state=" + attempt.state + "&state=other&code=x"
        )
    attempt.accept_callback("/callback?state=" + attempt.state + "&code=synthetic-code")
    assert attempt.code == "synthetic-code"
    with pytest.raises(ValueError, match="consumed"):
        attempt.accept_callback("/callback?state=" + attempt.state + "&code=other")


def test_store_atomic_private_owned_and_no_secret_status(config):
    from oauth_store import CredentialStore
    from poe_oauth import token_status

    seed(config)
    assert config.token_file.stat().st_mode & 0o777 == 0o600
    assert config.token_file.parent.stat().st_mode & 0o777 == 0o700
    status = token_status(config, now=200)
    assert status["token_status"] == "unexpired_local"
    assert status["expires_in_seconds"] == 35900
    assert status["server_validity"] == "not_checked"
    assert "synthetic-access" not in json.dumps(status)
    assert "synthetic-access" not in repr(
        CredentialStore(config.token_file, config.client_id).load()
    )
    assert token_status(config, now=40000)["token_status"] == "expired"


def test_store_rejects_symlinks_permissions_foreign_and_corrupt(config, tmp_path):
    from oauth_store import CredentialStore, StoreError

    seed(config)
    store = CredentialStore(config.token_file, config.client_id)
    config.token_file.chmod(0o644)
    with pytest.raises(StoreError):
        store.load()
    config.token_file.chmod(0o600)
    config.token_file.write_text("{broken synthetic-access")
    with pytest.raises(StoreError) as err:
        store.load()
    assert "synthetic-access" not in str(err.value)
    config.token_file.unlink()
    outside = tmp_path / "outside"
    outside.write_text("unrelated")
    config.token_file.symlink_to(outside)
    with pytest.raises(StoreError):
        store.load()
    assert outside.read_text() == "unrelated"


def test_owned_marker_and_client_mismatch_are_errors(config):
    from oauth_store import CredentialStore, StoreError

    seed(config)
    data = json.loads(config.token_file.read_text())
    data["client_id"] = "other-client"
    config.token_file.write_text(json.dumps(data))
    with pytest.raises(StoreError, match="client"):
        CredentialStore(config.token_file, config.client_id).load()
    data["client_id"] = config.client_id
    data["format"] = "foreign-store"
    config.token_file.write_text(json.dumps(data))
    with pytest.raises(StoreError, match="owned"):
        CredentialStore(config.token_file, config.client_id).load()


def test_client_route_headers_and_private_response(config):
    from poe_oauth_client import CharacterClient

    seed(config)
    http = FakeHTTP(
        {"characters": [{"name": "Test Char", "realm": "poe2"}]},
        {
            "character": {
                "name": "Test Char",
                "realm": "poe2",
                "equipment": [],
                "passives": {"hashes": [1]},
            }
        },
    )
    client = CharacterClient(config, http=http, now=lambda: 200)
    assert client.list_characters(confirm=True)["characters"][0]["name"] == "Test Char"
    result = client.get_character("Test Char", confirm=True)
    assert result["character"]["passives"] == {"hashes": [1]}
    assert result["source"]["access_method"] == "official_oauth"
    assert [r[1] for r in http.requests] == [
        "https://api.pathofexile.com/character/poe2",
        "https://api.pathofexile.com/character/poe2/Test%20Char",
    ]
    assert all(
        r[2]["Authorization"] == "Bearer synthetic-access" for r in http.requests
    )
    assert all("synthetic-access" not in r[1] for r in http.requests)
    assert http.requests[0][2]["User-Agent"].startswith("OAuth synthetic-client/")
    assert "synthetic-access" not in json.dumps(result)


def test_network_and_file_read_require_enable_and_confirmation(config, monkeypatch):
    from poe_oauth_client import CharacterClient, OAuthError
    from oauth_store import CredentialStore

    monkeypatch.setattr(
        CredentialStore,
        "load",
        lambda *a: (_ for _ in ()).throw(
            AssertionError("credential read before consent")
        ),
    )
    client = CharacterClient(config, http=FakeHTTP(), now=lambda: 200)
    with pytest.raises(OAuthError, match="confirm"):
        client.list_characters()
    from dataclasses import replace

    disabled = CharacterClient(replace(config, enabled=False), http=FakeHTTP())
    with pytest.raises(OAuthError, match="disabled"):
        disabled.list_characters(confirm=True)


def test_expired_token_never_sends_character_request(config):
    from poe_oauth_client import CharacterClient, OAuthError

    seed(config, token_response(expires_in=10))
    http = FakeHTTP()
    with pytest.raises(OAuthError, match="expired"):
        CharacterClient(config, http=http, now=lambda: 200).list_characters(
            confirm=True
        )
    assert http.requests == []


def test_refresh_rotation_preserves_original_deadline(config):
    from dataclasses import replace
    from poe_oauth_client import CharacterClient
    from oauth_store import CredentialStore

    config = replace(config, allow_refresh=True)
    initial = seed(
        config, token_response(expires_in=10, refresh_token="synthetic-refresh")
    )
    http = FakeHTTP(
        token_response(access_token="rotated-access", refresh_token="rotated-refresh"),
        {"characters": []},
    )
    client = CharacterClient(config, http=http, now=lambda: 200)
    assert client.list_characters(confirm=True)["characters"] == []
    form = http.requests[0][3]
    assert form == {
        "client_id": config.client_id,
        "grant_type": "refresh_token",
        "refresh_token": "synthetic-refresh",
    }
    assert http.requests[1][2]["Authorization"] == "Bearer rotated-access"
    stored = CredentialStore(config.token_file, config.client_id).load()
    assert stored.refresh_expires_at == initial.refresh_expires_at
    assert stored.refresh_token == "rotated-refresh"


def test_exchange_uses_pkce_scope_and_never_returns_tokens(config):
    from poe_oauth import AuthorizationAttempt, exchange_code
    from oauth_store import CredentialStore

    attempt = AuthorizationAttempt(config)
    attempt.accept_callback("/callback?state=" + attempt.state + "&code=synthetic-code")
    http = FakeHTTP(token_response(refresh_token="synthetic-refresh"))
    status = exchange_code(config, attempt, http=http, now=lambda: 200, confirm=True)
    method, url, headers, form = http.requests[0]
    assert (method, url) == ("POST", "https://www.pathofexile.com/oauth/token")
    assert form["scope"] == "account:characters"
    assert form["code_verifier"] == attempt.verifier
    assert "client_secret" not in form
    assert "synthetic-access" not in json.dumps(status)
    assert (
        CredentialStore(config.token_file, config.client_id).load().refresh_token
        is None
    )


def test_mcp_auth_default_is_status_and_authorization_needs_confirmation(monkeypatch):
    import poe_stash

    default = asyncio.run(poe_stash.call_tool("poe_auth", {}))
    assert not default.isError
    assert json.loads(default.content[0].text)["token_status"] == "not_inspected"
    guarded = asyncio.run(poe_stash.call_tool("poe_auth", {"action": "authorize"}))
    assert guarded.isError
    assert "confirm" in guarded.content[0].text


def test_public_character_default_never_uses_oauth(monkeypatch):
    import poe_char, poe_oauth_client

    monkeypatch.setattr(
        poe_oauth_client,
        "CharacterClient",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("OAuth selected by default")
        ),
    )
    monkeypatch.setattr(
        poe_char,
        "get_snapshot",
        lambda url: {
            "build": {"level": 90},
            "source": {"source_age_text_at_capture": "2 hours ago"},
        },
    )
    response = asyncio.run(
        poe_char.call_tool(
            "get_character",
            {
                "profile_url": "https://poe.ninja/poe2/profile/Test-1234/league/character/Test"
            },
        )
    )
    assert not response.isError
    assert (
        json.loads(response.content[0].text)["source"]["source_age_text_at_capture"]
        == "2 hours ago"
    )


def test_mcp_official_read_requires_explicit_source_and_confirmation():
    import poe_char

    response = asyncio.run(
        poe_char.call_tool(
            "get_character", {"source": "oauth", "character_name": "Test"}
        )
    )
    assert response.isError
    assert "confirm" in response.content[0].text
    names = {t.name for t in asyncio.run(poe_char.list_tools())}
    assert "list_oauth_characters" in names


def test_missing_expiry_and_scope_are_errors(config):
    from oauth_store import TokenRecord, StoreError

    for changes in (
        {"expires_in": None},
        {"expires_in": True},
        {"expires_in": float("inf")},
        {"scope": "account:stashes"},
        {"token_type": "mac"},
    ):
        with pytest.raises(StoreError):
            TokenRecord.from_response(
                token_response(**changes), client_id=config.client_id, now=100
            )


def test_refresh_failure_blocks_replay(config):
    from dataclasses import replace
    from poe_oauth_client import CharacterClient, OAuthError
    from poe_oauth import token_status

    config = replace(config, allow_refresh=True)
    seed(config, token_response(expires_in=10, refresh_token="synthetic-refresh"))
    http = FakeHTTP(
        OAuthError("HTTP 400 (invalid_grant)", status=400, code="invalid_grant")
    )
    client = CharacterClient(config, http=http, now=lambda: 200)
    with pytest.raises(OAuthError):
        client.list_characters(confirm=True)
    assert token_status(config, now=200)["token_status"] == "reauthorization_required"
    with pytest.raises(OAuthError, match="Reauthorization"):
        client.list_characters(confirm=True)
    assert len(http.requests) == 1


@pytest.mark.parametrize("status", [401, 403])
def test_rejected_bearer_blocks_repeated_invalid_requests(config, status):
    from poe_oauth_client import CharacterClient, OAuthError

    seed(config)
    http = FakeHTTP(OAuthError("Rejected", status=status))
    client = CharacterClient(config, http=http, now=lambda: 200)
    with pytest.raises(OAuthError):
        client.list_characters(confirm=True)
    with pytest.raises(OAuthError, match="Reauthorization"):
        client.list_characters(confirm=True)
    assert len(http.requests) == 1


def test_wrong_realm_name_or_null_character_are_errors(config):
    from poe_oauth_client import CharacterClient, OAuthError

    seed(config)
    for data in (
        {"character": None},
        {"character": {"name": "Test", "realm": "pc"}},
        {"character": {"name": "Other", "realm": "poe2"}},
    ):
        with pytest.raises(OAuthError):
            CharacterClient(config, http=FakeHTTP(data), now=lambda: 200).get_character(
                "Test", confirm=True
            )


@pytest.mark.parametrize(
    "url",
    [
        "https://example.invalid/callback",
        "http://localhost:8080/callback",
        "http://127.0.0.1:0/callback",
        "http://127.0.0.1:8080/callback?x=y",
        "http://0.0.0.0:8080/callback",
    ],
)
def test_client_rejects_unregistered_style_or_remote_redirect(config, url):
    from dataclasses import replace
    from poe_oauth_client import OAuthError

    with pytest.raises(OAuthError):
        replace(config, redirect_uri=url).validate()


def test_flow_gate_prevents_browser_and_socket(config, monkeypatch):
    import poe_oauth

    monkeypatch.setattr(
        poe_oauth,
        "HTTPServer",
        lambda *a: (_ for _ in ()).throw(AssertionError("listener started")),
    )
    with pytest.raises(poe_oauth.OAuthError, match="confirm"):
        poe_oauth.run_auth_flow(config, confirm=False)


def test_http_redacts_errors_and_never_follows_redirects():
    import io, urllib.error
    from poe_oauth_client import OAuthHTTP, OAuthError, _NoRedirect

    class Opener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(
                request.full_url,
                400,
                "synthetic-access",
                {},
                io.BytesIO(
                    b'{"error":"invalid_grant","error_description":"synthetic-access synthetic-refresh"}'
                ),
            )

    OAuthHTTP._next.clear()
    with pytest.raises(OAuthError) as err:
        OAuthHTTP(Opener()).request(
            "POST",
            "https://www.pathofexile.com/oauth/token",
            headers={},
            form={"code": "synthetic-code"},
        )
    assert err.value.code == "invalid_grant"
    assert "synthetic" not in str(err.value)
    with pytest.raises(OAuthError, match="redirect"):
        _NoRedirect().redirect_request(None, None, None, None, None, None)


def test_http_429_cooldown_and_no_automatic_retry():
    import urllib.error
    from poe_oauth_client import OAuthHTTP, OAuthError

    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            raise urllib.error.HTTPError(
                request.full_url, 429, "Rate limit", {"Retry-After": "120"}, None
            )

    OAuthHTTP._next.clear()
    http = OAuthHTTP(Opener())
    for message in ("429", "cooldown"):
        with pytest.raises(OAuthError, match=message):
            http.request(
                "GET",
                "https://api.pathofexile.com/character/poe2",
                headers={"Authorization": "Bearer synthetic-access"},
            )
    assert len(calls) == 1


def test_refresh_deadline_not_extended_and_refresh_off_drops_token(config):
    from oauth_store import TokenRecord

    initial = TokenRecord.from_response(
        token_response(refresh_token="first"), client_id=config.client_id, now=100
    )
    later = TokenRecord.from_response(
        token_response(refresh_token="second"),
        client_id=config.client_id,
        now=1000,
        previous=initial,
    )
    assert initial.refresh_expires_at == later.refresh_expires_at


def test_code_is_one_use_and_denial_does_not_exchange(config):
    from poe_oauth import AuthorizationAttempt, exchange_code, OAuthError

    denied = AuthorizationAttempt(config)
    denied.accept_callback(
        "/callback?state="
        + denied.state
        + "&error=access_denied&error_description=synthetic-secret"
    )
    http = FakeHTTP()
    with pytest.raises(OAuthError):
        exchange_code(config, denied, http=http, confirm=True)
    assert http.requests == []
    attempt = AuthorizationAttempt(config)
    attempt.accept_callback("/callback?state=" + attempt.state + "&code=synthetic-code")
    http = FakeHTTP(token_response())
    exchange_code(config, attempt, http=http, confirm=True)
    with pytest.raises(OAuthError):
        exchange_code(config, attempt, http=http, confirm=True)
    assert len(http.requests) == 1


def test_http_token_contract_form_body_not_url_and_no_redirect_handler_leak():
    import io
    from poe_oauth_client import OAuthHTTP, TOKEN_URL

    requests = []

    class Response(io.BytesIO):
        status = 200
        headers = {}

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            return Response(json.dumps(token_response()).encode())

    OAuthHTTP._next.clear()
    data = OAuthHTTP(Opener()).request(
        "POST",
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        form={"code": "synthetic+code", "code_verifier": "synthetic-verifier"},
    )
    request = requests[0]
    assert request.full_url == TOKEN_URL
    assert parse_qs(request.data.decode()) == {
        "code": ["synthetic+code"],
        "code_verifier": ["synthetic-verifier"],
    }
    assert request.get_header("Content-type") == "application/x-www-form-urlencoded"
    assert data["access_token"] == "synthetic-access"


def test_status_explicit_file_via_environment_is_metadata_only(config, monkeypatch):
    import poe_stash

    seed(config, now=1)
    monkeypatch.setenv("POE_OAUTH_CLIENT_ID", config.client_id)
    monkeypatch.setenv("POE_OAUTH_TOKEN_FILE", str(config.token_file))
    response = asyncio.run(poe_stash.call_tool("poe_auth_status", {}))
    assert not response.isError
    data = json.loads(response.content[0].text)
    assert data["token_status"] == "expired"
    assert data["server_validity"] == "not_checked"
    assert "synthetic-access" not in response.content[0].text


def test_flow_timeout_and_browser_failure_close_loopback(config, monkeypatch):
    import poe_oauth
    from poe_oauth_client import OAuthError

    servers = []

    class LocalServer:
        def __init__(self, address, handler):
            self.address = address
            self.closed = False
            servers.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

        def handle_request(self):
            pass

    monkeypatch.setattr(poe_oauth, "HTTPServer", LocalServer)
    with pytest.raises(OAuthError, match="browser"):
        poe_oauth.run_auth_flow(config, confirm=True, browser_open=lambda url: False)
    assert servers[0].closed
    clock = iter([0, 20])
    monkeypatch.setattr(poe_oauth.time, "monotonic", lambda: next(clock))
    with pytest.raises(OAuthError, match="timed out"):
        poe_oauth.run_auth_flow(
            config, confirm=True, browser_open=lambda url: True, timeout=10
        )
    assert servers[1].closed
    assert servers[1].address == ("127.0.0.1", 18787)
    assert not config.token_file.exists()


def test_synthetic_loopback_callback_redeems_without_browser_or_external_http(config):
    """Exercise the real local handler using a fake provider; never contact GGG."""
    from dataclasses import replace
    import http.client
    import socket
    import threading
    from poe_oauth import run_auth_flow

    # Choose an available unprivileged port for this local-only test.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = replace(config, redirect_uri=f"http://127.0.0.1:{port}/callback")
    workers = []
    replies = []

    def browser(url):
        query = parse_qs(urlsplit(url).query)

        def callback():
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            conn.request(
                "GET", "/callback?state=" + query["state"][0] + "&code=synthetic-code"
            )
            response = conn.getresponse()
            replies.append((response.status, response.read().decode()))
            conn.close()

        worker = threading.Thread(target=callback)
        workers.append(worker)
        worker.start()
        return True

    result = run_auth_flow(
        config,
        confirm=True,
        browser_open=browser,
        http=FakeHTTP(token_response()),
        timeout=10,
    )
    for worker in workers:
        worker.join(timeout=3)
    assert replies[0][0] == 200
    assert "synthetic-code" not in replies[0][1]
    assert result["token_status"] == "unexpired_local"
    assert "synthetic-access" not in json.dumps(result)


def test_store_write_failure_keeps_old_complete_record(config, monkeypatch):
    from dataclasses import replace
    from oauth_store import CredentialStore, StoreError

    old = seed(config)
    store = CredentialStore(config.token_file, config.client_id)
    monkeypatch.setattr(
        os,
        "replace",
        lambda *args: (_ for _ in ()).throw(OSError("synthetic-sensitive-message")),
    )
    with pytest.raises(StoreError) as err:
        store.save(replace(old, access_token="new-synthetic-access"))
    assert "synthetic-sensitive-message" not in str(err.value)
    assert store.load().access_token == old.access_token
    assert list(config.token_file.parent.glob(".poe2-oauth-*")) == []


def test_store_lock_prevents_concurrent_refresh(config):
    from oauth_store import CredentialStore, StoreError

    store = CredentialStore(config.token_file, config.client_id)
    with store.lock():
        with pytest.raises(StoreError, match="Another OAuth"):
            with CredentialStore(config.token_file, config.client_id).lock():
                pytest.fail("second operation entered critical section")


def test_store_rejects_writable_ancestor(config, tmp_path):
    from oauth_store import CredentialStore, StoreError

    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o777)
    unsafe.chmod(0o777)
    private = unsafe / "private"
    private.mkdir(mode=0o700)
    with pytest.raises(StoreError, match="ancestor"):
        CredentialStore(private / "poe2-oauth.json", config.client_id).load()


def test_reauthorization_status_does_not_offer_refresh(config):
    from dataclasses import replace
    from poe_oauth import token_status
    from oauth_store import CredentialStore

    config = replace(config, allow_refresh=True)
    record = seed(config, token_response(refresh_token="synthetic-refresh"))
    CredentialStore(config.token_file, config.client_id).save(
        replace(record, reauthorization_required=True)
    )
    assert token_status(config, now=200)["refresh_available"] is False


def test_unicode_callback_state_is_a_validation_error(config):
    from poe_oauth import AuthorizationAttempt

    with pytest.raises(ValueError, match="state"):
        AuthorizationAttempt(config).accept_callback(
            "/callback?state=%C3%A9&code=synthetic-code"
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://api.pathofexile.com/character/poe2/../../stash",
        "https://api.pathofexile.com/character/poe2/%2e%2e",
        "https://api.pathofexile.com/character/poe2/name%2Fstash",
        "https://api.pathofexile.com/character/poe2/%252e%252e",
    ],
)
def test_oauth_http_cannot_escape_character_route(url):
    from poe_oauth_client import OAuthHTTP, OAuthError

    class Opener:
        def open(self, *args, **kwargs):
            pytest.fail("invalid route reached transport")

    OAuthHTTP._next.clear()
    with pytest.raises(OAuthError, match="route"):
        OAuthHTTP(Opener()).request(
            "GET", url, headers={"Authorization": "Bearer synthetic-access"}
        )
