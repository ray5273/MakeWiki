from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from makewiki.analysis import ClangdAnalyzer, FixtureAnalyzer, JoernAnalyzer
from makewiki.build import discover_compile_commands
from makewiki.config import load_config
from makewiki.errors import GraphError, MakeWikiError
from makewiki.graph import GraphStore, extract_subgraph
from makewiki.llm import OPENROUTER_LOCKED_MODEL, RateLimitedLLMClient, openrouter_from_env
from makewiki.qa import QAOptions, answer_question, format_answer
from makewiki.render import render_mermaid
from makewiki.wiki import generate_wiki, validate_wiki


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "config" and args.config_command == "validate":
            return _cmd_config_validate(args)
        if args.command == "doctor":
            return _cmd_doctor(args)
        if args.command == "analyze":
            return _cmd_analyze(args)
        if args.command == "render":
            return _cmd_render(args)
        if args.command == "ask":
            return _cmd_ask(args)
        if args.command == "wiki" and args.wiki_command == "generate":
            return _cmd_wiki(args)
        if args.command == "wiki" and args.wiki_command == "validate":
            return _cmd_wiki_validate(args)
    except MakeWikiError as exc:
        print(f"makewiki: error: {exc}", file=sys.stderr)
        return 2

    parser.print_help()
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="makewiki")
    sub = parser.add_subparsers(dest="command")

    config = sub.add_parser("config")
    config_sub = config.add_subparsers(dest="config_command")
    validate = config_sub.add_parser("validate")
    validate.add_argument("repo")

    doctor = sub.add_parser("doctor")
    doctor.add_argument("repo", nargs="?", default=".")

    analyze = sub.add_parser("analyze")
    analyze.add_argument("repo")
    analyze.add_argument("--out", default=".makewiki/out")
    analyze.add_argument("--analyzer", choices=["fixture", "clangd", "joern"], default="joern")

    render = sub.add_parser("render")
    render.add_argument("repo")
    render.add_argument("--root", required=True)
    render.add_argument("--type", choices=["callgraph", "cfg", "sequence"], default="callgraph")
    render.add_argument("--depth", type=int, default=3)
    render.add_argument("--out", default=".makewiki/out")
    render.add_argument("--output")
    render.add_argument("--analyzer", choices=["fixture", "clangd", "joern"], default="joern")

    ask = sub.add_parser("ask")
    ask.add_argument("repo")
    ask.add_argument("question")
    ask.add_argument("--out", default=".makewiki/out")
    ask.add_argument("--depth", type=int, default=3)
    ask.add_argument("--format", choices=["text", "json"], default="text")
    ask.add_argument("--analyzer", choices=["fixture", "clangd", "joern"], default="joern")
    ask.add_argument("--llm", choices=["none", "openrouter"], default="none")
    ask.add_argument("--llm-model")
    ask.add_argument("--llm-rpm", type=int, default=20)

    wiki = sub.add_parser("wiki")
    wiki_sub = wiki.add_subparsers(dest="wiki_command")
    wiki_generate = wiki_sub.add_parser("generate")
    wiki_generate.add_argument("repo")
    wiki_generate.add_argument("--out", default=".makewiki/wiki")
    wiki_generate.add_argument("--graph-out", default=".makewiki/out")
    wiki_generate.add_argument("--depth", type=int, default=2)
    wiki_generate.add_argument("--analyzer", choices=["fixture", "clangd", "joern"], default="joern")
    wiki_generate.add_argument("--llm", choices=["none", "openrouter"], default="none")
    wiki_generate.add_argument("--llm-model")
    wiki_generate.add_argument("--llm-rpm", type=int, default=20)

    wiki_validate = wiki_sub.add_parser("validate")
    wiki_validate.add_argument("repo")
    wiki_validate.add_argument("--wiki", default=".makewiki/wiki")
    wiki_validate.add_argument("--graph-out", default=".makewiki/out")

    return parser


