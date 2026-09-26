# Analyses

Reproducible studies of jevotron on public data. Each study keeps its input
provenance, sampling method, guidance, evaluation scripts, saved predictions,
and interpretation together. Large downloaded inputs and inference caches stay
under the ignored `.jevotron/` directory.

| Study | Scope | Result |
| --- | --- | --- |
| [Agent trace classification](agent-traces/README.md) | 24 AgentProcessBench traces; 163 assistant steps; four domains | 79.8% step-label agreement; 89.7% harmful-step precision and 70.3% recall |

These are scoped experiments, not general performance guarantees. Read the
sampling constraints and baselines alongside the headline results.
