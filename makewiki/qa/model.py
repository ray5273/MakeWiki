from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class EvidenceCitation:
    symbol_id: str
    symbol_name: str
    file_path: str
    start_line: int

    def label(self) -> str:
        return f"{self.symbol_name} at {self.file_path}:{self.start_line}"


@dataclass(frozen=True)
class QAResult:
    question: str
    answer: str
    evidence: list[EvidenceCitation] = field(default_factory=list)
    involved_symbols: list[str] = field(default_factory=list)
    mermaid: str | None = None
    confidence: str = "high"
    fallback_reason: str | None = None
    kind: str = "unknown"

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["evidence"] = [asdict(item) for item in self.evidence]
        return data
