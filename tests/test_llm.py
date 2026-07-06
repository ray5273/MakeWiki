import pytest

from makewiki.errors import LLMError
from makewiki.llm import (
    OPENROUTER_DEFAULT_MODELS,
    FallbackLLMClient,
    openrouter_from_env,
)


def test_openrouter_from_env_builds_fallback_chain(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)

    client = openrouter_from_env()

    assert isinstance(client, FallbackLLMClient)
    assert [c.model for c in client.clients] == list(OPENROUTER_DEFAULT_MODELS)


def test_openrouter_from_env_honors_explicit_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)

    client = openrouter_from_env(model="openai/gpt-4o-mini")

    assert client.model == "openai/gpt-4o-mini"


def test_openrouter_from_env_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(LLMError):
        openrouter_from_env()


class _StubLLM:
    def __init__(self, *, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.calls = 0

    def complete(self, *, system, user):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.reply


def test_fallback_uses_primary_when_it_succeeds():
    primary = _StubLLM(reply="- primary")
    backup = _StubLLM(reply="- backup")

    result = FallbackLLMClient([primary, backup]).complete(system="s", user="u")

    assert result == "- primary"
    assert backup.calls == 0


def test_fallback_advances_on_error():
    primary = _StubLLM(error=LLMError("429"))
    backup = _StubLLM(reply="- backup")

    result = FallbackLLMClient([primary, backup]).complete(system="s", user="u")

    assert result == "- backup"
    assert primary.calls == 1
    assert backup.calls == 1


def test_fallback_reraises_last_error_when_all_fail():
    primary = _StubLLM(error=LLMError("first"))
    backup = _StubLLM(error=LLMError("last"))

    with pytest.raises(LLMError, match="last"):
        FallbackLLMClient([primary, backup]).complete(system="s", user="u")


def test_fallback_requires_at_least_one_client():
    with pytest.raises(ValueError):
        FallbackLLMClient([])
