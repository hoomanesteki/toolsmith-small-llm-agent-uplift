"""ToolSmith: an agent routing and certification control plane.

The question: which model should run which part of your agent?

The answer this repository gives is not an opinion. It is a harness that runs
the same tasks through many model, prompt and routing configurations, scores
every run against executable ground truth, gates every request through safety
and grounding checks, and publishes the cost-versus-quality frontier with
confidence intervals and the losing transcripts attached.

Layout
------
``config``     typed registry; every model is YAML, never Python
``providers``  vendor adapters plus the deterministic simulator
``worlds``     sandboxed domains with ground truth by construction
``tasks``      task generation, oracle programs, splits, decontamination
``runtime``    gate, plan, execute, review, gate
``harness``    runner, judges, statistics, the evaluation matrix
``optimize``   the four improvement tracks, measured on one axis
``report``     every published artifact, regenerated from results.jsonl
"""

__version__ = "3.0.0"

__all__ = ["__version__"]
