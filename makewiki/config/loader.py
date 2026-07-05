from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from makewiki.config.model import (
    ALLOWED_DIAGRAM_TYPES,
    BuildConfig,
    DiagramConfig,
    MakeWikiConfig,
    RepoNote,
)
from makewiki.errors import ConfigError

MAX_DIAGRAMS = 40
MAX_NOTES = 100
MAX_NOTE_CHARS = 10_000
MAX_DEPTH = 20


def load_config(repo: str | Path) -> MakeWikiConfig:
    repo_root = Path(repo).resolve()
    config_path = repo_root / ".makewiki" / "config.json"
    if not config_path.exists():
        return MakeWikiConfig(repo_root=repo_root)

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {config_path}: {exc.msg}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(".makewiki/config.json must contain a JSON object")
    return validate_config(raw, repo_root=repo_root)


def validate_config(raw: dict[str, Any], repo_root: str | Path) -> MakeWikiConfig:
    repo_root = Path(repo_root).resolve()
    build = _parse_build(raw.get("build", {}))
    notes = _parse_notes(raw.get("repo_notes", []))
    diagrams = _parse_diagrams(raw.get("diagrams", []))
    return MakeWikiConfig(repo_root=repo_root, build=build, repo_notes=notes, diagrams=diagrams)


def _parse_build(raw: Any) -> BuildConfig:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError("build must be an object")

    compile_commands_path = _optional_str(raw, "compile_commands_path")
    build_command = _optional_str(raw, "build_command")
    run_build = raw.get("run_build", False)
    if not isinstance(run_build, bool):
        raise ConfigError("build.run_build must be a boolean")

    exclude_dirs_raw = raw.get("exclude_dirs", [])
    if not isinstance(exclude_dirs_raw, list) or not all(isinstance(v, str) for v in exclude_dirs_raw):
        raise ConfigError("build.exclude_dirs must be a list of strings")

    return BuildConfig(
        compile_commands_path=compile_commands_path,
        build_command=build_command,
        run_build=run_build,
        exclude_dirs=tuple(exclude_dirs_raw),
    )


def _parse_notes(raw: Any) -> tuple[RepoNote, ...]:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ConfigError("repo_notes must be a list")
    if len(raw) > MAX_NOTES:
        raise ConfigError(f"repo_notes cannot contain more than {MAX_NOTES} entries")

    notes: list[RepoNote] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ConfigError(f"repo_notes[{idx}] must be an object")
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ConfigError(f"repo_notes[{idx}].content must be a non-empty string")
        if len(content) > MAX_NOTE_CHARS:
            raise ConfigError(f"repo_notes[{idx}].content cannot exceed {MAX_NOTE_CHARS} characters")
        author = item.get("author")
        if author is not None and not isinstance(author, str):
            raise ConfigError(f"repo_notes[{idx}].author must be a string")
        notes.append(RepoNote(content=content, author=author))
    return tuple(notes)


def _parse_diagrams(raw: Any) -> tuple[DiagramConfig, ...]:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ConfigError("diagrams must be a list")
    if len(raw) > MAX_DIAGRAMS:
        raise ConfigError(f"diagrams cannot contain more than {MAX_DIAGRAMS} entries")

    titles: set[str] = set()
    diagrams: list[DiagramConfig] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ConfigError(f"diagrams[{idx}] must be an object")

        title = item.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ConfigError(f"diagrams[{idx}].title must be a non-empty string")
        if title in titles:
            raise ConfigError(f"diagram title must be unique: {title}")
        titles.add(title)

        diagram_type = item.get("diagram_type", "callgraph")
        if diagram_type not in ALLOWED_DIAGRAM_TYPES:
            allowed = ", ".join(sorted(ALLOWED_DIAGRAM_TYPES))
            raise ConfigError(f"diagrams[{idx}].diagram_type must be one of: {allowed}")

        max_depth = item.get("max_depth", 3)
        if not isinstance(max_depth, int) or isinstance(max_depth, bool) or not 0 <= max_depth <= MAX_DEPTH:
            raise ConfigError(f"diagrams[{idx}].max_depth must be an integer between 0 and {MAX_DEPTH}")

        diagrams.append(
            DiagramConfig(
                title=title,
                purpose=_optional_str(item, "purpose"),
                root_function=_optional_str(item, "root_function"),
                diagram_type=diagram_type,
                max_depth=max_depth,
                parent=_optional_str(item, "parent"),
            )
        )
    return tuple(diagrams)


def _optional_str(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{key} must be a string")
    return value

