# ToolSmith: working notes

Read this before changing anything. It is short on purpose.

## What this repository is

A control plane that decides, per role and per task, which model should run
which part of an agent, and proves the decision with evidence. The deliverable
is not an agent. It is the harness that certifies one.

## The five rules

1. **Every model is configuration, never code.** Model identifiers live in
   `configs/models.yaml` and nowhere else. `toolsmith ci model-agnostic` parses
   `src/` with `ast` and fails the build if one appears in executable code.
   Docstrings may name models; code may not.

2. **The headline metric is never judged.** `pass@1` is
   `state_diff == oracle AND answer == oracle`, computed by code against a
   sandbox that actually ran the tool calls. Judges grade phrasing, tone and
   citation support: the parts execution cannot see. Their agreement with human
   labels is measured and published, not assumed.

3. **Nothing is hardcoded from a document.** Prices, model ids and rate limits
   come from `toolsmith probe models` and `toolsmith probe limits`. A model with
   `verified_on: null` is flagged everywhere it appears.

4. **The budget is enforced, not intended.** `CostLedger.check_affordable` runs
   before every live request and raises if the call would take cumulative spend
   past the cap in `configs/budget.yaml`.

5. **Frontier models are inference-only.** Anthropic, OpenAI and Google appear
   in the evaluation matrix and as escalation targets. They may never generate a
   training row. `toolsmith ci firewall` fails the build on violation.

## Where things live

| Path | What |
|---|---|
| `configs/` | models, pipelines, rubrics, limits, budget |
| `src/toolsmith/config/` | typed registry and loader |
| `src/toolsmith/providers/` | vendor adapters plus the deterministic simulator |
| `src/toolsmith/worlds/` | sandboxed domains; ground truth by construction |
| `src/toolsmith/tasks/` | generation, oracle programs, splits, decontamination |
| `src/toolsmith/runtime/` | gate, plan, execute, review, gate |
| `src/toolsmith/harness/` | runner, judges, statistics, matrix |
| `src/toolsmith/optimize/` | the four improvement tracks |
| `src/toolsmith/report/` | every published artifact, regenerated |
| `src/toolsmith/governance/` | provenance, license firewall, lineage |
| `app/` | FastAPI control plane and SSE |
| `web/` | the four screens |
| `docs/` | the Quarto report site and the ADRs |
| `eval/` | committed fixtures; the demo works with zero keys |

## Commands

```bash
make install          # uv sync --all-extras
make check            # what CI runs: lint, types, tests, gates
make all              # worlds, tasks, matrix, report, site: end to end
make serve            # the control plane on http://127.0.0.1:7860

uv run toolsmith doctor              # what can this machine run right now
uv run toolsmith ci all              # the five gates
uv run toolsmith matrix run          # simulated by default, costs nothing
```

## Conventions

- Python 3.12, `uv` for everything, `ruff` for format and lint, `mypy` on `src`.
- Line length 100. Comments explain *why*, never *what*.
- One phase per branch, merged to `main`. Commits are authored by the
  repository owner only.
- New domains are a folder under `src/toolsmith/worlds/`, registered by a
  manifest. Adding a fourth world must not require touching the runtime.
- Tests assert behaviour and claims, not implementation. If the README says it,
  a test should break when it stops being true.

## Skills

Three project skills carry the detail, so it is inherited rather than
rediscovered:

- `toolsmith-dev` - the five invariants, the gates that enforce them, and the
  specific bugs that motivated each one
- `toolsmith-eval` - running an evaluation, adding a configuration, and reading
  the results honestly
- `toolsmith-domain` - adding a fourth world

## Adding a model

1. Add a row to `configs/models.yaml` with a real `verified_on` date.
2. `uv run toolsmith probe models` to confirm the id exists.
3. Reference it from a pipeline in `configs/pipelines/`.
4. `uv run toolsmith matrix run` and it appears in the report.

No Python changes. If you find yourself needing one, that is a bug in the
abstraction, not in the model.
