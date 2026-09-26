# Agent trace classification

For the public-data worked example and measured results, see
[the guide](../../docs/examples/agent-traces.md) and
[the analysis](../../analysis/agent-traces/README.md).

This directory is an optional synthetic example for experimenting with
user-turn segmentation of normalized `agentic-trace-analyzer` output. It is
independent of the public-data analysis, which uses the original JSONL directly.

`session.json` is synthetic, created for this example; it contains no real user
session data. `jev_config.py` assesses user-turn segments. `whole_trace_config.py`
assesses the whole trace. Both use the same guidance and three-way labels for
the `step_repetition_loop` behavior. No model-generated results are bundled.

```sh
uv run jt preview examples/agent-traces/session.json \
  --config examples/agent-traces/jev_config.py
```
