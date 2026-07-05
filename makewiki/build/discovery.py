from __future__ import annotations

from pathlib import Path

from makewiki.config import MakeWikiConfig
from makewiki.errors import BuildDatabaseError


DEFAULT_CANDIDATES = (
    "compile_commands.json",
    "build/compile_commands.json",
    "cmake-build-debug/compile_commands.json",
    "cmake-build-release/compile_commands.json",
)


def discover_compile_commands(config: MakeWikiConfig, *, allow_missing_for_fixture: bool = False) -> Path | None:
    """Find compile_commands.json without executing user build commands."""
    repo_root = config.repo_root
    candidates: list[Path] = []
    if config.build.compile_commands_path:
        candidates.append(repo_root / config.build.compile_commands_path)
    candidates.extend(repo_root / rel for rel in DEFAULT_CANDIDATES)

    seen: set[Path] = set()
    for candidate in candidates:
        path = candidate.resolve()
        if path in seen:
            continue
        seen.add(path)
        if path.exists():
            if not path.is_file():
                raise BuildDatabaseError(f"compile_commands path is not a file: {path}")
            return path

    if allow_missing_for_fixture:
        return None

    if config.build.run_build:
        raise BuildDatabaseError(
            "build.run_build is true, but MakeWiki MVP does not execute build_command. "
            "Provide compile_commands.json or use --analyzer joern."
        )

    raise BuildDatabaseError(
        "compile_commands.json not found. Provide .makewiki/config.json build.compile_commands_path, "
        "generate it with CMake/Bear outside MakeWiki, or use --analyzer joern."
    )
