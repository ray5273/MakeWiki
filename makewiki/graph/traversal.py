from __future__ import annotations

from collections import deque

from makewiki.errors import GraphError
from makewiki.graph.model import CodeEdge, CodeGraph


def extract_subgraph(
    graph: CodeGraph,
    root: str,
    max_depth: int,
    edge_types: set[str] | None = None,
) -> CodeGraph:
    if max_depth < 0:
        raise GraphError("max_depth must be >= 0")
    root_node = graph.find_symbol(root)
    if root_node is None:
        raise GraphError(f"Root symbol not found: {root}")

    allowed = edge_types or {"calls"}
    adjacency: dict[str, list[CodeEdge]] = {}
    for edge in sorted(graph.edges):
        if edge.rel in allowed:
            adjacency.setdefault(edge.src_id, []).append(edge)

    selected_nodes = {root_node.id}
    selected_edges: set[CodeEdge] = set()
    queue: deque[tuple[str, int]] = deque([(root_node.id, 0)])

    while queue:
        current, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for edge in adjacency.get(current, []):
            selected_edges.add(edge)
            if edge.dst_id not in selected_nodes:
                selected_nodes.add(edge.dst_id)
                queue.append((edge.dst_id, depth + 1))

    subgraph = CodeGraph(repo_root=graph.repo_root)
    for node_id in sorted(selected_nodes):
        subgraph.add_node(graph.nodes[node_id])
    for edge in sorted(selected_edges):
        if edge.src_id in subgraph.nodes and edge.dst_id in subgraph.nodes:
            subgraph.add_edge(edge)
    return subgraph

