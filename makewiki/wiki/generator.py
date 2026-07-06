from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from makewiki.analysis.structs import StructDef
from makewiki.config import MakeWikiConfig
from makewiki.graph import CodeEdge, CodeGraph, DocComment, SymbolNode, extract_subgraph
from makewiki.llm import LLMClient
from makewiki.render import render_mermaid
from makewiki.wiki.evaluate import SummaryFinding, evaluate_summary_text
from makewiki.wiki.source import read_source_snippet


@dataclass(frozen=True)
class WikiPage:
    path: Path
    title: str


def generate_wiki(
    graph: CodeGraph,
    config: MakeWikiConfig,
    out_dir: str | Path,
    *,
    max_depth: int = 2,
    llm_client: LLMClient | None = None,
    llm_scope: str = "modules",
    docs: dict[str, DocComment] | None = None,
    structs: dict[str, StructDef] | None = None,
    repair_attempts: int = 0,
) -> list[WikiPage]:
    """Write deterministic graph-backed Markdown wiki pages."""
    if repair_attempts < 0:
        raise ValueError("repair_attempts must be >= 0")

    docs = docs or {}
    structs = structs or {}

    output_root = Path(out_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    symbols_dir = output_root / "symbols"
    symbols_dir.mkdir(parents=True, exist_ok=True)
    modules_dir = output_root / "modules"
    modules_dir.mkdir(parents=True, exist_ok=True)
    flows_dir = output_root / "flows"
    flows_dir.mkdir(parents=True, exist_ok=True)
    _clear_markdown_outputs(symbols_dir, modules_dir, flows_dir)

    nodes = sorted(graph.nodes.values(), key=_node_sort_key)
    incoming, outgoing = _edge_indexes(graph)
    modules = _module_indexes(nodes)

    symbol_paths: dict[str, Path] = {
        node.id: symbols_dir / f"{_symbol_slug(node)}.md"
        for node in nodes
    }
    module_paths: dict[str, Path] = {
        module: modules_dir / f"{_module_slug(module)}.md"
        for module in modules
    }
    flow_roots = _flow_roots(graph, config, outgoing)
    flow_paths: dict[str, Path] = {
        node.id: flows_dir / f"{_symbol_slug(node)}.md"
        for node in flow_roots
    }

    pages: list[WikiPage] = []
    index_path = output_root / "index.md"
    reference_path = output_root / "reference.md"
    index_path.write_text(
        _render_index(
            graph,
            config,
            nodes,
            modules,
            flow_roots,
            symbol_paths,
            module_paths,
            flow_paths,
            outgoing,
            max_depth=max_depth,
        ),
        encoding="utf-8",
    )
    pages.append(WikiPage(path=index_path, title="MakeWiki Index"))
    reference_path.write_text(_render_reference(nodes, symbol_paths), encoding="utf-8")
    pages.append(WikiPage(path=reference_path, title="Symbol Reference"))

    referenced_structs = _referenced_structs(nodes, structs)
    if referenced_structs:
        data_model_path = output_root / "data-model.md"
        data_model_path.write_text(_render_data_model(referenced_structs, nodes), encoding="utf-8")
        pages.append(WikiPage(path=data_model_path, title="Data Model"))

    for root in flow_roots:
        path = flow_paths[root.id]
        path.write_text(
            _render_flow_page(
                graph,
                root,
                outgoing,
                incoming,
                symbol_paths,
                max_depth=max_depth,
            ),
            encoding="utf-8",
        )
        pages.append(WikiPage(path=path, title=f"{root.name} flow"))

    for module, module_nodes in modules.items():
        path = module_paths[module]
        path.write_text(
            _render_module_page(
                graph,
                module,
                module_nodes,
                incoming,
                outgoing,
                symbol_paths,
                module_paths,
                max_depth=max_depth,
                llm_client=llm_client if llm_scope in {"modules", "all"} else None,
                repair_attempts=repair_attempts,
            ),
            encoding="utf-8",
        )
        pages.append(WikiPage(path=path, title=module))

    for node in nodes:
        path = symbol_paths[node.id]
        path.write_text(
            _render_symbol_page(
                graph,
                node,
                incoming.get(node.id, []),
                outgoing.get(node.id, []),
                symbol_paths,
                max_depth=max_depth,
                doc=docs.get(node.name),
                llm_client=llm_client if llm_scope in {"symbols", "all"} else None,
                repair_attempts=repair_attempts,
                structs=structs,
            ),
            encoding="utf-8",
        )
        pages.append(WikiPage(path=path, title=node.name))

    return pages


def _render_index(
    graph: CodeGraph,
    config: MakeWikiConfig,
    nodes: list[SymbolNode],
    modules: dict[str, list[SymbolNode]],
    flow_roots: list[SymbolNode],
    symbol_paths: dict[str, Path],
    module_paths: dict[str, Path],
    flow_paths: dict[str, Path],
    outgoing: dict[str, list[CodeEdge]],
    *,
    max_depth: int,
) -> str:
    default_root = flow_roots[0] if flow_roots else _default_root(graph)
    lines = [
        "# Architecture Guide",
        "",
        "This wiki explains the high-level design of the analyzed codebase. Use it to understand the architecture first, then drill into flows, modules, and symbol pages when you need exact call relationships or line evidence.",
        "",
        "## Purpose and Scope",
        "",
        "This document explains the analyzed code through source-backed graph evidence. It covers architectural shape, runtime flow, subsystem boundaries, and the source pages that support those claims.",
        f"- Root: `{graph.repo_root}`",
        f"- Symbols: {len(graph.nodes)}",
        f"- Edges: {len(graph.edges)}",
        "",
        "## Mental Model",
        "",
        _architecture_summary(modules, flow_roots),
        "",
    ]
    architecture_diagram = _architecture_diagram(modules, graph)
    if architecture_diagram:
        lines.extend([
            "## Architecture",
            "",
            "How the code areas call into each other (each arrow is one or more cross-module calls in the graph).",
            "",
            "```mermaid",
            architecture_diagram,
            "```",
            "",
        ])
    lines.extend(["## Major Subsystems", ""])
    lines.extend(_major_subsystem_lines(nodes))
    lines.extend(["", "## Subsystem Map", ""])
    lines.extend(_system_map_lines(nodes, modules, module_paths, outgoing))
    lines.extend(["", "## Runtime Story", ""])
    lines.extend(_runtime_story_lines(nodes, symbol_paths))
    sequence_diagram = _runtime_sequence_diagram(nodes)
    if sequence_diagram:
        lines.extend([
            "",
            "## Runtime Sequence",
            "",
            "The main runtime path as a handoff between source files, in call order.",
            "",
            "```mermaid",
            sequence_diagram,
            "```",
        ])
    lines.extend(["", "## Read by Task", ""])
    lines.extend(_read_by_task_lines(nodes, symbol_paths))
    lines.extend(
        [
            "",
            "## Runtime / Control Flow",
            "",
        ]
    )
    if flow_roots:
        for root in flow_roots:
            rel = _relative_to_output_root(flow_paths[root.id]).as_posix()
            lines.append(f"- [{root.name}]({rel}) - {_flow_one_line(root, graph, max_depth)}")
    else:
        lines.append("- No major runtime flows were identified from the graph.")
    lines.extend(
        [
            "",
            "## Key Interfaces",
            "",
        ]
    )
    lines.extend(_concept_lines(nodes, flow_roots, symbol_paths))
    lines.extend(["", "## Concurrency / Scheduling", ""])
    lines.extend(_concurrency_lines(nodes, symbol_paths))
    lines.extend(["", "## Configuration and Lifecycle", ""])
    lines.extend(_lifecycle_lines(nodes, symbol_paths))
    lines.extend(["", "## Failure Modes and Debugging", ""])
    lines.extend(_debugging_lines(nodes, symbol_paths))
    lines.extend(["", "## What To Read Next", ""])
    if default_root is not None:
        lines.extend(
            [
                f"1. Read [{default_root.name}]({_relative_to_output_root(flow_paths[default_root.id]).as_posix()}) to understand the main execution path from `{default_root.file_path}:{default_root.start_line}`.",
                "2. Read the module page for the code area you are working on.",
                "3. Use [Symbol Reference](reference.md) when you need the complete symbol index.",
                "4. Use symbol pages only when you need exact callers, callees, signatures, and line-level evidence.",
                "",
            ]
        )
    else:
        lines.extend(["1. Open a module page to start reading.", "2. Use [Symbol Reference](reference.md) when you need the complete symbol index.", ""])

    if config.repo_notes:
        lines.extend(["## Repository Notes", ""])
        for note in config.repo_notes:
            suffix = f" ({note.author})" if note.author else ""
            lines.append(f"- {note.content}{suffix}")
        lines.append("")

    lines.extend(["## Sources", ""])
    lines.append("- [Symbol Reference](reference.md) contains the complete symbol index.")
    for module, module_nodes in modules.items():
        rel = _relative_to_output_root(module_paths[module]).as_posix()
        lines.append(f"- [{_display_module(module)}]({rel}) contains {len(module_nodes)} symbol(s).")
    if not modules:
        lines.append("- No module pages were generated.")
    lines.append("")
    return "\n".join(lines)


_IDENTIFIER_RE = re.compile(r"[A-Za-z_]\w*")


def _signature_struct_names(signature: str | None, structs: dict[str, StructDef]) -> list[str]:
    """Struct names (from the index) that appear in a symbol's signature."""
    if not signature:
        return []
    seen: list[str] = []
    for token in _IDENTIFIER_RE.findall(signature):
        if token in structs and token not in seen:
            seen.append(token)
    return seen


def _referenced_structs(
    nodes: list[SymbolNode], structs: dict[str, StructDef]
) -> dict[str, StructDef]:
    """Keep only structs referenced by an analyzed symbol's signature.

    The header trees pull in hundreds of system/third-party structs; the data
    model a reader cares about is the set the analyzed functions actually take
    or return. Ordered by name for deterministic output.
    """
    if not structs:
        return {}
    referenced: set[str] = set()
    for node in nodes:
        referenced.update(_signature_struct_names(node.signature, structs))
    return {name: structs[name] for name in sorted(referenced)}


def _render_data_model(structs: dict[str, StructDef], nodes: list[SymbolNode]) -> str:
    """Render the data-model page: each referenced struct with its fields.

    Struct locations are shown as plain-text `file:line` (no backtick citation)
    because structs are parsed from source, not graph symbols, so they are not
    part of the graph-citation validation surface. Fields are verbatim from the
    definition, keeping the page accurate.
    """
    lines = [
        "# Data Model",
        "",
        "Key data structures the analyzed functions operate on, parsed from source. "
        "Each struct lists its fields and where it is defined.",
        "",
    ]
    # Map struct name -> functions whose signature references it, for cross-links.
    users: dict[str, list[str]] = {name: [] for name in structs}
    for node in nodes:
        for name in _signature_struct_names(node.signature, structs):
            users[name].append(node.name)

    for name, struct in structs.items():
        lines.append(f"## struct {name}")
        lines.append("")
        lines.append(f"Defined at {struct.file_path}:{struct.start_line} (lines {struct.start_line}-{struct.end_line}).")
        lines.append("")
        if struct.fields:
            lines.append("Fields:")
            lines.append("")
            for field_def in struct.fields:
                lines.append(f"- `{field_def.type} {field_def.name}`")
            lines.append("")
        symbol_users = users.get(name, [])
        if symbol_users:
            listed = ", ".join(f"`{fn}`" for fn in sorted(dict.fromkeys(symbol_users))[:8])
            lines.append(f"Used by: {listed}.")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_reference(nodes: list[SymbolNode], symbol_paths: dict[str, Path]) -> str:
    lines = [
        "# Symbol Reference",
        "",
        "This is the exact symbol index for the analyzed graph. Start with `index.md` for the reading guide, then use this page when you need a precise function page.",
        "",
    ]
    current_file = None
    for node in nodes:
        if node.file_path != current_file:
            current_file = node.file_path
            lines.extend(["", f"## `{current_file}`", ""])
        rel = _relative_to_output_root(symbol_paths[node.id]).as_posix()
        lines.append(f"- [{node.name}]({rel}) - `{node.file_path}:{node.start_line}`")
    lines.append("")
    return "\n".join(lines)


def _render_flow_page(
    graph: CodeGraph,
    root: SymbolNode,
    outgoing: dict[str, list[CodeEdge]],
    incoming: dict[str, list[CodeEdge]],
    symbol_paths: dict[str, Path],
    *,
    max_depth: int,
) -> str:
    subgraph = extract_subgraph(graph, root.id, max_depth, edge_types={"calls"})
    ordered = _flow_order(subgraph, root.id)
    lines = [
        f"# {root.name} Flow",
        "",
        "## Why Read This",
        "",
        _flow_summary(root, ordered, subgraph),
        "",
    ]
    phase_lines = _known_flow_phase_lines(root)
    if phase_lines:
        lines.extend(["## Key Phases", "", *phase_lines, ""])
    lines.extend(["## Phase Map", ""])
    lines.extend(_phase_map_lines(root, subgraph, outgoing, symbol_paths))
    lines.extend(
        [
            "",
            "## Walkthrough",
            "",
        ]
    )
    for idx, node in enumerate(ordered[:20], start=1):
        calls = [subgraph.nodes[edge.dst_id] for edge in sorted(outgoing.get(node.id, [])) if edge.dst_id in subgraph.nodes]
        lines.append(f"{idx}. {_walkthrough_sentence(node, calls, symbol_paths)}")
    if len(ordered) > 20:
        lines.append(f"{len(ordered) - 20} additional functions are included in the diagram below.")
    lines.extend(["", "## Failure and Shutdown Notes", ""])
    lines.extend(_flow_failure_shutdown_lines(ordered, symbol_paths))
    lines.extend(["", "## Call Graph", "", "```mermaid", render_mermaid(subgraph).rstrip(), "```", ""])
    lines.extend(["## Entry Context", ""])
    callers = [graph.nodes[edge.src_id] for edge in incoming.get(root.id, []) if edge.src_id in graph.nodes]
    if callers:
        for caller in sorted(callers, key=_node_sort_key)[:10]:
            lines.append(f"- Called by [{caller.name}]({_relative_symbol_from_flow(caller, symbol_paths)}) at `{caller.file_path}:{caller.start_line}`")
    else:
        lines.append("- No callers are present in this graph. Treat this as an entry point for the analyzed scope.")
    lines.append("")
    return "\n".join(lines)


def _render_module_page(
    graph: CodeGraph,
    module: str,
    nodes: list[SymbolNode],
    incoming: dict[str, list[CodeEdge]],
    outgoing: dict[str, list[CodeEdge]],
    symbol_paths: dict[str, Path],
    module_paths: dict[str, Path],
    *,
    max_depth: int,
    llm_client: LLMClient | None,
    repair_attempts: int,
) -> str:
    node_ids = {node.id for node in nodes}
    internal_edges = [
        edge
        for edge in graph.edges
        if edge.rel == "calls" and edge.src_id in node_ids and edge.dst_id in node_ids
    ]
    inbound_edges = [
        edge
        for edge in graph.edges
        if edge.rel == "calls" and edge.src_id not in node_ids and edge.dst_id in node_ids
    ]
    outbound_edges = [
        edge
        for edge in graph.edges
        if edge.rel == "calls" and edge.src_id in node_ids and edge.dst_id not in node_ids
    ]
    roots = _module_entrypoints(nodes, incoming, outgoing)
    diagram = _module_graph(graph, nodes, internal_edges, roots, max_depth=max_depth)

    lines = [
        f"# {_display_module(module)}",
        "",
        "## Overview",
        "",
        _module_summary(module, nodes, internal_edges, inbound_edges, outbound_edges),
        "",
    ]
    if llm_client is not None:
        lines.extend(
            [
                "## Concept",
                "",
                _generate_module_concept(
                    llm_client, module, nodes, internal_edges, inbound_edges, outbound_edges,
                    repair_attempts=repair_attempts,
                ),
                "",
            ]
        )
    lines.extend(["## Responsibilities", ""])
    lines.extend(_module_responsibility_lines(nodes, internal_edges, symbol_paths, module_paths, module))
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            _module_boundary_summary(inbound_edges, outbound_edges, graph),
            "",
            "## Metrics",
            "",
        ]
    )
    lines.extend(
        [
        f"- Symbols: {len(nodes)}",
        f"- Internal calls: {len(internal_edges)}",
        f"- Incoming calls: {len(inbound_edges)}",
        f"- Outgoing calls: {len(outbound_edges)}",
        "",
        ]
    )
    subarea_lines = _module_subarea_lines(module, nodes, symbol_paths, module_paths)
    if subarea_lines:
        lines.extend(["## Subareas", "", *subarea_lines, ""])
    if llm_client is not None:
        lines.extend(
            [
                "## LLM Summary",
                "",
                _generate_module_summary(
                    llm_client,
                    module,
                    nodes,
                    internal_edges,
                    inbound_edges,
                    outbound_edges,
                    repair_attempts=repair_attempts,
                ),
                "",
            ]
        )

    lines.extend(["## Top Anchors", ""])
    if roots:
        for node in roots[:10]:
            outgoing_count = len(outgoing.get(node.id, []))
            lines.append(f"- Start with [{node.name}]({_relative_symbol_from_module(module, node, symbol_paths, module_paths)}) at `{node.file_path}:{node.start_line}`. {_node_intent(node)} It reaches {outgoing_count} direct calls in this graph.")
    else:
        lines.append("- None")

    internal_paths = _important_internal_path_lines(module, nodes, outgoing, symbol_paths, module_paths)
    if internal_paths:
        lines.extend(["", "## Important Internal Paths", ""])
        lines.extend(internal_paths)

    lines.extend(["", "## Reading Path", ""])
    lines.append("Use Top Anchors for entry points, then follow Important Internal Paths and the symbol list below for exact source pages.")

    lines.extend(["", "## Module Call Graph", "", "```mermaid", render_mermaid(diagram).rstrip(), "```", "", "## Symbols", ""])
    for node in nodes:
        lines.append(f"- [{node.name}]({_relative_symbol_from_module(module, node, symbol_paths, module_paths)}) - `{node.file_path}:{node.start_line}`")
    lines.append("")
    return "\n".join(lines)


