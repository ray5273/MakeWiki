from pathlib import Path

from makewiki.analysis import FixtureAnalyzer
from makewiki.config import load_config
from makewiki.errors import WikiValidationError
from makewiki.graph import DocComment
from makewiki.graph.model import CodeEdge, CodeGraph, SymbolNode
from makewiki.wiki import generate_wiki, validate_wiki
from makewiki.wiki.generator import (
    _SUMMARY_FALLBACK,
    _dedupe_citations,
    _architecture_diagram,
    _known_flow_phase_lines,
    _module_indexes,
    _module_subarea_lines,
    _normalize_llm_summary,
    _runtime_sequence_diagram,
    _runtime_story_reason,
)


def _node(name: str) -> SymbolNode:
    return SymbolNode(id=name, name=name, kind="function", file_path="app.c", start_line=1)


def test_generate_wiki_renders_data_model_and_symbol_struct_links(tmp_path):
    from makewiki.analysis.structs import StructDef, StructField

    config = load_config(ROOT)
    graph = FixtureAnalyzer().analyze(ROOT.resolve(), config).graph
    # handle_request in the fixture has a signature; give it a struct to reference.
    handle = next(n for n in graph.nodes.values() if n.name == "handle_request")
    object.__setattr__(handle, "signature", "int(struct request*)")
    structs = {
        "request": StructDef(
            name="request",
            file_path="main.c",
            start_line=3,
            end_line=6,
            fields=(StructField("int", "id"), StructField("char *", "body")),
        )
    }

    generate_wiki(graph, config, tmp_path, max_depth=3, structs=structs)

    data_model = (tmp_path / "data-model.md").read_text(encoding="utf-8")
    assert "## struct request" in data_model
    assert "`int id`" in data_model
    assert "Used by: `handle_request`" in data_model

    handle_page = (tmp_path / "symbols" / "main.c-handle_request.md").read_text(encoding="utf-8")
    assert "## Data Structures" in handle_page
    assert "[`struct request`](../data-model.md)" in handle_page
    # Unreferenced structs stay out; validation still passes.
    assert validate_wiki(graph, tmp_path) == []


def test_generate_wiki_skips_data_model_when_no_referenced_structs(tmp_path):
    config = load_config(ROOT)
    graph = FixtureAnalyzer().analyze(ROOT.resolve(), config).graph

    generate_wiki(graph, config, tmp_path, max_depth=3, structs={})

    assert not (tmp_path / "data-model.md").exists()


def test_runtime_story_reason_classifies_app_start_shutdown_as_shutdown():
    # `app_start_shutdown` contains "app_start" but is teardown: shutdown wins.
    reason = _runtime_story_reason(_node("app_start_shutdown"))
    assert "shutdown" in reason
    assert "blocking event runtime" not in reason


def test_runtime_story_reason_keeps_startup_for_spdk_app_start():
    reason = _runtime_story_reason(_node("spdk_app_start"))
    assert "blocking event runtime" in reason


def test_architecture_diagram_shows_cross_module_edges():
    graph = CodeGraph(repo_root=Path("."))
    nodes = [
        SymbolNode("app:start", "spdk_app_start", "function", "app.c", 10),
        SymbolNode("app:shutdown", "app_start_shutdown", "function", "app.c", 80),
        SymbolNode("reactor:init", "spdk_reactors_init", "function", "reactor.c", 20),
        SymbolNode("reactor:run", "reactor_run", "function", "reactor.c", 140),
        SymbolNode("rpc:reactors", "rpc_framework_get_reactors", "function", "app_rpc.c", 30),
        SymbolNode("rpc:log", "rpc_framework_get_log_flags", "function", "log_rpc.c", 50),
        SymbolNode("sched:gather", "_reactors_scheduler_gather_metrics", "function", "scheduler_static.c", 70),
        SymbolNode("sched:balance", "balance_static", "function", "scheduler_static.c", 120),
    ]
    for node in nodes:
        graph.add_node(node)
    graph.add_edge(CodeEdge("app:start", "reactor:init"))  # Application Lifecycle -> Reactor Runtime
    modules = _module_indexes(nodes)

    diagram = _architecture_diagram(modules, graph)

    assert diagram.startswith("flowchart TD")
    assert "Application Lifecycle" in diagram
    assert "Reactor Runtime" in diagram
    assert "1 call" in diagram  # cross-module edge counted


