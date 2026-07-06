from __future__ import annotations

import re
import os
from dataclasses import dataclass
from pathlib import Path

from makewiki.errors import WikiValidationError
from makewiki.graph import CodeGraph

CODE_CITATION_RE = re.compile(r"`([^`\n]+):(\d+)(?:-(\d+))?`")
MARKDOWN_LINK_RE = re.compile(r"\[[^\]\n]+\]\(([^)\n]+)\)")


@dataclass(frozen=True)
class WikiValidationIssue:
    page: Path
    message: str


def validate_wiki(
    graph: CodeGraph,
    wiki_dir: str | Path,
    *,
    raise_on_error: bool = True,
) -> list[WikiValidationIssue]:
    wiki_root = Path(wiki_dir)
    issues: list[WikiValidationIssue] = []

    if not wiki_root.exists() or not wiki_root.is_dir():
        issues.append(WikiValidationIssue(wiki_root, "wiki directory does not exist"))
        return _finish(issues, raise_on_error)

    graph.validate()
    known_citations = _known_citations(graph)
    line_counts: dict[Path, int] = {}
    source_file_exists: dict[Path, bool] = {}
    pages = sorted(wiki_root.rglob("*.md"))
    if not pages:
        issues.append(WikiValidationIssue(wiki_root, "wiki contains no Markdown pages"))
        return _finish(issues, raise_on_error)
    normalized_wiki_root = _normalized_absolute(wiki_root)
    known_pages = {_normalized_absolute(page) for page in pages}

    for page in pages:
        text = page.read_text(encoding="utf-8")
        citations = list(CODE_CITATION_RE.finditer(text))
        # data-model.md documents structs (parsed from source, not graph
        # symbols), so it carries no graph-backed citations by design.
        if page.name not in {"index.md", "data-model.md"} and not citations:
            issues.append(WikiValidationIssue(page, "page has no file:line evidence citations"))

        for match in citations:
            file_path = match.group(1)
            start_line = int(match.group(2))
            end_line = int(match.group(3) or match.group(2))
            _validate_citation(
                graph,
                known_citations,
                page,
                file_path,
                start_line,
                end_line,
                issues,
                line_counts,
                source_file_exists,
            )

        for match in MARKDOWN_LINK_RE.finditer(text):
            target = match.group(1)
            _validate_link(normalized_wiki_root, known_pages, page, target, issues)

    return _finish(issues, raise_on_error)


def _finish(issues: list[WikiValidationIssue], raise_on_error: bool) -> list[WikiValidationIssue]:
    if issues and raise_on_error:
        details = "\n".join(f"{issue.page}: {issue.message}" for issue in issues[:20])
        suffix = "" if len(issues) <= 20 else f"\n... {len(issues) - 20} more issue(s)"
        raise WikiValidationError(f"wiki validation failed with {len(issues)} issue(s):\n{details}{suffix}")
    return issues


def _known_citations(graph: CodeGraph) -> set[tuple[str, int]]:
    return {(node.file_path, node.start_line) for node in graph.nodes.values()}


def _validate_citation(
    graph: CodeGraph,
    known_citations: set[tuple[str, int]],
    page: Path,
    file_path: str,
    start_line: int,
    end_line: int,
    issues: list[WikiValidationIssue],
    line_counts: dict[Path, int],
    source_file_exists: dict[Path, bool],
) -> None:
    if start_line < 1 or end_line < start_line:
        issues.append(WikiValidationIssue(page, f"invalid citation range: {file_path}:{start_line}-{end_line}"))
        return

    source_path = graph.repo_root / file_path
    exists = source_file_exists.get(source_path)
    if exists is None:
        exists = source_path.exists() and source_path.is_file()
        source_file_exists[source_path] = exists
    if not exists:
        issues.append(WikiValidationIssue(page, f"citation target does not exist: {file_path}"))
        return

    line_count = line_counts.get(source_path)
    if line_count is None:
        line_count = _line_count(source_path)
        line_counts[source_path] = line_count
    if end_line > line_count:
        issues.append(
            WikiValidationIssue(
                page,
                f"citation line is outside file: {file_path}:{end_line} > {line_count}",
            )
        )
        return

    if (file_path, start_line) not in known_citations:
        issues.append(WikiValidationIssue(page, f"citation is not backed by a graph symbol: {file_path}:{start_line}"))


def _validate_link(
    normalized_wiki_root: Path,
    known_pages: set[Path],
    page: Path,
    target: str,
    issues: list[WikiValidationIssue],
) -> None:
    if target.startswith(("http://", "https://", "mailto:", "#")):
        return
    target_path = target.split("#", 1)[0]
    if not target_path:
        return
    if Path(target_path).is_absolute():
        issues.append(WikiValidationIssue(page, f"wiki link must be relative: {target}"))
        return
    normalized = _normalized_absolute(page.parent / target_path)
    if normalized_wiki_root != normalized and normalized_wiki_root not in normalized.parents:
        issues.append(WikiValidationIssue(page, f"wiki link escapes output directory: {target}"))
        return
    if normalized not in known_pages and not normalized.exists():
        issues.append(WikiValidationIssue(page, f"wiki link target does not exist: {target}"))


def _line_count(path: Path) -> int:
    content = path.read_bytes()
    return content.count(b"\n") + (1 if content and not content.endswith(b"\n") else 0)


def _normalized_absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))
