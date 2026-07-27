---
name: toolsmith-dev
description: Working rules for this repository. Read before changing anything under src/, configs/ or app/. Covers the five invariants, the gates that enforce them, and the commands that check your work.
---

# Working in ToolSmith

## The five invariants

Everything else is style. These are load-bearing, and each has a CI gate.

1. **Every model is configuration, never code.** Model identifiers live in
   `configs/models.yaml` and nowhere else. `toolsmith ci model-agnostic` parses
   `src/` with `ast` and fails if one appears in executable code. Docstrings may
   name models; code may not.

2. **The headline metric is never judged.** `pass@1` is
   `state_diff == oracle AND every answer key present AND the right behaviour`,
   computed by code. If you find yourself adding a model call to the grading
   path, stop: you are about to make the evaluation circular.

3. **Nothing is hardcoded from a document.** Prices, model ids and rate limits
   come from `toolsmith probe models` and `probe limits`. A model with
   `verified_on: null` is flagged everywhere it appears.

4. **The budget is enforced, not intended.** `CostLedger.check_affordable` runs
   before every live request. Never bypass it, even in a script.

5. **Frontier models are inference-only.** Anthropic, OpenAI and Google may
   never generate a training row. `toolsmith ci firewall` fails the build.

## Before you commit

```bash
make check     # ruff format, ruff lint, mypy, 322 tests, 5 gates
```

If you touched anything that produces a published number:

```bash
make all       # regenerate worlds, tasks, matrix, report; ~6 minutes, $0
```

CI asserts the committed artifacts are byte-identical to a fresh run. Two
reproducibility bugs have already been caught that way, so if `git diff` shows
churn in `eval/` after a no-op change, something non-deterministic has crept in.
Look for measured wall-clock, timestamps, or unsorted iteration.

## Things that will bite you

- **Do not swallow exceptions in the pipeline.** `runtime/pipeline.py` re-raises
  `TypeError` and friends deliberately. A swallowed `TypeError` once turned a
  loud bug into 8,000 quietly zeroed rows.
- **Answer keys are matched on word boundaries.** Substring matching made "no"
  match "cannot" and inflated the entire matrix. `contains_answer_key` is the
  only correct way to check one.
- **The simulator's turn index counts world tool calls, not loop iterations.**
  A turn spent on `search_tools` must not advance the oracle pointer.
- **The stable prefix must never mutate mid-run.** A single changed character
  costs the whole cache discount. `ContextBuilder` counts violations.
- **Published latency is modelled, not measured.** Wall-clock in an artifact
  makes it unverifiable.

## Style

Python 3.12, `uv`, `ruff` (line length 100), `mypy` on `src` and `app`.
Comments explain *why*. A comment that restates the code is noise; a comment
that records the bug the code prevents is the most valuable line in the file.

Tests assert behaviour and claims, not implementation. If the README says it, a
test should break when it stops being true.
