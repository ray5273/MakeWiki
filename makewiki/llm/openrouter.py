from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

from makewiki.errors import LLMError

OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_LOCKED_MODEL = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"


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


def openrouter_from_env(*, model: str | None = None) -> OpenRouterClient:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    requested_model = model or os.environ.get("OPENROUTER_MODEL")
    if not api_key:
        raise LLMError("OPENROUTER_API_KEY is required for --llm openrouter")
    if requested_model and requested_model != OPENROUTER_LOCKED_MODEL:
        raise LLMError(f"OpenRouter model is locked to {OPENROUTER_LOCKED_MODEL}")
    return OpenRouterClient(api_key=api_key, model=OPENROUTER_LOCKED_MODEL)
