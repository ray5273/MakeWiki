from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from makewiki.errors import LLMError
from makewiki.llm.base import FallbackLLMClient, LLMClient

OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"

# Ordered free-tier fallback chain. Gemma 4 is a non-reasoning, strong
# instruction follower (clean summaries, no chain-of-thought leaking into the
# output); the non-reasoning nemotron is the backup when Gemma is rate-limited.
# Both are free models, tried per call by FallbackLLMClient.
OPENROUTER_DEFAULT_MODELS = (
    "google/gemma-4-31b-it:free",
    "nvidia/nemotron-3-nano-30b-a3b:free",
)

# Kept as an alias for backwards compatibility with earlier callers/tests that
# imported the single locked-model name.
OPENROUTER_LOCKED_MODEL = OPENROUTER_DEFAULT_MODELS[0]


@dataclass(frozen=True)
class OpenRouterClient:
    api_key: str
    model: str
    base_url: str = OPENROUTER_CHAT_COMPLETIONS_URL
    temperature: float = 0.2
    max_tokens: int = 1600
    timeout_seconds: int = 60

    def complete(self, *, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "include_reasoning": False,
        }
        request = urllib.request.Request(
            self.base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://makewiki.local",
                "X-Title": "MakeWiki",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise LLMError(f"OpenRouter request failed with HTTP {exc.code}: {body[:500]}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"OpenRouter request failed: {exc.reason}") from exc

        try:
            data = json.loads(raw)
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LLMError("OpenRouter response did not contain choices[0].message.content") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMError("OpenRouter returned an empty completion")
        return content.strip()


def openrouter_from_env(*, model: str | None = None) -> LLMClient:
    """Build an OpenRouter client from the environment.

    With no explicit model, returns a FallbackLLMClient over the default
    free-tier chain (Gemma 4 first, non-reasoning nemotron as backup). An
    explicit `model` (or `OPENROUTER_MODEL`) pins a single model instead.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    requested_model = model or os.environ.get("OPENROUTER_MODEL")
    if not api_key:
        raise LLMError("OPENROUTER_API_KEY is required for --llm openrouter")
    if requested_model:
        return OpenRouterClient(api_key=api_key, model=requested_model)
    clients: list[LLMClient] = [
        OpenRouterClient(api_key=api_key, model=name) for name in OPENROUTER_DEFAULT_MODELS
    ]
    return FallbackLLMClient(clients)
