# Documentation development

The site uses Material for MkDocs. From the checkout:

```sh
uv sync --group docs
uv run --group docs mkdocs serve
```

Open `http://127.0.0.1:8000`. The development server rebuilds as you edit.

## Build a static site

```sh
uv run --group docs mkdocs build --strict
```

Generated files are in `site/`, ready for a static host and ignored by Git.
No public deployment is configured yet.

## Maintain the CLI documentation

The reference is generated from the Typer app by `scripts/docs_hook.py`.
Update command help in the implementation to update the reference. The hook
also publishes files from `examples/` as downloadable assets, so examples are
not maintained in two places.

Keep routine documentation focused on shell commands and complete workflows.
Python API usage and custom parser implementation belong in Advanced.

```sh
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```
