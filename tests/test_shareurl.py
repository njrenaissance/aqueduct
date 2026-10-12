"""shareurl: the share URL is carried between login and enumerate via an env var;
resolution must prefer an explicit arg, fall back to the env, and fail loudly."""

from __future__ import annotations

import pytest

from aqueduct import shareurl


def test_get_returns_none_when_unset(monkeypatch):
    monkeypatch.delenv(shareurl.ENV, raising=False)
    assert shareurl.get() is None


def test_get_strips_whitespace(monkeypatch):
    monkeypatch.setenv(shareurl.ENV, "  https://host/share  ")
    assert shareurl.get() == "https://host/share"


def test_resolve_prefers_explicit_arg(monkeypatch):
    monkeypatch.setenv(shareurl.ENV, "https://env/share")
    assert shareurl.resolve("https://arg/share") == "https://arg/share"


def test_resolve_falls_back_to_env(monkeypatch):
    monkeypatch.setenv(shareurl.ENV, "https://env/share")
    assert shareurl.resolve(None) == "https://env/share"


def test_resolve_exits_when_missing(monkeypatch):
    monkeypatch.delenv(shareurl.ENV, raising=False)
    with pytest.raises(SystemExit):
        shareurl.resolve(None)
