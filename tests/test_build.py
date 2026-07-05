from pathlib import Path

import pytest

from makewiki.build import discover_compile_commands, load_compile_commands, source_files_from_compile_commands
from makewiki.config import load_config
from makewiki.errors import BuildDatabaseError


def test_missing_compile_commands_is_actionable():
    repo = Path(__file__).parent / "fixtures" / "no_build"
    config = load_config(repo)

    with pytest.raises(BuildDatabaseError, match="compile_commands.json not found"):
        discover_compile_commands(config)


def test_fixture_mode_can_allow_missing_build_db():
    repo = Path(__file__).parent / "fixtures" / "no_build"
    config = load_config(repo)

    assert discover_compile_commands(config, allow_missing_for_fixture=True) is None


def test_load_compile_commands_normalizes_relative_paths(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    source = src / "main.c"
    source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
    compile_db = tmp_path / "compile_commands.json"
    compile_db.write_text(
        """
        [
          {
            "directory": "src",
            "file": "main.c",
            "arguments": ["cc", "-c", "main.c"]
          }
        ]
        """,
        encoding="utf-8",
    )

    entries = load_compile_commands(compile_db)

    assert len(entries) == 1
    assert entries[0].directory == src.resolve()
    assert entries[0].file == source.resolve()


def test_source_files_from_compile_commands_filters_to_repo_and_suffix(tmp_path):
    included = tmp_path / "included.c"
    header = tmp_path / "included.h"
    outside = tmp_path.parent / "outside.c"
    included.write_text("int included(void) { return 1; }\n", encoding="utf-8")
    header.write_text("int header(void);\n", encoding="utf-8")
    outside.write_text("int outside(void) { return 2; }\n", encoding="utf-8")
    compile_db = tmp_path / "compile_commands.json"
    compile_db.write_text(
        f"""
        [
          {{"directory": "{tmp_path}", "file": "included.c", "command": "cc -c included.c"}},
          {{"directory": "{tmp_path}", "file": "included.h", "command": "cc -c included.h"}},
          {{"directory": "{tmp_path}", "file": "{outside}", "command": "cc -c {outside}"}}
        ]
        """,
        encoding="utf-8",
    )

    files = source_files_from_compile_commands(compile_db, tmp_path, suffixes={".c"})

    assert files == (included.resolve(),)