def test_runtime_sequence_diagram_orders_file_handoffs():
    nodes = [
        SymbolNode("app:start", "spdk_app_start", "function", "app.c", 881),
        SymbolNode("reactor:init", "spdk_reactors_init", "function", "reactor.c", 276),
        SymbolNode("app:shutdown", "app_start_shutdown", "function", "app.c", 277),
    ]

    diagram = _runtime_sequence_diagram(nodes)

    assert diagram.startswith("sequenceDiagram")
    assert "participant" in diagram
    assert "spdk_reactors_init (reactor.c:276)" in diagram


def test_known_flow_phase_lines_describe_branch_and_shutdown():
    app = _known_flow_phase_lines(SymbolNode(id="spdk_app_start", name="spdk_app_start", kind="function", file_path="app.c", start_line=881))
    app_text = "\n".join(app).lower()
    assert app_text  # curated flow exists
    assert "block" in app_text and "shutdown" in app_text and "return" in app_text

    reactor = _known_flow_phase_lines(SymbolNode(id="reactor_run", name="reactor_run", kind="function", file_path="reactor.c", start_line=987))
    reactor_text = "\n".join(reactor).lower()
    assert "interrupt" in reactor_text and "scheduler" in reactor_text and "drain" in reactor_text

    # Ordinary roots keep the generic walkthrough (no curated phases).
    assert _known_flow_phase_lines(_node("some_other_function")) == []


def test_large_module_gets_subareas(tmp_path):
    config = load_config(ROOT)
    reactor_names = [
        "spdk_reactors_init", "spdk_reactors_start", "spdk_reactors_stop", "spdk_reactors_fini",
        "spdk_event_allocate", "spdk_event_call", "event_queue_run_batch",
        "reactor_run", "_reactor_run", "reactor_interrupt_run",
        "_reactor_schedule_thread", "_threads_reschedule", "reactor_thread_op",
        "spdk_scheduler_get", "spdk_scheduler_set",
    ]
    # Pad past the 40-symbol threshold with filler symbols.
    filler = [f"reactor_helper_{i}" for i in range(30)]
    nodes = [
        SymbolNode(id=name, name=name, kind="function", file_path="reactor.c", start_line=i + 1)
        for i, name in enumerate(reactor_names + filler)
    ]
    modules = _module_indexes(nodes)
    # Everything is one reactor.c file -> one module bucket.
    module_name, module_nodes = max(modules.items(), key=lambda kv: len(kv[1]))
    symbol_paths = {n.id: tmp_path / "symbols" / f"reactor.c-{n.name}.md" for n in module_nodes}
    module_paths = {module_name: tmp_path / "modules" / "reactor.md"}

    lines = _module_subarea_lines(module_name, module_nodes, symbol_paths, module_paths)
    text = "\n".join(lines)

    assert "**Reactor lifecycle**" in text
    assert "**Event queue**" in text
    assert "**Runtime loop**" in text
    assert "**Thread movement**" in text
    assert "**Scheduler hooks**" in text
    # Thread movement wins over scheduler for a schedule_thread symbol.
    thread_line = next(line for line in lines if "Thread movement" in line)
    assert "_reactor_schedule_thread" in thread_line


ROOT = Path(__file__).parent / "fixtures" / "tiny_c"


class FakeLLM:
    def __init__(self):
        self.calls = []

    def complete(self, *, system: str, user: str) -> str:
        self.calls.append((system, user))
        return "- Start with `main.c:12`, then inspect request handling at `main.c:7`."