def _generate_module_concept(
    llm_client: LLMClient,
    module: str,
    nodes: list[SymbolNode],
    internal_edges: list[CodeEdge],
    inbound_edges: list[CodeEdge],
    outbound_edges: list[CodeEdge],
    *,
    repair_attempts: int = 0,
) -> str:
    """Generate a grounded concept narrative for a module.

    Unlike the terse LLM Summary, this explains the concept itself: what the
    code area is, why it exists, and how its key symbols realize it. Every claim
    is still pinned to a `file:line` citation and audited (the repair loop rejects
    hallucinated citations and reasoning leaks) so the added narrative depth does
    not cost accuracy.
    """
    evidence_nodes = _balanced_module_evidence(nodes, limit=40)
    allowed_tokens = [f"{node.file_path}:{node.start_line}" for node in evidence_nodes[:24]]
    allowed_citations = ", ".join(f"`{token}`" for token in allowed_tokens)
    system = (
        "You write explanatory code wiki documentation from provided graph evidence only. "
        "Explain the concept: what this code area is, why it exists, and how its key "
        "functions realize it. Do not invent files, functions, or call relationships. "
        "Every non-trivial claim must include one of the provided `file:line` citations. "
        "Return only final Markdown bullets. Do not reveal analysis or reasoning. Do not output Mermaid."
    )
    base_user_lines = [
        f"Code area: {_display_module(module)}",
        f"Symbol count: {len(nodes)}",
        "",
        "Allowed citations:",
        allowed_citations or "(none)",
        "",
        "Coverage anchors by file/domain:",
        *_module_prompt_anchor_lines(nodes),
        "",
        "Representative symbols:",
        *[
            f"- {node.name}: `{node.file_path}:{node.start_line}` signature={node.signature or '(unknown)'}"
            for node in evidence_nodes
        ],
        "",
        "Write 3-5 final Markdown bullets that explain this concept to a new reader: "
        "what it is and its responsibility, why it exists in the system, how the key "
        "functions work together to realize it, and how control enters and leaves it. "
        "Each bullet is 1-2 full sentences of explanation, not a label. "
        "Start every line with '- '. End EVERY bullet with exactly one trailing "
        "`(file:line)` citation drawn only from the allowed citations. "
        "Do not include a preface, labels, hidden reasoning, or draft notes.",
    ]
    return _complete_summary_with_repair(
        llm_client,
        system=system,
        base_user="\n".join(base_user_lines),
        repair_attempts=repair_attempts,
        audit=lambda summary: evaluate_summary_text(
            page=_module_slug(module),
            page_type="module",
            section="Concept",
            summary=summary,
            allowed_citations=set(allowed_tokens),
            anchor_names={module, _display_module(module)} | {node.name for node in evidence_nodes},
        ).findings,
    )


