"""Bounded anonymous HTTP, conditional caching and per-host rate limiting."""

import copy
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "poe-trade-mcp/2.0 (https://github.com/pilattao/poe-trade-mcp)"
_CACHE = {}
_LOCK = threading.RLock()
_NEXT = {}
_INTERVAL = {}
MAX_BYTES = 16_000_000


class SourceError(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def retry_seconds(value):
    try:
        return max(0, float(value))
    except (TypeError, ValueError):
        try:
            return max(0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError):
            return 60


def _response_interval(headers):
    interval = 1.5
    for name in ("Ip", "Account", "Client"):
        for rule in headers.get(f"X-Rate-Limit-{name}", "").split(","):
            try:
                hits, period, *_ = map(float, rule.split(":"))
                if hits > 0:
                    interval = max(interval, period / hits * 1.25)
            except ValueError:
                continue
    return interval


def request_json(url, *, payload=None, ttl=0):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in (
        "poe.ninja",
        "www.pathofexile.com",
    ):
        raise ValueError("Only configured public PoE2 sources are allowed")
    if parsed.username or parsed.password or parsed.port:
        raise ValueError("Unexpected source authority")
    # The only POST allowed is an anonymous read-only trade search.
    if payload is not None and not parsed.path.startswith("/api/trade2/search/poe2/"):
        raise ValueError("Only read-only trade search POST is supported")
    with _LOCK:
        cached = _CACHE.get(url) if payload is None else None
        if cached and time.monotonic() < cached["expires"]:
            return copy.deepcopy(cached["data"]), dict(cached["meta"])
        remaining = _NEXT.get(parsed.hostname, 0) - time.monotonic()
        if remaining > 10:
            raise SourceError(
                f"{parsed.hostname} rate limit cooldown; retry in {remaining:.0f}s"
            )
        if remaining > 0:
            time.sleep(remaining)
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if cached and cached.get("etag"):
            headers["If-None-Match"] = cached["etag"]
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, allow_nan=False).encode()
        request = urllib.request.Request(url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                response_headers = response.headers
                if urllib.parse.urlsplit(response.url).hostname != parsed.hostname:
                    raise SourceError("Unexpected source redirect")
                raw = response.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise SourceError("Source response exceeds size limit")
                try:
                    data = json.loads(raw)
                except (ValueError, UnicodeError) as exc:
                    raise SourceError(
                        f"{parsed.hostname} returned non-JSON data"
                    ) from exc
                if isinstance(data, dict) and data.get("error"):
                    raise SourceError(f"{parsed.hostname} returned an API error")
                meta = {
                    "source_url": url,
                    "fetched_at": utc_now(),
                    "source_timestamp": None,
                    "http_date": response_headers.get("Date"),
                    "http_age_seconds": response_headers.get("Age"),
                    "freshness_note": "Fetch time is not the underlying game-data timestamp.",
                }
        except urllib.error.HTTPError as exc:
            response_headers = exc.headers
            if exc.code == 304 and cached:
                data, meta = cached["data"], cached["meta"]
            else:
                if exc.code == 429:
                    _NEXT[parsed.hostname] = time.monotonic() + retry_seconds(
                        exc.headers.get("Retry-After")
                    )
                detail = ""
                try:
                    error = json.loads(exc.read(2048)).get("error", {})
                    if isinstance(error, dict) and isinstance(
                        error.get("message"), str
                    ):
                        detail = ": " + error["message"][:300]
                except (ValueError, AttributeError, TypeError):
                    pass
                exc.close()
                raise SourceError(
                    f"{parsed.hostname} HTTP {exc.code}"
                    + detail
                    + ("; honor Retry-After before retrying" if exc.code == 429 else "")
                ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise SourceError(
                f"{parsed.hostname} request failed: {exc.reason if isinstance(exc, urllib.error.URLError) else 'timeout'}"
            ) from exc
        finally:
            _NEXT[parsed.hostname] = max(
                _NEXT.get(parsed.hostname, 0),
                time.monotonic() + _INTERVAL.get(parsed.hostname, 1.5),
            )
        _INTERVAL[parsed.hostname] = _response_interval(response_headers)
        _NEXT[parsed.hostname] = max(
            _NEXT[parsed.hostname], time.monotonic() + _INTERVAL[parsed.hostname]
        )
        if ttl and payload is None:
            cache_ttl = ttl
            for part in response_headers.get("Cache-Control", "").split(","):
                if part.strip().startswith("max-age="):
                    try:
                        cache_ttl = max(ttl, int(part.strip().split("=")[1]))
                    except ValueError:
                        pass
            _CACHE[url] = {
                "data": data,
                "meta": meta,
                "etag": response_headers.get(
                    "ETag", cached.get("etag") if cached else None
                ),
                "expires": time.monotonic() + cache_ttl,
            }
        return copy.deepcopy(data), dict(meta)
