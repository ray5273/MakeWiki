from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from makewiki.errors import GraphError

ALLOWED_EDGE_RELS = {"calls", "imports", "inherits", "references", "cfg_next", "cfg_branch"}


@dataclass(frozen=True, order=True)
class SymbolNode:
    id: str
    name: str
    kind: str
    file_path: str
    start_line: int
    end_line: int | None = None
    signature: str | None = None

    def validate(self) -> None:
        if not self.id.strip():
            raise GraphError("SymbolNode.id is required")
        if not self.name.strip():
            raise GraphError(f"SymbolNode.name is required for {self.id}")
        if not self.kind.strip():
            raise GraphError(f"SymbolNode.kind is required for {self.id}")
        if not self.file_path.strip():
            raise GraphError(f"SymbolNode.file_path is required for {self.id}")
        if self.start_line < 1:
            raise GraphError(f"SymbolNode.start_line must be >= 1 for {self.id}")
        if self.end_line is not None and self.end_line < self.start_line:
            raise GraphError(f"SymbolNode.end_line must be >= start_line for {self.id}")


@dataclass(frozen=True, order=True)
class CodeEdge:
    src_id: str
    dst_id: str
    rel: str = "calls"

    def validate(self) -> None:
        if not self.src_id.strip() or not self.dst_id.strip():
            raise GraphError("CodeEdge src_id and dst_id are required")
        if self.rel not in ALLOWED_EDGE_RELS:
            allowed = ", ".join(sorted(ALLOWED_EDGE_RELS))
            raise GraphError(f"CodeEdge.rel must be one of: {allowed}")


@dataclass
class CodeGraph:
    repo_root: Path
    nodes: dict[str, SymbolNode] = field(default_factory=dict)
    edges: set[CodeEdge] = field(default_factory=set)

    def add_node(self, node: SymbolNode) -> None:
        node.validate()
        self.nodes[node.id] = node

    def add_edge(self, edge: CodeEdge) -> None:
        edge.validate()
        if edge.src_id not in self.nodes:
            raise GraphError(f"Edge source does not exist: {edge.src_id}")
        if edge.dst_id not in self.nodes:
            raise GraphError(f"Edge destination does not exist: {edge.dst_id}")
        self.edges.add(edge)

    def validate(self) -> None:
        for node in self.nodes.values():
            node.validate()
        for edge in self.edges:
            edge.validate()
            if edge.src_id not in self.nodes:
                raise GraphError(f"Edge source does not exist: {edge.src_id}")
            if edge.dst_id not in self.nodes:
                raise GraphError(f"Edge destination does not exist: {edge.dst_id}")

    def find_symbol(self, name_or_id: str) -> SymbolNode | None:
        if name_or_id in self.nodes:
            return self.nodes[name_or_id]
        matches = [node for node in self.nodes.values() if node.name == name_or_id]
        if not matches:
            return None
        return sorted(matches, key=lambda n: (n.file_path, n.start_line, n.id))[0]