def _generate_module_summary(
    llm_client: LLMClient,
    module: str,
    nodes: list[SymbolNode],
    internal_edges: list[CodeEdge],
    inbound_edges: list[CodeEdge],
    outbound_edges: list[CodeEdge],
    *,
    repair_attempts: int = 0,
) -> str:
    evidence_nodes = _balanced_module_evidence(nodes, limit=40)
    allowed_tokens = [f"{node.file_path}:{node.start_line}" for node in evidence_nodes[:24]]
    allowed_citations = ", ".join(f"`{token}`" for token in allowed_tokens)
    system = (
        "You write concise code wiki documentation from provided graph evidence only. "
        "Do not invent files, functions, or call relationships. "
        "Every non-trivial claim must include one of the provided `file:line` citations. "
        "Return only final Markdown bullets. Do not reveal analysis or reasoning. Do not output Mermaid."
    )
    base_user_lines = [
        f"Module: {module}",
        f"Symbol count: {len(nodes)}",
        f"Internal call count: {len(internal_edges)}",
        f"Inbound call count: {len(inbound_edges)}",
        f"Outbound call count: {len(outbound_edges)}",
        "",
        "Allowed citations:",
        allowed_citations or "(none)",
        "",
        "Coverage anchors by file/domain:",
        *_module_prompt_anchor_lines(nodes),
        "",
        "Representative symbols:",
        *[
            f"- {node.name}: `{node.file_path}:{node.start_line}` signature={node.signature or '(unknown)'}"
            for node in evidence_nodes
        ],
        "",
        "Write 3-4 final Markdown bullets. Cover responsibility, the main runtime path, and failure/shutdown/control-plane relevance when present. "
        "Do not summarize only one file if the coverage anchors list multiple files. "
        "Start every line with '- '. Use only the allowed citations. "
        "Do not include a preface, labels, hidden reasoning, or draft notes.",
    ]
    return _complete_summary_with_repair(
        llm_client,
        system=system,
        base_user="\n".join(base_user_lines),
        repair_attempts=repair_attempts,
        audit=lambda summary: evaluate_summary_text(
            page=_module_slug(module),
            page_type="module",
            section="LLM Summary",
            summary=summary,
            allowed_citations=set(allowed_tokens),
            anchor_names={module, _display_module(module)} | {node.name for node in evidence_nodes},
        ).findings,
    )


# Deterministic replacement when the model returns no usable prose. Same tone as
# the no-context branch of _symbol_reading_hint so the page still guides reading.
_SUMMARY_FALLBACK = (
    "- No reliable summary could be generated; read the source range and the "
    "caller/callee sections below as the primary evidence."
)

# Phrases that mark leaked chain-of-thought rather than final prose. Reasoning
# models (e.g. the nemotron fallback) sometimes emit their scratch work as
# bullets, which the "starts with '- '" filter alone would let through.
_REASONING_MARKERS = re.compile(
    r"\b(actually|let'?s|let us|not sure|we need to|we can|i'?ll|maybe)\b|\?\s",
    re.IGNORECASE,
)

# A `file:line` citation token, e.g. `app.c:441`.
_CITATION_TOKEN = re.compile(r"`[^`]+:\d+`")
_BARE_CITATION_TOKEN = re.compile(r"(?<!`)([A-Za-z0-9_./+-]+\.[A-Za-z0-9_]+:\d+)(?!`)")

_MAX_SUMMARY_BULLET_CHARS = 400


def _looks_like_reasoning(line: str) -> bool:
    return bool(_REASONING_MARKERS.search(line))


def _dedupe_citations(text: str) -> str:
    """Drop repeated `file:line` citations within one bullet, keeping the first.

    Models grounded on a small graph tend to restate the same citation on every
    clause (`app.c:441` six times in one bullet). Keeping the first occurrence
    and stripping the rest — plus the separators left behind — keeps the prose
    readable without dropping evidence.
    """
    seen: set[str] = set()
    sentinel = "\x00"

    def repl(match: re.Match[str]) -> str:
        token = match.group(0)
        if token in seen:
            return sentinel
        seen.add(token)
        return token

    out = _CITATION_TOKEN.sub(repl, text)
    # Remove sentinels along with an adjacent list separator, then any strays.
    out = re.sub(rf"\s*,\s*{sentinel}", "", out)
    out = re.sub(rf"{sentinel}\s*,\s*", "", out)
    out = out.replace(sentinel, "")
    # Clean up empty or dangling parentheticals left by removed tokens.
    out = re.sub(r"\(\s*\)", "", out)
    out = re.sub(r"\(\s*,\s*", "(", out)
    out = re.sub(r"\s*,\s*\)", ")", out)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([.,;)])", r"\1", out)
    return out.rstrip()


def _canonicalize_citations(text: str) -> str:
    parts = text.split("`")
    for idx in range(0, len(parts), 2):
        parts[idx] = _BARE_CITATION_TOKEN.sub(r"`\1`", parts[idx])
    return "`".join(parts)


def _normalize_llm_summary(text: str) -> str:
    bullet_lines = [line.rstrip() for line in text.splitlines() if line.lstrip().startswith("- ")]
    clean: list[str] = []
    for line in bullet_lines:
        if _looks_like_reasoning(line):
            continue
        line = _canonicalize_citations(line)
        line = _dedupe_citations(line)
        if len(line) > _MAX_SUMMARY_BULLET_CHARS:
            line = line[:_MAX_SUMMARY_BULLET_CHARS].rstrip() + "…"
        clean.append(line)
    if clean:
        return "\n".join(clean[:4])
    return _SUMMARY_FALLBACK


def _render_symbol_page(
    graph: CodeGraph,
    node: SymbolNode,
    incoming: list[CodeEdge],
    outgoing: list[CodeEdge],
    symbol_paths: dict[str, Path],
    *,
    max_depth: int,
    doc: DocComment | None = None,
    llm_client: LLMClient | None = None,
    repair_attempts: int = 0,
    structs: dict[str, StructDef] | None = None,
) -> str:
    subgraph = extract_subgraph(graph, node.id, max_depth, edge_types={"calls"})
    role = _symbol_role(node, incoming, outgoing)
    generated_summary = None
    if doc is None and llm_client is not None:
        generated_summary = _generate_symbol_summary(llm_client, graph, node, incoming, outgoing, repair_attempts=repair_attempts)
    # Load-bearing symbols have curated, domain-anchored What It Does bullets;
    # those stay authoritative over LLM prose (which can drop key vocabulary like
    # "blocking"/"shutdown"). The LLM prose still drives the ## Summary section.
    known = _known_symbol_bullets(node)
    if known:
        what_it_does = "\n".join(known)
    else:
        what_it_does = generated_summary or _symbol_what_it_does(graph, node, incoming, outgoing)
    lines = [
        f"# {node.name}",
        "",
        "## What It Does",
        "",
        what_it_does,
        "",
        "## Role",
        "",
        f"{role} The implementation starts at `{node.file_path}:{node.start_line}`.",
        "",
    ]
    if doc is not None:
        # A source doc comment is authoritative; prefer it over generated prose.
        lines.extend(_doc_comment_lines(doc))
    elif llm_client is not None:
        lines.extend(
            [
                "## Summary",
                "",
                generated_summary or what_it_does,
                "",
            ]
        )
    lines.extend(
        [
            "## What To Look For",
            "",
            _symbol_reading_hint(node, incoming, outgoing, graph),
            "",
            "## Evidence",
            "",
            f"- Location: `{node.file_path}:{node.start_line}`",
            f"- Kind: `{node.kind}`",
        ]
    )
    if node.signature:
        lines.append(f"- Signature: `{node.signature}`")
    if node.end_line is not None:
        lines.append(f"- Range: `{node.file_path}:{node.start_line}-{node.end_line}`")

    struct_names = _signature_struct_names(node.signature, structs or {})
    if struct_names:
        lines.extend(["", "## Data Structures", "", "This function's signature references these data structures (see the [Data Model](../data-model.md) page):", ""])
        for name in struct_names:
            struct = structs[name]  # type: ignore[index]
            lines.append(f"- [`struct {name}`](../data-model.md) — {len(struct.fields)} field(s), defined at {struct.file_path}:{struct.start_line}.")

    lines.extend(
        [
            "",
            "## Local Flow",
            "",
            "Use this graph to see what this function directly drives. The prose sections below list the same relationships as links.",
            "",
            "```mermaid",
            render_mermaid(subgraph, diagram_type="callgraph").rstrip(),
            "```",
            "",
            "## Calls",
            "",
        ]
    )

    if outgoing:
        for edge in sorted(outgoing, key=lambda e: _node_sort_key(graph.nodes[e.dst_id])):
            dst = graph.nodes[edge.dst_id]
            lines.append(f"- [{dst.name}]({_relative_symbol_link(node, dst, symbol_paths)}) - `{dst.file_path}:{dst.start_line}`")
    else:
        lines.append("- None")

    lines.extend(["", "## Called By", ""])
    if incoming:
        for edge in sorted(incoming, key=lambda e: _node_sort_key(graph.nodes[e.src_id])):
            src = graph.nodes[edge.src_id]
            lines.append(f"- [{src.name}]({_relative_symbol_link(node, src, symbol_paths)}) - `{src.file_path}:{src.start_line}`")
    else:
        lines.append("- None")

    lines.append("")
    return "\n".join(lines)