class RecordingLLM:
    def __init__(self, reply: str):
        self.reply = reply
        self.calls = []

    def complete(self, *, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.reply


class SequenceLLM:
    def __init__(self, replies: list[str]):
        self.replies = replies
        self.calls = []

    def complete(self, *, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.replies[len(self.calls) - 1]


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


def test_generate_wiki_adds_llm_symbol_summary_for_undocumented_symbols(tmp_path):
    config = load_config(ROOT)
    graph = FixtureAnalyzer().analyze(ROOT.resolve(), config).graph
    llm = RecordingLLM("- `handle_request` validates and dispatches the request. Evidence: `main.c:7`.")

    generate_wiki(graph, config, tmp_path, max_depth=3, llm_client=llm, llm_scope="symbols")

    page = (tmp_path / "symbols" / "main.c-handle_request.md").read_text(encoding="utf-8")
    assert "## What It Does" in page
    assert "## Summary" in page
    assert "validates and dispatches" in page
    # The prompt is grounded in the actual symbol and its source body.
    prompts = "\n".join(user for _, user in llm.calls)
    assert "handle_request" in prompts
    assert validate_wiki(graph, tmp_path) == []


def test_llm_symbol_summary_skipped_when_doc_comment_present(tmp_path):
    config = load_config(ROOT)
    graph = FixtureAnalyzer().analyze(ROOT.resolve(), config).graph
    docs = {
        "handle_request": DocComment(
            symbol_name="handle_request",
            summary="Handle one inbound request end to end.",
        )
    }
    llm = RecordingLLM("- generated prose that should not appear here.")

    generate_wiki(graph, config, tmp_path, max_depth=3, llm_client=llm, llm_scope="symbols", docs=docs)

    page = (tmp_path / "symbols" / "main.c-handle_request.md").read_text(encoding="utf-8")
    assert "## Description" in page
    assert "## Summary" not in page
    assert "generated prose that should not appear here." not in page


def test_normalize_summary_keeps_clean_bullets_unchanged():
    text = (
        "- The function validates options at `app.c:303`.\n"
        "- It is called by `spdk_app_parse_args` (`app.c:1233`)."
    )

    assert _normalize_llm_summary(text) == text


def test_normalize_summary_canonicalizes_bare_file_line_citation():
    text = "- The function validates options at app.c:303."

    assert _normalize_llm_summary(text) == "- The function validates options at `app.c:303`."


def test_normalize_summary_drops_leaked_reasoning_bullets():
    # Mirrors reactor.c-on_reactor.md, where scratch work leaked as bullets.
    text = (
        "- The function signature is at line 1394? Actually location given: `reactor.c:1394`.\n"
        "- Let's see: the call happens later. Not sure. But we can cite `reactor.c:1394`."
    )

    assert _normalize_llm_summary(text) == _SUMMARY_FALLBACK


def test_normalize_summary_keeps_good_bullets_and_drops_reasoning_ones():
    text = (
        "- Handles subsystem init completion at `app.c:441`.\n"
        "- Actually, let's re-check whether `app.c:441` is right."
    )

    assert _normalize_llm_summary(text) == "- Handles subsystem init completion at `app.c:441`."


def test_dedupe_citations_collapses_repeated_file_line():
    # Mirrors app.c-app_subsystem_init_done.md, which cited app.c:441 six times.
    bullet = (
        "- On success it configures the framework (`app.c:441`, `app.c:1134`) and then, "
        "if config exists (`app.c:441`, `app.c:1134`, `app.c:1572`), loads it (`app.c:441`)."
    )

    result = _dedupe_citations(bullet)

    assert result.count("`app.c:441`") == 1
    assert result.count("`app.c:1134`") == 1
    assert result.count("`app.c:1572`") == 1
    assert "()" not in result
    assert ", )" not in result


def test_normalize_summary_falls_back_when_no_bullets():
    assert _normalize_llm_summary("Some prose with no bullets.") == _SUMMARY_FALLBACK


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
    assert "## Concept" in module_text
    assert "## LLM Summary" in module_text
    assert "Start with `main.c:12`" in module_text
    assert validate_wiki(graph, tmp_path) == []
    # The module page now makes two LLM calls: the concept narrative and the summary.
    assert len(llm.calls) == 2
    assert "Allowed citations:" in llm.calls[0][1]


def test_flat_c_directory_splits_into_domain_modules():
    nodes = [
        SymbolNode("app:start", "spdk_app_start", "function", "app.c", 10),
        SymbolNode("app:shutdown", "app_start_shutdown", "function", "app.c", 80),
        SymbolNode("reactor:init", "spdk_reactors_init", "function", "reactor.c", 20),
        SymbolNode("reactor:run", "reactor_run", "function", "reactor.c", 140),
        SymbolNode("rpc:reactors", "rpc_framework_get_reactors", "function", "app_rpc.c", 30),
        SymbolNode("rpc:log", "rpc_framework_get_log_flags", "function", "log_rpc.c", 50),
        SymbolNode("sched:gather", "_reactors_scheduler_gather_metrics", "function", "scheduler_static.c", 70),
        SymbolNode("sched:balance", "balance_static", "function", "scheduler_static.c", 120),
    ]

    modules = _module_indexes(nodes)

    assert set(modules) == {
        "Application Lifecycle",
        "Reactor Runtime",
        "RPC Control Plane",
        "Scheduler Policy",
    }
    assert [node.file_path for node in modules["Application Lifecycle"]] == ["app.c", "app.c"]


def test_spdk_like_wiki_has_domain_sections_and_symbol_what_it_does(tmp_path):
    graph = CodeGraph(repo_root=tmp_path)
    for file_name in ("app.c", "reactor.c", "app_rpc.c", "log_rpc.c", "scheduler_static.c"):
        (tmp_path / file_name).write_text("\n".join(f"line {idx}" for idx in range(1, 220)), encoding="utf-8")
    nodes = [
        SymbolNode("app:start", "spdk_app_start", "function", "app.c", 10, 60),
        SymbolNode("app:shutdown", "app_start_shutdown", "function", "app.c", 80, 90),
        SymbolNode("reactor:init", "spdk_reactors_init", "function", "reactor.c", 20, 40),
        SymbolNode("reactor:start", "spdk_reactors_start", "function", "reactor.c", 55, 70),
        SymbolNode("reactor:run", "reactor_run", "function", "reactor.c", 140, 190),
        SymbolNode("rpc:reactors", "rpc_framework_get_reactors", "function", "app_rpc.c", 30, 60),
        SymbolNode("rpc:log", "rpc_framework_get_log_flags", "function", "log_rpc.c", 50, 80),
        SymbolNode("sched:gather", "_reactors_scheduler_gather_metrics", "function", "scheduler_static.c", 70, 110),
        SymbolNode("sched:balance", "balance_static", "function", "scheduler_static.c", 120, 160),
    ]
    for node in nodes:
        graph.add_node(node)
    for src, dst in (
        ("app:start", "reactor:init"),
        ("app:start", "reactor:start"),
        ("reactor:start", "reactor:run"),
        ("reactor:run", "sched:gather"),
        ("sched:gather", "sched:balance"),
    ):
        graph.add_edge(CodeEdge(src, dst))
    config = load_config(tmp_path)

    generate_wiki(graph, config, tmp_path / "wiki", max_depth=3)

    index = (tmp_path / "wiki" / "index.md").read_text(encoding="utf-8")
    assert "## Subsystem Map" in index
    assert "## Runtime Story" in index
    assert "`spdk_app_start` -> `spdk_reactors_init` -> `spdk_reactors_start` -> `reactor_run`" in index
    assert "## Read by Task" in index
    assert (tmp_path / "wiki" / "modules" / "application-lifecycle.md").exists()
    assert (tmp_path / "wiki" / "modules" / "reactor-runtime.md").exists()
    assert (tmp_path / "wiki" / "modules" / "rpc-control-plane.md").exists()
    assert (tmp_path / "wiki" / "modules" / "scheduler-policy.md").exists()
    symbol = (tmp_path / "wiki" / "symbols" / "app.c-spdk_app_start.md").read_text(encoding="utf-8")
    assert "## What It Does" in symbol
    assert "validates startup inputs" in symbol


def test_generate_wiki_repairs_bad_module_summary(tmp_path):
    config = load_config(ROOT)
    graph = FixtureAnalyzer().analyze(ROOT.resolve(), config).graph
    llm = SequenceLLM(
        [
            # First the (clean) concept narrative, then the summary repair sequence.
            "- The root area drives request handling from `main.c:12`.",
            "- This cites an invented location (`main.c:999`).",
            "- Root dispatch starts at `main.c:12`.",
        ]
    )

    generate_wiki(graph, config, tmp_path, max_depth=3, llm_client=llm, repair_attempts=1)

    module_text = (tmp_path / "modules" / "root.md").read_text(encoding="utf-8")
    assert "Root dispatch starts" in module_text
    assert "main.c:999" not in module_text
    assert len(llm.calls) == 3
    assert "Previous draft failed quality audit" in llm.calls[2][1]


def test_generate_wiki_falls_back_after_repair_attempts_exhausted(tmp_path):
    config = load_config(ROOT)
    graph = FixtureAnalyzer().analyze(ROOT.resolve(), config).graph
    llm = SequenceLLM(
        [
            # Clean concept narrative first, then a summary that never passes audit.
            "- The root area drives request handling from `main.c:12`.",
            "- This cites an invented location (`main.c:999`).",
            "- This still cites an invented location (`main.c:998`).",
        ]
    )

    generate_wiki(graph, config, tmp_path, max_depth=3, llm_client=llm, repair_attempts=1)

    module_text = (tmp_path / "modules" / "root.md").read_text(encoding="utf-8")
    assert _SUMMARY_FALLBACK in module_text
    assert len(llm.calls) == 3
