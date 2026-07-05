from pathlib import Path

import pytest

from makewiki.analysis import FixtureAnalyzer
from makewiki.config import load_config
from makewiki.errors import GraphError
from makewiki.qa import QAOptions, answer_question, format_answer


ROOT = Path(__file__).parent / "fixtures" / "tiny_c"


def _graph():
    config = load_config(ROOT)
    return FixtureAnalyzer().analyze(ROOT.resolve(), config).graph


def test_answer_what_symbol_calls():
    result = answer_question(_graph(), "what does main call?")

    assert result.kind == "callees"
    assert "`main` directly calls `handle_request`." == result.answer
    assert result.involved_symbols == ["main_c::main", "main_c::handle_request"]
    assert result.evidence[0].file_path == "main.c"


def test_answer_who_calls_symbol():
    result = answer_question(_graph(), "who calls handle_request?")

    assert result.kind == "callers"
    assert "`handle_request` is directly called by `main`." == result.answer


def test_answer_where_symbol_is_defined():
    result = answer_question(_graph(), "where is do_work defined?")

    assert result.kind == "definition"
    assert "`do_work` is defined at `worker.c:3`." in result.answer
    assert result.evidence[0].symbol_name == "do_work"


def test_answer_path_between_symbols():
    result = answer_question(_graph(), "show path from main to do_work")

    assert result.kind == "path"
    assert "Call path: `main` -> `handle_request` -> `do_work`." == result.answer
    assert result.mermaid is not None
    assert "flowchart TD" in result.mermaid


def test_answer_flow_from_symbol():
    result = answer_question(_graph(), "explain flow from main", QAOptions(max_depth=3))

    assert result.kind == "flow"
    assert "`main` expands to 3 reachable function(s)" in result.answer
    assert result.mermaid is not None


def test_unknown_symbol_has_actionable_error():
    with pytest.raises(GraphError, match="symbol not found: missing"):
        answer_question(_graph(), "what does missing call?")


def test_format_answer_json_is_structured():
    result = answer_question(_graph(), "what does main call?")
    text = format_answer(result, output_format="json")

    assert '"answer": "`main` directly calls `handle_request`."' in text
    assert '"evidence": [' in text
