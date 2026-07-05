from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass

from makewiki.errors import GraphError, LLMError
from makewiki.graph import CodeEdge, CodeGraph, SymbolNode, extract_subgraph
from makewiki.llm import LLMClient
from makewiki.render import render_mermaid

from .model import EvidenceCitation, QAResult


@dataclass(frozen=True)
class QAOptions:
    max_depth: int = 3
    llm_client: LLMClient | None = None


def answer_question(graph: CodeGraph, question: str, options: QAOptions | None = None) -> QAResult:
    """Answer graph-backed code questions with structured evidence."""

    options = options or QAOptions()
    normalized = _normalize_question(question)
    result = _answer_deterministic(graph, question, normalized, options)
    if options.llm_client is None or not result.evidence:
        return result
    return _polish_with_llm(options.llm_client, result)


def format_answer(result: QAResult, *, output_format: str = "text") -> str:
    if output_format == "json":
        return json.dumps(result.to_dict(), indent=2, sort_keys=True)
    if output_format != "text":
        raise ValueError(output_format)

    lines = [result.answer]
    if result.evidence:
        lines.extend(["", "Evidence:"])
        for citation in result.evidence:
            lines.append(f"- {citation.label()}")
    if result.mermaid:
        lines.extend(["", "```mermaid", result.mermaid.rstrip(), "```"])
    lines.extend(["", f"Confidence: {result.confidence}"])
    if result.fallback_reason:
        lines.append(f"Fallback: {result.fallback_reason}")
    return "\n".join(lines)


def _answer_deterministic(
    graph: CodeGraph,
    question: str,
    normalized: str,
    options: QAOptions,
) -> QAResult:
    if match := re.match(r"^(?:what does|what do) (.+?) call\??$", normalized):
        return _answer_callees(graph, question, match.group(1))
    if match := re.match(r"^(?:who calls|what calls|what starts) (.+?)\??$", normalized):
        return _answer_callers(graph, question, match.group(1))
    if match := re.match(r"^where is (.+?) defined\??$", normalized):
        return _answer_definition(graph, question, match.group(1))
    if match := re.match(r"^(?:show )?path from (.+?) to (.+?)\??$", normalized):
        return _answer_path(graph, question, match.group(1), match.group(2))
    if match := re.match(r"^explain flow from (.+?)\??$", normalized):
        return _answer_flow(graph, question, match.group(1), options.max_depth)
    return QAResult(
        question=question,
        answer=(
            "I can answer deterministic graph questions like `who calls X?`, "
            "`what does X call?`, `where is X defined?`, `show path from A to B`, "
            "and `explain flow from X`."
        ),
        confidence="low",
        fallback_reason="unsupported question pattern",
        kind="unsupported",
    )


def _answer_callees(graph: CodeGraph, question: str, symbol: str) -> QAResult:
    node = _require_symbol(graph, symbol)
    callees = [_edge_dst(graph, edge) for edge in _outgoing(graph, node.id)]
    evidence = _citations([node, *callees])
    if callees:
        names = ", ".join(f"`{callee.name}`" for callee in callees)
        answer = f"`{node.name}` directly calls {names}."
    else:
        answer = f"`{node.name}` has no direct callees in this graph."
    return QAResult(question=question, answer=answer, evidence=evidence, involved_symbols=_ids([node, *callees]), kind="callees")


def _answer_callers(graph: CodeGraph, question: str, symbol: str) -> QAResult:
    node = _require_symbol(graph, symbol)
    callers = [_edge_src(graph, edge) for edge in _incoming(graph, node.id)]
    evidence = _citations([node, *callers])
    if callers:
        names = ", ".join(f"`{caller.name}`" for caller in callers)
        answer = f"`{node.name}` is directly called by {names}."
    else:
        answer = f"`{node.name}` has no direct callers in this graph."
    return QAResult(question=question, answer=answer, evidence=evidence, involved_symbols=_ids([*callers, node]), kind="callers")


def _answer_definition(graph: CodeGraph, question: str, symbol: str) -> QAResult:
    node = _require_symbol(graph, symbol)
    answer = f"`{node.name}` is defined at `{node.file_path}:{node.start_line}`."
    if node.signature:
        answer += f" Signature: `{node.signature}`."
    return QAResult(question=question, answer=answer, evidence=_citations([node]), involved_symbols=[node.id], kind="definition")


def _answer_path(graph: CodeGraph, question: str, src: str, dst: str) -> QAResult:
    src_node = _require_symbol(graph, src)
    dst_node = _require_symbol(graph, dst)
    path = _shortest_call_path(graph, src_node.id, dst_node.id)
    if not path:
        return QAResult(
            question=question,
            answer=f"No call path from `{src_node.name}` to `{dst_node.name}` was found in this graph.",
            evidence=_citations([src_node, dst_node]),
            involved_symbols=[src_node.id, dst_node.id],
            confidence="medium",
            fallback_reason="no path in call graph",
            kind="path",
        )
    nodes = [graph.nodes[node_id] for node_id in path]
    answer = "Call path: " + " -> ".join(f"`{node.name}`" for node in nodes) + "."
    path_graph = CodeGraph(repo_root=graph.repo_root)
    for node in nodes:
        path_graph.add_node(node)
    for left, right in zip(path, path[1:]):
        path_graph.add_edge(CodeEdge(src_id=left, dst_id=right, rel="calls"))
    return QAResult(
        question=question,
        answer=answer,
        evidence=_citations(nodes),
        involved_symbols=path,
        mermaid=render_mermaid(path_graph),
        kind="path",
    )


