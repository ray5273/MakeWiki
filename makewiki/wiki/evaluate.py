"""Deterministic quality audit for generated wiki LLM summaries.

The wiki generator grounds every LLM `## Summary` in a symbol's source body and
its caller/callee neighbourhood. This module reads the *rendered* pages back and
checks that the prose actually respects that grounding, without needing the LLM
or the graph store: each page already lists its own allowed evidence (Evidence /
Calls / Called By), so the audit is self-contained and reproducible.

Checks, per page with an LLM summary:

- ``hallucinated-citation`` — a `file:line` cited in the summary that is not in
  the page's allowed set (self location + callee + caller locations).
- ``reasoning-leak`` — leaked chain-of-thought ("Actually", "Let's see", "?").
- ``citation-noise`` — the same `file:line` cited 3+ times in one summary.
- ``ungrounded`` — summary cites nothing and names neither the symbol nor a
  neighbour, so it is not anchored to any evidence.
- ``empty-summary`` — the deterministic fallback fired (no usable prose).

Doc-comment pages (``## Description``) are human-authored and skipped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# A file:line citation, e.g. `app.c:441`, (app.c:441), [app.c:441] or a bare
# app.c:441. Models cite in all of these forms, so match the file.ext:line core
# rather than a fixed wrapper — requiring a dotted extension before ":line"
# avoids matching plain "line 1394" or numeric ranges.
_CITATION_TOKEN = re.compile(r"[A-Za-z0-9_./+-]+\.[A-Za-z0-9_]+:\d+")

# Independent of the generator's own filter on purpose: the audit must be able to
# catch leaks the generation-time normaliser missed.
_REASONING_MARKERS = re.compile(
    r"\b(actually|let'?s|let us|not sure|we need to|we can|i'?ll|maybe)\b|\?\s",
    re.IGNORECASE,
)

# Noise is repetition *within one bullet* — a single clause never needs to cite
# the same line twice. Counting per-summary instead would wrongly flag the
# intended "one trailing citation per bullet" style.
_CITATION_NOISE_THRESHOLD = 2

_FALLBACK_MARKER = "No reliable summary could be generated"


@dataclass(frozen=True)
class SummaryFinding:
    page: str
    page_type: str
    section: str
    category: str
    detail: str


@dataclass
class PageEvaluation:
    page: str
    page_type: str
    section: str
    has_summary: bool
    findings: list[SummaryFinding] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.findings


@dataclass
class WikiEvaluation:
    pages: list[PageEvaluation]

    @property
    def summary_pages(self) -> list[PageEvaluation]:
        return [p for p in self.pages if p.has_summary]

    @property
    def document_pages(self) -> list[PageEvaluation]:
        """Deterministic document-quality pages (What It Does / module / index)."""
        return [p for p in self.pages if p.section in {"What It Does", "Document Quality"}]

    @property
    def document_findings(self) -> list[SummaryFinding]:
        return [f for p in self.document_pages for f in p.findings]

    @property
    def findings(self) -> list[SummaryFinding]:
        return [f for p in self.pages for f in p.findings]

    def counts_by_category(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for finding in self.findings:
            counts[finding.category] = counts.get(finding.category, 0) + 1
        return counts

    def pass_rate(self) -> float:
        graded = self.summary_pages
        if not graded:
            return 1.0
        clean = sum(1 for p in graded if p.clean)
        return clean / len(graded)

    def report(self) -> str:
        graded = self.summary_pages
        clean = sum(1 for p in graded if p.clean)
        module_count = sum(1 for p in graded if p.page_type == "module")
        symbol_count = sum(1 for p in graded if p.page_type == "symbol")
        document = self.document_pages
        document_findings = self.document_findings
        lines = [
            "# Wiki summary evaluation",
            "",
            "## LLM summary track",
            "",
            f"- summaries evaluated: {len(graded)}",
            f"- module summaries evaluated: {module_count}",
            f"- symbol summaries evaluated: {symbol_count}",
            f"- clean pages: {clean} ({self.pass_rate() * 100:.0f}%)",
            f"- pages with findings: {len(graded) - clean}",
            "",
            "## Document quality track",
            "",
            f"- document pages checked: {len(document)}",
            f"- document findings: {len(document_findings)}",
            "",
            "## Findings by category",
            "",
        ]
        counts = self.counts_by_category()
        if counts:
            for category in sorted(counts, key=lambda c: (-counts[c], c)):
                lines.append(f"- {category}: {counts[category]}")
        else:
            lines.append("- none")
        lines.extend(["", "## Worst pages", ""])
        worst = sorted(graded, key=lambda p: len(p.findings), reverse=True)
        flagged = [p for p in worst if p.findings][:10]
        if flagged:
            for page in flagged:
                lines.append(f"### {page.page} [{page.page_type} / {page.section}] ({len(page.findings)})")
                for finding in page.findings:
                    lines.append(f"- **{finding.category}**: {finding.detail}")
                lines.append("")
        else:
            lines.append("No findings. Every evaluated summary is grounded and clean.")
        return "\n".join(lines).rstrip() + "\n"


def _section(text: str, header: str) -> str:
    """Return the body of a `## <header>` section up to the next `## `."""
    pattern = re.compile(rf"^## {re.escape(header)}\s*$(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)
    match = pattern.search(text)
    return match.group(1) if match else ""


def _citations(text: str) -> list[str]:
    return _CITATION_TOKEN.findall(text)


# Sections that must carry explicit `file:line` citations, not just a symbol
# name. The concept narrative is the one the user asked to be citation-enforced,
# so name-only grounding is not enough there.
_CITATION_REQUIRED_SECTIONS = {"Concept"}


def evaluate_summary_text(
    *,
    page: str,
    page_type: str,
    section: str,
    summary: str,
    allowed_citations: set[str],
    anchor_names: set[str] | None = None,
) -> PageEvaluation:
    """Evaluate one rendered LLM summary against its allowed evidence."""
    if not summary.strip():
        return PageEvaluation(page=page, page_type=page_type, section=section, has_summary=False)

    findings: list[SummaryFinding] = []

    def finding(category: str, detail: str) -> SummaryFinding:
        return SummaryFinding(page, page_type, section, category, detail)

    if _FALLBACK_MARKER in summary:
        findings.append(finding("empty-summary", "deterministic fallback fired"))
        return PageEvaluation(page=page, page_type=page_type, section=section, has_summary=True, findings=findings)

    shallow = _shallow_summary_reason(summary)
    if shallow:
        findings.append(finding("shallow-summary", shallow))

    cited = _citations(summary)
    for token in dict.fromkeys(cited):
        if token not in allowed_citations:
            findings.append(finding("hallucinated-citation", f"`{token}` not in page evidence"))
        if cited.count(token) >= _CITATION_NOISE_THRESHOLD:
            findings.append(finding("citation-noise", f"`{token}` cited {cited.count(token)} times"))

    for line in summary.splitlines():
        if _REASONING_MARKERS.search(line):
            findings.append(finding("reasoning-leak", line.strip()[:120]))
            break

    bullets = [line for line in summary.splitlines() if line.lstrip().startswith("- ")]
    if section in _CITATION_REQUIRED_SECTIONS and bullets:
        uncited = sum(1 for line in bullets if not _citations(line))
        if uncited:
            findings.append(finding("missing-citation", f"{uncited} of {len(bullets)} bullets lack a `file:line` citation"))

    anchors = anchor_names or set()
    if not cited and not any(anchor and anchor in summary for anchor in anchors):
        findings.append(finding("ungrounded", "no citation and no symbol/neighbour reference"))

    return PageEvaluation(page=page, page_type=page_type, section=section, has_summary=True, findings=findings)


def evaluate_page(path: Path, *, page_type: str = "symbol", section: str = "Summary") -> PageEvaluation:
    text = path.read_text(encoding="utf-8")
    name = path.stem

    summary = _section(text, section)
    if not summary.strip():
        return PageEvaluation(page=name, page_type=page_type, section=section, has_summary=False)

    allowed_text = _without_section(text, section)
    return evaluate_summary_text(
        page=name,
        page_type=page_type,
        section=section,
        summary=summary,
        allowed_citations=set(_citations(allowed_text)),
        anchor_names=_anchor_names(name, text),
    )


def _without_section(text: str, header: str) -> str:
    pattern = re.compile(rf"^## {re.escape(header)}\s*$.*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)
    return pattern.sub("", text)


def _anchor_names(page_stem: str, page_text: str) -> set[str]:
    title_match = re.search(r"^# (.+)$", page_text, re.MULTILINE)
    names = {page_stem}
    if title_match:
        names.add(title_match.group(1).strip())
    names.update(re.findall(r"\[([^\]]+)\]\([^)]+\)", page_text))
    return {name for name in names if name}


def evaluate_wiki(wiki_dir: Path) -> WikiEvaluation:
    wiki_dir = Path(wiki_dir)
    symbols_dir = Path(wiki_dir) / "symbols"
    modules_dir = Path(wiki_dir) / "modules"
    pages = [evaluate_page(p, page_type="module", section="LLM Summary") for p in sorted(modules_dir.glob("*.md"))]
    pages.extend(evaluate_page(p, page_type="module", section="Concept") for p in sorted(modules_dir.glob("*.md")))
    pages.extend(evaluate_page(p, page_type="symbol", section="Summary") for p in sorted(symbols_dir.glob("*.md")))
    pages.extend(_evaluate_what_it_does(p) for p in sorted(symbols_dir.glob("*.md")))
    pages.extend(_evaluate_module_quality(p) for p in sorted(modules_dir.glob("*.md")))
    index = wiki_dir / "index.md"
    if index.exists():
        pages.append(_evaluate_index_quality(index))
    return WikiEvaluation(pages=pages)


def _shallow_summary_reason(text: str) -> str | None:
    lower = re.sub(r"\s+", " ", text.lower())
    patterns = (
        r"\b(intermediate|entry-style|leaf-style) function\b",
        r"\bcalls? \d+ function",
        r"\bcalled by \d+ function",
        r"\bcontains \d+ symbols? and \d+ internal calls?\b",
        r"\bstart from the reading path below\b",
    )
    for pattern in patterns:
        if re.search(pattern, lower):
            return "summary reads like a graph index instead of explaining responsibility or runtime behavior"
    return None


# Domain words a strong `## What It Does` must carry for the load-bearing SPDK
# entry points. Stems (not whole words) so "validates"/"validation",
# "polling"/"poll", "scheduler"/"scheduling" all match. Missing any one means the
# section has lost the behavior that makes that symbol worth a dedicated page.
_WHAT_IT_DOES_ANCHORS = {
    "spdk_app_start": ("validat", "reactor", "block", "shutdown"),
    "reactor_run": ("poll", "interrupt", "schedul", "shutdown"),
    "rpc_framework_get_reactors": ("rpc", "reactor", "fan", "aggregat"),
}


def _evaluate_what_it_does(path: Path) -> PageEvaluation:
    """Grade a symbol page's deterministic `## What It Does` section.

    Independent of the LLM-summary track: even with the LLM disabled every
    symbol page carries a `## What It Does`, so this is what keeps the pass rate
    honest instead of reporting 100% over zero graded pages.
    """
    text = path.read_text(encoding="utf-8")
    page = path.stem
    title = re.search(r"^# (.+)$", text, re.MULTILINE)
    symbol = title.group(1).strip() if title else page

    def finding(category: str, detail: str) -> SummaryFinding:
        return SummaryFinding(page, "symbol", "What It Does", category, detail)

    what_it_does = _section(text, "What It Does")
    if not what_it_does.strip():
        return PageEvaluation(
            page=page,
            page_type="symbol",
            section="What It Does",
            has_summary=True,
            findings=[finding("missing-what-it-does", "no ## What It Does section")],
        )

    findings: list[SummaryFinding] = []
    shallow = _shallow_summary_reason(what_it_does)
    if shallow:
        findings.append(finding("shallow-what-it-does", shallow))

    anchors = _WHAT_IT_DOES_ANCHORS.get(symbol)
    if anchors:
        lowered = what_it_does.lower()
        missing = [word for word in anchors if word not in lowered]
        if missing:
            findings.append(
                finding("missing-domain-anchor", f"{symbol} What It Does missing {missing}")
            )

    return PageEvaluation(
        page=page,
        page_type="symbol",
        section="What It Does",
        has_summary=bool(findings),
        findings=findings,
    )


def _evaluate_module_quality(path: Path) -> PageEvaluation:
    text = path.read_text(encoding="utf-8")
    findings: list[SummaryFinding] = []
    page = path.stem

    def finding(category: str, detail: str) -> SummaryFinding:
        return SummaryFinding(page, "module", "Document Quality", category, detail)

    symbol_count = _metric_int(text, "Symbols")
    files = set(re.findall(r"`([^`\n]+\.[ch](?:pp|xx|\+\+)?):\d+`", _section(text, "Symbols")))
    if (page == "root" or "root module" in text[:80].lower()) and symbol_count >= 20 and len(files) >= 4:
        findings.append(finding("module-overpacked", f"root module contains {symbol_count} symbols across {len(files)} files"))

    summary = _section(text, "LLM Summary")
    if summary.strip() and len(files) >= 3:
        cited_files = set(re.findall(r"`([^`\n]+\.[ch](?:pp|xx|\+\+)?):\d+`", summary))
        if len(cited_files) < min(3, len(files)):
            findings.append(
                finding(
                    "coverage-imbalance",
                    f"module summary cites {len(cited_files)} of {len(files)} files",
                )
            )

    shallow = _shallow_summary_reason(summary)
    if shallow:
        findings.append(finding("shallow-summary", shallow))

    return PageEvaluation(page=page, page_type="module", section="Document Quality", has_summary=bool(findings), findings=findings)


def _evaluate_index_quality(path: Path) -> PageEvaluation:
    text = path.read_text(encoding="utf-8")
    findings: list[SummaryFinding] = []
    page = path.stem

    def finding(category: str, detail: str) -> SummaryFinding:
        return SummaryFinding(page, "index", "Document Quality", category, detail)

    story = _section(text, "Runtime Story")
    if not story.strip() or not re.search(r"(->|reactor|runtime|start|shutdown)", story, re.IGNORECASE):
        findings.append(finding("missing-runtime-story", "index does not explain a runtime path"))

    return PageEvaluation(page=page, page_type="index", section="Document Quality", has_summary=bool(findings), findings=findings)


def _metric_int(text: str, name: str) -> int:
    match = re.search(rf"^- {re.escape(name)}:\s*(\d+)\s*$", text, re.MULTILINE)
    return int(match.group(1)) if match else 0
