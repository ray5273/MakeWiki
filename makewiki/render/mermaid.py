from __future__ import annotations

import hashlib
import html

from makewiki.graph import CodeGraph


def render_mermaid(graph: CodeGraph, *, diagram_type: str = "callgraph") -> str:
    if diagram_type in {"callgraph", "cfg"}:
        edge_types = {"calls"} if diagram_type == "callgraph" else {"cfg_next", "cfg_branch"}
        return _render_flowchart(graph, edge_types=edge_types)
    if diagram_type == "sequence":
        return _render_sequence(graph)
    raise ValueError(f"Unsupported diagram type: {diagram_type}")


def _render_flowchart(graph: CodeGraph, *, edge_types: set[str]) -> str:
    lines = ["flowchart TD"]
    id_map = {node_id: _mermaid_id(node_id) for node_id in sorted(graph.nodes)}
    for node_id in sorted(graph.nodes):
        node = graph.nodes[node_id]
        label = _escape_label(f"{node.name}\\n{node.file_path}:{node.start_line}")
        lines.append(f'    {id_map[node_id]}["{label}"]')

    for edge in sorted(graph.edges, key=lambda e: (e.src_id, e.dst_id, e.rel)):
        if edge.rel in edge_types and edge.src_id in id_map and edge.dst_id in id_map:
            lines.append(f"    {id_map[edge.src_id]} --> {id_map[edge.dst_id]}")
    return "\n".join(lines) + "\n"


def _render_sequence(graph: CodeGraph) -> str:
    lines = ["sequenceDiagram"]
    for node_id in sorted(graph.nodes):
        node = graph.nodes[node_id]
        lines.append(f"    participant {_mermaid_id(node_id)} as {_escape_label(node.name)}")
    for edge in sorted(graph.edges, key=lambda e: (e.src_id, e.dst_id, e.rel)):
        if edge.rel == "calls":
            src = graph.nodes[edge.src_id]
            dst = graph.nodes[edge.dst_id]
            lines.append(f"    {_mermaid_id(src.id)}->>+{_mermaid_id(dst.id)}: calls {dst.file_path}:{dst.start_line}")
            lines.append(f"    {_mermaid_id(dst.id)}-->>-{_mermaid_id(src.id)}: return")
    return "\n".join(lines) + "\n"


def _mermaid_id(node_id: str) -> str:
    digest = hashlib.sha1(node_id.encode("utf-8")).hexdigest()[:10]
    return f"N{digest}"


def _escape_label(value: str) -> str:
    return html.escape(value, quote=True).replace("\n", "\\n")

