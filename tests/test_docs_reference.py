"""Keep nested command options discoverable in the generated CLI reference."""

import runpy
from pathlib import Path

import pytest


def test_cli_reference_includes_nested_review_commands():
    pytest.importorskip("mkdocs")
    hook = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "scripts/docs_hook.py")
    )
    result = hook["on_page_markdown"]("{{ cli_reference }}", None, None, None)
    for command in ("import", "list", "decide", "start", "export", "exemplars"):
        assert f"## `jevotron review {command}`" in result
    assert "## `jevotron preview`" in result
    assert "## `jevotron scan`" in result
    assert (
        "--source" in result and "--request-hash" in result and "--decision" in result
    )
