from pathlib import Path

from makewiki.analysis import FixtureAnalyzer
from makewiki.config import load_config
from makewiki.errors import WikiValidationError
from makewiki.graph import DocComment
from makewiki.wiki import generate_wiki, validate_wiki


ROOT = Path(__file__).parent / "fixtures" / "tiny_c"


class FakeLLM:
    def __init__(self):
        self.calls = []

    def complete(self, *, system: str, user: str) -> str:
        self.calls.append((system, user))
        return "- Start with `main.c:12`, then inspect request handling at `main.c:7`."


def test_generate_wiki_writes_index_and_symbol_pages(tmp_path):
    config = load_config(ROOT)
    graph = FixtureAnalyzer().analyze(ROOT.resolve(), config).graph

    pages = generate_wiki(graph, config, tmp_path, max_depth=3)

    assert len(pages) == 8
    index = tmp_path / "index.md"
    assert index.exists()
    index_text = index.read_text(encoding="utf-8")
    assert "# Architecture Guide" in index_text
    assert "## Purpose and Scope" in index_text
    assert "## Mental Model" in index_text
    assert "## Major Subsystems" in index_text
    assert "## Runtime / Control Flow" in index_text
    assert "## Key Interfaces" in index_text
    assert "## Concurrency / Scheduling" in index_text
    assert "## Configuration and Lifecycle" in index_text
    assert "## Failure Modes and Debugging" in index_text
    assert "## What To Read Next" in index_text
    assert "## Sources" in index_text
    assert "`main.c:12`" in index_text
    assert "[main](flows/main.c-main.md)" in index_text
    assert "[Root Module](modules/root.md) contains 4 symbol(s)" in index_text
    assert "[Symbol Reference](reference.md)" in index_text
    assert "[handle_request](symbols/main.c-handle_request.md)" not in index_text

    reference = tmp_path / "reference.md"
    reference_text = reference.read_text(encoding="utf-8")
    assert "# Symbol Reference" in reference_text
    assert "## `main.c`" in reference_text
    assert "[main](symbols/main.c-main.md)" in reference_text
    assert "[handle_request](symbols/main.c-handle_request.md)" in reference_text

    flow_page = tmp_path / "flows" / "main.c-main.md"
    flow_text = flow_page.read_text(encoding="utf-8")
    assert "# main Flow" in flow_text
    assert "## Phase Map" in flow_text
    assert "## Walkthrough" in flow_text
    assert "```mermaid" in flow_text
    assert "[handle_request](../symbols/main.c-handle_request.md)" in flow_text

    module_page = tmp_path / "modules" / "root.md"
    module_text = module_page.read_text(encoding="utf-8")
    assert "# Root Module" in module_text
    assert "## Responsibilities" in module_text
    assert "## Reading Path" in module_text
    assert "## Module Call Graph" in module_text
    assert "[main](../symbols/main.c-main.md)" in module_text
    assert "`main.c:12`" in module_text

    handle_page = tmp_path / "symbols" / "main.c-handle_request.md"
    handle_text = handle_page.read_text(encoding="utf-8")
    assert "# handle_request" in handle_text
    assert "## Role" in handle_text
    assert "## What To Look For" in handle_text
    assert "- Location: `main.c:7`" in handle_text
    assert "[parse_input](main.c-parse_input.md)" in handle_text
    assert "[do_work](worker.c-do_work.md)" in handle_text
    assert "[main](main.c-main.md)" in handle_text
    assert "flowchart TD" in handle_text


def test_generate_wiki_renders_doc_comment_description(tmp_path):
    config = load_config(ROOT)
    graph = FixtureAnalyzer().analyze(ROOT.resolve(), config).graph
    docs = {
        "handle_request": DocComment(
            symbol_name="handle_request",
            summary="Handle one inbound request end to end.",
            params=(("req", "The request to process."),),
            returns="0 on success.",
        )
    }

    generate_wiki(graph, config, tmp_path, max_depth=3, docs=docs)

    page = (tmp_path / "symbols" / "main.c-handle_request.md").read_text(encoding="utf-8")
    assert "## Description" in page
    assert "Handle one inbound request end to end." in page
    assert "`req`" in page
    assert "The request to process." in page
    assert "0 on success." in page
    # The Description must appear before the inferred heuristic section.
    assert page.index("## Description") < page.index("## What To Look For")
    # A symbol without a doc comment stays heuristic-only.
    other = (tmp_path / "symbols" / "main.c-parse_input.md").read_text(encoding="utf-8")
    assert "## Description" not in other
    assert validate_wiki(graph, tmp_path) == []


def test_validate_wiki_accepts_graph_backed_pages(tmp_path):
    config = load_config(ROOT)
    graph = FixtureAnalyzer().analyze(ROOT.resolve(), config).graph
    generate_wiki(graph, config, tmp_path, max_depth=3)

    assert validate_wiki(graph, tmp_path) == []


def test_generate_wiki_removes_stale_markdown_pages(tmp_path):
    config = load_config(ROOT)
    graph = FixtureAnalyzer().analyze(ROOT.resolve(), config).graph
    stale = tmp_path / "flows" / "old-flow.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("# old\n", encoding="utf-8")

    generate_wiki(graph, config, tmp_path, max_depth=3)

    assert not stale.exists()


def test_validate_wiki_rejects_missing_evidence_target(tmp_path):
    config = load_config(ROOT)
    graph = FixtureAnalyzer().analyze(ROOT.resolve(), config).graph
    generate_wiki(graph, config, tmp_path, max_depth=3)
    page = tmp_path / "symbols" / "main.c-main.md"
    page.write_text(page.read_text(encoding="utf-8").replace("`main.c:12`", "`missing.c:12`"), encoding="utf-8")

    try:
        validate_wiki(graph, tmp_path)
    except WikiValidationError as exc:
        assert "citation target does not exist: missing.c" in str(exc)
    else:
        raise AssertionError("expected WikiValidationError")


def test_generate_wiki_can_add_llm_module_summary(tmp_path):
    config = load_config(ROOT)
    graph = FixtureAnalyzer().analyze(ROOT.resolve(), config).graph
    llm = FakeLLM()

    generate_wiki(graph, config, tmp_path, max_depth=3, llm_client=llm)

    module_text = (tmp_path / "modules" / "root.md").read_text(encoding="utf-8")
    assert "## LLM Summary" in module_text
    assert "Start with `main.c:12`" in module_text
    assert validate_wiki(graph, tmp_path) == []
    assert len(llm.calls) == 1
    assert "Allowed citations:" in llm.calls[0][1]
