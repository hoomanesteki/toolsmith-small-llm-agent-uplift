---
name: toolsmith-domain
description: How to add a fourth world. Use when asked to support a new domain, dataset or customer scenario. The runtime, harness, report and UI do not change.
---

# Adding a domain

A world is a folder under `src/toolsmith/worlds/`, plus one line in
`registry.py`. Nothing in the runtime, harness, report or UI changes, and the
conformance suite runs against it automatically the moment it is registered.

## What the folder contains

```
worlds/<key>/
  __init__.py     the WorldSpec: schema, seed, entities, tools, policy, lexicon, samplers
  schema.sql      tables. Money is INTEGER cents; dates are ISO offsets from BASE_DATE
  seed.py         a builder taking (conn, seed). Seeded RNG only: no faker, no network, no clock
  tools.py        the verb bindings and the server-side policy function
  samplers.py     nine named row samplers
```

## The three contracts

**Verbs.** Bind the twelve canonical verbs in `worlds/base.Verb` to your nouns.
`SEARCH_PRINCIPALS` is `search_customers` in retail and `search_patients` in a
clinic. Seven are required; the rest are optional and the task generator only
emits programs using verbs you bind. Exactly one tool must be `privileged`, and
a world with one must supply a policy function.

**Lexicon.** Ten words so task templates can phrase questions in your domain's
language without knowing what domain it is: `principal`, `record`, `case`,
`privileged_action`, `policy_noun` and their plurals and id fields.

**Samplers.** Nine named functions that draw an *interesting* row. This is where
the domain knowledge lives, deliberately: `privileged_blocked` has to know that
a refund is blocked by an expired window, an existing refund, or a missing
ticket, and no generic template could.

## The rules that make it ground truth

- Build twice, get the same `db_digest`. The conformance test asserts it.
- Money is integer cents. A float does not survive a state diff.
- No date may be later than `BASE_DATE`. Clamp derived dates.
- Errors are data, not exceptions. A missing record returns a clean `not_found`,
  because what the agent does next is the interesting question.
- The policy function runs **after** the model asks and **before** the mutation.
  It reads the world's own rules from a table, never a constant.

## Check your work

```bash
uv run toolsmith world build --all      # digests must be stable
uv run toolsmith world show <key>       # entities and verb bindings
uv run toolsmith world call <key> <tool> --args '{...}'
uv run pytest tests/test_worlds.py      # the conformance suite, parametrised over your world
uv run toolsmith tasks build            # your world now generates tasks
```

If `tests/test_worlds.py` fails on `_mutate_something`, add a branch for your
world: the helper needs to know one legal write in your domain.
