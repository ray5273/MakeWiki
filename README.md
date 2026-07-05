# MakeWiki

MakeWiki generates source-backed reading guides for C and C++ codebases. It uses Joern for accurate call graphs, then writes a Markdown wiki with overview pages, flow walkthroughs, module pages, and exact symbol references.

## Quick Start

From this repository:

```bash
python3 -m venv .venv
.venv/bin/pip install -e . pytest
.venv/bin/makewiki doctor tests/fixtures/tiny_c
.venv/bin/makewiki wiki generate tests/fixtures/tiny_c --analyzer fixture --out /tmp/makewiki-tiny-wiki --graph-out /tmp/makewiki-tiny-graph
```

Expected output:

```text
wiki: /tmp/makewiki-tiny-wiki
pages: 8
```

Open `/tmp/makewiki-tiny-wiki/index.md`. Start there, then use `reference.md` only when you need exact symbol pages.

## SPDK Example

For a larger C project, Joern gives better results than the buildless fallback:

```bash
.venv/bin/makewiki doctor external/spdk/lib/event
.venv/bin/makewiki wiki generate external/spdk/lib/event \
  --analyzer joern \
  --out external/output/spdk_lib_event_wiki \
  --graph-out external/output/spdk_lib_event_graph \
  --depth 2
```

Open `external/output/spdk_lib_event_wiki/index.md`.

## Optional LLM Summaries

MakeWiki can add short module summaries through OpenRouter. The current locked model is:

```text
nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free
```

Use it like this:

```bash
export OPENROUTER_API_KEY=...
.venv/bin/makewiki wiki generate external/spdk/lib/event \
  --analyzer joern \
  --llm openrouter \
  --llm-model nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free \
  --out external/output/spdk_lib_event_nemotron_wiki \
  --graph-out external/output/spdk_lib_event_nemotron_graph
```

If the LLM is unavailable, generate without `--llm openrouter`; deterministic wiki pages still work.

## Commands

```bash
makewiki doctor [repo]
makewiki config validate <repo>
makewiki analyze <repo> --analyzer joern|clangd|fixture
makewiki render <repo> --root <symbol>
makewiki wiki generate <repo>
makewiki wiki validate <repo>
```

## Analyzer Choices

- `joern`: accurate C call graph and the default; requires `joern` and `joern-parse` on `PATH`.
- `clangd`: reserved for compile database based analysis; the adapter currently reports that it is not implemented.
- `fixture`: deterministic buildless analyzer for the tiny demo and tests (`tests/fixtures/tiny_c`); no external tools, but functions+calls only — not for real analysis.

Run `makewiki doctor <repo>` first when setup is unclear. It reports missing tools and the exact next fix.

## Generated Wiki Layout

- `index.md`: the narrative reading guide.
- `flows/`: walkthroughs for major execution paths.
- `modules/`: module-level responsibilities, metrics, and reading paths.
- `symbols/`: exact function reference pages.
- `reference.md`: complete symbol index.

Every non-index page includes source citations such as `app.c:881`, and `makewiki wiki validate` checks that those citations and links resolve.
