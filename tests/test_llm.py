from makewiki.errors import LLMError
from makewiki.llm import OPENROUTER_LOCKED_MODEL, openrouter_from_env


def test_openrouter_from_env_uses_locked_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)

    client = openrouter_from_env()

    assert client.model == OPENROUTER_LOCKED_MODEL


def test_openrouter_from_env_rejects_non_locked_env_model(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")

    try:
        openrouter_from_env()
    except LLMError as exc:
        assert OPENROUTER_LOCKED_MODEL in str(exc)
    else:
        raise AssertionError("expected LLMError")
