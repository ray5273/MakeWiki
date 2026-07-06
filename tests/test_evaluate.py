from pathlib import Path

from makewiki.wiki.evaluate import evaluate_page, evaluate_wiki


def _write_page(dir_path: Path, name: str, summary: str, *, evidence="`app.c:10`", calls="- None", called_by="- None", what_it_does=None) -> Path:
    what_it_does = what_it_does or f"- `{name}` performs its documented work on the request path (`app.c:10`)."
    page = f"""# {name}

## What It Does

{what_it_does}

## Role

`{name}` is a function.

## Summary

{summary}

## What To Look For

Read the source.

## Evidence

- Location: {evidence}
- Kind: `function`

## Calls

{calls}

## Called By

{called_by}
"""
    path = dir_path / f"app.c-{name}.md"
    path.write_text(page, encoding="utf-8")
    return path


def test_clean_summary_has_no_findings(tmp_path):
    path = _write_page(
        tmp_path,
        "app_opts_validate",
        "- The function `app_opts_validate` validates the option string (`app.c:10`).",
    )

    result = evaluate_page(path)

    assert result.has_summary
    assert result.clean


def test_hallucinated_citation_flagged(tmp_path):
    path = _write_page(
        tmp_path,
        "foo",
        "- Does something at `app.c:999` which is not in the evidence (`app.c:10`).",
    )

    result = evaluate_page(path)

    cats = {f.category for f in result.findings}
    assert "hallucinated-citation" in cats


def test_reasoning_leak_flagged(tmp_path):
    path = _write_page(
        tmp_path,
        "on_reactor",
        "- The signature is at line 1394? Actually location given: `app.c:10`. Let's see.",
    )

    result = evaluate_page(path)

    assert "reasoning-leak" in {f.category for f in result.findings}


def test_citation_noise_flagged(tmp_path):
    path = _write_page(
        tmp_path,
        "noisy",
        "- It does A (`app.c:10`) then B (`app.c:10`) then C (`app.c:10`).",
    )

    result = evaluate_page(path)

    noise = [f for f in result.findings if f.category == "citation-noise"]
    assert noise and "3 times" in noise[0].detail


def test_empty_fallback_summary_flagged(tmp_path):
    path = _write_page(
        tmp_path,
        "blank",
        "- No reliable summary could be generated; read the source range and the caller/callee sections below as the primary evidence.",
    )

    result = evaluate_page(path)

    assert {f.category for f in result.findings} == {"empty-summary"}


def test_ungrounded_summary_flagged(tmp_path):
    path = _write_page(
        tmp_path,
        "orphan",
        "- This does general things with no citation and no names.",
    )

    result = evaluate_page(path)

    assert "ungrounded" in {f.category for f in result.findings}


def test_page_without_summary_is_not_graded(tmp_path):
    # A doc-comment page has ## Description, no ## Summary.
    page = tmp_path / "app.c-documented.md"
    page.write_text(
        "# documented\n\n## Description\n\nHuman prose.\n\n## Evidence\n\n- Location: `app.c:10`\n",
        encoding="utf-8",
    )

    result = evaluate_page(page)

    assert not result.has_summary
    assert result.clean


def test_evaluate_wiki_aggregates_pass_rate(tmp_path):
    symbols = tmp_path / "symbols"
    symbols.mkdir()
    _write_page(symbols, "good", "- `good` validates input (`app.c:10`).")
    _write_page(symbols, "bad", "- Uses `app.c:999` not in evidence (`app.c:10`).")

    evaluation = evaluate_wiki(tmp_path)

    assert len(evaluation.summary_pages) == 2
    assert evaluation.pass_rate() == 0.5
    assert "hallucinated-citation" in evaluation.counts_by_category()
    assert "Wiki summary evaluation" in evaluation.report()


def test_evaluate_wiki_includes_module_llm_summary(tmp_path):
    modules = tmp_path / "modules"
    symbols = tmp_path / "symbols"
    modules.mkdir()
    symbols.mkdir()
    (modules / "root.md").write_text(
        """# Root Module

## Overview

- Root starts at `main.c:12`.

## LLM Summary

- The module starts request handling (`main.c:12`).
""",
        encoding="utf-8",
    )

    evaluation = evaluate_wiki(tmp_path)

    assert len(evaluation.summary_pages) == 1
    assert evaluation.summary_pages[0].page_type == "module"
    assert evaluation.summary_pages[0].section == "LLM Summary"
    assert evaluation.pass_rate() == 1.0
    assert "module summaries evaluated: 1" in evaluation.report()


def test_module_llm_summary_uses_only_citations_outside_llm_section(tmp_path):
    page = tmp_path / "root.md"
    page.write_text(
        """# Root Module

## Overview

- Root starts at `main.c:12`.

## LLM Summary

- This invents another location (`main.c:99`).

## Symbols

- [main](../symbols/main.c-main.md) - `main.c:12`
""",
        encoding="utf-8",
    )

    result = evaluate_page(page, page_type="module", section="LLM Summary")

    assert "hallucinated-citation" in {finding.category for finding in result.findings}


