# jevotron and `llm`

[LLM](https://llm.datasette.io/) can call Jev through
[llm-typesafe](https://github.com/simonw/llm-typesafe) and OpenAI Decisions
through [llm-openai-decisions](https://github.com/simonw/llm-openai-decisions).
Both plugins ask one question about one input. jevotron works on whole datasets.
The two tools work well side by side.

## When to use which

Use `llm` to ask one ad-hoc question about some text or an image:

```sh
llm -m openai-decisions/gpt-6-luna 'I was charged twice.' \
  -s 'Does this message request a refund?'
```

Use jevotron to check every field of a file or table and get back a review queue:

```sh
jt scan airports.csv --id-column ident --model gpt-6-luna \
  --guidance "Check airport locations." -a -s -O yaml
```

| | `llm` + plugin | jevotron |
|---|---|---|
| Unit of work | One prompt | Every selected field of every entry |
| Inputs | Text, JSON, or images | [Tabular, structured, and text files](../reference/formats.md), SQLite, DuckDB, custom parsers |
| Question | You write it | One choice question per field, built from your guidance and exemplars |
| Repeat runs | Logged | Cached per entry, so only changed entries are sent again |
| Large inputs | One request at a time | Token-aware batching, with automatic splitting when a request is too large |
| Bad responses | Returned as an error | Validated, and never cached |
| Output | JSON for one answer | Scored entries you can sort, filter with `--where`, and export |

## Why jevotron does not call the API through `llm`

jevotron sends requests itself (see `jevotron.client`), for these reasons:

- **Batching.** One request holds many named questions that share the same
  guidance. The plugins are built around one question per prompt.
- **Request shape.** Jev receives the entry, guidance, and exemplars as a
  structured `state` object. Sending the same content as a prompt changes what
  the model sees, and so changes scores and cache keys.
- **Errors.** Splitting an oversized batch depends on recognising "input too large"
  errors. jevotron also never shows service response bodies in its error messages.
- **Exact requests.** `jt preview` and the cache both rely on knowing exactly what
  is sent. For OpenAI models, `preview` also shows the translated `wire_request`.

If you need another service, implement the small `evaluate(request) -> response`
contract described in [Python integration](python.md) and pass it as `client=`.
That keeps caching, batching, and validation unchanged.
