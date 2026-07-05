from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


ALLOWED_DIAGRAM_TYPES = {"callgraph", "sequence", "cfg"}


@dataclass(frozen=True)
class BuildConfig:
    compile_commands_path: str | None = None
    build_command: str | None = None
    run_build: bool = False
    exclude_dirs: tuple[str, ...] = ()


@dataclass(frozen=True)
class RepoNote:
    content: str
    author: str | None = None


@dataclass(frozen=True)
class DiagramConfig:
    title: str
    purpose: str | None = None
    root_function: str | None = None
    diagram_type: str = "callgraph"
    max_depth: int = 3
    parent: str | None = None


@dataclass(frozen=True)
class MakeWikiConfig:
    repo_root: Path
    build: BuildConfig = field(default_factory=BuildConfig)
    repo_notes: tuple[RepoNote, ...] = ()
    diagrams: tuple[DiagramConfig, ...] = ()

