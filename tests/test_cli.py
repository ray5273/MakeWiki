from pathlib import Path
import json

from makewiki.cli import main
from makewiki.llm import OPENROUTER_DEFAULT_MODELS


ROOT = Path(__file__).parent / "fixtures" / "tiny_c"


def test_cli_config_validate(capsys):
    assert main(["config", "validate", str(ROOT)]) == 0
    assert "valid:" in capsys.readouterr().out


def test_cli_doctor_reports_environment(capsys):
    assert main(["doctor", str(ROOT)]) == 0

    out = capsys.readouterr().out
    assert "MakeWiki doctor:" in out
    assert "repo" in out
    assert "openrouter_model" in out
    assert "summary:" in out


def test_cli_analyze_and_render(tmp_path, capsys):
    out = tmp_path / "out"

    assert main(["analyze", str(ROOT), "--out", str(out), "--analyzer", "fixture"]) == 0
    analyze_out = capsys.readouterr().out
    assert "analyzed: 4 symbols, 3 edges" in analyze_out

    assert main(["render", str(ROOT), "--root", "main", "--type", "callgraph", "--depth", "3", "--out", str(out)]) == 0
    render_out = capsys.readouterr().out
    assert render_out.startswith("flowchart TD")
    assert "handle_request" in render_out
    assert "do_work" in render_out


def test_cli_render_output_file(tmp_path, capsys):
    out = tmp_path / "out"
    mermaid = tmp_path / "main.mmd"

    assert main(["analyze", str(ROOT), "--out", str(out), "--analyzer", "fixture"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "render",
                str(ROOT),
                "--root",
                "main",
                "--type",
                "callgraph",
                "--depth",
                "3",
                "--out",
                str(out),
                "--output",
                str(mermaid),
            ]
        )
        == 0
    )

    assert "rendered:" in capsys.readouterr().out
    assert mermaid.read_text(encoding="utf-8").startswith("flowchart TD")


def test_cli_ask_answers_graph_question(tmp_path, capsys):
    out = tmp_path / "out"

    assert main(["ask", str(ROOT), "what does main call?", "--out", str(out), "--analyzer", "fixture"]) == 0

    cli_out = capsys.readouterr().out
    assert "`main` directly calls `handle_request`." in cli_out
    assert "Evidence:" in cli_out
    assert "main at main.c:12" in cli_out


def test_cli_ask_json_output(tmp_path, capsys):
    out = tmp_path / "out"

    assert (
        main(
            [
                "ask",
                str(ROOT),
                "where is do_work defined?",
                "--out",
                str(out),
                "--analyzer",
                "fixture",
                "--format",
                "json",
            ]
        )
        == 0
    )

    data = json.loads(capsys.readouterr().out)
    assert data["kind"] == "definition"
    assert data["evidence"][0]["file_path"] == "worker.c"


def test_cli_wiki_generates_markdown_pages(tmp_path, capsys):
    out = tmp_path / "wiki"
    graph_out = tmp_path / "graph"

    assert (
        main(
            [
                "wiki",
                "generate",
                str(ROOT),
                "--out",
                str(out),
                "--graph-out",
                str(graph_out),
                "--analyzer",
                "fixture",
            ]
        )
        == 0
    )

    cli_out = capsys.readouterr().out
    assert "pages: 8" in cli_out
    assert (out / "index.md").exists()
    assert (out / "reference.md").exists()
    assert (out / "flows" / "main.c-main.md").exists()
    assert (out / "modules" / "root.md").exists()
    assert (out / "symbols" / "main.c-main.md").exists()


def test_cli_wiki_validate_accepts_generated_pages(tmp_path, capsys):
    out = tmp_path / "wiki"
    graph_out = tmp_path / "graph"

    assert (
        main(
            [
                "wiki",
                "generate",
                str(ROOT),
                "--out",
                str(out),
                "--graph-out",
                str(graph_out),
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                "wiki",
                "validate",
                str(ROOT),
                "--wiki",
                str(out),
                "--graph-out",
                str(graph_out),
            ]
        )
        == 0
    )

    assert "valid wiki:" in capsys.readouterr().out


