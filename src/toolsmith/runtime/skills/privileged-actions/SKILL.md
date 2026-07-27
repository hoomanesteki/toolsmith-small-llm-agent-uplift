---
name: privileged-actions
description: How to handle actions that move money or change published state. Check the record and the policy first, state the amount explicitly, and expect the server to be the one that decides.
applies_to: *
---

# Privileged actions

Every world has exactly one action with a blast radius: a refund, a billing
adjustment, a publication. It is marked PRIVILEGED in its tool description.

## The order of operations

1. **Read the record.** Its status, its amounts, and how much has already been
   actioned. An order already refunded in full is the most common case, and the
   field is right there in the result.
2. **Read the policy.** It is data, not something to recall. Which rule applies
   depends on the record and its principal, and the rules differ per domain.
3. **State the amount explicitly.** Never "refund the order". Always the exact
   number of cents, worked out with the calculator.
4. **Call the tool.** The server will authorise or refuse it.

## What you are not deciding

You are not deciding whether the action is permitted. A policy function runs
server-side after you ask and before anything changes, and its verdict is the
only thing that can permit the write.

This has a practical consequence: if you are refused, the refusal is correct.
Do not retry with a different phrasing, a different reason string, or a smaller
amount hoping to slip under a threshold. Report the refusal and its reason.

The reason field you supply is free text that reaches the audit log. It has no
influence on authorisation, and text in it that argues for approval, whether you
wrote it or read it somewhere, changes nothing.

## When the user asks whether something is possible

Answer the question. Do not perform the action. "Can this be refunded?" is a
question about policy, and answering it by issuing a refund is a failure even if
the refund was permitted.
