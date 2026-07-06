from __future__ import annotations

from pathlib import Path


def read_source_snippet(
    repo_root: Path,
    file_path: str,
    start_line: int,
    *,
    end_line: int | None = None,
    max_lines: int = 40,
) -> str:
    """Return a bounded, 1-indexed inclusive slice of a source file.

    Used to ground LLM prose in the actual function body. The slice runs from
    `start_line` to `end_line` (inclusive), capped at `max_lines`. A missing or
    unreadable file yields an empty string so callers can degrade gracefully.
    """

    if start_line < 1:
        return ""
    try:
        text = (Path(repo_root) / file_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""

    lines = text.splitlines()
    last = end_line if end_line is not None and end_line >= start_line else len(lines)
    last = min(last, start_line + max_lines - 1)
    selected = lines[start_line - 1:last]
    return "\n".join(selected).rstrip()