def _doc_comment_lines(doc: DocComment) -> list[str]:
    """Render an authoritative source doc comment as a Description section.

    This is verbatim human-written prose from the source, so it leads over the
    name-pattern heuristics in the sections that follow.
    """
    lines = ["## Description", "", doc.summary, ""]
    if doc.params:
        lines.extend(["**Parameters**", ""])
        for name, desc in doc.params:
            suffix = f" - {desc}" if desc else ""
            lines.append(f"- `{name}`{suffix}")
        lines.append("")
    if doc.returns:
        lines.extend(["**Returns:** " + doc.returns, ""])
    return lines


def _generate_symbol_summary(
    llm_client: LLMClient,
    graph: CodeGraph,
    node: SymbolNode,
    incoming: list[CodeEdge],
    outgoing: list[CodeEdge],
    *,
    repair_attempts: int = 0,
) -> str:
    """Generate grounded prose for an undocumented symbol from its source body and graph context."""
    callees = [graph.nodes[edge.dst_id] for edge in outgoing if edge.dst_id in graph.nodes]
    callers = [graph.nodes[edge.src_id] for edge in incoming if edge.src_id in graph.nodes]
    snippet = read_source_snippet(
        graph.repo_root, node.file_path, node.start_line, end_line=node.end_line
    )

    allowed = [f"`{node.file_path}:{node.start_line}`"]
    allowed.extend(f"`{n.file_path}:{n.start_line}`" for n in (*callees, *callers))
    allowed_citations = ", ".join(dict.fromkeys(allowed))
    allowed_tokens = {token.strip("`") for token in dict.fromkeys(allowed)}

    system = (
        "You write concise code wiki documentation from the provided source and graph evidence only. "
        "Explain what the function does and why, based on its body. "
        "Do not invent files, functions, or call relationships. "
        "Ground claims in the provided `file:line` citations, but cite at most once per bullet, "
        "as a trailing `(file:line)`. Never repeat the same citation and never narrate citations in prose. "
        "Return only final Markdown bullets. Do not reveal analysis or reasoning. Do not output Mermaid."
    )
    base_user_lines = [
        f"Function: {node.name}",
        f"Location: `{node.file_path}:{node.start_line}`",
        f"Signature: {node.signature or '(unknown)'}",
        f"Calls: {', '.join(sorted(n.name for n in callees)) or '(none)'}",
        f"Called by: {', '.join(sorted(n.name for n in callers)) or '(none)'}",
        "",
        "Allowed citations:",
        allowed_citations or "(none)",
        "",
        "Source:",
        "```c",
        snippet or "(source unavailable)",
        "```",
        "",
        "Write 1-3 final Markdown bullets explaining what this function does and its role. "
        "Start every line with '- '. Use only the allowed citations, at most one per bullet, "
        "placed as a trailing `(file:line)`. Do not repeat a citation. "
        "Do not include a preface, labels, hidden reasoning, or draft notes.",
    ]
    return _complete_summary_with_repair(
        llm_client,
        system=system,
        base_user="\n".join(base_user_lines),
        repair_attempts=repair_attempts,
        audit=lambda summary: evaluate_summary_text(
            page=_symbol_slug(node),
            page_type="symbol",
            section="Summary",
            summary=summary,
            allowed_citations=allowed_tokens,
            anchor_names={node.name} | {n.name for n in (*callees, *callers)},
        ).findings,
    )


def _complete_summary_with_repair(
    llm_client: LLMClient,
    *,
    system: str,
    base_user: str,
    repair_attempts: int,
    audit,
) -> str:
    failures: list[SummaryFinding] = []
    attempts = max(1, repair_attempts + 1)
    for attempt in range(attempts):
        user = base_user
        if failures:
            user = "\n".join(
                [
                    base_user,
                    "",
                    "Previous draft failed quality audit. Fix these deterministic findings:",
                    *[f"- {finding.category}: {finding.detail}" for finding in failures],
                    "Return a clean final draft only.",
                ]
            )
        summary = _normalize_llm_summary(llm_client.complete(system=system, user=user))
        if repair_attempts == 0:
            return summary
        failures = audit(summary)
        if not failures:
            return summary
    return _SUMMARY_FALLBACK


def _edge_indexes(graph: CodeGraph) -> tuple[dict[str, list[CodeEdge]], dict[str, list[CodeEdge]]]:
    incoming: dict[str, list[CodeEdge]] = {}
    outgoing: dict[str, list[CodeEdge]] = {}
    for edge in sorted(graph.edges):
        if edge.rel != "calls":
            continue
        outgoing.setdefault(edge.src_id, []).append(edge)
        incoming.setdefault(edge.dst_id, []).append(edge)
    return incoming, outgoing


def _clear_markdown_outputs(*dirs: Path) -> None:
    for directory in dirs:
        for path in directory.glob("*.md"):
            path.unlink()


def _system_map_lines(
    nodes: list[SymbolNode],
    modules: dict[str, list[SymbolNode]],
    module_paths: dict[str, Path],
    outgoing: dict[str, list[CodeEdge]],
) -> list[str]:
    lines: list[str] = []
    for module, module_nodes in modules.items():
        rel = _relative_to_output_root(module_paths[module]).as_posix()
        display = _display_module(module)
        files = _file_clusters(module_nodes)
        file_text = ", ".join(f"`{file}`" for file in list(files)[:5])
        if len(files) > 5:
            file_text += f", and {len(files) - 5} more"
        entry = _best_entry(module_nodes, {node.id: len(outgoing.get(node.id, [])) for node in module_nodes})
        if entry is not None:
            lines.append(
                f"- [{display}]({rel}) covers {len(module_nodes)} symbols across {len(files)} file(s): {file_text}. "
                f"A useful entry point is `{entry.name}` at `{entry.file_path}:{entry.start_line}`."
            )
        else:
            lines.append(f"- [{display}]({rel}) covers {len(module_nodes)} symbols across {len(files)} file(s): {file_text}.")
    if not lines:
        lines.append("- No code areas were identified.")
    return lines


def _runtime_story_lines(nodes: list[SymbolNode], symbol_paths: dict[str, Path]) -> list[str]:
    ordered = _select_runtime_story_nodes(nodes)
    if not ordered:
        return ["- No high-signal runtime path was identified from symbol names."]

    lines: list[str] = []
    story = " -> ".join(f"`{node.name}`" for node in ordered[:8])
    lines.append(f"- Main path: {story}.")
    for node in ordered[:8]:
        rel = _relative_to_output_root(symbol_paths[node.id]).as_posix()
        lines.append(f"- [{node.name}]({rel}) - {_runtime_story_reason(node)} Evidence: `{node.file_path}:{node.start_line}`.")
    return lines


def _select_runtime_story_nodes(nodes: list[SymbolNode]) -> list[SymbolNode]:
    preferred = (
        "spdk_app_start",
        "spdk_reactors_init",
        "spdk_reactors_start",
        "reactor_run",
        "_reactor_run",
        "event_queue_run_batch",
        "spdk_app_stop",
        "app_start_shutdown",
        "spdk_reactors_fini",
    )
    by_name: dict[str, list[SymbolNode]] = {}
    for node in nodes:
        by_name.setdefault(node.name, []).append(node)
    selected: list[SymbolNode] = []
    for name in preferred:
        matches = by_name.get(name, [])
        if matches:
            selected.append(sorted(matches, key=_node_sort_key)[0])
    if selected:
        return selected
    ranked = sorted(
        nodes,
        key=lambda node: (-_runtime_story_score(node), node.file_path, node.start_line, node.id),
    )
    return [node for node in ranked if _runtime_story_score(node) > 0][:8]


def _runtime_story_score(node: SymbolNode) -> int:
    text = f"{node.file_path} {node.name}".lower()
    score = 0
    for token in ("app", "reactor", "event", "thread", "scheduler", "subsystem", "rpc"):
        if token in text:
            score += 4
    for token in ("start", "init", "run", "bootstrap", "shutdown", "stop", "fini"):
        if token in node.name.lower():
            score += 8
    if node.name.startswith("_"):
        score -= 2
    return score


def _runtime_story_reason(node: SymbolNode) -> str:
    name = node.name.lower()
    # Shutdown/stop/fini must be checked before app_start: a name like
    # `app_start_shutdown` contains "app_start" but is shutdown behavior, so
    # matching startup first would describe teardown as bootstrap.
    if "shutdown" in name or "stop" in name or "fini" in name:
        return "belongs to shutdown, cleanup, or return-code handling"
    if "app_start" in name or name == "spdk_app_start":
        return "validates application options, initializes runtime state, and enters the blocking event runtime"
    if "reactors_init" in name:
        return "prepares reactor/core runtime state before work is dispatched"
    if "reactors_start" in name:
        return "starts reactor execution after initialization has succeeded"
    if "reactor_run" in name:
        return "drives the reactor loop where queued work, polling, interrupts, and scheduling hooks meet"
    if "rpc" in name:
        return "exposes control-plane inspection or mutation while the runtime is active"
    return _node_intent(node)


