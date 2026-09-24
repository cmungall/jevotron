# Documentation development

The site uses Material for MkDocs. From the checkout:

```sh
uv sync --locked --group docs
uv run --group docs mkdocs serve
```

Open `http://127.0.0.1:8000`. The development server rebuilds as you edit.

## Build a static site

```sh
uv run --group docs mkdocs build --strict
```

Generated files are in `site/` and ignored by Git.

## Publish to GitHub Pages

The public site is [cmungall.github.io/jevotron](https://cmungall.github.io/jevotron/).
The canonical URL is configured by `site_url` in `mkdocs.yml`.

The **Deploy documentation** workflow in `.github/workflows/docs.yml` builds with
locked dependencies and `mkdocs build --strict`, uploads `site/`, and deploys it to
GitHub Pages after pushes to `main`. Pull requests run the existing CI build checks
without publishing. Deployments run one at a time and use the `github-pages`
environment.

For repository setup, open **Settings → Pages → Build and deployment** and select
**GitHub Actions** as the source. The `github-pages` environment must permit
deployments from `main`. The workflow uses the built-in `GITHUB_TOKEN`; no personal
token or API credential is needed.

To republish the current documentation, open **Actions → Deploy documentation →
Run workflow** and select `main`. Manual runs from other branches are skipped.
The deployment job links to the published site. A failed strict build leaves the
last successful deployment in place.

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
