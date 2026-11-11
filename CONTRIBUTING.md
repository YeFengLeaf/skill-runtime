# Contributing

## Development setup

The project has no runtime dependencies and supports Python 3.9 or newer.

```bash
python -m unittest discover -s tests -v
python -m compileall -q skill_runtime tests
python -m skill_runtime --skills examples/skills demo
```

Keep changes focused and add a regression test for behavior that is observable at the public API or command line. Do not add credentials, local skill directories, chat history, or generated build output to commits.

## Pull requests

Describe the user-visible behavior, the test command you ran, and any limitation that remains. Changes to routing, context construction, or frontmatter parsing need tests covering both the successful path and the relevant rejection or boundary case.
