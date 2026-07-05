from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from makewiki.errors import BuildDatabaseError


@dataclass(frozen=True)
class CompileCommand:
    directory: Path
    file: Path
    arguments: tuple[str, ...] = ()
    command: str | None = None


def load_compile_commands(path: str | Path) -> tuple[CompileCommand, ...]:
    db_path = Path(path)
    try:
        raw = json.loads(db_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BuildDatabaseError(f"Invalid JSON in compile_commands.json: {exc.msg}") from exc

    if not isinstance(raw, list):
        raise BuildDatabaseError("compile_commands.json must contain a JSON array")

    entries: list[CompileCommand] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise BuildDatabaseError(f"compile_commands[{idx}] must be an object")
        entries.append(_parse_entry(item, idx, db_path.parent))
    return tuple(entries)


def source_files_from_compile_commands(
    path: str | Path,
    repo_root: str | Path,
    *,
    suffixes: set[str],
    exclude_dirs: tuple[str, ...] = (),
) -> tuple[Path, ...]:
    root = _absolute_path(Path(repo_root))
    excluded = tuple(part.strip("/") for part in exclude_dirs if part.strip("/"))
    files: set[Path] = set()
    for entry in load_compile_commands(path):
        source = entry.file
        if not source.exists() or source.suffix.lower() not in suffixes:
            continue
        try:
            rel = source.relative_to(root).as_posix()
        except ValueError:
            continue
        if any(rel == prefix or rel.startswith(prefix + "/") for prefix in excluded):
            continue
        files.add(source)
    return tuple(sorted(files))


def _parse_entry(item: dict[str, Any], idx: int, db_dir: Path) -> CompileCommand:
    directory_raw = item.get("directory", str(db_dir))
    file_raw = item.get("file")
    if not isinstance(directory_raw, str) or not directory_raw.strip():
        raise BuildDatabaseError(f"compile_commands[{idx}].directory must be a non-empty string")
    if not isinstance(file_raw, str) or not file_raw.strip():
        raise BuildDatabaseError(f"compile_commands[{idx}].file must be a non-empty string")

    directory = Path(directory_raw)
    if not directory.is_absolute():
        directory = db_dir / directory
    directory = _absolute_path(directory)

    file_path = Path(file_raw)
    if not file_path.is_absolute():
        file_path = directory / file_path
    file_path = _absolute_path(file_path)

    arguments_raw = item.get("arguments", ())
    command_raw = item.get("command")
    if arguments_raw != () and (
        not isinstance(arguments_raw, list)
        or not all(isinstance(value, str) for value in arguments_raw)
    ):
        raise BuildDatabaseError(f"compile_commands[{idx}].arguments must be a list of strings")
    if command_raw is not None and not isinstance(command_raw, str):
        raise BuildDatabaseError(f"compile_commands[{idx}].command must be a string")
    if not arguments_raw and command_raw is None:
        raise BuildDatabaseError(f"compile_commands[{idx}] must contain arguments or command")

    return CompileCommand(
        directory=directory,
        file=file_path,
        arguments=tuple(arguments_raw),
        command=command_raw,
    )


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.path.normpath(path)))
