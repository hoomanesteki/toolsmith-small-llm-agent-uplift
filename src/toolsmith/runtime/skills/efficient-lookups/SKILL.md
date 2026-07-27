---
name: efficient-lookups
description: Getting to the answer in as few calls as the oracle would. Resolve names to ids once, use aggregates instead of listing rows, and never re-read what is already in context.
applies_to: *
---

# Efficient lookups

Efficiency is a graded dimension and a real cost. Every redundant call is input
tokens on every subsequent turn, because the transcript grows.

## Patterns that work

- **Name to id, once.** Search resolves a name to an id. Having resolved it,
  use the id. Searching the same name again later is pure waste.
- **Aggregate rather than enumerate.** "How much revenue by region" is one
  `query_metrics` call. Listing every row and adding them up is a dozen calls
  and an arithmetic error waiting to happen.
- **Read the counts.** List results carry `match_count` and `truncated`. If you
  need a count, it is already there; do not page through to get it.
- **Use the calculator.** Every arithmetic step. It is free and you are not.
- **Resolve dates before reasoning about them.** "In the last thirty days"
  needs `today` first. The system clock is fixed and is not the wall clock.

## Patterns that waste

- Re-fetching a record whose result is already in the transcript above.
- Listing a principal's records when you only needed one you already have.
- Calling a tool to confirm something a previous result already stated.
- Searching with a single letter, which matches everything and tells you
  nothing.

## The test

Before each call, ask what the result will tell you that you do not already
know. If the answer is nothing, you have your answer already.