def _answer_flow(graph: CodeGraph, question: str, symbol: str, max_depth: int) -> QAResult:
    node = _require_symbol(graph, symbol)
    subgraph = extract_subgraph(graph, node.id, max_depth, edge_types={"calls"})
    ordered = _flow_order(subgraph, node.id)
    if len(ordered) <= 1:
        answer = f"`{node.name}` does not expand to any callees within depth {max_depth}."
    else:
        answer = (
            f"`{node.name}` expands to {len(ordered) - 1} reachable function(s) within depth {max_depth}: "
            + ", ".join(f"`{item.name}`" for item in ordered[1:])
            + "."
        )
    return QAResult(
        question=question,
        answer=answer,
        evidence=_citations(ordered),
        involved_symbols=[item.id for item in ordered],
        mermaid=render_mermaid(subgraph),
        kind="flow",
    )


def _polish_with_llm(llm_client: LLMClient, result: QAResult) -> QAResult:
    allowed = ", ".join(f"`{item.file_path}:{item.start_line}`" for item in result.evidence)
    system = (
        "You rewrite code graph answers using only provided evidence. "
        "Do not add new claims, symbols, files, or citations. Return one concise paragraph."
    )
    user = "\n".join(
        [
            f"Question: {result.question}",
            f"Deterministic answer: {result.answer}",
            f"Allowed citations: {allowed}",
            "Rewrite the answer naturally. Keep cited file:line references when useful.",
        ]
    )
    try:
        polished = llm_client.complete(system=system, user=user).strip()
    except (LLMError, Exception) as exc:
        return QAResult(
            question=result.question,
            answer=result.answer,
            evidence=result.evidence,
            involved_symbols=result.involved_symbols,
            mermaid=result.mermaid,
            confidence=result.confidence,
            fallback_reason=f"LLM polish failed: {exc}",
            kind=result.kind,
        )
    if not polished:
        return result
    return QAResult(
        question=result.question,
        answer=polished,
        evidence=result.evidence,
        involved_symbols=result.involved_symbols,
        mermaid=result.mermaid,
        confidence=result.confidence,
        fallback_reason=result.fallback_reason,
        kind=result.kind,
    )


def _normalize_question(question: str) -> str:
    return re.sub(r"\s+", " ", question.strip().lower()).strip()


def _require_symbol(graph: CodeGraph, name_or_id: str) -> SymbolNode:
    cleaned = name_or_id.strip().strip("`'\" ")
    node = graph.find_symbol(cleaned)
    if node is None:
        raise GraphError(f"symbol not found: {cleaned}")
    return node


def _outgoing(graph: CodeGraph, node_id: str) -> list[CodeEdge]:
    return sorted(
        [edge for edge in graph.edges if edge.rel == "calls" and edge.src_id == node_id],
        key=lambda edge: _node_sort_key(graph.nodes[edge.dst_id]),
    )


def _incoming(graph: CodeGraph, node_id: str) -> list[CodeEdge]:
    return sorted(
        [edge for edge in graph.edges if edge.rel == "calls" and edge.dst_id == node_id],
        key=lambda edge: _node_sort_key(graph.nodes[edge.src_id]),
    )


def _edge_src(graph: CodeGraph, edge: CodeEdge) -> SymbolNode:
    return graph.nodes[edge.src_id]


def _edge_dst(graph: CodeGraph, edge: CodeEdge) -> SymbolNode:
    return graph.nodes[edge.dst_id]


def _shortest_call_path(graph: CodeGraph, src_id: str, dst_id: str) -> list[str] | None:
    adjacency: dict[str, list[str]] = {}
    for edge in sorted(graph.edges):
        if edge.rel == "calls":
            adjacency.setdefault(edge.src_id, []).append(edge.dst_id)
    queue: deque[list[str]] = deque([[src_id]])
    seen = {src_id}
    while queue:
        path = queue.popleft()
        current = path[-1]
        if current == dst_id:
            return path
        for next_id in sorted(adjacency.get(current, []), key=lambda item: _node_sort_key(graph.nodes[item])):
            if next_id not in seen:
                seen.add(next_id)
                queue.append([*path, next_id])
    return None


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
        for edge in sorted(adjacency.get(current, []), key=lambda item: _node_sort_key(graph.nodes[item.dst_id])):
            if edge.dst_id not in seen:
                queue.append(edge.dst_id)
    return ordered


def _citations(nodes: list[SymbolNode]) -> list[EvidenceCitation]:
    seen: set[str] = set()
    citations: list[EvidenceCitation] = []
    for node in nodes:
        if node.id in seen:
            continue
        seen.add(node.id)
        citations.append(
            EvidenceCitation(
                symbol_id=node.id,
                symbol_name=node.name,
                file_path=node.file_path,
                start_line=node.start_line,
            )
        )
    return citations


def _ids(nodes: list[SymbolNode]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for node in nodes:
        if node.id not in seen:
            seen.add(node.id)
            result.append(node.id)
    return result


def _node_sort_key(node: SymbolNode) -> tuple[str, int, str]:
    return node.file_path, node.start_line, node.id
