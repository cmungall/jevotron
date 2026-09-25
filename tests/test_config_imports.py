"""Project-local helper imports cannot leak into another config load."""

import dataclasses
import importlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from jevotron.config import load_config


def project_config(root, name, kind="module"):
    directory = root / name
    directory.mkdir()
    helper = directory / "review_helpers.py"
    imported = "review_helpers"
    if kind != "module":
        package = directory / "review_helpers"
        package.mkdir()
        helper = package / "parser.py"
        imported = "review_helpers.parser"
        if kind == "package":
            (package / "__init__.py").write_text(
                "from .parser import Parser, GUIDANCE\n"
            )
            imported = "review_helpers"
    helper.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "from jevotron import Chunk\n"
        f"GUIDANCE = {name!r}\n"
        "@dataclass\n"
        "class Parser:\n"
        "    name: str = GUIDANCE\n"
        "    def __call__(self, path):\n"
        "        yield Chunk(self.name, {'guidance': GUIDANCE})\n"
    )
    config = directory / "config.py"
    config.write_text(
        "from jevotron import Config\n"
        f"from {imported} import Parser, GUIDANCE\n"
        "config = Config(parser=Parser(), guidance=GUIDANCE)\n"
    )
    return config


@pytest.mark.parametrize("kind", ["module", "package", "namespace"])
def test_each_project_uses_its_helpers_and_retains_parser_objects(tmp_path, kind):
    paths = [project_config(tmp_path, name, kind) for name in ("first", "second")]
    previous_path = sys.path[:]
    previous_httpx = sys.modules["httpx"]
    first, second = [load_config(path) for path in paths]
    assert (first.guidance, second.guidance) == ("first", "second")
    for config, name in [(first, "first"), (second, "second")]:
        assert dataclasses.asdict(config.parser) == {"name": name}
        assert dataclasses.replace(config.parser).name == name
        (chunk,) = config.parser(Path("unused"))
        assert chunk.id == name and chunk.data == {"guidance": name}
    assert sys.path == previous_path
    assert sys.modules["httpx"] is previous_httpx is httpx
    assert not any(name.startswith("review_helpers") for name in sys.modules)


def test_restore_existing_package_and_submodule(tmp_path, monkeypatch):
    parent = ModuleType("review_helpers")
    child = ModuleType("review_helpers.parser")
    parent.parser = child
    parent.GUIDANCE = "embedding application"
    monkeypatch.setitem(sys.modules, parent.__name__, parent)
    monkeypatch.setitem(sys.modules, child.__name__, child)
    config = load_config(project_config(tmp_path, "project", "package"))
    assert config.guidance == "project"
    assert sys.modules[parent.__name__] is parent
    assert sys.modules[child.__name__] is child
    assert parent.parser is child and parent.GUIDANCE == "embedding application"


def test_local_name_can_overlap_imported_library_without_replacing_it(tmp_path):
    (tmp_path / "json.py").write_text("GUIDANCE = 'local helper'\n")
    config = tmp_path / "config.py"
    config.write_text(
        "from jevotron import Config\nimport json\n"
        "config = Config(guidance=json.GUIDANCE)\n"
    )
    before = {
        name: module
        for name, module in sys.modules.items()
        if name == "json" or name.startswith("json.")
    }
    assert load_config(config).guidance == "local helper"
    assert {
        name: module
        for name, module in sys.modules.items()
        if name == "json" or name.startswith("json.")
    } == before
    assert importlib.import_module("json") is json
    assert json.loads('{"ok": true}') == {"ok": True}


@pytest.mark.parametrize(
    "failure", ["raise RuntimeError('broken config')", "config = None"]
)
def test_failed_load_restores_path_and_modules(tmp_path, monkeypatch, failure):
    previous = ModuleType("review_helpers")
    previous.GUIDANCE = "original"
    monkeypatch.setitem(sys.modules, "review_helpers", previous)
    sentinel = ModuleType("_jevotron_local_config")
    monkeypatch.setitem(sys.modules, sentinel.__name__, sentinel)
    path = project_config(tmp_path, "broken", "package")
    path.write_text(path.read_text() + failure + "\n")
    original_path = sys.path[:]
    with pytest.raises((RuntimeError, ValueError)):
        load_config(path)
    assert sys.path == original_path
    assert sys.modules["review_helpers"] is previous
    assert sys.modules[sentinel.__name__] is sentinel
    assert "review_helpers.parser" not in sys.modules


def test_parallel_loads_do_not_mix_helpers(tmp_path):
    paths = [project_config(tmp_path, f"project_{index}") for index in range(8)]
    original_path = sys.path[:]
    with ThreadPoolExecutor(max_workers=4) as executor:
        configs = list(executor.map(load_config, paths))
    assert [config.guidance for config in configs] == [
        f"project_{index}" for index in range(8)
    ]
    assert sys.path == original_path


def test_nested_load_restores_enclosing_project_helpers(tmp_path):
    outer = project_config(tmp_path, "outer")
    inner = project_config(tmp_path, "inner")
    outer.write_text(
        "from pathlib import Path\n"
        "from jevotron import Config\n"
        "from jevotron.config import load_config\n"
        "import review_helpers\n"
        f"inner = load_config(Path({str(inner)!r}))\n"
        "from review_helpers import Parser, GUIDANCE\n"
        "assert GUIDANCE == review_helpers.GUIDANCE == 'outer'\n"
        "config = Config(parser=Parser(), guidance=GUIDANCE + ':' + inner.guidance)\n"
    )
    config = load_config(outer)
    assert config.guidance == "outer:inner"
    assert list(config.parser(Path("unused")))[0].id == "outer"


def test_namespace_candidates_do_not_replace_installed_or_builtin_modules(tmp_path):
    import jevotron

    # Ordinary directories must not cause a second copy of an installed package.
    for name in ("jevotron", "httpx", "json"):
        (tmp_path / name).mkdir()
    (tmp_path / "sys.py").write_text("raise AssertionError('builtin must win')\n")
    config = tmp_path / "config.py"
    config.write_text(
        "from jevotron import Config\nimport httpx, json, sys\n"
        "config = Config(guidance='valid')\n"
    )
    previous = {
        name: sys.modules[name] for name in ("jevotron", "httpx", "json", "sys")
    }
    assert load_config(config).guidance == "valid"
    assert sys.modules["jevotron"] is jevotron
    assert {name: sys.modules[name] for name in previous} == previous
