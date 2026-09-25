# jevotron developer commands. Run `just` with no arguments to list them.

# List all commands. The "_" prefix hides this recipe from the listing.
_default:
    @just --list

# ============== Setup ==============

# Install every dependency group used by the checks below.
[group('project')]
install:
    uv sync --group dev --group docs

# ============== Checks ==============

# Everything CI runs, in the order CI runs it.
[group('check')]
check: lint test docs build

# Unit tests, CLI tests, and doctests.
[group('check')]
test:
    uv run pytest -q

# Doctests only. They are part of `just test`; this narrows to them.
[group('check')]
doctest:
    uv run pytest --doctest-modules src -q

# Lint and formatting, without rewriting anything.
[group('check')]
lint:
    uv run ruff check .
    uv run ruff format --check .

# Apply the fixes that `just lint` only reports.
[group('check')]
fix:
    uv run ruff format .
    uv run ruff check --fix .

# Static types. Not part of `just check`: the tree has pre-existing errors.
[group('check')]
mypy:
    uv run mypy src tests

# ============== Docs ==============

# Build the site the way the Pages workflow does, failing on any warning.
[group('docs')]
docs:
    uv run --group docs mkdocs build --strict

# Serve the docs locally with live reload.
[group('docs')]
serve:
    uv run --group docs mkdocs serve

# ============== Release ==============

# Build the sdist and wheel that the publish workflow uploads.
[group('release')]
build:
    uv build

# Confirm the working tree is releasable at the given version, before tagging.
# The publish workflow refuses a tag that disagrees with pyproject.
[group('release')]
release-check version: check
    #!/usr/bin/env bash
    set -euo pipefail
    packaged=$(uv run --no-project python -c \
      "import tomllib, pathlib; print(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['version'])")
    if [ "$packaged" != "{{ version }}" ]; then
      echo "pyproject version is $packaged, not {{ version }}; bump it before tagging" >&2
      exit 1
    fi
    echo "Ready to tag v{{ version }}"
