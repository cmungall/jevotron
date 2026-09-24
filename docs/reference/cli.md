# CLI reference

The commands and options below are generated from the installed Typer app on
every documentation build. Use `jevotron COMMAND --help` for help in your terminal.
`jt` is an alias for `jevotron` and accepts the same commands and options.

See the [file format reference](formats.md) for parser defaults, examples, and
the options accepted by `--format-option`. `jevotron formats NAME` shows the
same options in your terminal.

{{ cli_reference }}

## Environment

`TYPESAFE_API_KEY` supplies the API credential for uncached assessments. Preview
and complete cache replays do not require it.

## Precedence

Explicit CLI flags override project config values. Parser defaults apply when
neither provides a setting. See [project configuration](../guides/config.md).
An explicit `Config(parser=...)` owns parsing; set its options in that Python
config. Combining a custom parser with `--format` or `--format-option` is an error.