def _read_by_task_lines(nodes: list[SymbolNode], symbol_paths: dict[str, Path]) -> list[str]:
    tasks = [
        ("Understand app startup/shutdown", ("spdk_app_start", "app_start_shutdown", "spdk_app_stop", "spdk_app_fini")),
        ("Understand the reactor loop", ("reactor_run", "_reactor_run", "event_queue_run_batch", "spdk_reactors_start")),
        ("Understand RPC introspection", ("rpc_framework_get_reactors", "rpc_framework_get_scheduler", "rpc_get_subsystems")),
        ("Understand scheduler behavior", ("_reactors_scheduler_gather_metrics", "balance_static", "scheduler_static_init")),
    ]
    lines: list[str] = []
    for label, names in tasks:
        matches = _find_named_nodes(nodes, names)
        if not matches:
            continue
        links = ", ".join(
            f"[{node.name}]({_relative_to_output_root(symbol_paths[node.id]).as_posix()})"
            for node in matches[:5]
        )
        lines.append(f"- {label}: {links}.")
    if lines:
        return lines
    return ["- Start with lifecycle, runtime-loop, control-plane, and scheduler-looking symbols listed in Key Interfaces."]


def _find_named_nodes(nodes: list[SymbolNode], names: tuple[str, ...]) -> list[SymbolNode]:
    selected: list[SymbolNode] = []
    for name in names:
        matches = [node for node in nodes if node.name == name]
        if matches:
            selected.append(sorted(matches, key=_node_sort_key)[0])
    return selected


def _major_subsystem_lines(nodes: list[SymbolNode]) -> list[str]:
    files = _file_clusters(nodes)
    lines: list[str] = []
    for file_path, file_nodes in files.items():
        ranked = sorted(file_nodes, key=lambda node: (-_subsystem_anchor_score(file_path, node), node.start_line, node.id))
        anchors = ", ".join(f"`{node.name}` at `{node.file_path}:{node.start_line}`" for node in ranked[:3])
        lines.append(f"- `{file_path}`: {_file_role(file_path, file_nodes)} Key anchors: {anchors}.")
    return lines or ["- No source files were identified."]


def _subsystem_anchor_score(file_path: str, node: SymbolNode) -> int:
    score = _name_concept_score(node)
    lower_file = file_path.lower()
    name = node.name.lower()
    if lower_file.endswith("app.c"):
        if name == "spdk_app_start":
            score += 100
        if any(token in name for token in ("app_start", "app_stop", "app_fini", "opts", "setup")):
            score += 40
        if name.startswith("rpc_") or "rpc_" in name:
            score -= 35
    return score


def _file_role(file_path: str, nodes: list[SymbolNode]) -> str:
    lower = file_path.lower()
    names = " ".join(node.name.lower() for node in nodes)
    text = f"{lower} {names}"
    if lower.endswith("app.c"):
        return "application lifecycle setup, startup, shutdown, and option handling."
    if "app_rpc" in lower or "rpc" in text:
        return "management and RPC-facing control-plane behavior."
    if "reactor" in lower:
        return "reactor execution, event dispatch, scheduling hooks, and thread/core runtime behavior."
    if "scheduler" in lower:
        return "scheduler registration and default scheduling policy glue."
    if "log" in lower:
        return "logging control or runtime log-level management."
    if "app" in lower:
        return "application lifecycle setup, startup, shutdown, and option handling."
    return "supporting code in the analyzed scope."


def _concept_lines(
    nodes: list[SymbolNode],
    flow_roots: list[SymbolNode],
    symbol_paths: dict[str, Path],
) -> list[str]:
    selected: list[SymbolNode] = []
    for node in flow_roots:
        if node.id not in {existing.id for existing in selected}:
            selected.append(node)
    for node in sorted(nodes, key=lambda n: (-_name_concept_score(n), n.file_path, n.start_line, n.id)):
        if len(selected) >= 8:
            break
        if _name_concept_score(node) <= 0:
            continue
        if node.id not in {existing.id for existing in selected}:
            selected.append(node)

    if not selected:
        return ["- No high-signal concepts were identified from names in this graph."]

    lines = []
    for node in selected:
        rel = _relative_to_output_root(symbol_paths[node.id]).as_posix()
        lines.append(f"- [{node.name}]({rel}) - {_node_intent(node)} Evidence: `{node.file_path}:{node.start_line}`.")
    return lines


def _concurrency_lines(nodes: list[SymbolNode], symbol_paths: dict[str, Path]) -> list[str]:
    concurrency_nodes = _nodes_matching(nodes, ("thread", "reactor", "scheduler", "core", "lock", "mutex", "poll"), include_file=True)
    if not concurrency_nodes:
        return ["- No explicit concurrency or scheduling symbols were identified from names in this graph."]
    return [_architectural_bullet(node, symbol_paths) for node in concurrency_nodes[:8]]


def _lifecycle_lines(nodes: list[SymbolNode], symbol_paths: dict[str, Path]) -> list[str]:
    lifecycle_nodes = _nodes_matching(nodes, ("main", "start", "init", "setup", "config", "opts", "parse", "stop", "shutdown", "fini"))
    if not lifecycle_nodes:
        return ["- No explicit configuration or lifecycle symbols were identified from names in this graph."]
    return [_architectural_bullet(node, symbol_paths) for node in lifecycle_nodes[:8]]


def _debugging_lines(nodes: list[SymbolNode], symbol_paths: dict[str, Path]) -> list[str]:
    debug_nodes = _nodes_matching(nodes, ("error", "fail", "assert", "debug", "log", "trace", "warn", "abort", "usage"), include_file=True)
    if not debug_nodes:
        return ["- No explicit failure-handling or debugging symbols were identified from names in this graph. Use module and symbol pages to inspect error paths manually."]
    return [_architectural_bullet(node, symbol_paths) for node in debug_nodes[:8]]


def _nodes_matching(nodes: list[SymbolNode], tokens: tuple[str, ...], *, include_file: bool = False) -> list[SymbolNode]:
    return sorted(
        [
            node
            for node in nodes
            if any(token in node.name.lower() or (include_file and token in node.file_path.lower()) for token in tokens)
        ],
        key=lambda node: (-_name_concept_score(node), node.file_path, node.start_line, node.id),
    )


def _architectural_bullet(node: SymbolNode, symbol_paths: dict[str, Path]) -> str:
    rel = _relative_to_output_root(symbol_paths[node.id]).as_posix()
    return f"- [{node.name}]({rel}) - {_node_intent(node)} Evidence: `{node.file_path}:{node.start_line}`."


def _flow_one_line(root: SymbolNode, graph: CodeGraph, max_depth: int) -> str:
    subgraph = extract_subgraph(graph, root.id, max_depth, edge_types={"calls"})
    ordered = _flow_order(subgraph, root.id)
    if len(ordered) <= 1:
        return f"starts at `{root.file_path}:{root.start_line}` and has no expanded calls in this graph"
    later = ", ".join(f"`{node.name}`" for node in ordered[1:4])
    return f"starts at `{root.file_path}:{root.start_line}` and reaches {len(ordered) - 1} function(s), including {later}"


def _flow_summary(root: SymbolNode, ordered: list[SymbolNode], subgraph: CodeGraph) -> str:
    files = sorted({node.file_path for node in ordered})
    file_text = ", ".join(f"`{file}`" for file in files[:4])
    if len(files) > 4:
        file_text += f", and {len(files) - 4} more"
    return (
        f"This flow explains why `{root.name}` matters in the runtime rather than only listing calls. "
        f"It starts at `{root.file_path}:{root.start_line}`, expands to {len(ordered)} function(s), and "
        f"connects {len(subgraph.edges)} call relationship(s) across {file_text}. {_runtime_story_reason(root)}."
    )


def _phase_map_lines(
    root: SymbolNode,
    subgraph: CodeGraph,
    outgoing: dict[str, list[CodeEdge]],
    symbol_paths: dict[str, Path],
) -> list[str]:
    direct = [subgraph.nodes[edge.dst_id] for edge in outgoing.get(root.id, []) if edge.dst_id in subgraph.nodes]
    if not direct:
        return [f"- `{root.name}` has no expanded direct callees in this graph. Evidence: `{root.file_path}:{root.start_line}`."]

    phases: dict[str, list[SymbolNode]] = {}
    for node in direct:
        phases.setdefault(_phase_for_name(node.name), []).append(node)

    lines = []
    for phase, nodes in sorted(phases.items(), key=lambda item: _phase_sort_key(item[0])):
        links = ", ".join(
            f"[{node.name}]({_relative_symbol_from_flow(node, symbol_paths)}) (`{node.file_path}:{node.start_line}`)"
            for node in sorted(nodes, key=_node_sort_key)
        )
        lines.append(f"- {phase}: {links}")
    return lines


def _walkthrough_sentence(
    node: SymbolNode,
    calls: list[SymbolNode],
    symbol_paths: dict[str, Path],
) -> str:
    link = f"[{node.name}]({_relative_symbol_from_flow(node, symbol_paths)})"
    if not calls:
        return f"{link} is a terminal step for this expanded view. {_node_intent(node)} Evidence: `{node.file_path}:{node.start_line}`."
    call_links = ", ".join(
        f"[{called.name}]({_relative_symbol_from_flow(called, symbol_paths)})"
        for called in sorted(calls, key=_node_sort_key)[:5]
    )
    extra = "" if len(calls) <= 5 else f", plus {len(calls) - 5} more"
    return (
        f"{link} handles {_intent_noun(node)} and moves the flow into {call_links}{extra}. "
        f"{_runtime_story_reason(node)}. Evidence: `{node.file_path}:{node.start_line}`."
    )


def _flow_failure_shutdown_lines(ordered: list[SymbolNode], symbol_paths: dict[str, Path]) -> list[str]:
    selected = [
        node
        for node in ordered
        if any(token in node.name.lower() for token in ("fail", "error", "stop", "shutdown", "fini", "cleanup", "exit", "return"))
    ]
    if not selected:
        return ["- No explicit failure or shutdown step was named in this expanded flow; inspect return-value checks in the source ranges linked above."]
    lines: list[str] = []
    for node in selected[:8]:
        lines.append(
            f"- [{node.name}]({_relative_symbol_from_flow(node, symbol_paths)}) - {_runtime_story_reason(node)}. Evidence: `{node.file_path}:{node.start_line}`."
        )
    return lines


