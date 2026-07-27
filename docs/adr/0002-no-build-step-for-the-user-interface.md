# ADR 0002: The user interface has no build step

- Status: accepted
- Date: 2026-07-27
- Supersedes: the stack named in the source design document (Next.js 16, shadcn,
  AI Elements, Recharts, xyflow, elkjs)

## Context

The design document this project was built from specified a modern React stack
and called the choice binding. It was a reasonable specification: those
libraries ship a chat surface, a workflow canvas and human-in-the-loop
components, and assembling them would have been faster than writing four screens
by hand.

Two constraints emerged during the build that the specification could not have
known about.

**The demo must work on a fresh clone with no keys and no network.** That is not
a nice-to-have; it is the property that makes every number in this repository
checkable by a stranger. A build step is one more thing that can be broken at
the moment someone is looking, and `node_modules` on a machine with no network
is a dead end rather than a slow start.

**The deployment target is a free container that sleeps after inactivity.** One
process, one port, one URL, no CORS. A separate frontend deployment doubles the
things that can be down when a link is clicked, and a portfolio link that 502s
has negative value.

## Decision

The interface is hand-written ES modules and CSS, served as static files by the
same FastAPI process that serves the API and the event stream. No bundler, no
framework, no dependencies.

The charts are hand-written SVG against a palette validated for colour-vision
deficiency in both light and dark modes.

## Consequences

**What this costs.** Roughly 1,900 lines that a library would have provided:
a router, a chart layer, a tooltip, an animated counter. Some of that is
genuinely reinvented. There is no component library to reach for when a fifth
screen is needed.

**What it buys.**

- `git clone && uv sync && make serve` works, always, offline.
- The dependency count of the whole user interface is zero, which matters
  disproportionately in a project whose argument is about counting costs
  honestly.
- The charts follow the rules that make charts readable, because they were
  written against those rules rather than configured around a library's
  defaults. One axis. Colour follows the entity, not its rank. A table twin on
  every figure. Those are difficult to guarantee through a wrapper.
- Server-sent events reach the browser through fourteen lines of `EventSource`.
  A framework adapter for the same thing would have been longer.

**What would change the decision.** A fifth and sixth screen, or a second
developer, or any requirement for offline-capable client-side state. At that
point the hand-written router stops being cheaper than the library.

## What was kept from the specification

The four screens, exactly as scoped: chat, the agent graph, the comparison lab,
and the review queue. The Apple-adjacent visual direction, including the
restraint the specification recommended: glass on the navigation layer only, no
refraction, negative tracking that grows more negative with size, OKLCH tokens
where dark mode lifts rather than blackens and accents gain chroma. And the
insistence that the interface is where the work becomes visible rather than a
wrapper over a JSON dump.
