from pathlib import Path

import pytest

from makewiki.errors import GraphError
from makewiki.graph.facts import (
    CodeFacts,
    GlobalFact,
    IncludeFact,
    MemberFact,
    StructFact,
)


def test_codefacts_holds_struct_with_location():
    facts = CodeFacts(repo_root=Path("/tmp/repo"))
    facts.add_struct(StructFact(name="spdk_thread", file_path="lib/thread.c", start_line=42))

    assert [(s.name, s.file_path, s.start_line) for s in facts.structs] == [
        ("spdk_thread", "lib/thread.c", 42)
    ]


def test_struct_fact_requires_name():
    with pytest.raises(GraphError):
        StructFact(name="  ", file_path="lib/thread.c", start_line=1).validate()


def test_struct_fact_requires_positive_line():
    with pytest.raises(GraphError):
        StructFact(name="spdk_thread", file_path="lib/thread.c", start_line=0).validate()


def test_codefacts_rejects_invalid_struct_on_add():
    facts = CodeFacts(repo_root=Path("/tmp/repo"))
    with pytest.raises(GraphError):
        facts.add_struct(StructFact(name="", file_path="lib/thread.c", start_line=1))


def test_codefacts_holds_global_with_type():
    facts = CodeFacts(repo_root=Path("/tmp/repo"))
    facts.add_global(
        GlobalFact(name="g_reactors", file_path="lib/reactor.c", start_line=30, type_name="struct spdk_reactor *")
    )

    assert [(g.name, g.type_name) for g in facts.globals] == [("g_reactors", "struct spdk_reactor *")]


def test_codefacts_holds_include_edge():
    facts = CodeFacts(repo_root=Path("/tmp/repo"))
    facts.add_include(IncludeFact(file_path="lib/reactor.c", target="spdk/thread.h", start_line=5))

    assert [(i.file_path, i.target) for i in facts.includes] == [("lib/reactor.c", "spdk/thread.h")]


def test_member_fact_flags_function_pointer():
    facts = CodeFacts(repo_root=Path("/tmp/repo"))
    facts.add_member(
        MemberFact(
            struct_name="spdk_bdev_fn_table",
            name="destruct",
            file_path="include/spdk/bdev_module.h",
            start_line=88,
            is_function_pointer=True,
        )
    )

    fps = [m.name for m in facts.members if m.is_function_pointer]
    assert fps == ["destruct"]


def test_function_tags_roundtrip():
    facts = CodeFacts(repo_root=Path("/tmp/repo"))
    facts.tag_function("lib_reactor_c::spdk_reactors_start", "entrypoint")
    facts.tag_function("lib_env_c::spdk_malloc", "alloc")
    facts.tag_function("lib_env_c::spdk_free", "free")

    assert facts.tags_for("lib_reactor_c::spdk_reactors_start") == {"entrypoint"}
    assert facts.functions_with_tag("alloc") == ["lib_env_c::spdk_malloc"]


def test_function_tag_rejects_empty():
    facts = CodeFacts(repo_root=Path("/tmp/repo"))
    with pytest.raises(GraphError):
        facts.tag_function("lib_env_c::spdk_malloc", "  ")