def _module_responsibility_lines(
    nodes: list[SymbolNode],
    internal_edges: list[CodeEdge],
    symbol_paths: dict[str, Path],
    module_paths: dict[str, Path],
    module: str,
) -> list[str]:
    node_ids = {node.id for node in nodes}
    outgoing: dict[str, int] = {node.id: 0 for node in nodes}
    for edge in internal_edges:
        if edge.src_id in node_ids:
            outgoing[edge.src_id] += 1
    ranked = sorted(nodes, key=lambda node: (-outgoing[node.id], -_name_concept_score(node), node.file_path, node.start_line, node.id))
    lines: list[str] = []
    for node in ranked[:6]:
        rel = _relative_symbol_from_module(module, node, symbol_paths, module_paths)
        lines.append(
            f"- [{node.name}]({rel}) anchors {_intent_noun(node)}. "
            f"It has {outgoing[node.id]} internal call(s) in this module. Evidence: `{node.file_path}:{node.start_line}`."
        )
    return lines or ["- No responsibilities were identified from this module."]


def _important_internal_path_lines(
    module: str,
    nodes: list[SymbolNode],
    outgoing: dict[str, list[CodeEdge]],
    symbol_paths: dict[str, Path],
    module_paths: dict[str, Path],
) -> list[str]:
    ranked = sorted(nodes, key=lambda node: (-_name_concept_score(node), node.file_path, node.start_line, node.id))
    lines: list[str] = []
    for node in ranked:
        if len(lines) >= 6:
            break
        callees = [
            edge.dst_id
            for edge in outgoing.get(node.id, [])
            if any(candidate.id == edge.dst_id for candidate in nodes)
        ]
        if not callees and _name_concept_score(node) < 15:
            continue
        rel = _relative_symbol_from_module(module, node, symbol_paths, module_paths)
        lines.append(f"- [{node.name}]({rel}) - {_runtime_story_reason(node)} Evidence: `{node.file_path}:{node.start_line}`.")
    return lines


def _balanced_module_evidence(nodes: list[SymbolNode], *, limit: int) -> list[SymbolNode]:
    by_file = _file_clusters(nodes)
    selected: list[SymbolNode] = []
    for file_nodes in by_file.values():
        ranked = sorted(file_nodes, key=lambda node: (-_name_concept_score(node), node.start_line, node.id))
        selected.extend(ranked[:3])
    seen = {node.id for node in selected}
    for node in sorted(nodes, key=lambda node: (-_name_concept_score(node), node.file_path, node.start_line, node.id)):
        if len(selected) >= limit:
            break
        if node.id not in seen:
            selected.append(node)
            seen.add(node.id)
    return selected[:limit]


def _module_prompt_anchor_lines(nodes: list[SymbolNode]) -> list[str]:
    lines: list[str] = []
    for file_path, file_nodes in _file_clusters(nodes).items():
        anchors = sorted(file_nodes, key=lambda node: (-_name_concept_score(node), node.start_line, node.id))[:4]
        anchor_text = ", ".join(f"{node.name} `{node.file_path}:{node.start_line}`" for node in anchors)
        lines.append(f"- `{file_path}` ({_file_role(file_path, file_nodes)}): {anchor_text}")
    return lines or ["- (none)"]


def _module_boundary_summary(
    inbound_edges: list[CodeEdge],
    outbound_edges: list[CodeEdge],
    graph: CodeGraph,
) -> str:
    if not inbound_edges and not outbound_edges:
        return "The analyzed graph shows this area as internally closed: no calls cross into or out of it from another module."
    parts = []
    if inbound_edges:
        callers = sorted({graph.nodes[edge.src_id] for edge in inbound_edges if edge.src_id in graph.nodes}, key=_node_sort_key)
        cited = ", ".join(f"`{node.name}` at `{node.file_path}:{node.start_line}`" for node in callers[:3])
        parts.append(f"It is entered by {len(inbound_edges)} external call(s), including {cited}.")
    if outbound_edges:
        callees = sorted({graph.nodes[edge.dst_id] for edge in outbound_edges if edge.dst_id in graph.nodes}, key=_node_sort_key)
        cited = ", ".join(f"`{node.name}` at `{node.file_path}:{node.start_line}`" for node in callees[:3])
        parts.append(f"It calls out through {len(outbound_edges)} external call(s), including {cited}.")
    return " ".join(parts)


def _mermaid_node_id(key: str) -> str:
    import hashlib

    return "N" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]


def _architecture_diagram(modules: dict[str, list[SymbolNode]], graph: CodeGraph) -> str:
    """Deterministic module-dependency flowchart from cross-module call edges.

    Nodes are the code areas; an edge A --> B means at least one symbol in A
    calls a symbol in B. Purely graph-derived, so it stays as accurate as the
    call graph itself.
    """
    if len(modules) < 2:
        return ""
    node_to_module: dict[str, str] = {}
    for module, module_nodes in modules.items():
        for node in module_nodes:
            node_to_module[node.id] = module

    edges: dict[tuple[str, str], int] = {}
    for edge in graph.edges:
        if edge.rel != "calls":
            continue
        src_mod = node_to_module.get(edge.src_id)
        dst_mod = node_to_module.get(edge.dst_id)
        if src_mod is None or dst_mod is None or src_mod == dst_mod:
            continue
        edges[(src_mod, dst_mod)] = edges.get((src_mod, dst_mod), 0) + 1

    if not edges:
        return ""

    lines = ["flowchart TD"]
    ids = {module: _mermaid_node_id(module) for module in modules}
    for module in modules:
        label = _escape_mermaid(_display_module(module))
        lines.append(f'    {ids[module]}["{label}"]')
    for (src_mod, dst_mod), count in sorted(edges.items()):
        suffix = f"{count} calls" if count > 1 else "1 call"
        lines.append(f"    {ids[src_mod]} -->|{suffix}| {ids[dst_mod]}")
    return "\n".join(lines)


def _runtime_sequence_diagram(nodes: list[SymbolNode]) -> str:
    """Render the runtime-story main path as a file-to-file sequence diagram.

    Participants are the source files the path touches; each message is one hop
    in the curated runtime order, labelled with the symbol and its `file:line`.
    Reuses the same node selection as the Runtime Story so the two always agree.
    """
    ordered = _select_runtime_story_nodes(nodes)[:8]
    if len(ordered) < 2:
        return ""

    files: list[str] = []
    for node in ordered:
        if node.file_path not in files:
            files.append(node.file_path)
    lines = ["sequenceDiagram"]
    file_ids = {path: _mermaid_node_id(path) for path in files}
    for path in files:
        lines.append(f"    participant {file_ids[path]} as {_escape_mermaid(path)}")
    for src, dst in zip(ordered, ordered[1:]):
        label = _escape_mermaid(f"{dst.name} ({dst.file_path}:{dst.start_line})")
        lines.append(f"    {file_ids[src.file_path]}->>{file_ids[dst.file_path]}: {label}")
    return "\n".join(lines)


def _escape_mermaid(value: str) -> str:
    return value.replace('"', "'").replace("\n", " ")


def _architecture_summary(modules: dict[str, list[SymbolNode]], flow_roots: list[SymbolNode]) -> str:
    module_names = ", ".join(f"`{_display_module(name)}`" for name in list(modules)[:8])
    flow_names = ", ".join(f"`{node.name}`" for node in flow_roots[:5])
    if not module_names:
        module_names = "no modules"
    if not flow_names:
        flow_names = "no selected flows"
    return (
        f"The analyzed graph is split into {len(modules)} code area(s): {module_names}. "
        f"The primary reading paths are {flow_names}. Each section below links to source-backed pages with exact `file:line` evidence."
    )


# Ordered subarea rules for large single-file modules. A symbol joins the first
# subarea whose keyword it matches, so ordering matters: "Thread movement" is
# checked before "Scheduler hooks" so `_reactor_schedule_thread` (both "schedul"
# and "thread") lands in thread movement, matching how reactor.c actually reads.
_MODULE_SUBAREA_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Reactor lifecycle", ("reactors_init", "reactors_start", "reactors_stop", "reactors_fini", "reactor_construct", "reactor_deinit")),
    ("Event queue", ("event_allocate", "event_call", "event_queue", "run_batch")),
    ("Runtime loop", ("reactor_run", "_reactor_run", "interrupt_run", "reactor_interrupt")),
    ("Thread movement", ("thread", "reschedule", "migrat")),
    ("Scheduler hooks", ("schedul",)),
)

_MODULE_SUBAREA_MIN_SYMBOLS = 40


def _module_subarea_lines(
    module: str,
    nodes: list[SymbolNode],
    symbol_paths: dict[str, Path],
    module_paths: dict[str, Path],
) -> list[str]:
    """Group a large module's symbols into deterministic reading subareas.

    Only fires for modules above ~40 symbols, where a flat symbol list is too
    long to navigate. Subareas are keyword-derived, so they stay reproducible
    and need no LLM. Subareas with fewer than two members are dropped to avoid
    noise, and a section is only emitted when at least two subareas survive.
    """
    if len(nodes) <= _MODULE_SUBAREA_MIN_SYMBOLS:
        return []

    groups: dict[str, list[SymbolNode]] = {label: [] for label, _ in _MODULE_SUBAREA_RULES}
    for node in nodes:
        lowered = node.name.lower()
        for label, keywords in _MODULE_SUBAREA_RULES:
            if any(keyword in lowered for keyword in keywords):
                groups[label].append(node)
                break

    populated = [(label, members) for label, members in groups.items() if len(members) >= 2]
    if len(populated) < 2:
        return []

    lines: list[str] = []
    for label, members in populated:
        links = ", ".join(
            f"[{node.name}]({_relative_symbol_from_module(module, node, symbol_paths, module_paths)})"
            for node in sorted(members, key=_node_sort_key)[:8]
        )
        lines.append(f"- **{label}**: {links}.")
    return lines


