# Changelog

## 0.1.0 - 2026-11-11

Initial public release candidate.

- Indexes local `SKILL.md` metadata without loading every skill body.
- Routes tasks to a bounded set of skills with explainable scores and budget decisions.
- Keeps duplicate skill names addressable through stable catalog keys.
- Tracks short leases and reports release reasons during task switches.
- Separates physical context removal from logical unloads in append-only histories.
- Includes deterministic examples and unit tests for routing, lifecycle, parsing, and context behavior.

Known limitations are documented in the README: routing is lexical, token estimates are approximate, and the runtime does not execute or download skills.
