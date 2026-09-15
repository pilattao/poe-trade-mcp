"""Exercise the public HTTP boundary and real MCP stdio protocol without network."""

import asyncio
import io
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import public_http


class Response(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(body)
        self.headers = headers or {}
        self.url = "https://poe.ninja/poe2/api/economy/leagues"


@pytest.fixture(autouse=True)
def isolated_http(monkeypatch):
    public_http._CACHE.clear()
    public_http._NEXT.clear()
    public_http._INTERVAL.clear()
    monkeypatch.setattr(public_http.time, "sleep", lambda _: None)


def test_http_cache_etag_and_freshness(monkeypatch):
    requests = []

    def open_request(req, **kw):
        requests.append(req)
        if len(requests) > 1:
            raise urllib.error.HTTPError(req.full_url, 304, "Not Modified", {}, None)
        return Response(
            b'[{"id":"Test"}]', {"ETag": '"v1"', "Cache-Control": "max-age=300"}
        )

    monkeypatch.setattr(urllib.request, "urlopen", open_request)
    url = "https://poe.ninja/poe2/api/economy/leagues"
    first, meta = public_http.request_json(url, ttl=3600)
    first[0]["id"] = "mutated"
    second, _ = public_http.request_json(url, ttl=3600)
    assert second == [{"id": "Test"}]
    assert len(requests) == 1
    public_http._CACHE[url]["expires"] = 0
    _, new_meta = public_http.request_json(url, ttl=3600)
    assert requests[1].get_header("If-none-match") == '"v1"'
    assert new_meta["fetched_at"] == meta["fetched_at"]
    assert meta["source_timestamp"] is None
    assert all(
        not req.has_header("Cookie") and not req.has_header("Authorization")
        for req in requests
    )


def test_http_failures_not_cached_or_retried(monkeypatch):
    requests = []

    def open_request(req, **kw):
        requests.append(req)
        raise urllib.error.HTTPError(
            req.full_url, 429, "Slow down", {"Retry-After": "120"}, None
        )

    monkeypatch.setattr(urllib.request, "urlopen", open_request)
    url = "https://poe.ninja/poe2/api/economy/leagues"
    with pytest.raises(public_http.SourceError, match="429"):
        public_http.request_json(url, ttl=3600)
    with pytest.raises(public_http.SourceError, match="cooldown"):
        public_http.request_json(url, ttl=3600)
    assert len(requests) == 1
    assert url not in public_http._CACHE


@pytest.mark.parametrize(
    "body", [b"<html>Access denied</html>", b'{"error":{"code":1}}', b""]
)
def test_http_bad_responses_raise(monkeypatch, body):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: Response(body))
    with pytest.raises(public_http.SourceError):
        public_http.request_json("https://poe.ninja/poe2/api/economy/leagues", ttl=3600)


def test_stdio_discovers_all_tools_and_keeps_errors(tmp_path):
    async def scenario():
        root = Path(__file__).resolve().parents[1]
        env = {k: v for k, v in os.environ.items() if not k.startswith("POE_")}
        env["POE_PRICE_DB"] = str(tmp_path / "empty.sqlite3")
        # cwd is deliberately unrelated: no sibling imports or parent helpers.
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(root / "poe_all.py")],
            cwd=str(tmp_path),
            env=env,
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                import poe_all

                assert {t.name for t in listed.tools} == {
                    t.name for t in poe_all._ALL_TOOLS
                }
                assert len(listed.tools) == len(poe_all._ALL_TOOLS)
                for name, args in [
                    ("unknown", {}),
                    ("search_trade", {"stats": "bad"}),
                    ("list_tabs", {}),
                    ("get_price", {"name": "Test", "league": "Empty"}),
                    (
                        "get_block",
                        {"line": 1, "filter_path": str(tmp_path / "absent.filter")},
                    ),
                ]:
                    response = await session.call_tool(name, args)
                    assert response.isError, (name, response)
                response = await session.call_tool("get_economy_categories", {})
                assert not response.isError
                assert (
                    "UniqueAccessories" in json.loads(response.content[0].text)["stash"]
                )

    asyncio.run(scenario())


def test_public_json_error_keeps_actionable_reason(monkeypatch):
    def bad(req, **kwargs):
        raise urllib.error.HTTPError(
            req.full_url,
            400,
            "Bad Request",
            {},
            io.BytesIO(b'{"error":{"code":2,"message":"Invalid sale type"}}'),
        )

    monkeypatch.setattr(urllib.request, "urlopen", bad)
    with pytest.raises(public_http.SourceError, match="Invalid sale type"):
        public_http.request_json("https://poe.ninja/poe2/api/economy/leagues")
