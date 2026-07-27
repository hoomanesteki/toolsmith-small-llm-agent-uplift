---
name: toolsmith-eval
description: How to run an evaluation, read the results, and add a configuration. Use when asked to compare models, measure a change, tune a threshold, or explain what a number in the matrix means.
---

# Running an evaluation

## The commands

```bash
uv run toolsmith matrix run                       # all configs, test split, simulated, $0
uv run toolsmith matrix run --provider live       # the same code path, real models, under the cap
uv run toolsmith matrix run --pipelines a,b --n 60 --trials 1   # quick comparison
uv run toolsmith matrix compare cascade_default frontier_all_opus
uv run toolsmith matrix show cascade_default
uv run toolsmith report build
```

## Adding a configuration

A row in `configs/pipelines/matrix.yaml`. No Python. Give it a `label` a reader
will understand and `tags` that say what it is for:

- `control` excludes it from the Pareto frontier (the oracle and the coin flip)
- `self-review` marks a reviewer sharing the executor's family, which is
  deliberate and measured, not an accident
- `ablation` marks a one-lever change from `cascade_default`

Then `toolsmith matrix run` and it appears everywhere.

## Reading the results honestly

- **The headline column is dollars per SUCCESS.** Per-task cost ranks
  configurations differently and dishonestly.
- **Overlapping intervals mean no established difference**, whatever the point
  estimates say. Check `matrix compare` for the McNemar p-value.
- **64 of 91 comparisons survive Holm correction.** An uncorrected comparison
  from a 105-family is not evidence.
- **`pass^k` is harsher than mean pass@1** on purpose. A system that succeeds
  two times in three is not deployable, and averaging hides that.
- **Abstain recall alone is meaningless.** Read it beside over-refusal: a system
  that abstains on everything scores perfectly on the first.

## Tuning anything

Tune on **val**, report on **test**. Never the other way round. Track B
publishes `left_on_the_table_by_not_tuning_on_test` so the cost of that
discipline is visible.

The hidden split exists for the "you tuned on your test set" question and is
sealed in the `hidden-split-sealed` git tag. Do not run against it casually.

## When a number looks wrong

1. `uv run toolsmith matrix show <pipeline>` for the per-tier breakdown and the
   named failure modes.
2. The Review screen at `make serve`, or `/api/gallery`, for the actual
   transcripts.
3. `eval/transcripts/` for the raw event streams.

A number that surprises you is usually a grading bug, and grading bugs in this
repository have all been found by the floor row behaving impossibly rather than
by inspection.
