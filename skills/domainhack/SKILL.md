---
name: domainhack
description: Find an available domain name for a project or brand, check whether specific domains are registered, find domain hacks (plato -> pla.to), or watch a taken name until it frees up. Use when the user is naming a project, asks "is X.com available", wants domain ideas, or wants to know when a domain expires. Asks the registries directly over RDAP and WHOIS; needs no API key.
---

# domainhack

`domainhack` is a command-line tool. Run it with `uvx domainhack ...` (or `domainhack ...`
when it is installed). It queries domain registries live, one request per registry host at
a time, so a command can take from a few seconds to a minute. Add `--no-progress` to every
command that checks names: the progress bar is noise outside a terminal.

## Pick the command

| The user wants | Command |
|---|---|
| A domain for a name they have | `uvx domainhack name NAME --format json --no-progress` |
| To know whether specific domains are free | `uvx domainhack name NAME --tlds com,io,dev --format json --no-progress` (same name, several TLDs) |
| Short invented names | `uvx domainhack --tld io check --pattern CVCV --limit 30 --no-progress` |
| Which words from a list are domain hacks | `uvx domainhack --tld to,io,it filter words.txt` (offline) |
| To be told when a taken name frees up | `uvx domainhack watch add NAME.TLD`, then `uvx domainhack watch run --no-progress` |
| Which TLDs can be checked | `uvx domainhack tlds` (offline) |

## `name`: a domain for a name

```bash
uvx domainhack name sumanda --format json --no-progress
uvx domainhack name sumanda --tlds startup,com.ar --variants --handles --format json --no-progress
```

- `NAME` has no TLD. `--tlds` mixes presets (`startup`, `latam`, `classic`) and suffixes.
- `--variants` also tries `getNAME`, `NAMEhq` and similar; `--prefix a,b` and `--suffix c,d`
  use the user's own words.
- `--handles` also says whether the name is taken on GitHub, npm and PyPI.
- Read `recommendations` first (ranked, each with a `reason`), then `taken` for names worth
  waiting for (`expires_at`, `parked_hint`, a `dropping` status) and `unverifiable` for what
  could not be checked.

## Patterns

One character per letter of the name: `C` consonant, `V` vowel, `L` any letter, `D` digit;
lowercase letters, digits and hyphens are literal. `CVCV` matches `baba` to `zuzu`; `goCV`
matches `goba`, `gobe`... Always pass `--limit` (it is per TLD): registries forbid bulk
queries, and runs over 10,000 queries are refused.

## Reporting results

- Only a name with status `available` is free. `dns-conflict`, `taken`, `dropping` and
  `unverifiable` are not.
- "Available" means "not registered when asked". Premium and reserved names can show as
  available, and prices shown are a reference list, not a quote. Tell the user to confirm
  at a registrar before counting on a name.
- Exit status 1 means some checks failed (a registry did not answer); the report is still
  printed and names what could not be verified. Do not present those names as taken or free.
- Do not retry a failed registry in a loop. Re-running later is the fix.

## MCP alternative

If the `domainhack` MCP server is connected, prefer its tools (`find_domain_for_name`,
`check_domains`, `check_pattern`, `watch_domains`, `find_hacks_for_words`, `list_tlds`):
they return the same data without shelling out.
