from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from makewiki.config import MakeWikiConfig
from makewiki.graph import CodeEdge, CodeGraph, DocComment, SymbolNode, extract_subgraph
from makewiki.llm import LLMClient
from makewiki.render import render_mermaid


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
) -> list[WikiPage]:
    """Write deterministic graph-backed Markdown wiki pages."""

    docs = docs or {}

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
        "## Major Subsystems",
        "",
    ]
    lines.extend(_major_subsystem_lines(nodes))
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
        "## Phase Map",
        "",
    ]
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
        "## Responsibilities",
        "",
    ]
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
                ),
                "",
            ]
        )

    lines.extend(["## Reading Path", ""])
    if roots:
        for node in roots[:10]:
            outgoing_count = len(outgoing.get(node.id, []))
            lines.append(f"- Start with [{node.name}]({_relative_symbol_from_module(module, node, symbol_paths, module_paths)}) at `{node.file_path}:{node.start_line}`. {_node_intent(node)} It reaches {outgoing_count} direct calls in this graph.")
    else:
        lines.append("- None")

    lines.extend(["", "## Module Call Graph", "", "```mermaid", render_mermaid(diagram).rstrip(), "```", "", "## Symbols", ""])
    for node in nodes:
        lines.append(f"- [{node.name}]({_relative_symbol_from_module(module, node, symbol_paths, module_paths)}) - `{node.file_path}:{node.start_line}`")
    lines.append("")
    return "\n".join(lines)


def _generate_module_summary(
    llm_client: LLMClient,
    module: str,
    nodes: list[SymbolNode],
    internal_edges: list[CodeEdge],
    inbound_edges: list[CodeEdge],
    outbound_edges: list[CodeEdge],
) -> str:
    evidence_nodes = nodes[:40]
    allowed_citations = ", ".join(f"`{node.file_path}:{node.start_line}`" for node in evidence_nodes[:20])
    system = (
        "You write concise code wiki documentation from provided graph evidence only. "
        "Do not invent files, functions, or call relationships. "
        "Every non-trivial claim must include one of the provided `file:line` citations. "
        "Return only final Markdown bullets. Do not reveal analysis or reasoning. Do not output Mermaid."
    )
    user = "\n".join(
        [
            f"Module: {module}",
            f"Symbol count: {len(nodes)}",
            f"Internal call count: {len(internal_edges)}",
            f"Inbound call count: {len(inbound_edges)}",
            f"Outbound call count: {len(outbound_edges)}",
            "",
            "Allowed citations:",
            allowed_citations or "(none)",
            "",
            "Representative symbols:",
            *[
                f"- {node.name}: `{node.file_path}:{node.start_line}` signature={node.signature or '(unknown)'}"
                for node in evidence_nodes
            ],
            "",
            "Write 2-4 final Markdown bullets explaining the module's likely responsibility and reading order. "
            "Start every line with '- '. Use only the allowed citations. "
            "Do not include a preface, labels, hidden reasoning, or draft notes.",
        ]
    )
    return _normalize_llm_summary(llm_client.complete(system=system, user=user))


def _normalize_llm_summary(text: str) -> str:
    bullet_lines = [line.rstrip() for line in text.splitlines() if line.lstrip().startswith("- ")]
    if bullet_lines:
        return "\n".join(bullet_lines[:4])
    return text.strip()


def _render_symbol_page(
    graph: CodeGraph,
    node: SymbolNode,
    incoming: list[CodeEdge],
    outgoing: list[CodeEdge],
    symbol_paths: dict[str, Path],
    *,
    max_depth: int,
    doc: DocComment | None = None,
) -> str:
    subgraph = extract_subgraph(graph, node.id, max_depth, edge_types={"calls"})
    role = _symbol_role(node, incoming, outgoing)
    lines = [
        f"# {node.name}",
        "",
        "## Role",
        "",
        f"{role} The implementation starts at `{node.file_path}:{node.start_line}`.",
        "",
    ]
    if doc is not None:
        lines.extend(_doc_comment_lines(doc))
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
        f"This flow explains `{root.name}` as a reader-facing execution path, starting at "
        f"`{root.file_path}:{root.start_line}`. It expands to {len(ordered)} function(s) and "
        f"{len(subgraph.edges)} call relationship(s) across {file_text}. {_node_intent(root)}"
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
        f"{link} handles {_intent_noun(node)} and then delegates to {call_links}{extra}. "
        f"Evidence: `{node.file_path}:{node.start_line}`."
    )


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
    modules: dict[str, list[SymbolNode]] = {}
    for node in nodes:
        modules.setdefault(_module_name(node.file_path), []).append(node)
    return {
        module: sorted(module_nodes, key=_node_sort_key)
        for module, module_nodes in sorted(modules.items())
    }


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
