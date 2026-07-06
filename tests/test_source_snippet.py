from makewiki.wiki.source import read_source_snippet


def _write(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_reads_inclusive_line_range(tmp_path):
    _write(tmp_path, "a.c", "l1\nl2\nl3\nl4\nl5\n")

    snippet = read_source_snippet(tmp_path, "a.c", 2, end_line=4)

    assert snippet == "l2\nl3\nl4"


def test_caps_at_max_lines(tmp_path):
    _write(tmp_path, "a.c", "\n".join(f"line{i}" for i in range(1, 101)) + "\n")

    snippet = read_source_snippet(tmp_path, "a.c", 1, end_line=100, max_lines=3)

    assert snippet == "line1\nline2\nline3"


def test_reads_from_start_when_no_end_line(tmp_path):
    _write(tmp_path, "a.c", "l1\nl2\nl3\nl4\n")

    snippet = read_source_snippet(tmp_path, "a.c", 3, max_lines=10)

    assert snippet == "l3\nl4"


def test_missing_file_returns_empty(tmp_path):
    assert read_source_snippet(tmp_path, "nope.c", 1) == ""
