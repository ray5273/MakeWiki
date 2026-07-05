from pathlib import Path
import shutil

import pytest

from makewiki.analysis import ClangdAnalyzer, FixtureAnalyzer, JoernAnalyzer
from makewiki.config import load_config
from makewiki.errors import AnalyzerUnavailableError
from makewiki.graph import extract_subgraph
from makewiki.render import render_mermaid


ROOT = Path(__file__).parent / "fixtures" / "tiny_c"


def test_analyze_returns_result_with_graph_and_facts():
    from makewiki.analysis.base import AnalysisResult
    from makewiki.graph import CodeFacts, CodeGraph

    config = load_config(ROOT)
    result = FixtureAnalyzer().analyze(ROOT.resolve(), config)

    assert isinstance(result, AnalysisResult)
    assert isinstance(result.graph, CodeGraph)
    assert isinstance(result.facts, CodeFacts)
    # the fixture analyzer is functions-only: it produces no structural facts
    assert result.facts.structs == []
    assert result.facts.globals == []


def test_fixture_analyzer_extracts_expected_callgraph():
    config = load_config(ROOT)
    graph = FixtureAnalyzer().analyze(ROOT.resolve(), config).graph

    assert {node.name for node in graph.nodes.values()} == {
        "do_work",
        "handle_request",
        "main",
        "parse_input",
    }
    assert {(graph.nodes[e.src_id].name, graph.nodes[e.dst_id].name) for e in graph.edges} == {
        ("handle_request", "do_work"),
        ("handle_request", "parse_input"),
        ("main", "handle_request"),
    }


def test_fixture_analyzer_prefers_compile_commands(tmp_path):
    compiled = tmp_path / "compiled.c"
    ignored = tmp_path / "ignored.c"
    compiled.write_text("int compiled(void) { return 0; }\n", encoding="utf-8")
    ignored.write_text("int ignored(void) { return 0; }\n", encoding="utf-8")
    (tmp_path / "compile_commands.json").write_text(
        f"""
        [
          {{"directory": "{tmp_path}", "file": "compiled.c", "command": "cc -c compiled.c"}}
        ]
        """,
        encoding="utf-8",
    )
    config = load_config(tmp_path)

    graph = FixtureAnalyzer().analyze(tmp_path.resolve(), config).graph

    assert {node.name for node in graph.nodes.values()} == {"compiled"}


def test_mermaid_is_deterministic_and_escaped():
    config = load_config(ROOT)
    graph = FixtureAnalyzer().analyze(ROOT.resolve(), config).graph
    subgraph = extract_subgraph(graph, "main", 3, {"calls"})

    first = render_mermaid(subgraph)
    second = render_mermaid(subgraph)

    assert first == second
    assert first.startswith("flowchart TD\n")
    assert "main.c:12" in first
    assert "-->" in first


def test_missing_external_analyzers_fail_with_actionable_messages(monkeypatch):
    config = load_config(ROOT)
    monkeypatch.setattr("shutil.which", lambda _name: None)

    with pytest.raises(AnalyzerUnavailableError, match="clangd binary not found"):
        ClangdAnalyzer().analyze(ROOT.resolve(), config)
    with pytest.raises(AnalyzerUnavailableError, match="Joern analyzer unavailable"):
        JoernAnalyzer().analyze(ROOT.resolve(), config)


@pytest.mark.skipif(
    shutil.which("joern") is None or shutil.which("joern-parse") is None,
    reason="Joern CLI is not installed",
)
def test_joern_analyzer_extracts_fixture_callgraph():
    config = load_config(ROOT)
    graph = JoernAnalyzer().analyze(ROOT.resolve(), config).graph

    assert {node.name for node in graph.nodes.values()} == {
        "do_work",
        "handle_request",
        "main",
        "parse_input",
    }
    assert {(graph.nodes[e.src_id].name, graph.nodes[e.dst_id].name) for e in graph.edges} == {
        ("handle_request", "do_work"),
        ("handle_request", "parse_input"),
        ("main", "handle_request"),
    }
