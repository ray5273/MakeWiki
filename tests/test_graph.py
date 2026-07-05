from pathlib import Path

import pytest

from makewiki.errors import GraphError
from makewiki.graph import CodeEdge, CodeGraph, GraphStore, SymbolNode, extract_subgraph


def _graph(tmp_path: Path) -> CodeGraph:
    src = tmp_path / "main.c"
    src.write_text("int main(void) { return helper(); }\nint helper(void) { return 1; }\n", encoding="utf-8")
    graph = CodeGraph(repo_root=tmp_path)
    graph.add_node(SymbolNode(id="main", name="main", kind="function", file_path="main.c", start_line=1))
    graph.add_node(SymbolNode(id="helper", name="helper", kind="function", file_path="main.c", start_line=2))
    graph.add_edge(CodeEdge(src_id="main", dst_id="helper", rel="calls"))
    return graph


def test_model_rejects_missing_file_line():
    graph = CodeGraph(repo_root=Path("."))

    with pytest.raises(GraphError, match="start_line"):
        graph.add_node(SymbolNode(id="bad", name="bad", kind="function", file_path="bad.c", start_line=0))


def test_sqlite_round_trip(tmp_path):
    graph = _graph(tmp_path)

    with GraphStore(tmp_path / "graph.sqlite") as store:
        store.save_graph(graph)
        loaded = store.load_graph(tmp_path)

    assert loaded.nodes == graph.nodes
    assert loaded.edges == graph.edges


def test_extract_subgraph_bfs_depth(tmp_path):
    graph = _graph(tmp_path)

    subgraph = extract_subgraph(graph, "main", 1, {"calls"})

    assert set(subgraph.nodes) == {"main", "helper"}
    assert len(subgraph.edges) == 1

