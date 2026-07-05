from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from makewiki.graph.facts import (
    CodeFacts,
    GlobalFact,
    IncludeFact,
    MemberFact,
    StructFact,
)
from makewiki.graph.model import CodeEdge, CodeGraph, SymbolNode

# Bump when the on-disk schema changes. v1 = graph-only (symbols/edges/files).
# v2 = adds CodeFacts tables (structs/members/globals/includes/function tags).
SCHEMA_VERSION = 2


class GraphStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    @property
    def schema_version(self) -> int:
        return int(self.conn.execute("PRAGMA user_version").fetchone()[0])

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "GraphStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def save_graph(self, graph: CodeGraph, *, repo_id: str = "default") -> None:
        graph.validate()
        with self.conn:
            self.conn.execute("DELETE FROM code_edges WHERE repo_id = ?", (repo_id,))
            self.conn.execute("DELETE FROM code_symbols WHERE repo_id = ?", (repo_id,))
            self.conn.execute("DELETE FROM files WHERE repo_id = ?", (repo_id,))

            file_ids: dict[str, str] = {}
            for node in sorted(graph.nodes.values()):
                file_ids.setdefault(node.file_path, _stable_file_id(node.file_path))

            for file_path, file_id in sorted(file_ids.items()):
                abs_path = graph.repo_root / file_path
                content = abs_path.read_bytes() if abs_path.exists() else b""
                language = _language_for(file_path)
                loc = content.count(b"\n") + (1 if content and not content.endswith(b"\n") else 0)
                self.conn.execute(
                    """
                    INSERT INTO files(id, repo_id, path, language, sha256, loc)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (file_id, repo_id, file_path, language, hashlib.sha256(content).hexdigest(), loc),
                )

            for node in sorted(graph.nodes.values()):
                self.conn.execute(
                    """
                    INSERT INTO code_symbols(id, repo_id, file_id, name, kind, signature, start_line, end_line)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        node.id,
                        repo_id,
                        file_ids[node.file_path],
                        node.name,
                        node.kind,
                        node.signature,
                        node.start_line,
                        node.end_line,
                    ),
                )

            for idx, edge in enumerate(sorted(graph.edges), start=1):
                self.conn.execute(
                    """
                    INSERT INTO code_edges(id, repo_id, src_id, dst_id, rel)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (idx, repo_id, edge.src_id, edge.dst_id, edge.rel),
                )

    def load_graph(self, repo_root: str | Path, *, repo_id: str = "default") -> CodeGraph:
        graph = CodeGraph(repo_root=Path(repo_root).resolve())
        rows = self.conn.execute(
            """
            SELECT s.*, f.path AS file_path
            FROM code_symbols s
            JOIN files f ON f.id = s.file_id AND f.repo_id = s.repo_id
            WHERE s.repo_id = ?
            ORDER BY f.path, s.start_line, s.id
            """,
            (repo_id,),
        ).fetchall()
        for row in rows:
            graph.add_node(
                SymbolNode(
                    id=row["id"],
                    name=row["name"],
                    kind=row["kind"],
                    file_path=row["file_path"],
                    start_line=row["start_line"],
                    end_line=row["end_line"],
                    signature=row["signature"],
                )
            )

        edges = self.conn.execute(
            "SELECT src_id, dst_id, rel FROM code_edges WHERE repo_id = ? ORDER BY id",
            (repo_id,),
        ).fetchall()
        for row in edges:
            graph.add_edge(CodeEdge(src_id=row["src_id"], dst_id=row["dst_id"], rel=row["rel"]))
        return graph

    def save_facts(self, facts: CodeFacts, *, repo_id: str = "default") -> None:
        facts.validate()
        with self.conn:
            for table in ("code_structs", "code_members", "code_globals", "code_includes", "code_function_tags"):
                self.conn.execute(f"DELETE FROM {table} WHERE repo_id = ?", (repo_id,))

            for idx, struct in enumerate(sorted(facts.structs), start=1):
                self.conn.execute(
                    "INSERT INTO code_structs(id, repo_id, name, file_path, start_line) VALUES (?, ?, ?, ?, ?)",
                    (idx, repo_id, struct.name, struct.file_path, struct.start_line),
                )
            for idx, member in enumerate(sorted(facts.members), start=1):
                self.conn.execute(
                    """
                    INSERT INTO code_members(id, repo_id, struct_name, name, file_path, start_line, is_function_pointer)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (idx, repo_id, member.struct_name, member.name, member.file_path, member.start_line, int(member.is_function_pointer)),
                )
            for idx, glob in enumerate(sorted(facts.globals), start=1):
                self.conn.execute(
                    "INSERT INTO code_globals(id, repo_id, name, file_path, start_line, type_name) VALUES (?, ?, ?, ?, ?, ?)",
                    (idx, repo_id, glob.name, glob.file_path, glob.start_line, glob.type_name),
                )
            for idx, include in enumerate(sorted(facts.includes), start=1):
                self.conn.execute(
                    "INSERT INTO code_includes(id, repo_id, file_path, target, start_line) VALUES (?, ?, ?, ?, ?)",
                    (idx, repo_id, include.file_path, include.target, include.start_line),
                )
            for function_id in sorted(facts.tags):
                for tag in sorted(facts.tags[function_id]):
                    self.conn.execute(
                        "INSERT INTO code_function_tags(repo_id, function_id, tag) VALUES (?, ?, ?)",
                        (repo_id, function_id, tag),
                    )

    def load_facts(self, repo_root: str | Path, *, repo_id: str = "default") -> CodeFacts:
        facts = CodeFacts(repo_root=Path(repo_root).resolve())
        for row in self.conn.execute(
            "SELECT name, file_path, start_line FROM code_structs WHERE repo_id = ? ORDER BY id",
            (repo_id,),
        ):
            facts.add_struct(StructFact(name=row["name"], file_path=row["file_path"], start_line=row["start_line"]))
        for row in self.conn.execute(
            "SELECT struct_name, name, file_path, start_line, is_function_pointer FROM code_members WHERE repo_id = ? ORDER BY id",
            (repo_id,),
        ):
            facts.add_member(
                MemberFact(
                    struct_name=row["struct_name"],
                    name=row["name"],
                    file_path=row["file_path"],
                    start_line=row["start_line"],
                    is_function_pointer=bool(row["is_function_pointer"]),
                )
            )
        for row in self.conn.execute(
            "SELECT name, file_path, start_line, type_name FROM code_globals WHERE repo_id = ? ORDER BY id",
            (repo_id,),
        ):
            facts.add_global(
                GlobalFact(name=row["name"], file_path=row["file_path"], start_line=row["start_line"], type_name=row["type_name"])
            )
        for row in self.conn.execute(
            "SELECT file_path, target, start_line FROM code_includes WHERE repo_id = ? ORDER BY id",
            (repo_id,),
        ):
            facts.add_include(IncludeFact(file_path=row["file_path"], target=row["target"], start_line=row["start_line"]))
        for row in self.conn.execute(
            "SELECT function_id, tag FROM code_function_tags WHERE repo_id = ? ORDER BY function_id, tag",
            (repo_id,),
        ):
            facts.tag_function(row["function_id"], row["tag"])
        return facts

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS files (
                    id TEXT NOT NULL,
                    repo_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    language TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    loc INTEGER NOT NULL,
                    PRIMARY KEY (repo_id, id)
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS code_symbols (
                    id TEXT NOT NULL,
                    repo_id TEXT NOT NULL,
                    file_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    signature TEXT,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER,
                    PRIMARY KEY (repo_id, id),
                    FOREIGN KEY (repo_id, file_id) REFERENCES files(repo_id, id)
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS code_edges (
                    id INTEGER NOT NULL,
                    repo_id TEXT NOT NULL,
                    src_id TEXT NOT NULL,
                    dst_id TEXT NOT NULL,
                    rel TEXT NOT NULL,
                    PRIMARY KEY (repo_id, id),
                    FOREIGN KEY (repo_id, src_id) REFERENCES code_symbols(repo_id, id),
                    FOREIGN KEY (repo_id, dst_id) REFERENCES code_symbols(repo_id, id)
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS code_structs (
                    id INTEGER NOT NULL,
                    repo_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    PRIMARY KEY (repo_id, id)
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS code_members (
                    id INTEGER NOT NULL,
                    repo_id TEXT NOT NULL,
                    struct_name TEXT NOT NULL,
                    name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    is_function_pointer INTEGER NOT NULL,
                    PRIMARY KEY (repo_id, id)
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS code_globals (
                    id INTEGER NOT NULL,
                    repo_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    type_name TEXT,
                    PRIMARY KEY (repo_id, id)
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS code_includes (
                    id INTEGER NOT NULL,
                    repo_id TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    target TEXT NOT NULL,
                    start_line INTEGER NOT NULL,
                    PRIMARY KEY (repo_id, id)
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS code_function_tags (
                    repo_id TEXT NOT NULL,
                    function_id TEXT NOT NULL,
                    tag TEXT NOT NULL,
                    PRIMARY KEY (repo_id, function_id, tag)
                )
                """
            )
            self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


def _stable_file_id(file_path: str) -> str:
    return hashlib.sha1(file_path.encode("utf-8")).hexdigest()[:16]


def _language_for(file_path: str) -> str:
    suffix = Path(file_path).suffix.lower()
    if suffix in {".c", ".h"}:
        return "c"
    if suffix in {".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"}:
        return "cpp"
    return "unknown"

