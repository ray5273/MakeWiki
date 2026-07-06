import base64
from pathlib import Path

import pytest

from makewiki.wiki.html import build_html_site


def _make_wiki(root: Path) -> None:
    (root / "index.md").write_text("# Overview\n\nSee [root](modules/root.md).\n", encoding="utf-8")
    (root / "modules").mkdir()
    (root / "modules" / "root.md").write_text(
        "# Root Module\n\n```mermaid\nflowchart TD\n  A --> B\n```\n", encoding="utf-8"
    )


def test_build_html_site_mirrors_structure(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _make_wiki(wiki)
    out = tmp_path / "site"

    pages = build_html_site(wiki, out)

    assert (out / "index.html").exists()
    assert (out / "modules" / "root.html").exists()
    assert {p.output.name for p in pages} == {"index.html", "root.html"}


def test_index_html_embeds_markdown_and_nav(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _make_wiki(wiki)
    out = tmp_path / "site"

    build_html_site(wiki, out)
    html = (out / "index.html").read_text(encoding="utf-8")

    # Sidebar lists the module page and top-level Overview.
    assert 'class="sidebar"' in html
    assert "Root Module" in html
    assert "marked" in html and "mermaid" in html

    # The page's own markdown is embedded (base64) so it renders from file://.
    start = html.index('type="application/base64">') + len('type="application/base64">')
    end = html.index("</script>", start)
    decoded = base64.b64decode(html[start:end].strip()).decode("utf-8")
    assert "# Overview" in decoded


def test_nested_page_uses_relative_prefix(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _make_wiki(wiki)
    out = tmp_path / "site"

    build_html_site(wiki, out)
    nested = (out / "modules" / "root.html").read_text(encoding="utf-8")

    # A page one level deep links back to the root with ../.
    assert 'href="../index.html"' in nested


def test_build_html_site_requires_pages(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        build_html_site(empty, tmp_path / "site")
