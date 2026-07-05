from makewiki.analysis.docs import build_doc_index, extract_doc_comments


def test_extracts_summary_for_function_declaration():
    source = """\
/**
 * Pass the given event to the associated lcore and call the function.
 *
 * \\param event Event to execute.
 */
void spdk_event_call(struct spdk_event *event);
"""
    docs = extract_doc_comments(source)

    assert len(docs) == 1
    doc = docs[0]
    assert doc.symbol_name == "spdk_event_call"
    assert doc.summary == "Pass the given event to the associated lcore and call the function."


def test_extracts_params_and_return():
    source = """\
/**
 * Allocate an event to be passed to spdk_event_call().
 *
 * \\param lcore Lcore to run this event.
 * \\param fn Function used to execute event.
 *
 * \\return a pointer to the allocated event.
 */
struct spdk_event *spdk_event_allocate(uint32_t lcore, spdk_event_fn fn);
"""
    docs = extract_doc_comments(source)

    assert len(docs) == 1
    doc = docs[0]
    assert doc.symbol_name == "spdk_event_allocate"
    assert doc.summary == "Allocate an event to be passed to spdk_event_call()."
    assert doc.params == (("lcore", "Lcore to run this event."), ("fn", "Function used to execute event."))
    assert doc.returns == "a pointer to the allocated event."


def test_param_and_return_descriptions_span_continuation_lines():
    source = """\
/**
 * Do a thing.
 *
 * \\param opts Options for the thing. It should not be
 *             NULL under any circumstance.
 * \\return 0 on success, or a negative
 *         errno on failure.
 */
void thing(struct opts *opts);
"""
    docs = extract_doc_comments(source)

    assert docs[0].params == (
        ("opts", "Options for the thing. It should not be NULL under any circumstance."),
    )
    assert docs[0].returns == "0 on success, or a negative errno on failure."


def test_joins_multiline_summary():
    source = """\
/**
 * Print usage strings for common SPDK command line options.
 *
 * May only be called after spdk_app_parse_args().
 */
void spdk_app_usage(void);
"""
    docs = extract_doc_comments(source)

    assert len(docs) == 1
    assert docs[0].summary == (
        "Print usage strings for common SPDK command line options. "
        "May only be called after spdk_app_parse_args()."
    )


def test_ignores_single_star_comments():
    source = """\
/* not a doc comment, just a note */
void spdk_internal(void);
"""
    assert extract_doc_comments(source) == []


def test_ignores_block_without_following_declaration():
    source = "/**\n * Trailing doc with nothing after it.\n */\n"
    assert extract_doc_comments(source) == []


def test_names_function_pointer_typedef_not_return_type():
    source = """\
/** Signature of a callback executed by an event. */
typedef void (*spdk_event_fn)(void *arg1, void *arg2);
"""
    docs = extract_doc_comments(source)

    assert len(docs) == 1
    assert docs[0].symbol_name == "spdk_event_fn"


def test_extracts_multiple_doc_comments_in_order():
    source = """\
/** First function. */
void first(void);

/** Second function. */
void second(void);
"""
    docs = extract_doc_comments(source)

    assert [doc.symbol_name for doc in docs] == ["first", "second"]


def test_build_doc_index_prefers_header_over_source(tmp_path):
    (tmp_path / "foo.h").write_text("/** Public foo API. */\nint foo(void);\n")
    (tmp_path / "foo.c").write_text(
        "/** Internal impl note. */\n"
        "int foo(void) { return 0; }\n"
        "/** Helper that supports foo. */\n"
        "static void bar(void) {}\n"
    )

    index = build_doc_index(tmp_path)

    assert index["foo"].summary == "Public foo API."
    assert index["bar"].summary == "Helper that supports foo."


def test_build_doc_index_scans_extra_roots(tmp_path):
    # Public API docs commonly live in a separate include/ tree, outside the
    # analyzed source directory.
    src = tmp_path / "lib"
    src.mkdir()
    inc = tmp_path / "include"
    inc.mkdir()
    (src / "reactor.c").write_text("void spdk_event_call(struct spdk_event *e) {}\n")
    (inc / "event.h").write_text("/** Pass the event to the lcore. */\nvoid spdk_event_call(struct spdk_event *e);\n")

    index = build_doc_index(src, extra_roots=[inc])

    assert index["spdk_event_call"].summary == "Pass the event to the lcore."


def test_build_doc_index_skips_ignored_directories(tmp_path):
    (tmp_path / "keep.h").write_text("/** Kept API. */\nvoid keep(void);\n")
    hidden = tmp_path / ".git"
    hidden.mkdir()
    (hidden / "junk.h").write_text("/** Should be ignored. */\nvoid junk(void);\n")

    index = build_doc_index(tmp_path)

    assert "keep" in index
    assert "junk" not in index
