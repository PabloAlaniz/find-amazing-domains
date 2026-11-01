# domainhack

[![PyPI](https://img.shields.io/pypi/v/domainhack)](https://pypi.org/project/domainhack/)
[![Python](https://img.shields.io/pypi/pyversions/domainhack)](https://pypi.org/project/domainhack/)
[![CI](https://github.com/PabloAlaniz/find-amazing-domains/actions/workflows/lint-typecheck-test.yml/badge.svg)](https://github.com/PabloAlaniz/find-amazing-domains/actions/workflows/lint-typecheck-test.yml)
[![Coverage](https://codecov.io/gh/PabloAlaniz/find-amazing-domains/graph/badge.svg)](https://codecov.io/gh/PabloAlaniz/find-amazing-domains)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/PabloAlaniz/find-amazing-domains/blob/main/LICENSE)

**Find domain hacks hiding in real words, and check them live against the registries.**

Some words have a secret: they end in a top-level domain. The Spanish word *plato* (plate) becomes `pla.to`. *Abasto* (supply) becomes `abas.to`. *Grato* (pleasant) becomes `gra.to`. domainhack finds those words, asks the registry whether each domain is really free, and tells you what to register.

![domainhack demo: filter a word list for .to hacks, then find a domain for a name](demo.gif)

## Install

```bash
pipx install domainhack          # or: pip install domainhack
uvx domainhack name myname       # run without installing
```

Python 3.10 or newer. No API keys, no accounts, no captchas: domainhack talks to the registries directly.

## Why domainhack

- **It asks the registry, not a reseller.** Every generic TLD is checked over RDAP through the IANA bootstrap, and 170 of the 248 country-code TLDs over RDAP or port-43 WHOIS, each verified live with a registered and a random name. Requests are paced per host, back off on 429/503 as RFC 7480 asks, and a circuit breaker skips a host that stops answering.
- **It knows when "available" lies.** A public-DNS cross-check flags names the registry calls free but that already have nameservers. Taken names come with their registration date, expiry date, registrar, the parking or aftermarket service behind them, and whether they are about to drop (redemption or pending delete).
- **One command for a name.** `domainhack name acme` checks the exact name under a set of TLDs, finds hacks that spell it (`acme` + studio -> `acmestud.io`), optionally tries brand variants, and ends with a ranked recommendation.
- **It handles the hard cases.** Second-level suffixes (`com.ar`, `co.uk`), IDNs (`ñandú.de`), per-TLD label rules, a SQLite cache with TTLs that match how fast each kind of result goes stale, and CSV, JSON Lines, markdown and JSON output.

## Find a domain for a name

You have a name and want a domain for it. One command checks the exact name under a set of TLDs, looks for domain hacks that spell it, optionally tries brand variants, and recommends what to register:

```bash
domainhack name sumanda
domainhack name sumanda --tlds startup,latam --variants --format markdown --output sumanda.csv
```

```
Domains for 'sumanda'
TLDs: com, app, io, ai, co, dev, so, xyz

Exact name
  sumanda.com  taken      since 2015-11-12, expires 2026-11-12, registrar Nikko Reg LLP, parked: domainrecover
  sumanda.app  available  DNS: not delegated
  sumanda.io   available  DNS: not delegated
  ...

Domain hacks
  no TLD is a suffix of 'sumanda'
  sumanda.studio     available    sumanda studio: a creative or production studio -- DNS: not delegated
  sumandastud.io     available    sumanda studio: a creative or production studio -- DNS: not delegated
  sumandarad.io      available    sumanda radio: a radio show or podcast -- DNS: not delegated
  sumandagam.es      unsupported  sumanda games: games -- no registrar supports .es (restricted: ...)
  ...

Recommendation
  1.  sumanda.ai   exact name, strong TLD .ai
  ...
  - sumanda.com is taken (expires 2026-11-12); parked at domainrecover: may be purchasable from the owner
```

What it checks:

- **Exact name** under every TLD of `--tlds`. It takes presets and TLDs, mixed: `--tlds startup,com.ar,la`. The presets are `startup` (the default: com, app, io, ai, co, dev, so, xyz), `latam` (com.ar, ar, com.mx, mx, com.co, co, com.br, cl, pe), `classic` (com, net, org) and `all-supported` (every TLD the tool can check). A TLD the tool cannot check (e.g. `.es`, whose registry only serves accredited users) is reported as unsupported, with the reason.
- **Domain hacks**: the name split at a TLD it ends with (`plato` -> `pla.to`), and the name plus a curated word ending in a TLD (`sumanda` + studio -> `sumandastud.io`, or `sumanda.studio` where the word is itself a TLD). The words and their meanings live in `src/domainhack/data/hack_words.json`. When the name ends in no TLD, the report says so.
- **Brand variants** (`--variants`): `get`/`use`/`try`/`hola`/`my` + name and name + `hq`/`app`/`labs`/`studio`. They are checked under `.com` and the first 3 other requested TLDs only, so there are at most 36 of them.

The report lists the exact names by TLD, the hacks, the available variants and the taken names. Taken names come with their details (registration and expiry dates, parking service, dropping status). It also names everything it could not verify (registry error or unreachable, no registrar for the TLD) and every DNS conflict (the registry says available, but DNS has nameservers). `--format` picks `text` (default), `markdown` (to share) or `json`. `--output FILE` also saves the raw results as CSV or JSON Lines, like `check`.

The recommendation ranks available names only:

- an exact name under `.com`;
- then an exact name under `.app`/`.io`/`.ai`/`.co`;
- then an exact name under another TLD;
- then a `.com` variant;
- then a hack that spells the name;
- then other variants;
- then hacks with an extra word.

Shorter names win within a tier. Taken, dropping, DNS-conflict and unverifiable names are never recommended.

`--confirm-dns` / `--no-confirm-dns` turns the DNS cross-check on or off (on by default when a resolver is available). The registry check uses the same options as `check`: `--delay`, `--parallel`, `--no-cache`, `--cache-*`, `--contact` and `--no-progress`. The exit status follows `check`: it is 1 when some check ended in an error, and the report is still printed.

## Examples

| Word | Language | Domain | Meaning |
|------|----------|--------|---------|
| plato | Spanish | `pla.to` | plate / dish |
| grato | Spanish | `gra.to` | pleasant |
| abasto | Spanish | `abas.to` | supply |
| veto | English | `ve.to` | veto |
| gusto | English | `gus.to` | gusto |
| pinto | English | `pin.to` | pinto |

## Next

- [Usage](usage.md): word-list and brute-force modes, DNS confirmation, parallel checks, output files, caching.
- [Supported TLDs](tlds.md): every TLD, how it is checked, and why some cannot be.
- [How it compares](compare.md) with other domain tools.
- [Architecture](architecture.md) and [contributing](contributing.md).
