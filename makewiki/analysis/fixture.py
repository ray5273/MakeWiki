from __future__ import annotations

import re
from pathlib import Path

from makewiki.build import discover_compile_commands, source_files_from_compile_commands
from makewiki.config import MakeWikiConfig
from makewiki.graph import CodeEdge, CodeFacts, CodeGraph, SymbolNode

from .base import AnalysisResult

CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
CONTROL_WORDS = {
    "alignof",
    "catch",
    "defined",
    "do",
    "for",
    "if",
    "return",
    "sizeof",
    "switch",
    "throw",
    "while",
}
FUNC_NAME_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}
DEFAULT_EXCLUDE_DIRS = {
    ".git",
    ".makewiki",
    "build",
    "cmake-build-debug",
    "cmake-build-release",
    "node_modules",
    "test",
    "tests",
    "unittest",
}
MACRO_LIKE_FUNCTIONS = {
    "SPDK_LOG_REGISTER_COMPONENT",
}


class FixtureAnalyzer:
    """Deterministic regex C/C++ analyzer used for tests and the tiny demo.

    This is a conservative source scanner that produces a functions+calls
    CodeGraph without any external tools. It is NOT a product analyzer: it does
    not resolve structs, globals, macros, or function-pointer fields and cannot
    feed the importance-lens signal layer. Use Joern for real analysis. This
    class exists so the pipeline (graph -> render -> wiki) stays testable on the
    small `tests/fixtures/tiny_c` fixture without a Joern binary.
    """

    name = "fixture"

    def analyze(self, repo_root: Path, config: MakeWikiConfig) -> AnalysisResult:
        graph = CodeGraph(repo_root=repo_root.resolve())
        files = _source_files(repo_root, config)
        functions: list[tuple[str, str, Path, int, int, str]] = []
        file_lines: dict[Path, list[str]] = {}

        for path in files:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            file_lines[path] = lines
            idx = 0
            while idx < len(lines):
                match = _match_function_start(lines, idx)
                if match is None:
                    if "{" in lines[idx] and not lines[idx].lstrip().startswith("#"):
                        idx = _find_function_end(lines, idx) + 1
                        continue
                    idx += 1
                    continue
                name, signature = match
                end = _find_function_end(lines, idx)
                rel_path = path.relative_to(repo_root).as_posix()
                node_id = _node_id(rel_path, name)
                graph.add_node(
                    SymbolNode(
                        id=node_id,
                        name=name,
                        kind="function",
                        file_path=rel_path,
                        start_line=idx + 1,
                        end_line=end + 1,
                        signature=signature,
                    )
                )
                functions.append((name, node_id, path, idx + 1, end + 1, signature))
                idx = end + 1

        by_name: dict[str, list[SymbolNode]] = {}
        for node in graph.nodes.values():
            by_name.setdefault(node.name, []).append(node)
        for nodes in by_name.values():
            nodes.sort(key=lambda n: (n.file_path, n.start_line, n.id))

        for _name, node_id, path, start, end, _signature in sorted(functions):
            src = graph.nodes[node_id]
            body = "\n".join(file_lines[path][start:end])
            for called in sorted(set(CALL_RE.findall(body)) - CONTROL_WORDS):
                dst = _resolve_call(called, src.file_path, by_name)
                if dst is not None and dst.id != src.id:
                    graph.add_edge(CodeEdge(src_id=src.id, dst_id=dst.id, rel="calls"))

        return AnalysisResult(graph=graph, facts=CodeFacts(repo_root=repo_root.resolve()))


def _source_files(repo_root: Path, config: MakeWikiConfig) -> list[Path]:
    excluded = _normalized_excludes(config.build.exclude_dirs)
    compile_db = discover_compile_commands(config, allow_missing_for_fixture=True)
    if compile_db is not None:
        compiled = source_files_from_compile_commands(
            compile_db,
            repo_root,
            suffixes=SOURCE_SUFFIXES,
            exclude_dirs=excluded,
        )
        if compiled:
            return list(compiled)

    files: list[Path] = []
    for path in repo_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        rel = path.relative_to(repo_root).as_posix()
        if _is_excluded(rel, excluded):
            continue
        files.append(path)
    return sorted(files)


def _normalized_excludes(exclude_dirs: tuple[str, ...]) -> tuple[str, ...]:
    configured = {part.strip("/") for part in exclude_dirs if part.strip("/")}
    return tuple(sorted(DEFAULT_EXCLUDE_DIRS | configured))


def _is_excluded(rel_path: str, excluded: tuple[str, ...]) -> bool:
    parts = rel_path.split("/")
    if any(part in DEFAULT_EXCLUDE_DIRS for part in parts[:-1]):
        return True
    return any(rel_path == prefix or rel_path.startswith(prefix + "/") for prefix in excluded)


def _find_function_end(lines: list[str], start_idx: int) -> int:
    depth = 0
    seen_open = False
    for idx in range(start_idx, len(lines)):
        for char in lines[idx]:
            if char == "{":
                depth += 1
                seen_open = True
            elif char == "}":
                depth -= 1
                if seen_open and depth == 0:
                    return idx
    return len(lines) - 1


def _match_function_start(lines: list[str], start_idx: int) -> tuple[str, str] | None:
    parts: list[str] = []
    idx = start_idx
    paren_depth = 0

    while idx < len(lines):
        raw = lines[idx]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("//"):
            return None
        parts.append(stripped)

        for char in stripped:
            if char == "(":
                paren_depth += 1
            elif char == ")":
                paren_depth -= 1

        before_brace = stripped.split("{", 1)[0]
        if ";" in before_brace:
            return None
        if "{" in stripped:
            if paren_depth != 0:
                return None
            signature = " ".join(parts).split("{", 1)[0].strip()
            return _extract_function_name(signature)
        if stripped.endswith("="):
            return None
        idx += 1
        if idx - start_idx > 20:
            return None
    return None


def _extract_function_name(signature: str) -> tuple[str, str] | None:
    normalized = " ".join(signature.replace("*", " * ").split())
    matches = [match.group(1) for match in FUNC_NAME_RE.finditer(normalized)]
    matches = [name for name in matches if name not in CONTROL_WORDS]
    if not matches:
        return None
    name = matches[0]
    if name in MACRO_LIKE_FUNCTIONS:
        return None
    return name, normalized


def _resolve_call(
    called: str,
    src_file_path: str,
    by_name: dict[str, list[SymbolNode]],
) -> SymbolNode | None:
    candidates = by_name.get(called)
    if not candidates:
        return None
    same_file = [node for node in candidates if node.file_path == src_file_path]
    if same_file:
        return same_file[0]
    non_test = [node for node in candidates if not _is_test_path(node.file_path)]
    if non_test:
        return non_test[0]
    return candidates[0]


def _is_test_path(file_path: str) -> bool:
    return any(part in {"test", "tests", "unittest"} for part in file_path.split("/"))


def _node_id(file_path: str, name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", file_path).strip("_")
    return f"{safe}::{name}"
