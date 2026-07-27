# ToolSmith

**Which model should run which part of your agent?**
A control plane that routes planner, executor and reviewer across frontier and
open models, verifies every step against executable ground truth, and publishes
the cost-versus-quality frontier with confidence intervals and the losing
transcripts attached.

[**Read the report**](https://hoomanesteki.github.io/toolsmith-small-llm-agent-uplift/)
· 329 tests · 5 governance gates · $0.00 of a $20 cap spent

---

## The finding, in one table

Fifteen configurations, 8,100 graded runs, one stratified sample of 180 tasks,
three trials each. Every configuration ran the same tasks.

| Configuration | pass@1 | 95% CI | $ / success | vs all-frontier |
|---|---:|---:|---:|---:|
| All frontier (Opus 5) | 0.950 | [0.917, 0.978] | $0.05242 | 1.00x |
| **Cascade (gpt-5.4-mini executor)** | **0.956** | [0.922, 0.983] | **$0.01464** | **0.28x** |
| Cascade (frontier verifier) | 0.917 | [0.872, 0.956] | $0.02877 | 0.55x |
| Single model (Sonnet 5) | 0.883 | [0.833, 0.928] | $0.02278 | 0.43x |
| **Cascade (fully open weights)** | 0.828 | [0.772, 0.883] | **$0.00683** | **0.13x** |
| Naive role split (frontier bookends) | 0.639 | [0.567, 0.706] | $0.02741 | 0.52x |
| Coin flip (floor) | 0.033 | [0.011, 0.061] | - | - |

The intuitive move, frontier on the ends and something cheap in the middle,
loses to a cascade on **both** axes at once. 64 of 91 pairwise comparisons
survive Holm-Bonferroni correction.

## Three things worth taking away

1. **Input tokens are 82% of the bill.** The executor re-reads its transcript
   every turn, so input grows quadratically in turns while output grows
   linearly. Retrieving tool schemas instead of inlining them cuts input 48% per
   task at no measurable cost in accuracy.

2. **The executor is the only role whose errors compound.** Per-step reliability
   *q* over N steps is *q^N*. Measured: the all-cheap row passes 71% of one-step
   tasks and 57% of two-step ones, a 15-point fall, while the all-frontier row
   gives up 4. The planner is cheap to upgrade because it runs *once*, not
   because its input is cheap.

3. **Escalation is a second independent attempt, worth 18 points of pass@1** at
   unchanged cost per success. The retries add 26% to the bill and buy back
   exactly enough successes to cancel it. Whether that *pays* depends on your
   objective, and both ends of the trade are published.

Plus one where the source design document was wrong: it predicted the executor
at 73% of spend on a six-turn loop. At the 2.4 turns these tasks take, the
planner is 48% and the executor 30%. Reported rather than smoothed.

## Run it

```bash
git clone https://github.com/hoomanesteki/toolsmith-small-llm-agent-uplift
cd toolsmith-small-llm-agent-uplift
uv sync --all-extras

make all      # worlds, tasks, matrix, report, site: about six minutes, $0
make serve    # the control plane on http://127.0.0.1:7860
```

No API keys needed. Drop a `GROQ_API_KEY` in `.env` and add `--provider live`
to regenerate every table against real models, through the identical code path,
under a spend cap enforced before each call.

## What is in here

```
configs/          every model, price and role assignment. The whole point.
src/toolsmith/
  config/         typed registry; a bad config fails at load, not mid-run
  providers/      vendor adapters plus the deterministic simulator
  worlds/         three sandboxed domains behind one twelve-verb grammar
  tasks/          generation, oracle programs, splits, decontamination
  runtime/        gate -> plan -> execute -> review -> gate
  harness/        runner, judges, statistics, the matrix
  optimize/       four improvement tracks, measured on one axis
  governance/     provenance, licence firewall, lineage DAG
  report/         every published artifact, regenerated
app/ web/         the control plane and its four screens
docs/             the Quarto report site
eval/             committed results and traces: the demo works with zero keys
```

## The properties it is built to have

**Every model is configuration, never code.** Swapping an executor is a YAML
edit. A CI gate parses `src/` with `ast` and fails if a model identifier appears
in executable code.

**The headline metric is never judged.** `pass@1` is `state_diff == oracle AND
every answer key present AND the right behaviour`, computed against a sandbox
that actually ran the tool calls. Judges grade only what execution cannot see,
and their agreement with human labels is published or declared missing.

**Ground truth is computed.** Every task carries an oracle program; the
generator executes it and discards the task if the result disagrees. Worlds
rebuild to identical SHA-256 digests.

**Privileged actions are authorised server-side.** The model asks for a refund;
a policy function reading the world's own rules decides, after the request and
before the mutation. A model talked into it by an injected instruction still
does not get one.

**Tool results are marked as data before they re-enter context.** The step
almost every agent implementation skips, and the one the trap tier exists to
measure.

**The budget is enforced, not intended.** The ledger refuses a call that would
breach the cap before the request goes out.

## Adding a domain

A fourth world is a folder: a schema, a seeded builder, twelve verb bindings, a
lexicon, and nine row samplers. Nothing in the runtime, harness, report or UI
changes, and the conformance suite runs against it the moment it is registered.

## Adding a model

1. A row in `configs/models.yaml` with a real `verified_on` date.
2. `uv run toolsmith probe models` to confirm the id exists.
3. Reference it from a pipeline in `configs/pipelines/`.
4. `uv run toolsmith matrix run`, and it appears in the report.

No Python changes. If you need one, that is a bug in the abstraction.

## Honest limitations

The published numbers come from a deterministic behavioural simulator seeded
from published model-card priors. It is a **provider**, not a test fixture: the
executor loop, the gates, the cascade, the ledger, the statistics and the report
are the production code path. But the absolute values are as good as the priors,
every artifact is stamped `provenance: simulated`, and `--provider live`
regenerates all of it for real.

The judge panel and the behaviour classifier are **uncalibrated**: there are no
human labels in this repository yet. Every judged number says so.

See [Where it loses](https://hoomanesteki.github.io/toolsmith-small-llm-agent-uplift/failures.html)
for the ten worst transcripts with diagnoses, and the attack-and-defence table.

## Licence

MIT. The task suite carries no model-generated rows, so nothing constrains its
reuse.