def _module_summary(
    module: str,
    nodes: list[SymbolNode],
    internal_edges: list[CodeEdge],
    inbound_edges: list[CodeEdge],
    outbound_edges: list[CodeEdge],
) -> str:
    if nodes:
        first = nodes[0]
        evidence = f"`{first.file_path}:{first.start_line}`"
    else:
        evidence = "`unknown:1`"
    if inbound_edges and outbound_edges:
        boundary = "It both receives calls from outside this area and calls out to other areas."
    elif inbound_edges:
        boundary = "It is mostly entered from other code areas in this graph."
    elif outbound_edges:
        boundary = "It mostly drives behavior in other code areas from this graph."
    else:
        boundary = "All known calls stay inside this analyzed area."
    display = _display_module(module)
    return (
        f"{display} contains {len(nodes)} symbols and {len(internal_edges)} internal calls. "
        f"{boundary} Start from the reading path below, then use the symbol list as reference. Source evidence begins at {evidence}."
    )


def _symbol_what_it_does(
    graph: CodeGraph,
    node: SymbolNode,
    incoming: list[CodeEdge],
    outgoing: list[CodeEdge],
) -> str:
    return "\n".join(_deterministic_symbol_bullets(graph, node, incoming, outgoing))


def _deterministic_symbol_bullets(
    graph: CodeGraph,
    node: SymbolNode,
    incoming: list[CodeEdge],
    outgoing: list[CodeEdge],
) -> list[str]:
    name = node.name.lower()
    bullets: list[str] = []
    bullets.extend(_known_symbol_bullets(node))
    if not bullets:
        bullets.append(f"- `{node.name}` handles {_intent_noun(node)}. {_node_intent(node)} Evidence: `{node.file_path}:{node.start_line}`.")
    if outgoing:
        callees = [graph.nodes[edge.dst_id] for edge in outgoing if edge.dst_id in graph.nodes]
        names = ", ".join(f"`{callee.name}`" for callee in sorted(callees, key=_node_sort_key)[:5])
        bullets.append(f"- Its control flow continues through {names}, so those callees define the next concrete behavior. Evidence: `{node.file_path}:{node.start_line}`.")
    elif "run" in name or "start" in name:
        bullets.append(f"- The call graph has no expanded callees here, so inspect the source body for loop branches and return-code handling. Evidence: `{node.file_path}:{node.start_line}`.")
    if incoming:
        callers = [graph.nodes[edge.src_id] for edge in incoming if edge.src_id in graph.nodes]
        names = ", ".join(f"`{caller.name}`" for caller in sorted(callers, key=_node_sort_key)[:5])
        bullets.append(f"- It is reached from {names}, which gives the entry context for why this code runs. Evidence: `{node.file_path}:{node.start_line}`.")
    if any(token in name for token in ("fail", "error", "shutdown", "stop", "fini", "cleanup")):
        bullets.append(f"- Failure or shutdown relevance is signaled by the symbol name; check this page before changing cleanup ordering. Evidence: `{node.file_path}:{node.start_line}`.")
    return bullets[:4]


def _known_flow_phase_lines(root: SymbolNode) -> list[str]:
    """Explicit branch/failure/shutdown phase narrative for load-bearing flows.

    The generic walkthrough lists callees but not control-flow meaning. For the
    two flows a reader most needs to understand, spell out the phases (branches,
    blocking handoff, return-code and shutdown behavior) grounded in the root
    location. Returns [] for every other root, which keeps the generic
    walkthrough as the default.
    """
    cite = f"`{root.file_path}:{root.start_line}`"
    if root.name == "spdk_app_start":
        return [
            f"1. Validation and options copy: it rejects an unusable options struct and copies caller options into runtime state before anything starts. Evidence: {cite}.",
            f"2. Environment and signal setup: it prepares the SPDK environment, signal handlers, and tracing so the runtime can come up safely. Evidence: {cite}.",
            f"3. Reactor initialization: it builds per-core reactor state via the reactor-init path before any work is dispatched. Evidence: {cite}.",
            f"4. Blocking runtime handoff: it starts the reactors and blocks here while they run, so this call does not return during normal operation. Evidence: {cite}.",
            f"5. Shutdown and return code: on init failure it returns non-zero early without running, and on normal stop it returns after the reactor loop drains. Evidence: {cite}.",
        ]
    if root.name == "reactor_run":
        return [
            f"1. Mode branch: each iteration takes either the poller (busy-poll) branch or the interrupt branch depending on the reactor's interrupt mode. Evidence: {cite}.",
            f"2. Event and poller work: it drains the event queue and runs registered pollers for the threads on this core. Evidence: {cite}.",
            f"3. Scheduler metrics trigger: on the scheduler period it gathers per-thread load metrics that later drive rebalancing. Evidence: {cite}.",
            f"4. Lightweight-thread cleanup: it post-processes and removes lightweight threads that have exited or migrated. Evidence: {cite}.",
            f"5. Shutdown drain: when a stop is requested it stops looping and lets remaining work drain so the core can exit cleanly. Evidence: {cite}.",
        ]
    return []


def _known_symbol_bullets(node: SymbolNode) -> list[str]:
    name = node.name
    cite = f"`{node.file_path}:{node.start_line}`"
    if name == "spdk_app_start":
        return [
            f"- `spdk_app_start` is the application bootstrap path: it validates startup inputs, prepares environment/runtime state, and hands execution to the reactor layer. Evidence: {cite}.",
            f"- It is important because the function typically blocks while reactors run, so its return path represents shutdown or startup failure completion. Evidence: {cite}.",
        ]
    if name == "reactor_run":
        return [
            f"- `reactor_run` is a reactor execution loop where polling, interrupt-mode decisions, scheduler metric collection, and shutdown cleanup meet. Evidence: {cite}.",
            f"- It is the page to read when changing event dispatch latency, interrupt behavior, or shutdown drain ordering. Evidence: {cite}.",
        ]
    if name == "rpc_framework_get_reactors":
        return [
            f"- `rpc_framework_get_reactors` is a JSON-RPC control-plane entry point for reactor introspection. Evidence: {cite}.",
            f"- It matters because it fans out over reactor state and aggregates response data for external management clients. Evidence: {cite}.",
        ]
    return []


def _symbol_role(node: SymbolNode, incoming: list[CodeEdge], outgoing: list[CodeEdge]) -> str:
    if incoming and outgoing:
        return f"`{node.name}` is an intermediate function: it is called by {len(incoming)} function(s) and calls {len(outgoing)} function(s)."
    if outgoing:
        return f"`{node.name}` is an entry-style function in this graph: it has no known callers here and calls {len(outgoing)} function(s)."
    if incoming:
        return f"`{node.name}` is a leaf-style function in this graph: it is called by {len(incoming)} function(s) and has no known outgoing calls here."
    return f"`{node.name}` is isolated in the current graph."


def _flow_roots(
    graph: CodeGraph,
    config: MakeWikiConfig,
    outgoing: dict[str, list[CodeEdge]],
) -> list[SymbolNode]:
    roots: list[SymbolNode] = []
    for diagram in config.diagrams:
        if diagram.root_function:
            node = graph.find_symbol(diagram.root_function)
            if node is not None and node.id not in {root.id for root in roots}:
                roots.append(node)
    if roots:
        return roots[:5]

    default = _default_root(graph, outgoing)
    if default is not None:
        roots.append(default)
    ranked = sorted(
        graph.nodes.values(),
        key=lambda node: (-_reading_priority(node, outgoing), node.file_path, node.start_line, node.id),
    )
    for node in ranked:
        if len(roots) >= 3:
            break
        if node.id not in {root.id for root in roots} and outgoing.get(node.id):
            roots.append(node)
    return roots


def _flow_order(graph: CodeGraph, root_id: str) -> list[SymbolNode]:
    adjacency: dict[str, list[CodeEdge]] = {}
    for edge in sorted(graph.edges):
        adjacency.setdefault(edge.src_id, []).append(edge)
    ordered: list[SymbolNode] = []
    seen: set[str] = set()
    queue = [root_id]
    while queue:
        current = queue.pop(0)
        if current in seen or current not in graph.nodes:
            continue
        seen.add(current)
        ordered.append(graph.nodes[current])
        for edge in sorted(adjacency.get(current, []), key=lambda e: (graph.nodes[e.dst_id].file_path, graph.nodes[e.dst_id].start_line, e.dst_id)):
            if edge.dst_id not in seen:
                queue.append(edge.dst_id)
    return ordered


def _module_indexes(nodes: list[SymbolNode]) -> dict[str, list[SymbolNode]]:
    flat_domain_modules = _flat_domain_modules(nodes)
    if flat_domain_modules is not None:
        return flat_domain_modules

    modules: dict[str, list[SymbolNode]] = {}
    for node in nodes:
        modules.setdefault(_module_name(node.file_path), []).append(node)
    return {
        module: sorted(module_nodes, key=_node_sort_key)
        for module, module_nodes in sorted(modules.items())
    }


