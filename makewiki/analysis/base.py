from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from makewiki.config import MakeWikiConfig
from makewiki.graph import CodeFacts, CodeGraph


@dataclass(frozen=True)
class AnalysisResult:
    """What an analyzer produces in a single pass over a repo.

    `graph` is the function call/CFG graph. `facts` holds the non-call
    structural signals (structs, globals, members, includes, function tags)
    that the importance lenses score against. They travel together so an
    analyzer runs once (e.g. one Joern CPG build) and callers persist both.
    """

    graph: CodeGraph
    facts: CodeFacts


class Analyzer(Protocol):
    name: str

    def analyze(self, repo_root: Path, config: MakeWikiConfig) -> AnalysisResult:
        ...
