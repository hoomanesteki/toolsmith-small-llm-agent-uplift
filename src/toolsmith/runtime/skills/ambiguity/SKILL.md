---
name: ambiguity
description: When a reference matches more than one record, ask which one. Do not pick the first match, and do not answer about all of them as though they were one.
applies_to: *
---

# Ambiguous references

## The situation

A search returns `match_count: 6` for a given name. Six different people share
it. Nothing in the request distinguishes them.

## The wrong moves

- **Picking the first.** The ordering is by id, which correlates with nothing
  the user cares about. You have a one-in-six chance and no way to convey that.
- **Answering about all of them.** "Here are six customers named Mira" answers a
  question nobody asked and buries the one they wanted.
- **Guessing from context.** Unless the request actually narrows it, inferring
  which one they meant is a guess wearing a reason.

## The right move

Say how many matched, show enough to distinguish them, and ask.

> Six customers are named Mira: CUS-1042 (Mira Chen, premium), CUS-1188 (Mira
> Okafor, free), and four others. Which one did you mean?

Include the attribute that is likely to distinguish them for this request. If
the question was about a refund, the tier matters; if it was about delivery, the
region does.

## When it is not ambiguous

If the request supplies an id, or the search returns one match, proceed. Asking
a clarifying question that the request already answered is its own failure, and
it is measured too.
