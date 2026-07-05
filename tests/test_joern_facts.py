from makewiki.analysis.external import _facts_from_joern_dump, _node_id

DUMP = "\n".join(
    [
        'MAKEWIKI_FACT|{"kind":"struct","name":"spdk_thread","filename":"lib/thread.c","line":42}',
        'MAKEWIKI_FACT|{"kind":"member","struct":"spdk_bdev_fn_table","name":"destruct","filename":"include/spdk/bdev_module.h","line":88,"func_ptr":true}',
        'MAKEWIKI_FACT|{"kind":"member","struct":"spdk_bdev_fn_table","name":"name_len","filename":"include/spdk/bdev_module.h","line":90,"func_ptr":false}',
        'MAKEWIKI_FACT|{"kind":"global","name":"g_reactors","filename":"lib/reactor.c","line":30,"type":"struct spdk_reactor *"}',
        'MAKEWIKI_FACT|{"kind":"include","filename":"lib/reactor.c","target":"spdk/thread.h","line":5}',
        'MAKEWIKI_FACT|{"kind":"tag","name":"spdk_reactors_start","filename":"lib/reactor.c","line":120,"tag":"entrypoint"}',
        # a call-graph line the facts parser must ignore:
        'MAKEWIKI_JSON|{"name":"noise","filename":"x.c","line":1,"lineEnd":2,"signature":"","calls":[]}',
    ]
)


def test_facts_from_joern_dump_parses_all_kinds(tmp_path):
    facts = _facts_from_joern_dump(tmp_path.resolve(), DUMP)

    assert [(s.name, s.file_path, s.start_line) for s in facts.structs] == [
        ("spdk_thread", "lib/thread.c", 42)
    ]
    assert [(m.name, m.is_function_pointer) for m in facts.members] == [
        ("destruct", True),
        ("name_len", False),
    ]
    assert [(g.name, g.type_name) for g in facts.globals] == [
        ("g_reactors", "struct spdk_reactor *")
    ]
    assert [i.target for i in facts.includes] == ["spdk/thread.h"]

    fid = _node_id("lib/reactor.c", "spdk_reactors_start")
    assert facts.tags_for(fid) == {"entrypoint"}


def test_facts_from_joern_dump_relativizes_absolute_paths(tmp_path):
    repo = tmp_path.resolve()
    line = f'MAKEWIKI_FACT|{{"kind":"struct","name":"s","filename":"{repo}/lib/a.c","line":3}}'
    facts = _facts_from_joern_dump(repo, line)

    assert [(s.name, s.file_path) for s in facts.structs] == [("s", "lib/a.c")]


def test_facts_from_joern_dump_ignores_graph_lines_and_blanks(tmp_path):
    facts = _facts_from_joern_dump(tmp_path.resolve(), "MAKEWIKI_JSON|{}\n\ngarbage line\n")

    assert facts.structs == []
    assert facts.globals == []
    assert facts.members == []
    assert facts.tags == {}