def _cmd_doctor(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    config = load_config(repo_root)
    checks = [
        _doctor_check("repo", repo_root.exists() and repo_root.is_dir(), str(repo_root), "pass an existing source directory"),
        _doctor_check("config", True, str(config.repo_root), "run makewiki config validate <repo>"),
        _doctor_check("joern", shutil.which("joern") is not None and shutil.which("joern-parse") is not None, _which_pair("joern", "joern-parse"), "install Joern (required for analysis)"),
        _doctor_check("clangd", shutil.which("clangd") is not None, shutil.which("clangd") or "not found", "install clangd or use --analyzer joern"),
        _doctor_check("compile_commands", (repo_root / "compile_commands.json").is_file(), str(repo_root / "compile_commands.json"), "generate compile_commands.json for clangd analysis"),
        _doctor_check("openrouter_key", bool(os.environ.get("OPENROUTER_API_KEY")), "set" if os.environ.get("OPENROUTER_API_KEY") else "not set", "export OPENROUTER_API_KEY for --llm openrouter"),
        _doctor_check("openrouter_model", True, OPENROUTER_LOCKED_MODEL, "pass the locked model with --llm-model or omit --llm-model"),
    ]

    width = max(len(check.name) for check in checks)
    print(f"MakeWiki doctor: {repo_root}")
    for check in checks:
        status = "OK" if check.ok else "WARN"
        print(f"{status:4} {check.name:<{width}} {check.detail}")
        if not check.ok:
            print(f"     fix: {check.fix}")
    warnings = sum(1 for check in checks if not check.ok)
    print(f"summary: {len(checks) - warnings} ok, {warnings} warning(s)")
    return 0


def _cmd_config_validate(args: argparse.Namespace) -> int:
    config = load_config(args.repo)
    print(f"valid: {config.repo_root}")
    if config.build.run_build:
        print("note: build.run_build is true, but this MVP will not execute build_command")
    return 0


class _DoctorCheck(argparse.Namespace):
    name: str
    ok: bool
    detail: str
    fix: str


def _doctor_check(name: str, ok: bool, detail: str, fix: str) -> _DoctorCheck:
    return _DoctorCheck(name=name, ok=ok, detail=detail, fix=fix)


def _which_pair(first: str, second: str) -> str:
    first_path = shutil.which(first)
    second_path = shutil.which(second)
    if first_path and second_path:
        return f"{first_path}, {second_path}"
    missing = [name for name, path in ((first, first_path), (second, second_path)) if path is None]
    return f"missing: {', '.join(missing)}"


def _cmd_analyze(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    config = load_config(repo_root)
    if args.analyzer == "clangd":
        discover_compile_commands(config)
    analysis = _analyzer(args.analyzer).analyze(repo_root, config)
    store_path = _store_path(repo_root, args.out)
    with GraphStore(store_path) as store:
        store.save_graph(analysis.graph)
        store.save_facts(analysis.facts)
    print(f"analyzed: {len(analysis.graph.nodes)} symbols, {len(analysis.graph.edges)} edges")
    print(f"graph: {store_path}")
    return 0


def _ensure_graph(analyzer_name: str, repo_root: Path, config, store_path: Path):
    """Load the stored graph, analyzing first (and persisting graph+facts) if absent."""
    if not store_path.exists():
        analysis = _analyzer(analyzer_name).analyze(repo_root, config)
        with GraphStore(store_path) as store:
            store.save_graph(analysis.graph)
            store.save_facts(analysis.facts)
    with GraphStore(store_path) as store:
        return store.load_graph(repo_root)


def _cmd_render(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    config = load_config(repo_root)
    store_path = _store_path(repo_root, args.out)
    graph = _ensure_graph(args.analyzer, repo_root, config, store_path)
    edge_types = {"calls"} if args.type in {"callgraph", "sequence"} else {"cfg_next", "cfg_branch"}
    subgraph = extract_subgraph(graph, args.root, args.depth, edge_types=edge_types)
    mermaid = render_mermaid(subgraph, diagram_type=args.type)
    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = repo_root / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(mermaid, encoding="utf-8")
        print(f"rendered: {output_path}")
    else:
        print(mermaid, end="")
    return 0


def _cmd_ask(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    config = load_config(repo_root)
    store_path = _store_path(repo_root, args.out)
    graph = _ensure_graph(args.analyzer, repo_root, config, store_path)

    llm_client = _llm_client(args.llm, args.llm_model, args.llm_rpm)
    result = answer_question(graph, args.question, QAOptions(max_depth=args.depth, llm_client=llm_client))
    print(format_answer(result, output_format=args.format))
    return 0


def _cmd_wiki(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    config = load_config(repo_root)
    store_path = _store_path(repo_root, args.graph_out)
    graph = _ensure_graph(args.analyzer, repo_root, config, store_path)

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = repo_root / out_path
    llm_client = _llm_client(args.llm, args.llm_model, args.llm_rpm)
    pages = generate_wiki(graph, config, out_path, max_depth=args.depth, llm_client=llm_client)
    print(f"wiki: {out_path}")
    print(f"pages: {len(pages)}")
    return 0


def _llm_client(provider: str, model: str | None, requests_per_minute: int = 20):
    if provider == "none":
        return None
    if requests_per_minute < 1:
        raise MakeWikiError("--llm-rpm must be >= 1")
    if provider == "openrouter":
        return RateLimitedLLMClient(openrouter_from_env(model=model), requests_per_minute)
    raise ValueError(provider)


def _cmd_wiki_validate(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo).resolve()
    store_path = _resolve_store_path(repo_root, args.graph_out)
    if not store_path.exists():
        raise GraphError(f"graph store not found: {store_path}")
    with GraphStore(store_path) as store:
        graph = store.load_graph(repo_root)

    wiki_path = Path(args.wiki)
    if not wiki_path.is_absolute():
        wiki_path = repo_root / wiki_path
    validate_wiki(graph, wiki_path)
    print(f"valid wiki: {wiki_path}")
    return 0


def _analyzer(name: str):
    if name == "fixture":
        return FixtureAnalyzer()
    if name == "clangd":
        return ClangdAnalyzer()
    if name == "joern":
        return JoernAnalyzer()
    raise ValueError(name)


def _store_path(repo_root: Path, out: str) -> Path:
    store_path = _resolve_store_path(repo_root, out)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    return store_path


def _resolve_store_path(repo_root: Path, out: str) -> Path:
    out_path = Path(out)
    if not out_path.is_absolute():
        out_path = repo_root / out_path
    return out_path / "graph.sqlite"


if __name__ == "__main__":
    raise SystemExit(main())