def test_cli_wiki_validate_requires_existing_graph(tmp_path, capsys):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "index.md").write_text("# MakeWiki\n", encoding="utf-8")

    assert (
        main(
            [
                "wiki",
                "validate",
                str(ROOT),
                "--wiki",
                str(wiki),
                "--graph-out",
                str(tmp_path / "missing-graph"),
            ]
        )
        == 2
    )

    assert "graph store not found:" in capsys.readouterr().err


def test_cli_wiki_test_accepts_generated_pages(tmp_path, capsys):
    out = tmp_path / "wiki"
    graph_out = tmp_path / "graph"
    report = tmp_path / "quality.md"

    assert (
        main(
            [
                "wiki",
                "generate",
                str(ROOT),
                "--out",
                str(out),
                "--graph-out",
                str(graph_out),
                "--analyzer",
                "fixture",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                "wiki",
                "test",
                str(ROOT),
                "--wiki",
                str(out),
                "--graph-out",
                str(graph_out),
                "--report",
                str(report),
            ]
        )
        == 0
    )

    cli_out = capsys.readouterr().out
    assert "validation issues: 0" in cli_out
    assert "pass rate: 100%" in cli_out
    assert report.exists()


def test_cli_wiki_test_returns_2_for_validation_issue(tmp_path, capsys):
    out = tmp_path / "wiki"
    graph_out = tmp_path / "graph"
    assert main(["wiki", "generate", str(ROOT), "--out", str(out), "--graph-out", str(graph_out), "--analyzer", "fixture"]) == 0
    capsys.readouterr()
    page = out / "symbols" / "main.c-main.md"
    page.write_text(page.read_text(encoding="utf-8").replace("`main.c:12`", "`missing.c:12`", 1), encoding="utf-8")

    assert main(["wiki", "test", str(ROOT), "--wiki", str(out), "--graph-out", str(graph_out)]) == 2

    assert "validation issues: 1" in capsys.readouterr().out


def test_cli_wiki_test_returns_2_when_pass_rate_below_threshold(tmp_path, capsys):
    out = tmp_path / "wiki"
    graph_out = tmp_path / "graph"
    assert main(["wiki", "generate", str(ROOT), "--out", str(out), "--graph-out", str(graph_out), "--analyzer", "fixture"]) == 0
    capsys.readouterr()
    module = out / "modules" / "root.md"
    module.write_text(
        module.read_text(encoding="utf-8").replace(
            "## Reading Path",
            "## LLM Summary\n\n- This summary makes broad claims without anchors.\n\n## Reading Path",
        ),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "wiki",
                "test",
                str(ROOT),
                "--wiki",
                str(out),
                "--graph-out",
                str(graph_out),
                "--min-pass-rate",
                "1.0",
            ]
        )
        == 2
    )

    cli_out = capsys.readouterr().out
    assert "pass rate: 0%" in cli_out
    assert "ungrounded: 1" in cli_out


def test_cli_wiki_openrouter_requires_key(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)

    assert (
        main(
            [
                "wiki",
                "generate",
                str(ROOT),
                "--out",
                str(tmp_path / "wiki"),
                "--graph-out",
                str(tmp_path / "graph"),
                "--llm",
                "openrouter",
            ]
        )
        == 2
    )

    assert "OPENROUTER_API_KEY is required" in capsys.readouterr().err


def test_cli_doctor_reports_openrouter_fallback_chain(capsys):
    assert main(["doctor", str(ROOT)]) == 0

    out = capsys.readouterr().out
    for model in OPENROUTER_DEFAULT_MODELS:
        assert model in out


def test_cli_fixture_analyzer_pipeline(tmp_path, capsys):
    out = tmp_path / "out"

    assert main(["analyze", str(ROOT), "--analyzer", "fixture", "--out", str(out)]) == 0

    analyze_out = capsys.readouterr().out
    assert "analyzed: 4 symbols, 3 edges" in analyze_out
    assert (out / "graph.sqlite").exists()
