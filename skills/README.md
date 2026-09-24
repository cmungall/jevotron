# jevotron skill catalog

| Skill | Use it for |
| --- | --- |
| [jt-triage](jt-triage/SKILL.md) | Iteratively narrow a large dataset to semantic anomalies or cases of interest, investigate the evidence, and refine the next pass. |

## Install with npx skills

```sh
# Browse the available skills without installing.
npx skills add cmungall/jevotron --list

# Install the triage skill; choose your agent interactively.
npx skills add cmungall/jevotron --skill jt-triage

# Or choose an agent explicitly (add --global for all projects).
npx skills add cmungall/jevotron --skill jt-triage --agent codex
```

From a checkout, including before these changes are published:

```sh
npx skills add . --list
npx skills add . --skill jt-triage
```

This uses the standard `skills/<name>/SKILL.md` layout supported by the
[skills CLI](https://github.com/vercel-labs/skills). No npm package is needed.
Remote commands use the version published on GitHub.

## Claude Code marketplace

The same skill is also bundled in the `jevotron` marketplace:

```text
/plugin marketplace add cmungall/jevotron
/plugin install jt-skills@jevotron
```

For a local checkout, use `/plugin marketplace add /absolute/path/to/jevotron`.
Choose either this route or `npx skills` for Claude Code to avoid duplicate skill
installations. The marketplace definition follows the
[Claude Code marketplace format](https://code.claude.com/docs/en/plugin-marketplaces).

## Install the runtime

Skills do not install the Python CLI or supply API credentials. With Python
3.12+ and uv:

```sh
uv tool install git+https://github.com/cmungall/jevotron.git
jt --help
```

From a checkout, use `uv tool install .` or `uv run jt` instead. Preview is local
and free; uncached scans require `TYPESAFE_API_KEY` and send records to Jev.
See the [agent workflow guide](../docs/guides/agents.md) for the review loop.
