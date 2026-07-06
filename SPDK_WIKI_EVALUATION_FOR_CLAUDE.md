# SPDK lib/event Wiki Evaluation for Claude

Date: 2026-07-06

This document is the handoff for improving MakeWiki's SPDK `lib/event` documentation quality. It summarizes the actual evaluation run, the current generator behavior, remaining quality gaps, and concrete implementation tasks.

## Evaluation Targets

Existing LLM wiki:

- Wiki: `external/output/spdk_lib_event_nemotron_wiki`
- Graph: `external/output/spdk_lib_event_nemotron_graph`
- Report: `SPDK_WIKI_QUALITY_REPORT.md`

Fresh deterministic wiki generated with the current code:

- Wiki: `external/output/spdk_lib_event_current_wiki`
- Graph: `external/output/spdk_lib_event_nemotron_graph`
- Report: `SPDK_WIKI_CURRENT_QUALITY_REPORT.md`

Commands used:

```bash
.venv/bin/makewiki wiki test external/spdk/lib/event \
  --wiki /mnt/c/Users/Sanghyeok/Documents/codex_workspace/makewiki/external/output/spdk_lib_event_nemotron_wiki \
  --graph-out /mnt/c/Users/Sanghyeok/Documents/codex_workspace/makewiki/external/output/spdk_lib_event_nemotron_graph \
  --report /mnt/c/Users/Sanghyeok/Documents/codex_workspace/makewiki/SPDK_WIKI_QUALITY_REPORT.md

.venv/bin/makewiki wiki generate external/spdk/lib/event \
  --analyzer joern \
  --out /mnt/c/Users/Sanghyeok/Documents/codex_workspace/makewiki/external/output/spdk_lib_event_current_wiki \
  --graph-out /mnt/c/Users/Sanghyeok/Documents/codex_workspace/makewiki/external/output/spdk_lib_event_nemotron_graph \
  --depth 2

.venv/bin/makewiki wiki test external/spdk/lib/event \
  --wiki /mnt/c/Users/Sanghyeok/Documents/codex_workspace/makewiki/external/output/spdk_lib_event_current_wiki \
  --graph-out /mnt/c/Users/Sanghyeok/Documents/codex_workspace/makewiki/external/output/spdk_lib_event_nemotron_graph \
  --report /mnt/c/Users/Sanghyeok/Documents/codex_workspace/makewiki/SPDK_WIKI_CURRENT_QUALITY_REPORT.md
```

## Results

Existing LLM wiki result:

- Validation issues: 0
- Summaries evaluated: 3
- Pass rate: 33%
- Findings:
  - `module-overpacked`: `root.md` contains 123 symbols across 5 files.
  - `coverage-imbalance`: module summary cites 0 of 5 files.
  - `missing-runtime-story`: index does not explain a runtime path.

Fresh deterministic wiki result:

- Validation issues: 0
- Summaries evaluated: 0
- Pass rate: 100%
- Findings: none
- Generated modules:
  - `application-lifecycle.md`
  - `reactor-runtime.md`
  - `rpc-control-plane.md`
  - `scheduler-policy.md`

Interpretation: the current code fixes the largest structural problem, but the 100% result is not strong enough as a quality claim because deterministic `## What It Does` sections are not currently included in the summary-quality denominator.

## Manual Review Notes

What is now better:

- The fresh index has 4 readable modules instead of one `Root Module`.
- `Runtime Story` now shows the intended path: `spdk_app_start -> spdk_reactors_init -> spdk_reactors_start -> reactor_run -> _reactor_run -> event_queue_run_batch -> spdk_app_stop -> app_start_shutdown`.
- Key symbol pages now include `## What It Does`.
- `spdk_app_start`, `reactor_run`, and `rpc_framework_get_reactors` are much clearer than the old pages.

Remaining quality gaps:

- `app_start_shutdown` is misclassified in `Runtime Story` as startup/bootstrap behavior. The reason string says it "validates application options, initializes runtime state, and enters the blocking event runtime", which is wrong for shutdown.
- The deterministic `wiki test` pass rate is inflated because it evaluates zero summary pages when LLM is disabled. It should still grade deterministic `## What It Does`, `Runtime Story`, and module structure.
- The index still has generic sections below the improved material. `Key Interfaces`, `Concurrency / Scheduling`, `Configuration and Lifecycle`, and `Failure Modes and Debugging` include heuristic bullets that are sometimes less useful than the new `Read by Task` section.
- `reactor-runtime.md` contains 65 symbols in a single file-backed module. This may be acceptable for the first milestone, but for human reading it likely needs sub-anchors or subgroups for event queue, scheduler hooks, interrupt mode, thread migration, and reactor lifecycle.
- Flow walkthrough prose is improved but still often says "moves the flow into" without explaining branch semantics, return-code behavior, or shutdown implications from source snippets.
- LLM acceptance has not been rerun with `--llm openrouter --llm-scope all --repair-attempts 2` in this evaluation.

## Implementation Tasks for Claude

1. Fix runtime-story classification.

- Edit `makewiki/wiki/generator.py`.
- In `_runtime_story_reason`, check shutdown/stop/fini names before checking `app_start` or `spdk_app_start`.
- Add a unit test where `app_start_shutdown` maps to shutdown wording, not startup wording.

