from makewiki.graph import (
    CodeFacts,
    GlobalFact,
    GraphStore,
    IncludeFact,
    MemberFact,
    StructFact,
)


def _sample_facts(repo_root):
    facts = CodeFacts(repo_root=repo_root)
    facts.add_struct(StructFact(name="spdk_thread", file_path="lib/thread.c", start_line=42))
    facts.add_member(
        MemberFact(
            struct_name="spdk_bdev_fn_table",
            name="destruct",
            file_path="include/spdk/bdev_module.h",
            start_line=88,
            is_function_pointer=True,
        )
    )
    facts.add_global(
        GlobalFact(name="g_reactors", file_path="lib/reactor.c", start_line=30, type_name="struct spdk_reactor *")
    )
    facts.add_include(IncludeFact(file_path="lib/reactor.c", target="spdk/thread.h", start_line=5))
    facts.tag_function("lib_reactor_c::spdk_reactors_start", "entrypoint")
    return facts


def test_facts_store_round_trip(tmp_path):
    store_path = tmp_path / "g.sqlite"
    with GraphStore(store_path) as store:
        store.save_facts(_sample_facts(tmp_path))
    with GraphStore(store_path) as store:
        loaded = store.load_facts(tmp_path)

    assert [(s.name, s.file_path, s.start_line) for s in loaded.structs] == [
        ("spdk_thread", "lib/thread.c", 42)
    ]
    assert [(m.struct_name, m.name, m.is_function_pointer) for m in loaded.members] == [
        ("spdk_bdev_fn_table", "destruct", True)
    ]
    assert [(g.name, g.type_name) for g in loaded.globals] == [
        ("g_reactors", "struct spdk_reactor *")
    ]
    assert [(i.file_path, i.target) for i in loaded.includes] == [("lib/reactor.c", "spdk/thread.h")]
    assert loaded.tags_for("lib_reactor_c::spdk_reactors_start") == {"entrypoint"}


def test_facts_store_save_replaces_previous(tmp_path):
    store_path = tmp_path / "g.sqlite"
    with GraphStore(store_path) as store:
        store.save_facts(_sample_facts(tmp_path))
        replacement = CodeFacts(repo_root=tmp_path)
        replacement.add_struct(StructFact(name="only_one", file_path="a.c", start_line=1))
        store.save_facts(replacement)
    with GraphStore(store_path) as store:
        loaded = store.load_facts(tmp_path)

    assert [s.name for s in loaded.structs] == ["only_one"]
    assert loaded.globals == []
    assert loaded.tags == {}


def test_store_records_schema_version(tmp_path):
    store_path = tmp_path / "g.sqlite"
    with GraphStore(store_path) as store:
        assert store.schema_version >= 2
