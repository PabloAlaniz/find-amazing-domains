## What

<!-- One or two sentences: what changes and why. Link the issue if there is one. -->

## How it was tested

<!-- `pytest`, `pre-commit run --all-files`, and for TLD changes: which registry
     you probed, with which registered and random names. -->

## Checklist

- [ ] Tests added or updated (CI enforces a 95% coverage floor)
- [ ] `pre-commit run --all-files` passes (ruff, ruff-format, mypy --strict)
- [ ] `CHANGELOG.md` has a line under `## [Unreleased]`
- [ ] For a new or changed TLD: `registry_sources.json` entry has `verified` and `source`, and WHOIS fixtures are the real replies
