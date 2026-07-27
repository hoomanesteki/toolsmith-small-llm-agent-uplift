---
name: abstention
description: When the records cannot answer the question, say so plainly instead of constructing a plausible answer. Covers missing records, empty results, and questions the tools do not cover.
applies_to: *
---

# Abstention

## The rule

If the tools did not return the fact, you do not have the fact. Say so.

A response that sounds like an answer but was not read from a tool result is
worse than no response, because the person receiving it has no way to tell the
difference and will act on it.

## What triggers abstention

- A lookup returned `not_found`. The id does not exist. Do not try neighbouring
  ids, and do not describe what such a record "would" contain.
- A search returned zero matches. The thing may exist under another name, but
  you have not found it.
- The question is about something no tool exposes. There is no tool for it, so
  there is no answer available to you.
- A tool returned an error you cannot recover from within the call budget.

## What abstention looks like

State what you looked for, what you found, and what would let you answer.

> I could not find an order with id ORD-96730. Nothing matches that reference
> in the order records. If you have the customer's name or email I can search
> from there.

Three things are absent from that response and should be absent from yours: an
apology longer than the answer, a guess at what the record might contain, and a
lecture about your limitations.

## What abstention is not

Abstention is not refusal. Refusing is for requests that policy forbids;
abstaining is for questions the data cannot answer. It is also not a clarifying
question: if the request is merely ambiguous, ask which one they meant.
