from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from makewiki.build import discover_compile_commands
from makewiki.config import MakeWikiConfig
from makewiki.errors import AnalyzerUnavailableError, GraphError
from makewiki.graph import (
    CodeEdge,
    CodeFacts,
    CodeGraph,
    GlobalFact,
    IncludeFact,
    MemberFact,
    StructFact,
    SymbolNode,
)

from .base import AnalysisResult


class ClangdAnalyzer:
    name = "clangd"

    def analyze(self, repo_root: Path, config: MakeWikiConfig) -> AnalysisResult:
        if shutil.which("clangd") is None:
            raise AnalyzerUnavailableError(
                "clangd analyzer unavailable: clangd binary not found on PATH. "
                "Install clangd and provide compile_commands.json, or use --analyzer joern."
            )
        discover_compile_commands(config)
        raise AnalyzerUnavailableError("clangd adapter is detected but not implemented in the Phase 1-3 MVP.")


class JoernAnalyzer:
    name = "joern"

    def analyze(self, repo_root: Path, config: MakeWikiConfig) -> AnalysisResult:
        joern = shutil.which("joern")
        joern_parse = shutil.which("joern-parse")
        if joern is None or joern_parse is None:
            raise AnalyzerUnavailableError(
                "Joern analyzer unavailable: joern or joern-parse binary not found on PATH. "
                "Install Joern, or use its Docker workflow externally."
            )

        repo_root = repo_root.resolve()
        with tempfile.TemporaryDirectory(prefix="makewiki-joern-") as tmp:
            tmp_path = Path(tmp)
            cpg_path = tmp_path / "cpg.bin"
            script_path = tmp_path / "dump.sc"
            _run(
                [
                    joern_parse,
                    str(repo_root),
                    "--language",
                    "c",
                    "-o",
                    str(cpg_path),
                ],
                cwd=repo_root,
            )
            script_path.write_text(_dump_script(cpg_path), encoding="utf-8")
            output = _run([joern, "--script", str(script_path)], cwd=repo_root)
        graph = _graph_from_joern_dump(repo_root, output)
        facts = _facts_from_joern_dump(repo_root, output)
        return AnalysisResult(graph=graph, facts=facts)


