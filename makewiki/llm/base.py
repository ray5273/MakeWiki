from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol


class LLMClient(Protocol):
    def complete(self, *, system: str, user: str) -> str:
        ...


@dataclass
class RateLimitedLLMClient:
    client: LLMClient
    requests_per_minute: int
    _last_request_at: float | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        if self.requests_per_minute < 1:
            raise ValueError("requests_per_minute must be >= 1")

    def complete(self, *, system: str, user: str) -> str:
        now = time.monotonic()
        if self._last_request_at is not None:
            interval = 60.0 / self.requests_per_minute
            wait = interval - (now - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
        self._last_request_at = time.monotonic()
        return self.client.complete(system=system, user=user)
