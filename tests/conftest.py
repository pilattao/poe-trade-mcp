"""Tests cannot inherit credentials or make accidental external HTTP requests."""

import os
import urllib.request
import pytest


@pytest.fixture(autouse=True)
def no_ambient_oauth_or_network(monkeypatch):
    for key in list(os.environ):
        if key.startswith("POE_OAUTH_"):
            monkeypatch.delenv(key)

    def forbidden(*args, **kwargs):
        raise AssertionError(
            "External HTTP is forbidden in offline tests; inject a synthetic transport"
        )

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", forbidden)