def _run(argv: list[str], *, cwd: Path) -> str:
    proc = subprocess.run(
        argv,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if proc.returncode != 0:
        raise AnalyzerUnavailableError(f"Joern command failed ({proc.returncode}): {' '.join(argv)}\n{proc.stdout}")
    return proc.stdout


def _dump_script(cpg_path: Path) -> str:
    escaped = str(cpg_path).replace("\\", "\\\\").replace("\"", "\\\"")
    return f"""
importCpg("{escaped}")
cpg.method.filter(m => !m.isExternal && !m.name.startsWith("<") && m.filename != "<empty>").map {{ m =>
  val calls = m.callOut.filter(c => !c.name.startsWith("<operator>")).name.dedup.l
  val payload = ujson.Obj(
    "name" -> m.name,
    "filename" -> m.filename,
    "line" -> m.lineNumber.getOrElse(-1),
    "lineEnd" -> m.lineNumberEnd.getOrElse(-1),
    "signature" -> m.signature,
    "calls" -> calls
  )
  "MAKEWIKI_JSON|" + payload.render()
}}.l.foreach(println)
cpg.typeDecl.filter(t => !t.isExternal && t.filename != "<empty>" && !t.name.startsWith("<")).foreach {{ t =>
  val s = ujson.Obj(
    "kind" -> "struct",
    "name" -> t.name,
    "filename" -> t.filename,
    "line" -> t.lineNumber.getOrElse(-1)
  )
  println("MAKEWIKI_FACT|" + s.render())
  t.member.foreach {{ mem =>
    val isFp = mem.typeFullName.contains("(")
    val mo = ujson.Obj(
      "kind" -> "member",
      "struct" -> t.name,
      "name" -> mem.name,
      "filename" -> t.filename,
      "line" -> mem.lineNumber.getOrElse(t.lineNumber.getOrElse(-1)),
      "func_ptr" -> isFp
    )
    println("MAKEWIKI_FACT|" + mo.render())
  }}
}}
"""


def _graph_from_joern_dump(repo_root: Path, output: str) -> CodeGraph:
    graph = CodeGraph(repo_root=repo_root)
    call_names_by_node: dict[str, list[str]] = {}
    by_name: dict[str, list[SymbolNode]] = {}

    for line in output.splitlines():
        if not line.startswith("MAKEWIKI_JSON|"):
            continue
        raw = line.split("|", 1)[1]
        item = json.loads(raw)
        node = _node_from_item(repo_root, item)
        if node is None:
            continue
        graph.add_node(node)
        by_name.setdefault(node.name, []).append(node)
        calls = item.get("calls", [])
        call_names_by_node[node.id] = [value for value in calls if isinstance(value, str)]

    if not graph.nodes:
        raise GraphError("Joern produced no source methods")

    for nodes in by_name.values():
        nodes.sort(key=lambda n: (n.file_path, n.start_line, n.id))

    for src_id, call_names in call_names_by_node.items():
        src = graph.nodes[src_id]
        for call_name in sorted(set(call_names)):
            dst = _resolve_call(call_name, src.file_path, by_name)
            if dst is not None and dst.id != src.id:
                graph.add_edge(CodeEdge(src_id=src.id, dst_id=dst.id, rel="calls"))
    return graph


def _node_from_item(repo_root: Path, item: dict) -> SymbolNode | None:
    name = item.get("name")
    filename = item.get("filename")
    line = item.get("line")
    line_end = item.get("lineEnd")
    signature = item.get("signature")
    if not isinstance(name, str) or not isinstance(filename, str) or not isinstance(line, int):
        return None
    if not name.strip():
        return None
    if line < 1:
        return None
    rel_path = _rel_path(repo_root, filename)
    if rel_path is None:
        return None
    end_line = line_end if isinstance(line_end, int) and line_end >= line else None
    return SymbolNode(
        id=_node_id(rel_path, name),
        name=name,
        kind="function",
        file_path=rel_path,
        start_line=line,
        end_line=end_line,
        signature=signature if isinstance(signature, str) else None,
    )


def _rel_path(repo_root: Path, filename: object) -> str | None:
    if not isinstance(filename, str) or not filename.strip():
        return None
    source_path = Path(filename)
    if source_path.is_absolute():
        try:
            return source_path.resolve().relative_to(repo_root).as_posix()
        except ValueError:
            return None
    return source_path.as_posix()


def _facts_from_joern_dump(repo_root: Path, output: str) -> CodeFacts:
    """Parse deterministic CodeFacts from a Joern dump.

    Kept as a pure function over the dump string (like _graph_from_joern_dump)
    so signal extraction is testable from recorded dump fixtures without a Joern
    binary. Lines are prefixed `MAKEWIKI_FACT|<json>`; call-graph lines
    (`MAKEWIKI_JSON|`), blanks, and malformed lines are ignored.
    """
    facts = CodeFacts(repo_root=repo_root)
    for line in output.splitlines():
        if not line.startswith("MAKEWIKI_FACT|"):
            continue
        try:
            item = json.loads(line.split("|", 1)[1])
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(item, dict):
            continue
        rel = _rel_path(repo_root, item.get("filename"))
        line_no = item.get("line")
        if rel is None or not isinstance(line_no, int) or line_no < 1:
            continue

        kind = item.get("kind")
        if kind == "struct":
            name = item.get("name")
            if _nonempty(name):
                facts.add_struct(StructFact(name=name, file_path=rel, start_line=line_no))
        elif kind == "member":
            struct = item.get("struct")
            name = item.get("name")
            if _nonempty(struct) and _nonempty(name):
                facts.add_member(
                    MemberFact(
                        struct_name=struct,
                        name=name,
                        file_path=rel,
                        start_line=line_no,
                        is_function_pointer=bool(item.get("func_ptr")),
                    )
                )
        elif kind == "global":
            name = item.get("name")
            if _nonempty(name):
                type_name = item.get("type") if _nonempty(item.get("type")) else None
                facts.add_global(GlobalFact(name=name, file_path=rel, start_line=line_no, type_name=type_name))
        elif kind == "include":
            target = item.get("target")
            if _nonempty(target):
                facts.add_include(IncludeFact(file_path=rel, target=target, start_line=line_no))
        elif kind == "tag":
            name = item.get("name")
            tag = item.get("tag")
            if _nonempty(name) and _nonempty(tag):
                facts.tag_function(_node_id(rel, name), tag)
    return facts


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _resolve_call(call_name: str, src_file_path: str, by_name: dict[str, list[SymbolNode]]) -> SymbolNode | None:
    candidates = by_name.get(call_name)
    if not candidates:
        return None
    same_file = [node for node in candidates if node.file_path == src_file_path]
    if same_file:
        return same_file[0]
    return candidates[0]


def _node_id(file_path: str, name: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in file_path).strip("_")
    return f"{safe}::{name}"