2. Make `wiki test` grade deterministic documentation.

- Edit `makewiki/wiki/evaluate.py`.
- Add quality evaluation for every symbol page's `## What It Does`.
- Flag:
  - missing `## What It Does`
  - shallow `What It Does` text that only says "calls N functions", "intermediate function", or "entry-style function"
  - key SPDK anchors missing expected domain words:
    - `spdk_app_start`: validation/setup/reactor/blocking/shutdown
    - `reactor_run`: polling/interrupt/scheduler/shutdown
    - `rpc_framework_get_reactors`: JSON-RPC/reactor/fanout/aggregation
- Keep the report clear: distinguish "LLM summary pages" from "document quality pages" so pass rate is not misleading.

3. Add acceptance tests for SPDK-like output quality.

- Add or extend tests in `tests/test_wiki.py` and `tests/test_evaluate.py`.
- Required checks:
  - flat C fixture yields at least 4 modules.
  - generated SPDK-like index has `Runtime Story` and `Read by Task`.
  - `app_start_shutdown` is described as shutdown.
  - `spdk_app_start`, `reactor_run`, and `rpc_framework_get_reactors` pages have non-shallow `## What It Does`.
  - `wiki test` fails if those sections regress.

4. Improve index section ranking.

- Reduce noisy heuristic sections or move them after task-based navigation.
- Prefer the current ordering:
  - `Mental Model`
  - `Subsystem Map`
  - `Runtime Story`
  - `Read by Task`
  - `Runtime / Control Flow`
  - `Sources`
- Keep lower-confidence heuristic sections only if they add information not already covered by task navigation.

5. Improve module pages for large single-file modules.

- For modules with more than about 40 symbols, add a deterministic `## Subareas` section.
- For `reactor.c`, expected subareas:
  - Reactor lifecycle: `spdk_reactors_init`, `spdk_reactors_start`, `spdk_reactors_stop`, `spdk_reactors_fini`
  - Event queue: `spdk_event_allocate`, `spdk_event_call`, `event_queue_run_batch`
  - Runtime loop: `reactor_run`, `_reactor_run`, `reactor_interrupt_run`
  - Scheduler hooks: `_reactors_scheduler_gather_metrics`, `_reactors_scheduler_balance`, scheduler getters/setters
  - Thread movement: `_reactor_schedule_thread`, `_threads_reschedule`, `reactor_thread_op`

6. Improve flow page explanation.

- `spdk_app_start` flow should explicitly describe:
  - validation/options copy
  - environment and signal setup
  - reactor initialization
  - reactor start as blocking runtime handoff
  - shutdown/return-code path
- `reactor_run` flow should explicitly describe:
  - polling branch versus interrupt branch
  - scheduler metrics trigger
  - lightweight-thread cleanup/post-processing
  - shutdown drain behavior

## Acceptance Criteria

Run:

```bash
.venv/bin/pytest

.venv/bin/makewiki wiki generate external/spdk/lib/event \
  --analyzer joern \
  --out /mnt/c/Users/Sanghyeok/Documents/codex_workspace/makewiki/external/output/spdk_lib_event_current_wiki \
  --graph-out /mnt/c/Users/Sanghyeok/Documents/codex_workspace/makewiki/external/output/spdk_lib_event_nemotron_graph \
  --depth 2

.venv/bin/makewiki wiki test external/spdk/lib/event \
  --wiki /mnt/c/Users/Sanghyeok/Documents/codex_workspace/makewiki/external/output/spdk_lib_event_current_wiki \
  --graph-out /mnt/c/Users/Sanghyeok/Documents/codex_workspace/makewiki/external/output/spdk_lib_event_nemotron_graph \
  --report /mnt/c/Users/Sanghyeok/Documents/codex_workspace/makewiki/SPDK_WIKI_CURRENT_QUALITY_REPORT.md
```

Expected:

- `pytest` passes.
- `wiki test` validation issues: 0.
- `wiki test` findings: 0.
- The report must evaluate deterministic document quality, not only LLM summaries.
- The fresh wiki must still have at least 4 module pages.
- The generated `index.md` must include a correct runtime story.
- The three key symbols must contain strong `## What It Does` sections:
  - `symbols/app.c-spdk_app_start.md`
  - `symbols/reactor.c-reactor_run.md`
  - `symbols/app_rpc.c-rpc_framework_get_reactors.md`

Optional LLM acceptance, if `OPENROUTER_API_KEY` is available:

```bash
.venv/bin/makewiki wiki generate external/spdk/lib/event \
  --analyzer joern \
  --llm openrouter \
  --llm-scope all \
  --repair-attempts 2 \
  --out /mnt/c/Users/Sanghyeok/Documents/codex_workspace/makewiki/external/output/spdk_lib_event_llm_current_wiki \
  --graph-out /mnt/c/Users/Sanghyeok/Documents/codex_workspace/makewiki/external/output/spdk_lib_event_nemotron_graph \
  --depth 2
```

Then run `wiki test` against that LLM output and require 100% pass rate with zero `coverage-imbalance`, `shallow-summary`, and citation findings.
