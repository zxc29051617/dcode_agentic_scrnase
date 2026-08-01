"""Agentic scRNA-seq workflow: deterministic analysis, local judges, human gates.

Layers, kept separate on purpose:
  `registry`   — which steps exist and which skill implements each
  `nodes`      — graph nodes: run a step, judge a step, stop for a person
  `judge`      — the local judge contract and its backends
  `policy`     — when a verdict is allowed to continue
  `graph`      — the wiring in `workflows/fastq_count_main_graph.md`
  `state`      — what flows between nodes
  `provenance` — the append-only audit log
"""

__version__ = "0.1.0"
