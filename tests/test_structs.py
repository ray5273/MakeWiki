from pathlib import Path

from makewiki.analysis.structs import build_struct_index, extract_structs


def test_extract_plain_struct_with_fields():
    source = """
struct spdk_reactor {
    uint32_t lcore;
    struct spdk_ring *events;
    bool in_interrupt;
};
"""
    structs = extract_structs(source)

    assert len(structs) == 1
    reactor = structs[0]
    assert reactor.name == "spdk_reactor"
    assert reactor.start_line == 2
    names = [f.name for f in reactor.fields]
    assert names == ["lcore", "events", "in_interrupt"]
    types = {f.name: f.type for f in reactor.fields}
    assert types["events"] == "struct spdk_ring *"
    assert types["lcore"] == "uint32_t"


def test_extract_typedef_struct():
    source = """
typedef struct {
    int a;
    char *name;
} my_opts;
"""
    structs = extract_structs(source)

    assert [s.name for s in structs] == ["my_opts"]
    assert [f.name for f in structs[0].fields] == ["a", "name"]


def test_extract_ignores_comments_in_fields():
    source = """
struct s {
    // a comment line
    int x; /* trailing */
};
"""
    structs = extract_structs(source)
    assert [f.name for f in structs[0].fields] == ["x"]


def test_build_struct_index_uses_repo_relative_path(tmp_path):
    (tmp_path / "reactor.c").write_text(
        "struct reactor_state {\n    int lcore;\n    bool busy;\n};\n", encoding="utf-8"
    )

    index = build_struct_index(tmp_path)

    assert "reactor_state" in index
    assert index["reactor_state"].file_path == "reactor.c"
    assert index["reactor_state"].start_line == 1


def test_build_struct_index_shows_docroot_parent_relative_path(tmp_path):
    repo = tmp_path / "lib" / "event"
    repo.mkdir(parents=True)
    include = tmp_path / "include"
    (include / "spdk").mkdir(parents=True)
    (include / "spdk" / "event.h").write_text(
        "struct spdk_app_opts {\n    const char *name;\n    int shm_id;\n};\n", encoding="utf-8"
    )

    index = build_struct_index(repo, extra_roots=[include])

    assert index["spdk_app_opts"].file_path == "include/spdk/event.h"


def test_build_struct_index_skips_forward_declarations(tmp_path):
    # A struct with no fields (forward decl / opaque) is not indexed.
    (tmp_path / "a.c").write_text("struct opaque;\nstruct real {\n    int x;\n};\n", encoding="utf-8")

    index = build_struct_index(tmp_path)

    assert "opaque" not in index
    assert "real" in index
