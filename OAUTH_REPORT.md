# Official PoE2 OAuth character component — review handoff

Implemented directly after trade checkpoint `c950576`. This component has been
tested offline with synthetic credentials only. **No real authorization flow,
authenticated external request, credential inspection, or runtime configuration
change was performed.** The broader suite restoration remains incomplete.

## Scope delivered

- `poe_oauth.py`: registered **public-client** Authorization Code + PKCE S256,
  exact IPv4 loopback callback, one-use state/code, short code redemption window,
  explicit status and opt-in flow. No client secret is accepted or needed.
- `oauth_store.py`: explicitly configured, component-owned token record,
  client/realm binding, bounded parsing, strict POSIX permissions, atomic writes,
  fsync and cross-process locking. No legacy global-store default or migration.
- `poe_oauth_client.py`: only official PoE2 character list/detail GETs and token
  POSTs; bearer header, documented User-Agent, no HTTP redirect/proxy inheritance,
  no response-body logging, bounded responses, pacing, rate-header and 429
  cooldown handling. No automatic retry after rejected/uncertain requests.
- `poe_stash.py`: `poe_auth` defaults to local status; authorization needs an
  explicit action, confirmation and enable setting. `poe_auth_status` can inspect
  only the explicitly configured owned store and returns metadata, never tokens.
- `poe_char.py`: ninja is still the default. `get_character` accepts an explicit
  OAuth source, and `list_oauth_characters` lists the authorized account's PoE2
  characters. Both need explicit confirmation and enablement. Public exports and
  age handling are unchanged; there is no fallback from ninja to OAuth.

No new mandatory dependencies were added. The OAuth implementation uses Python's
standard library and the existing AnyIO/MCP adapter. File-store security currently
requires POSIX permissions, including WSL; native Windows ACL/keychain storage is
not implemented and fails explicitly.

## Source contract, verified from public documentation

