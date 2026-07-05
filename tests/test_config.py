from pathlib import Path

import pytest

from makewiki.config import load_config, validate_config
from makewiki.errors import ConfigError


ROOT = Path(__file__).parent / "fixtures" / "tiny_c"


def test_load_config_defaults_and_values():
    config = load_config(ROOT)

    assert config.repo_root == ROOT.resolve()
    assert config.build.run_build is False
    assert config.build.exclude_dirs == ("ignored/",)
    assert config.repo_notes[0].content.startswith("Tiny fixture")
    assert config.diagrams[0].root_function == "main"


def test_invalid_config_errors_are_clear():
    with pytest.raises(ConfigError, match="title must be a non-empty string"):
        load_config(Path(__file__).parent / "fixtures" / "bad_config")


def test_config_bounds():
    raw = {"diagrams": [{"title": f"d{i}"} for i in range(41)]}

    with pytest.raises(ConfigError, match="more than 40"):
        validate_config(raw, repo_root=ROOT)


def test_run_build_false_does_not_require_command_execution():
    config = validate_config(
        {"build": {"build_command": "exit 99", "run_build": False}},
        repo_root=ROOT,
    )

    assert config.build.build_command == "exit 99"
    assert config.build.run_build is False