def test_evaluate_wiki_flags_missing_runtime_story(tmp_path):
    (tmp_path / "symbols").mkdir()
    (tmp_path / "modules").mkdir()
    (tmp_path / "index.md").write_text("# Architecture Guide\n\n## Runtime Story\n\n", encoding="utf-8")

    evaluation = evaluate_wiki(tmp_path)

    assert "missing-runtime-story" in evaluation.counts_by_category()


def test_evaluate_wiki_flags_overpacked_root_module(tmp_path):
    modules = tmp_path / "modules"
    symbols = tmp_path / "symbols"
    modules.mkdir()
    symbols.mkdir()
    symbol_lines = "\n".join(
        f"- [f{i}](../symbols/file{i % 4}.c-f{i}.md) - `file{i % 4}.c:{i + 1}`"
        for i in range(20)
    )
    (modules / "root.md").write_text(
        f"""# Root Module

## Overview

Root.

## Metrics

- Symbols: 20
- Internal calls: 0
- Incoming calls: 0
- Outgoing calls: 0

## Symbols

{symbol_lines}
""",
        encoding="utf-8",
    )

    evaluation = evaluate_wiki(tmp_path)

    assert "module-overpacked" in evaluation.counts_by_category()


def test_evaluate_wiki_flags_coverage_imbalance(tmp_path):
    modules = tmp_path / "modules"
    symbols = tmp_path / "symbols"
    modules.mkdir()
    symbols.mkdir()
    (modules / "runtime.md").write_text(
        """# Runtime

## Overview

Runtime.

## LLM Summary

- Only discusses app startup (`app.c:10`).

## Symbols

- [a](../symbols/app.c-a.md) - `app.c:10`
- [b](../symbols/reactor.c-b.md) - `reactor.c:20`
- [c](../symbols/app_rpc.c-c.md) - `app_rpc.c:30`
""",
        encoding="utf-8",
    )

    evaluation = evaluate_wiki(tmp_path)

    assert "coverage-imbalance" in evaluation.counts_by_category()


def test_what_it_does_missing_section_flagged(tmp_path):
    symbols = tmp_path / "symbols"
    symbols.mkdir()
    page = symbols / "app.c-foo.md"
    page.write_text("# foo\n\n## Role\n\n`foo` is a function.\n\n## Evidence\n\n- Location: `app.c:10`\n", encoding="utf-8")

    evaluation = evaluate_wiki(tmp_path)

    assert "missing-what-it-does" in evaluation.counts_by_category()


def test_what_it_does_missing_domain_anchor_flagged(tmp_path):
    symbols = tmp_path / "symbols"
    symbols.mkdir()
    # spdk_app_start must mention validation/reactor/blocking/shutdown; this
    # deliberately drops them.
    _write_page(
        symbols,
        "irrelevant",
        "- placeholder (`app.c:10`).",
    )
    page = symbols / "app.c-spdk_app_start.md"
    page.write_text(
        "# spdk_app_start\n\n## What It Does\n\n- It does some generic bootstrapping work (`app.c:10`).\n\n## Evidence\n\n- Location: `app.c:10`\n",
        encoding="utf-8",
    )

    evaluation = evaluate_wiki(tmp_path)

    assert "missing-domain-anchor" in evaluation.counts_by_category()
    assert evaluation.document_findings
    assert len(evaluation.document_pages) >= 2


def test_what_it_does_with_anchors_is_clean(tmp_path):
    symbols = tmp_path / "symbols"
    symbols.mkdir()
    page = symbols / "app.c-spdk_app_start.md"
    page.write_text(
        "# spdk_app_start\n\n## What It Does\n\n"
        "- Validates options, sets up the reactor runtime, blocks while reactors run, and returns on shutdown (`app.c:10`).\n\n"
        "## Evidence\n\n- Location: `app.c:10`\n",
        encoding="utf-8",
    )

    evaluation = evaluate_wiki(tmp_path)

    assert evaluation.document_findings == []


def test_concept_section_requires_citations(tmp_path):
    from makewiki.wiki.evaluate import evaluate_summary_text

    # Named symbols but no file:line citation -> concept section must flag it.
    uncited = evaluate_summary_text(
        page="reactor-runtime",
        page_type="module",
        section="Concept",
        summary="- The reactor runtime drives spdk_event_call across cores.",
        allowed_citations={"reactor.c:987"},
        anchor_names={"spdk_event_call"},
    )
    assert "missing-citation" in {f.category for f in uncited.findings}

    cited = evaluate_summary_text(
        page="reactor-runtime",
        page_type="module",
        section="Concept",
        summary="- The reactor runtime drives events across cores (`reactor.c:987`).",
        allowed_citations={"reactor.c:987"},
        anchor_names={"spdk_event_call"},
    )
    assert "missing-citation" not in {f.category for f in cited.findings}
    assert cited.clean


def test_evaluate_summary_flags_shallow_summary(tmp_path):
    result = evaluate_page(
        _write_page(
            tmp_path,
            "shallow_makewiki_test",
            "- `foo` is an intermediate function: it is called by 1 function and calls 2 functions (`app.c:10`).",
        )
    )

    assert "shallow-summary" in {finding.category for finding in result.findings}
