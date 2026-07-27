# ADR 0001: A deterministic simulator is a first-class provider

- Status: accepted
- Date: 2026-07-26

## Context

The project's central promise is that every published number can be
regenerated. Two things get in the way.

First, cost. The evaluation matrix is fifteen configurations across five task
tiers with three trials each. Run live against frontier models, that is well
past the $20 the whole project is allowed to spend, and it would have to be
re-run every time the harness changed.

Second, reach. A reader who clones this repository does not have five API keys.
If reproduction requires them, then in practice nothing is reproducible and the
receipts are decorative.

## Decision

The simulator is a provider, not a test fixture. It implements the same
`Provider` interface as the Groq and Anthropic adapters, and every layer above
it, the executor loop, the gates, the cascade, the ledger, the statistics, the
report, is the production code path.

Given a model's published capability priors from `configs/models.yaml`, it
samples whether that model would have chosen the right tool, filled the right
arguments, emitted valid JSON, resisted an instruction planted in a tool result,
or correctly abstained on an unanswerable question. Token counts come from the
real prompts. Latency comes from the model's published tokens-per-second and
time-to-first-token. Cost comes from the real price table.

Every decision is seeded from `sha256(model_id, task_id, role, turn, trial)`.
There is no wall clock and no global RNG, so the same inputs give the same
trajectory on any machine, forever.

## Consequences

**What we get.** `make all` runs end to end in about six minutes, for nothing,
on a fresh checkout with no keys. CI asserts that the committed artifacts are
byte-identical to a fresh run, which means the statistics code is actually
exercised on every commit rather than merely imported.

**What we owe the reader.** The absolute numbers are as good as the priors, and
the priors are self-reported model-card figures. So every artifact is stamped
`provenance: simulated`, the report carries a banner rather than a footnote, and
`--provider live` regenerates the identical tables for real.

**What this is not.** The simulator does not generate text that anyone should
mistake for model output. It decides outcomes, not prose. A transcript in the
failure gallery is a real trajectory through the real runtime; the sentences in
it are templated.

## Alternatives considered

*Record and replay real traffic.* Better fidelity, and the repository does
commit trace fixtures for exactly that reason. But replay cannot answer "what
happens if I change the escalation threshold", which is the entire point of the
optimizer tracks.

*Skip the matrix until keys are available.* Would have left the statistics,
report and UI untested against real data shapes until the last day.
