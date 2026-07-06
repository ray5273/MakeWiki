"""Deterministic C/C++ struct extraction for the data-model wiki page.

The call graph only knows about functions, so struct/data-model documentation
(a strength of hand-written wikis) needs a separate, source-parsed pass. This
module scans the same trees as the doc-comment index and pulls out struct/union
definitions with their fields, grounded by `file:line`. It is intentionally a
lightweight brace scanner, not a full C parser: it handles the common
`struct name { ... };` and `typedef struct [tag] { ... } name;` shapes and skips
what it cannot parse cleanly rather than guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from makewiki.analysis.docs import _HEADER_EXTS, _SOURCE_EXTS, _iter_source_files


@dataclass(frozen=True)
class StructField:
    type: str
    name: str


@dataclass(frozen=True)
class StructDef:
    name: str
    file_path: str
    start_line: int
    end_line: int
    fields: tuple[StructField, ...] = ()


_STRUCT_OPEN_RE = re.compile(r"\b(?:struct|union)\b(?:\s+([A-Za-z_]\w*))?\s*\{")
_TYPEDEF_NAME_RE = re.compile(r"\}\s*([A-Za-z_]\w*)\s*;")
_LINE_COMMENT_RE = re.compile(r"//.*$", re.MULTILINE)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
# One struct field: a type (possibly with * and qualifiers) then the member
# name, then ; — e.g. "struct spdk_thread *thread;" or "uint32_t lcore;".
_FIELD_RE = re.compile(r"^\s*([A-Za-z_][\w\s\*]*?[\s\*])([A-Za-z_]\w*)\s*(?:\[[^\]]*\])?\s*;")


def extract_structs(source: str) -> list[StructDef]:
    """Parse struct/union definitions with fields from one C/C++ source string."""
    cleaned = _BLOCK_COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), source)
    cleaned = _LINE_COMMENT_RE.sub("", cleaned)

    structs: list[StructDef] = []
    for match in _STRUCT_OPEN_RE.finditer(cleaned):
        tag = match.group(1)
        body_start = match.end()  # just after the '{'
        body_end = _matching_brace(cleaned, body_start)
        if body_end is None:
            continue
        body = cleaned[body_start:body_end]
        # Anonymous struct with a nested brace inside its body is skipped as a
        # field parse (best-effort); we still record the outer struct.
        typedef_name = None
        after = cleaned[body_end + 1 : body_end + 80]
        typedef_match = _TYPEDEF_NAME_RE.match("}" + after)
        if typedef_match:
            typedef_name = typedef_match.group(1)
        name = tag or typedef_name
        if not name:
            continue
        start_line = cleaned.count("\n", 0, match.start()) + 1
        end_line = cleaned.count("\n", 0, body_end) + 1
        structs.append(
            StructDef(
                name=name,
                file_path="",  # filled in by build_struct_index
                start_line=start_line,
                end_line=end_line,
                fields=tuple(_parse_fields(body)),
            )
        )
    return structs


def _matching_brace(text: str, open_index: int) -> int | None:
    """Index of the '}' that closes the '{' preceding `open_index`."""
    depth = 1
    i = open_index
    while i < len(text):
        char = text[i]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _parse_fields(body: str) -> list[StructField]:
    fields: list[StructField] = []
    depth = 0
    for raw_line in body.splitlines():
        depth += raw_line.count("{") - raw_line.count("}")
        if depth > 0 or "{" in raw_line or "}" in raw_line:
            continue  # inside a nested/anonymous struct or union
        match = _FIELD_RE.match(raw_line)
        if not match:
            continue
        type_text = " ".join(match.group(1).split())
        name = match.group(2)
        if type_text in {"return", "typedef"}:
            continue
        fields.append(StructField(type=type_text, name=name))
    return fields


def build_struct_index(repo_root: Path, extra_roots: object = ()) -> dict[str, StructDef]:
    """Index struct definitions by name across the repo and extra header roots.

    Mirrors build_doc_index: sources first, then headers, so a public header
    definition wins over an implementation-side one. `file_path` is shown
    relative to the repo root for in-tree files and relative to a doc root's
    parent for header trees, so `include/...` paths read naturally.
    """
    repo_root = Path(repo_root)
    roots = [(repo_root, repo_root)] + [
        (Path(root), Path(root).parent) for root in extra_roots  # type: ignore[union-attr]
    ]
    index: dict[str, StructDef] = {}
    for exts in (_SOURCE_EXTS, _HEADER_EXTS):
        for root, display_base in roots:
            for path in _iter_source_files(root, exts):
                try:
                    source = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                try:
                    display = path.relative_to(display_base).as_posix()
                except ValueError:
                    display = path.name
                for struct in extract_structs(source):
                    if not struct.fields:
                        continue  # skip forward declarations / opaque shapes
                    index[struct.name] = StructDef(
                        name=struct.name,
                        file_path=display,
                        start_line=struct.start_line,
                        end_line=struct.end_line,
                        fields=struct.fields,
                    )
    return index
