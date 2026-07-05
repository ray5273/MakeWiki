from __future__ import annotations

import re
from pathlib import Path

from makewiki.graph import DocComment

# Header extensions carry the public/documented API; source extensions carry
# implementation and static-function docs. Headers win on name collisions.
_HEADER_EXTS = (".h", ".hpp", ".hh", ".hxx")
_SOURCE_EXTS = (".c", ".cc", ".cpp", ".cxx")
_IGNORED_DIRS = frozenset({".git", ".hg", ".svn", "node_modules", ".makewiki"})

# Doxygen documentation blocks use the two-star opener `/** ... */`. A single-star
# `/* ... */` block is an ordinary comment and is deliberately ignored.
_DOC_BLOCK_RE = re.compile(r"/\*\*(.*?)\*/", re.DOTALL)
# The declared symbol is the identifier immediately preceding the first `(` of the
# declaration that follows the block.
_DECL_NAME_RE = re.compile(r"(\w+)\s*\(")
# Function-pointer typedefs name the symbol inside `(*name)`, e.g.
# `typedef void (*spdk_event_fn)(void *arg)`.
_FN_PTR_NAME_RE = re.compile(r"\(\s*\*\s*(\w+)\s*\)\s*\(")
# C type/qualifier keywords that can sit before `(` (a return type on a function
# pointer typedef) but are never the declared symbol name.
_TYPE_KEYWORDS = frozenset(
    {"void", "int", "char", "short", "long", "float", "double", "unsigned",
     "signed", "const", "volatile", "struct", "union", "enum", "static", "inline"}
)


def extract_doc_comments(source: str) -> list[DocComment]:
    """Extract Doxygen `/** */` doc comments and attach each to the symbol it documents.

    Pure function over source text so it is testable without a repo or analyzer.
    Only blocks immediately followed by a declaration from which a symbol name can
    be recovered are returned; summary-less blocks are skipped.
    """

    docs: list[DocComment] = []
    for match in _DOC_BLOCK_RE.finditer(source):
        name = _declared_name(source[match.end():])
        if name is None:
            continue
        summary, params, returns = _parse_body(match.group(1))
        if not summary:
            continue
        docs.append(DocComment(symbol_name=name, summary=summary, params=params, returns=returns))
    return docs


def build_doc_index(repo_root: Path, extra_roots: object = ()) -> dict[str, DocComment]:
    """Walk the repo's C/C++ sources and index doc comments by symbol name.

    `extra_roots` supplies additional trees to scan (e.g. a project's `include/`
    directory), because a public API's doc comment often lives in a header
    outside the analyzed source subtree.

    Headers are parsed after sources so a public-API doc comment wins over an
    implementation-side one for the same name. Roots are scanned in order, files
    in sorted order, for deterministic tie-breaking.
    """

    roots = [Path(repo_root), *(Path(root) for root in extra_roots)]
    index: dict[str, DocComment] = {}
    # Sources first, then headers, so header docs overwrite source docs.
    for exts in (_SOURCE_EXTS, _HEADER_EXTS):
        for root in roots:
            for path in _iter_source_files(root, exts):
                try:
                    source = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                for doc in extract_doc_comments(source):
                    index[doc.symbol_name] = doc
    return index


def _iter_source_files(repo_root: Path, exts: tuple[str, ...]):
    for path in sorted(repo_root.rglob("*")):
        if path.suffix not in exts or not path.is_file():
            continue
        if any(part in _IGNORED_DIRS for part in path.relative_to(repo_root).parts):
            continue
        yield path


def _declared_name(following: str) -> str | None:
    declaration = re.split(r"[{;]", following, maxsplit=1)[0]
    fn_ptr = _FN_PTR_NAME_RE.search(declaration)
    if fn_ptr is not None:
        return fn_ptr.group(1)
    match = _DECL_NAME_RE.search(declaration)
    if match is None or match.group(1) in _TYPE_KEYWORDS:
        return None
    return match.group(1)


def _parse_body(body: str) -> tuple[str, tuple[tuple[str, str], ...], str | None]:
    summary_parts: list[str] = []
    params: list[list[str]] = []  # [name, desc]; desc grows with continuation lines
    returns_parts: list[str] | None = None
    # Where a wrapped continuation line appends: the current param's desc, the
    # return description, or nothing (still in the summary).
    sink: list[str] | None = None
    for raw in body.splitlines():
        line = raw.strip().lstrip("*").strip()
        if not line:
            continue
        if line.startswith(("\\param", "@param")):
            rest = line.split(None, 1)[1] if len(line.split(None, 1)) > 1 else ""
            name, _, desc = rest.partition(" ")
            if name:
                param = [name, desc.strip()]
                params.append(param)
                sink = param
        elif line.startswith(("\\return", "@return")):
            returns_parts = [line.split(None, 1)[1].strip() if len(line.split(None, 1)) > 1 else ""]
            sink = returns_parts
        elif sink is not None:
            sink[-1] = f"{sink[-1]} {line}".strip()
        else:
            summary_parts.append(line)
    resolved_params = tuple((name, desc) for name, desc in params)
    returns = " ".join(returns_parts).strip() if returns_parts is not None else None
    return " ".join(summary_parts), resolved_params, returns