def _flat_domain_modules(nodes: list[SymbolNode]) -> dict[str, list[SymbolNode]] | None:
    if len(nodes) < 8:
        return None
    paths = [Path(node.file_path) for node in nodes]
    if any(len(path.parts) > 1 for path in paths):
        return None
    source_files = {path.name for path in paths if path.suffix.lower() in {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"}}
    if len(source_files) < 4:
        return None

    modules: dict[str, list[SymbolNode]] = {}
    for node in nodes:
        modules.setdefault(_domain_module_name(node), []).append(node)
    if len(modules) < 4:
        return None
    return {
        module: sorted(module_nodes, key=_node_sort_key)
        for module, module_nodes in sorted(modules.items())
    }


def _domain_module_name(node: SymbolNode) -> str:
    text = f"{Path(node.file_path).stem} {node.name}".lower()
    if "app_rpc" in text or "log_rpc" in text or "rpc" in text:
        return "RPC Control Plane"
    if "reactor" in text or "event_queue" in text:
        return "Reactor Runtime"
    if "scheduler" in text or "balance" in text:
        return "Scheduler Policy"
    if "app" in text or any(token in text for token in ("subsystem", "opts", "shutdown", "start")):
        return "Application Lifecycle"
    return _title_from_file_stem(node.file_path)


def _title_from_file_stem(file_path: str) -> str:
    stem = Path(file_path).stem.replace("_", " ").replace("-", " ").strip()
    if not stem:
        return "Root Module"
    return " ".join(part.capitalize() for part in stem.split())


def _module_name(file_path: str) -> str:
    path = Path(file_path)
    if len(path.parts) <= 1:
        return path.parent.as_posix() or "."
    return path.parts[0]


def _module_entrypoints(
    nodes: list[SymbolNode],
    incoming: dict[str, list[CodeEdge]],
    outgoing: dict[str, list[CodeEdge]],
) -> list[SymbolNode]:
    node_ids = {node.id for node in nodes}
    roots = [
        node
        for node in nodes
        if any(edge.src_id not in node_ids for edge in incoming.get(node.id, []))
    ]
    if not roots:
        roots = [node for node in nodes if not incoming.get(node.id)]
    return sorted(roots, key=lambda node: (-_reading_priority(node, outgoing), node.file_path, node.start_line, node.id))


def _module_graph(
    graph: CodeGraph,
    nodes: list[SymbolNode],
    internal_edges: list[CodeEdge],
    roots: list[SymbolNode],
    *,
    max_depth: int,
) -> CodeGraph:
    selected_ids = _module_diagram_node_ids(nodes, internal_edges, roots, max_depth=max_depth)
    subgraph = CodeGraph(repo_root=graph.repo_root)
    for node in nodes:
        if node.id in selected_ids:
            subgraph.add_node(node)
    for edge in sorted(internal_edges):
        if edge.src_id in subgraph.nodes and edge.dst_id in subgraph.nodes:
            subgraph.add_edge(edge)
    return subgraph


def _module_diagram_node_ids(
    nodes: list[SymbolNode],
    edges: list[CodeEdge],
    roots: list[SymbolNode],
    *,
    max_depth: int,
) -> set[str]:
    if not nodes:
        return set()
    root_ids = [node.id for node in roots[:5]] or [nodes[0].id]
    selected = set(root_ids)
    adjacency: dict[str, list[CodeEdge]] = {}
    for edge in edges:
        adjacency.setdefault(edge.src_id, []).append(edge)

    frontier = [(node_id, 0) for node_id in root_ids]
    while frontier and len(selected) < 60:
        current, depth = frontier.pop(0)
        if depth >= max_depth:
            continue
        for edge in sorted(adjacency.get(current, []), key=lambda e: (e.dst_id, e.rel)):
            if edge.dst_id not in selected:
                selected.add(edge.dst_id)
                frontier.append((edge.dst_id, depth + 1))
            if len(selected) >= 60:
                break
    return selected


def _default_root(graph: CodeGraph, outgoing: dict[str, list[CodeEdge]] | None = None) -> SymbolNode | None:
    main = graph.find_symbol("main")
    if main is not None:
        return main
    if not graph.nodes:
        return None
    outgoing = outgoing or {}
    return sorted(
        graph.nodes.values(),
        key=lambda node: (-_reading_priority(node, outgoing), node.file_path, node.start_line, node.id),
    )[0]


def _reading_priority(node: SymbolNode, outgoing: dict[str, list[CodeEdge]]) -> int:
    direct_calls = len(outgoing.get(node.id, []))
    score = direct_calls * 10
    name = node.name.lower()
    if name == "main":
        score += 1000
    if any(token in name for token in ("start", "run", "init", "bootstrap", "setup")):
        score += 60
    if any(token in name for token in ("stop", "shutdown", "fini")):
        score += 25
    if any(token in name for token in ("get", "set", "parse", "usage", "find", "name")):
        score -= 30
    if node.name.startswith("_"):
        score -= 20
    return score


def _file_clusters(nodes: list[SymbolNode]) -> dict[str, list[SymbolNode]]:
    clusters: dict[str, list[SymbolNode]] = {}
    for node in nodes:
        clusters.setdefault(node.file_path, []).append(node)
    return {
        file_path: sorted(file_nodes, key=_node_sort_key)
        for file_path, file_nodes in sorted(clusters.items())
    }


def _best_entry(nodes: list[SymbolNode], outgoing_counts: dict[str, int]) -> SymbolNode | None:
    if not nodes:
        return None
    return sorted(
        nodes,
        key=lambda node: (
            -outgoing_counts.get(node.id, 0),
            -_name_concept_score(node),
            node.file_path,
            node.start_line,
            node.id,
        ),
    )[0]


def _name_concept_score(node: SymbolNode) -> int:
    name = node.name.lower()
    score = 0
    for token in ("app", "reactor", "scheduler", "thread", "event", "rpc", "subsystem", "framework"):
        if token in name or token in node.file_path.lower():
            score += 10
    for token in ("start", "run", "init", "setup", "stop", "shutdown", "fini", "allocate", "call", "poll"):
        if token in name:
            score += 8
    for token in ("get", "set", "parse", "usage", "find", "name"):
        if token in name:
            score -= 5
    if node.name.startswith("_"):
        score -= 3
    return score


def _phase_for_name(name: str) -> str:
    normalized = name.lower()
    if any(token in normalized for token in ("stop", "shutdown", "fini", "deinit", "unclaim")):
        return "Shutdown and cleanup"
    if "start" in normalized or "bootstrap" in normalized:
        return "Application lifecycle"
    if any(token in normalized for token in ("opts", "parse", "config")):
        return "Configuration and option handling"
    if any(token in normalized for token in ("env", "setup", "trace", "signal", "mempool")):
        return "Environment setup"
    if any(token in normalized for token in ("core", "reactor", "thread", "scheduler")):
        return "Threading and reactor runtime"
    if any(token in normalized for token in ("rpc", "subsystem", "framework")):
        return "Subsystem and RPC coordination"
    if any(token in normalized for token in ("event", "call", "allocate", "queue")):
        return "Event dispatch"
    return "Supporting work"


def _phase_sort_key(phase: str) -> tuple[int, str]:
    order = {
        "Application lifecycle": 0,
        "Configuration and option handling": 1,
        "Environment setup": 2,
        "Subsystem and RPC coordination": 3,
        "Threading and reactor runtime": 4,
        "Event dispatch": 5,
        "Shutdown and cleanup": 6,
        "Supporting work": 7,
    }
    return order.get(phase, 99), phase


def _node_intent(node: SymbolNode) -> str:
    name = node.name.lower()
    if "stop" in name or "shutdown" in name or "fini" in name or "deinit" in name:
        return "It belongs to shutdown or cleanup behavior."
    if "rpc" in name:
        return "It is part of the RPC/control-plane surface."
    if "reactor" in name:
        return "It is part of the reactor runtime that drives work on cores or threads."
    if "scheduler" in name:
        return "It participates in scheduling or core-placement behavior."
    if "subsystem" in name:
        return "It coordinates subsystem startup or shutdown."
    if "start" in name or "bootstrap" in name:
        return "It begins a larger lifecycle and is a natural place to start reading."
    if "run" in name:
        return "It represents an active runtime loop or execution step."
    if "init" in name or "setup" in name:
        return "It prepares state needed before the main runtime path continues."
    if "event" in name:
        return "It handles event allocation, queuing, or dispatch."
    if "parse" in name or "opts" in name:
        return "It handles configuration or command-line option state."
    return "Its role is inferred from its callers, callees, and source location."


def _intent_noun(node: SymbolNode) -> str:
    phase = _phase_for_name(node.name)
    return phase[0].lower() + phase[1:]


def _symbol_reading_hint(
    node: SymbolNode,
    incoming: list[CodeEdge],
    outgoing: list[CodeEdge],
    graph: CodeGraph,
) -> str:
    parts = [_node_intent(node)]
    if outgoing:
        callees = [graph.nodes[edge.dst_id] for edge in outgoing if edge.dst_id in graph.nodes]
        names = ", ".join(f"`{callee.name}`" for callee in sorted(callees, key=_node_sort_key)[:5])
        parts.append(f"Read its outgoing calls next: {names}.")
    if incoming:
        callers = [graph.nodes[edge.src_id] for edge in incoming if edge.src_id in graph.nodes]
        names = ", ".join(f"`{caller.name}`" for caller in sorted(callers, key=_node_sort_key)[:5])
        parts.append(f"To understand why it runs, inspect its callers: {names}.")
    if not incoming and not outgoing:
        parts.append("This graph has no local caller/callee context for it, so treat the source range as the main evidence.")
    return " ".join(parts)


def _relative_symbol_link(src: SymbolNode, dst: SymbolNode, symbol_paths: dict[str, Path]) -> str:
    return symbol_paths[dst.id].relative_to(symbol_paths[src.id].parent).as_posix()


def _relative_symbol_from_module(
    module: str,
    dst: SymbolNode,
    symbol_paths: dict[str, Path],
    module_paths: dict[str, Path],
) -> str:
    return _relative_path(symbol_paths[dst.id], module_paths[module].parent)


def _relative_symbol_from_flow(dst: SymbolNode, symbol_paths: dict[str, Path]) -> str:
    return _relative_path(symbol_paths[dst.id], symbol_paths[dst.id].parents[1] / "flows")


def _relative_to_output_root(path: Path) -> Path:
    return path.relative_to(path.parents[1])


def _relative_path(target: Path, start: Path) -> str:
    return os.path.relpath(target, start).replace(os.sep, "/")


def _edge_types_for(diagram_type: str) -> set[str]:
    if diagram_type in {"callgraph", "sequence"}:
        return {"calls"}
    if diagram_type == "cfg":
        return {"cfg_next", "cfg_branch"}
    return {"calls"}


def _symbol_slug(node: SymbolNode) -> str:
    base = f"{node.file_path}-{node.name}"
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", base).strip("-").lower()


def _module_slug(module: str) -> str:
    if module == ".":
        return "root"
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", module).strip("-").lower()


def _display_module(module: str) -> str:
    if module == ".":
        return "Root Module"
    return module


def _node_sort_key(node: SymbolNode) -> tuple[str, int, str]:
    return node.file_path, node.start_line, node.id
