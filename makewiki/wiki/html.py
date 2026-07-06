"""Render the Markdown wiki into a browsable static HTML site.

The generator writes Markdown (with mermaid code blocks and relative `.md`
links). This module wraps each page in a self-contained HTML file that renders
the Markdown and its mermaid diagrams client-side, adds a shared sidebar, and
rewrites `.md` links to `.html`. Pages embed their own Markdown (base64) instead
of fetching it, so the site opens directly from `file://` with no local server.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

_MARKED_CDN = "https://cdn.jsdelivr.net/npm/marked@12/marked.min.js"
_MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"


@dataclass(frozen=True)
class HtmlPage:
    source: Path
    output: Path


def build_html_site(wiki_dir: str | Path, out_dir: str | Path) -> list[HtmlPage]:
    """Convert every Markdown page under `wiki_dir` into an HTML page under `out_dir`."""
    wiki_root = Path(wiki_dir)
    out_root = Path(out_dir)
    if not wiki_root.is_dir():
        raise FileNotFoundError(f"wiki directory does not exist: {wiki_root}")

    md_pages = sorted(wiki_root.rglob("*.md"))
    if not md_pages:
        raise FileNotFoundError(f"no Markdown pages under {wiki_root}")

    nav_template = _build_nav_template(wiki_root, md_pages)

    built: list[HtmlPage] = []
    for md_path in md_pages:
        rel = md_path.relative_to(wiki_root)
        depth = len(rel.parts) - 1
        prefix = "../" * depth
        html_path = out_root / rel.with_suffix(".html")
        html_path.parent.mkdir(parents=True, exist_ok=True)
        markdown = md_path.read_text(encoding="utf-8")
        html_path.write_text(
            _render_page(
                title=_page_title(rel, markdown),
                markdown=markdown,
                nav=nav_template.replace("{{PREFIX}}", prefix),
                prefix=prefix,
            ),
            encoding="utf-8",
        )
        built.append(HtmlPage(source=md_path, output=html_path))
    return built


def _page_title(rel: Path, markdown: str) -> str:
    for line in markdown.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return rel.stem


def _nav_link(rel: Path, label: str) -> str:
    href = "{{PREFIX}}" + rel.with_suffix(".html").as_posix()
    return f'<a href="{href}">{_escape(label)}</a>'


def _build_nav_template(wiki_root: Path, md_pages: list[Path]) -> str:
    def rel_of(name: str) -> Path | None:
        path = wiki_root / name
        return path.relative_to(wiki_root) if path.exists() else None

    parts: list[str] = ['<nav class="sidebar">']
    parts.append('<div class="brand">MakeWiki</div>')

    # Top-level pages in a stable, reading-friendly order.
    top: list[str] = []
    for name, label in (("index.md", "Overview"), ("data-model.md", "Data Model"), ("reference.md", "Symbol Reference")):
        rel = rel_of(name)
        if rel is not None:
            top.append(f"<li>{_nav_link(rel, label)}</li>")
    if top:
        parts.append("<ul>" + "".join(top) + "</ul>")

    for folder, label in (("modules", "Modules"), ("flows", "Flows"), ("symbols", "Symbols")):
        folder_pages = sorted(p for p in md_pages if p.parent.name == folder)
        if not folder_pages:
            continue
        # Symbols get a collapsed group to keep the long list manageable.
        open_attr = "" if folder == "symbols" else " open"
        items = "".join(
            f"<li>{_nav_link(p.relative_to(wiki_root), _page_title(p.relative_to(wiki_root), p.read_text(encoding='utf-8')))}</li>"
            for p in folder_pages
        )
        parts.append(f"<details{open_attr}><summary>{_escape(label)} ({len(folder_pages)})</summary><ul>{items}</ul></details>")

    parts.append("</nav>")
    return "".join(parts)


def _render_page(*, title: str, markdown: str, nav: str, prefix: str) -> str:
    encoded = base64.b64encode(markdown.encode("utf-8")).decode("ascii")
    return _TEMPLATE.format(
        title=_escape(title),
        nav=nav,
        markdown_b64=encoded,
        marked=_MARKED_CDN,
        mermaid=_MERMAID_CDN,
        css=_CSS,
    )


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


_CSS = """
:root { color-scheme: light dark; --bg:#ffffff; --fg:#1b1f24; --muted:#5b6570;
  --border:#e3e8ee; --accent:#2f6feb; --code-bg:#f4f6f9; --sidebar:#f7f9fc; }
@media (prefers-color-scheme: dark) { :root { --bg:#0f1419; --fg:#d7dde3;
  --muted:#8b95a1; --border:#232a33; --accent:#5a8bff; --code-bg:#161b22; --sidebar:#12171d; } }
* { box-sizing: border-box; }
body { margin:0; font: 16px/1.65 -apple-system, "Segoe UI", Roboto, sans-serif;
  color:var(--fg); background:var(--bg); }
.layout { display:flex; min-height:100vh; }
.sidebar { width:300px; flex:0 0 300px; background:var(--sidebar);
  border-right:1px solid var(--border); padding:20px 16px; overflow-y:auto;
  height:100vh; position:sticky; top:0; }
.sidebar .brand { font-weight:700; font-size:18px; margin-bottom:14px; color:var(--accent); }
.sidebar ul { list-style:none; margin:0 0 12px; padding:0; }
.sidebar li { margin:2px 0; }
.sidebar a { color:var(--fg); text-decoration:none; font-size:14px; display:block;
  padding:3px 6px; border-radius:6px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.sidebar a:hover { background:var(--border); }
.sidebar summary { cursor:pointer; font-weight:600; font-size:14px; margin:8px 0 4px; color:var(--muted); }
.content { flex:1; padding:32px 48px; max-width:900px; margin:0 auto; }
.content h1 { font-size:30px; margin-top:0; border-bottom:1px solid var(--border); padding-bottom:10px; }
.content h2 { font-size:22px; margin-top:32px; border-bottom:1px solid var(--border); padding-bottom:6px; }
.content h3 { font-size:18px; margin-top:24px; }
.content a { color:var(--accent); text-decoration:none; }
.content a:hover { text-decoration:underline; }
.content code { background:var(--code-bg); padding:1px 5px; border-radius:4px;
  font: 13px/1.5 "SF Mono", ui-monospace, Menlo, Consolas, monospace; }
.content pre { background:var(--code-bg); padding:14px 16px; border-radius:8px; overflow-x:auto; }
.content pre code { background:none; padding:0; }
.content table { border-collapse:collapse; width:100%; }
.content th, .content td { border:1px solid var(--border); padding:6px 10px; text-align:left; }
.mermaid { background:var(--code-bg); border:1px solid var(--border); border-radius:8px;
  padding:16px; margin:16px 0; text-align:center; overflow-x:auto; }
"""

_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · MakeWiki</title>
<style>{css}</style>
</head>
<body>
<div class="layout">
{nav}
<main class="content" id="content">Rendering…</main>
</div>
<script src="{marked}"></script>
<script src="{mermaid}"></script>
<script id="md-src" type="application/base64">{markdown_b64}</script>
<script>
(function () {{
  var b64 = document.getElementById('md-src').textContent.trim();
  var md = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), function (c) {{ return c.charCodeAt(0); }}));
  var content = document.getElementById('content');
  content.innerHTML = marked.parse(md);
  // .md links -> .html so navigation works in the rendered site.
  content.querySelectorAll('a[href$=".md"]').forEach(function (a) {{
    a.setAttribute('href', a.getAttribute('href').replace(/\\.md(#|$)/, '.html$1'));
  }});
  // Turn ```mermaid code blocks into mermaid diagrams.
  content.querySelectorAll('code.language-mermaid').forEach(function (code) {{
    var div = document.createElement('div');
    div.className = 'mermaid';
    div.textContent = code.textContent;
    code.parentElement.replaceWith(div);
  }});
  var dark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
  mermaid.initialize({{ startOnLoad: false, theme: dark ? 'dark' : 'default', securityLevel: 'strict' }});
  mermaid.run();
  document.title = (content.querySelector('h1') ? content.querySelector('h1').textContent : '{title}') + ' · MakeWiki';
}})();
</script>
</body>
</html>
"""