[GGG OAuth client types and grants](https://www.pathofexile.com/developer/docs/authorization):
public desktop clients use Authorization Code with PKCE and a local redirect
URI such as `http://127.0.0.1:8080/callback`. The callback must match the client's
registered URI. Confidential clients require an HTTPS registered-domain callback;
this implementation does not claim to support that different deployment model.

Only `account:characters` is requested. The token exchange includes the same
scope and redirect URI plus `code_verifier`. Codes expire after 30 seconds;
this component refuses redemption once 25 seconds have elapsed locally.
Bearer tokens are sent exclusively in the `Authorization` header.

[GGG character API](https://www.pathofexile.com/developer/docs/reference#characters):

- `GET https://api.pathofexile.com/character/poe2`
- `GET https://api.pathofexile.com/character/poe2/{name}`

The returned `character` is retained as official JSON, including equipment,
PoE2 skills and passive specialisations when provided. No PoE1 socket-color
model, fabricated PoB XML, or invented live stats are applied. A null character,
wrong name/realm or malformed container fields produces an explicit error.
The generic Character type table still lists old realm values while the routes
explicitly allow `poe2`; the first authorized live check must confirm the actual
response shape. This implementation requires a returned `realm=poe2`.

[GGG developer guidelines](https://www.pathofexile.com/developer/docs/index)
require `OAuth {clientId}/{version} (contact: {contact})` in the User-Agent and
rate-limit compliance. The same page currently says new application registrations
cannot be processed. An existing registered public client may therefore be a
prerequisite, not something this tool can create automatically.

Account/guild/public stash sections remain labeled PoE1-only in the official
reference. OAuth character support does **not** restore private stash routes.

## Configuration required later, after review

Configuration reads only these explicit inputs; it never falls back to
`POE_SESSION_ID`, `POE_CLIENT_ID`, the old global tokens file, or browser state.
Do not pass bearer tokens, refresh tokens, passwords, or authorization codes
through MCP arguments or chat.

| Setting | Required value / behavior |
|---|---|
| `POE_OAUTH_CLIENT_ID` | An existing registered **public** client ID approved for `account:characters`. This is an identifier, not a client secret. |
| `POE_OAUTH_CONTACT` | The application contact email for GGG's User-Agent requirement. |
| `POE_OAUTH_REDIRECT_URI` | Exact registered `http://127.0.0.1:PORT/PATH`, port 1024–65535. No `localhost` alias, wildcard address, remote host, query, fragment, or port guessing. |
| `POE_OAUTH_TOKEN_FILE` | Explicit absolute dedicated path ending in `poe2-oauth.json`, preferably outside the repository on the WSL/Linux filesystem. Its immediate parent must be private to the current user (0700); newly created directories use 0700. No symlink path traversal. |
| `POE_OAUTH_ENABLED` | Defaults off. Main review must happen before configuring `1`; the per-call confirmation is required as well. |
| `POE_OAUTH_ALLOW_REFRESH` | Defaults off. Set `1` only if the registered client has refresh capability and persistence of its refresh token in this private store has been approved. |

The store uses a version marker, `realm=poe2`, matching client ID, 0600 files,
0700 immediate parent, safe ancestors, regular-file/owner/single-link checks,
atomic replacement and a private lock. Foreign/corrupt/insecure files are refused,
not silently replaced. Tokens are private plaintext on disk, protected by POSIX
permissions; this does not provide at-rest encryption against root or the same
OS user. Native Windows paths without enforceable POSIX protection are not a
supported storage backend. Credential/staging filenames are gitignored.

Status without explicit store/client configuration does not touch any credential
file. With both configured, `poe_auth_status` reads the owned record and reports
`not_authorized`, `unexpired_local`, `expiring`, `expired`, or
`reauthorization_required`. `server_validity=not_checked` always distinguishes
local metadata from token introspection or a successful API call.

## Reviewable user actions, not executed here

1. After review, configure the registered public client, callback, contact,
   private store path and enable setting. A local status call is read-only:

   ```json
   {"tool":"poe_auth_status","arguments":{}}
   ```

2. Only after the user explicitly chooses OAuth, start authorization:

   ```json
   {"tool":"poe_auth","arguments":{"action":"authorize","confirm":true}}
   ```

   The component binds the exact registered loopback address, opens the GGG
   consent page, verifies the callback state/path/Host and redeems the one-use
   code. It returns status metadata. Tokens, codes and PKCE verifier are not
   printed or returned. Callback errors do not echo provider descriptions.
   Timeout, denied consent, occupied port or browser failure closes the listener.

3. A separate explicitly confirmed character read is then possible:

   ```json
   {"tool":"list_oauth_characters","arguments":{"confirm_oauth":true}}
   ```

   ```json
   {"tool":"get_character","arguments":{"source":"oauth","character_name":"EXACT_NAME","confirm_oauth":true}}
   ```

   The authorized account comes from the token; a profile URL/account argument
   cannot redirect OAuth access to another account. Responses stay in the caller
   session and are not written to a snapshot/cache file or published.
   Public ninja requests keep `source=public` by default and keep source age.

## Expiry, refresh and failures

Access expiry comes from the token response, with a 60-second request margin.
Expired/expiring tokens cause a local error before any character request unless
refresh was explicitly enabled and a stored refresh token remains eligible.
Refresh tokens returned during authorization are discarded when refresh is off.

The public-client documentation gives refresh tokens a seven-day lifetime.
The store records this deadline at the original grant and preserves it on
rotation; it is a local limit, not proof of remote validity. Refresh cannot
extend the original deadline. Omitted refresh tokens do not resurrect the old
single-use token.

Refresh is serialized across processes. Before a refresh request, the record is
marked as requiring reauthorization. Only a validated response persisted
successfully clears that state. Thus a timeout, `invalid_grant`, malformed
response, crash or failed write cannot silently replay a possibly consumed
refresh token. Recovery in an uncertain case is an explicit new authorization.
HTTP 401/403 likewise marks the record as requiring reauthorization and blocks
repeated requests. No failed read triggers a browser flow automatically.

Provider error bodies/descriptions are never surfaced because they can contain
codes, tokens or private data. Only HTTP status and a small allowlist of OAuth
error codes are returned. Redirects are refused so the bearer cannot be forwarded
to another endpoint. HTTP 429 establishes a cooldown from `Retry-After` without
automatic retries; GGG rate-limit/state headers widen normal pacing.

## Tests and remaining work

Final verification: **74 tests passed on MCP 1.26.0 and MCP 1.30.0** in the
isolated submodule `.test-venv`. Dependency checks, undefined/unused-name lint,
and `git diff --check` passed. The preserved original OAuth file was compared
byte-for-byte against checkpoint `c950576`. No mandatory requirements changed.


The offline suite uses synthetic tokens, fake provider HTTP and a local-only
callback test with a fake browser/provider. Test fixtures clear ambient OAuth
configuration and prohibit external urllib requests. Coverage includes PKCE,
scopes, state/path/duplicate callbacks, code one-use/denial, callback cleanup,
form encoding, official routes, bearer headers, JSON errors/redaction, 429,
401/403 replay prevention, expiry, refresh rotation/deadlines/uncertainty,
atomic store failure, permissions/symlinks/foreign records and process locks,
MCP status/confirmation, and regression protection for the public default.

Live requirements still outstanding: registered public client ID with the correct
scope, exact registered loopback redirect and usable local port, contact, explicit
private store configuration, reviewed enablement, and the user's choice to grant
OAuth access. No client secret, password or POESESSID is needed for this public
client flow. Nothing is being requested from the user now.

Seven unrelated tools remain explicit gaps: `get_tab`, `list_tabs`, `price_tab`,
`find_items`, `cache_status`, `scan_stash_tabs`, `kf_check`. Official JSON-to-PoB2
conversion and native Windows secure storage are also outside this component.
Legacy source is preserved in `legacy/poe_oauth.py.txt` from checkpoint `c950576`.
