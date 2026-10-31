# Contributing to domainhack

Thanks for taking the time. Bug reports, new TLDs, better WHOIS patterns and
documentation fixes are all welcome.

## Setup

```bash
git clone https://github.com/PabloAlaniz/find-amazing-domains.git
cd find-amazing-domains
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
```

```bash
pytest                      # unit tests, no network (about 30 s)
pre-commit run --all-files  # ruff, ruff-format, mypy --strict: exactly what CI runs
pytest -m integration       # live registry tests (network, slow, optional)
```

CI runs the unit tests on Python 3.10 to 3.14 with a 95% coverage floor, so new
code needs tests. Keep `mypy --strict` clean; `Any` and `# type: ignore` need a
comment saying why.

## Adding or fixing a TLD

This is the most useful contribution and usually a one-file change plus two
fixtures.

1. Find how the registry answers. Try RDAP first (`https://rdap.nic.<tld>/domain/example.<tld>`
   or whatever the registry documents), then port-43 WHOIS (`whois -h whois.nic.<tld> example.<tld>`).
2. Edit `src/domainhack/data/registry_sources.json`. Copy a neighbouring entry:
   - `status`: `rdap`, `whois` or `unsupported`;
   - `rdap`: the base URL ending in `/`, or `null`;
   - `whois`: `{"server": "...", "not_found": ["<pattern>"], "min_interval": null}`, or `null`.
     `not_found` patterns must match the registry's real "no such domain" reply;
   - `reason`: for `unsupported`, say why and where to check by hand;
   - `verified`: the date and the two names you probed (a registered one and a
     random one), `source`: how you found out.
3. For WHOIS, save the two real replies, byte for byte, as
   `tests/fixtures/whois/<tld>.taken.txt` and `tests/fixtures/whois/<tld>.free.txt`.
   The fixture tests make sure the `not_found` pattern matches the free reply
   and does not match the taken one, so a wrong pattern (which would report
   false "available" results) fails the build.
4. Run `pytest` and `domainhack tlds <tld>`.
5. Add a line under `## [Unreleased]` in `CHANGELOG.md`.

Registries limit automated lookups. Probe each one a handful of times, not in a loop.

## Pull requests

- One topic per PR. Small PRs get reviewed faster.
- Say what you tested, including against which registry when a TLD is involved.
- `CHANGELOG.md` follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/):
  add your change under `## [Unreleased]`.
- Commit messages: a short imperative subject line (`Add .xx WHOIS server`).

## Architecture in one paragraph

`domain/` holds pure entities, `ports/` the interfaces, `usecases/` the logic
(filter words, rank, check, confirm with DNS, brand report), `adapters/` the
implementations (RDAP, WHOIS, SQLite cache, writers) and `cli/` wires them
together. A new registrar, cache or output format is a new adapter that
implements one interface; existing code should not need to change. See
"Architecture" in the README.

## Reporting bugs and asking questions

- Bugs and TLD requests: open an issue with the template.
- Questions and ideas: GitHub Discussions.
- Security issues: see `SECURITY.md`.

By contributing you agree that your contributions are licensed under the MIT
License, like the rest of the project.
