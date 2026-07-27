---
name: grounded-answers
description: Answering from retrieved documents. Cite the section you actually read, check that it is the current version, and never cite an id you did not fetch.
applies_to: doc
---

# Grounded answers

## Cite what you read

A citation is a section id that came back from a tool call in this
conversation. Not a plausible id, not the document id when you read a section,
and not an id you remember from an earlier task.

The output gate checks every id you cite against what the run actually
retrieved. An invented citation is caught, and it is a more serious failure than
a wrong answer, because it manufactures the appearance of evidence.

## Check the version

Search results carry `doc_status` and `version`. Roughly a third of policies
exist in two versions with different values, and the superseded one still reads
like an authoritative document.

- `current` is what to answer from.
- `superseded` means a newer version exists. Do not quote its numbers.
- `draft` is not yet in force. Say so if you use it.

## Narrow before you search

Headings repeat across services: a dozen documents have a section called "Data
retention". Searching for "retention period" alone returns the right heading
from the wrong service. Resolve the service first and pass `service_id`.

## When the snippet is not enough

`search_docs` returns a truncated snippet. If the value you need is at the edge
of it, or the sentence is ambiguous, fetch the section in full before answering.
One extra call is cheaper than a wrong number.
