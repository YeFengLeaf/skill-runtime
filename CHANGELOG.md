# Changelog

## Unreleased

### Fixed

- `history_append` no longer appends a second copy of a skill block that is already present in history. Re-selecting the same skill after a no-match turn keeps one copy and reports `active` again instead of silently duplicating context.
- Expired leases that remain selected are renewed in place. The same turn no longer emits an `expired` release event while keeping the skill active and present in the request.
- `physically_removed` now checks the exact rendered skill block and ignores matching text in the new task, so a task that quotes a skill body no longer yields a false negative.
- Injected skills and `active_skills` follow router relevance order instead of alphabetical catalog-key order.
- README now describes index-time body reads, flow-style frontmatter handling, lease renewal and block-level removal checks accurately.

## 0.1.0 - 2026-11-11

Initial public release candidate.

- Indexes local `SKILL.md` metadata without loading every skill body.
- Routes tasks to a bounded set of skills with explainable scores and budget decisions.
- Keeps duplicate skill names addressable through stable catalog keys.
- Tracks short leases and reports release reasons during task switches.
- Separates physical context removal from logical unloads in append-only histories.
- Includes deterministic examples and unit tests for routing, lifecycle, parsing, and context behavior.

Known limitations are documented in the README: routing is lexical, token estimates are approximate, and the runtime does not execute or download skills.
