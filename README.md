# domainhack

[![PyPI](https://img.shields.io/pypi/v/domainhack)](https://pypi.org/project/domainhack/)
[![Python](https://img.shields.io/pypi/pyversions/domainhack)](https://pypi.org/project/domainhack/)
[![CI](https://github.com/PabloAlaniz/find-amazing-domains/actions/workflows/lint-typecheck-test.yml/badge.svg)](https://github.com/PabloAlaniz/find-amazing-domains/actions/workflows/lint-typecheck-test.yml)
[![Coverage](https://codecov.io/gh/PabloAlaniz/find-amazing-domains/graph/badge.svg)](https://codecov.io/gh/PabloAlaniz/find-amazing-domains)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Find domain hacks hiding in real words, and check them live against the registries.**

Some words have a secret: they end in a top-level domain. The Spanish word *plato* (plate) becomes `pla.to`. *Abasto* (supply) becomes `abas.to`. *Grato* (pleasant) becomes `gra.to`. domainhack finds those words, asks the registry whether each domain is really free, and tells you what to register.

![domainhack demo: filter a word list for .to hacks, then find a domain for a name](docs/demo.gif)

## Install

```bash
pipx install domainhack          # or: pip install domainhack
uvx domainhack name myname       # run without installing
brew install pabloalaniz/tap/domainhack   # Homebrew (macOS, Linux)
```

Python 3.10 or newer. No API keys, no accounts, no captchas: domainhack talks to the registries directly.

## Why domainhack

- **It asks the registry, not a reseller.** Every generic TLD is checked over RDAP through the IANA bootstrap, and 170 of the 248 country-code TLDs over RDAP or port-43 WHOIS, each verified live with a registered and a random name. Requests are paced per host, back off on 429/503 as RFC 7480 asks, and a circuit breaker skips a host that stops answering.
- **It tells you when a name changes hands.** `domainhack watch` follows the names you care about and reports only what changed: became available, started dropping, was registered, expiry date moved.
- **It knows when "available" lies.** A public-DNS cross-check flags names the registry calls free but that already have nameservers. Taken names come with their registration date, expiry date, registrar, the parking or aftermarket service behind them, whether their holder lists them for sale (the `_for-sale` DNS record), and whether they are about to drop (redemption or pending delete).
- **One command for a name.** `domainhack name acme` checks the exact name under a set of TLDs, finds hacks that spell it (`acme` + studio -> `acmestud.io`), optionally tries brand variants, and ends with a ranked recommendation.
- **It works from an AI agent too.** `domainhack-mcp` exposes the same checks as [MCP](https://modelcontextprotocol.io/) tools, so Claude or Cursor can find a domain while it helps you name a project. See [Use it from an AI agent](#use-it-from-an-ai-agent-mcp).
- **It handles the hard cases.** Second-level suffixes (`com.ar`, `co.uk`), IDNs (`ñandú.de`), per-TLD label rules, a SQLite cache with TTLs that match how fast each kind of result goes stale, and CSV, JSON Lines, markdown and JSON output.

## How it compares

| | domainhack | [domaingrep](https://github.com/ysm-dev/domaingrep) | [tldx](https://github.com/brandonyoungdev/tldx) | [DomainHacks.info](https://domainhacks.info/) | [domain-check](https://github.com/saidutt46/domain-check) | [domh](https://github.com/codeatlasdev/domain-hunter) |
|---|---|---|---|---|---|---|
| Finds domain hacks (word split at a TLD) | yes | yes | no: prefix/suffix permutations | yes | no: patterns and prefix/suffix | no: pronounceable patterns and prefix/suffix |
| Availability source | registry RDAP + WHOIS | DNS | RDAP | registry lookups (about 900 of 1466 TLDs) | RDAP, WHOIS as fallback | DNS first, then RDAP, WHOIS and SSL |
| Cross-checks "available" against DNS | yes | DNS is the source | not documented | not documented | not documented | DNS is the first filter |
| Taken-name details (expiry, registrar, parking, dropping) | yes | not documented | `_for-sale` TXT record | not documented | registrar, dates and status codes (`--info`) | registrar (`--info`) |
| Second-level suffixes (`com.ar`, `co.uk`) and IDN | yes | not documented | not documented | not documented | not documented | `com.br` and its siblings; IDN not documented |
| Country-code TLDs you can check | 170 of 248, each verified live | "every TLD", list not published | not published | about 900 TLDs | "1,200+ TLDs", list not published | 1,437 TLDs (1,199 with RDAP), listed by `domh tlds` |
| Runs as | CLI and MCP server, Python 3.10+ | CLI, Rust | CLI, Go | website | CLI, Rust library, MCP server | CLI and TUI, MCP server, Go |
| License | MIT | MIT | Apache-2.0 | proprietary | MIT or Apache-2.0 | MIT |

"Not documented" means the project's README or site did not say so when it was last read (domaingrep, tldx and DomainHacks.info in October 2026; domain-check and domh in November 2026); corrections are welcome in an issue.

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
- **Domain hacks**: the name split at a TLD it ends with (`plato` -> `pla.to`), and the name plus a curated word ending in a TLD (`sumanda` + studio -> `sumandastud.io`, or `sumanda.studio` where the word is itself a TLD). The words and their meanings live in `src/domainhack/data/hack_words.json`. When the name ends in no TLD, the report says so. When it does, hacks that move part of the name to a subdomain are checked too: `delicious` -> `del.icio.us`, where the name to register is `icio.us`. `--no-subdomain-hacks` leaves them out.
- **Brand variants** (`--variants`): `get`/`use`/`try`/`hola`/`my` + name and name + `hq`/`app`/`labs`/`studio`. They are checked under `.com` and the first 3 other requested TLDs only, so there are at most 36 of them. `--prefix get,try` and `--suffix hq,labs` use your own words instead (at most 10 of each). On their own they add only the words you give; with `--variants` the side you did not name keeps its default list.
- **Reference prices**: available names show what their TLD costs a year (`$28.12/yr, renews $51.50`) when the bundled price list has it. The list is a dated snapshot of one registrar's public prices for an ordinary name, not a quote: premium and reserved names cost more. The report names its source and date. `--no-prices` hides them.
- **Handles** (`--handles`): whether the name is taken as a GitHub user or organization, an npm package and a PyPI project, with one request to each. "Not found" means nothing answers to that name today; services also reserve names and refuse look-alikes. Anything a service does not answer clearly is reported as "could not check".

The report lists the exact names by TLD, the hacks, the available variants and the taken names. Taken names come with their details (registration and expiry dates, parking service, dropping status). It also names everything it could not verify (registry error or unreachable, no registrar for the TLD) and every DNS conflict (the registry says available, but DNS has nameservers). `--format` picks `text` (default), `markdown` (to share) or `json`. `--output FILE` also saves the raw results as CSV or JSON Lines, like `check`.

The recommendation ranks available names only:

- an exact name under `.com`;
- then an exact name under `.app`/`.io`/`.ai`/`.co`;
- then an exact name under another TLD;
- then a `.com` variant;
- then a hack that spells the name;
- then other variants;
- then hacks with an extra word or with a subdomain.

Shorter names win within a tier, and between two names of the same tier and length the one with the cheaper TLD comes first. Handles never change the ranking. Taken, dropping, DNS-conflict and unverifiable names are never recommended.

`--confirm-dns` / `--no-confirm-dns` turns the DNS cross-check on or off (on by default when a resolver is available). The registry check uses the same options as `check`: `--delay`, `--parallel`, `--no-cache`, `--cache-*`, `--contact` and `--no-progress`. The exit status follows `check`: it is 1 when some check ended in an error, and the report is still printed.

## Use it from an AI agent (MCP)

domainhack runs as a [Model Context Protocol](https://modelcontextprotocol.io/) server with four tools: `find_domain_for_name` (what `name` does, as structured data), `check_domains` (specific names, with registration details), `find_hacks_for_words` (offline) and `list_tlds` (offline).

```bash
pipx install "domainhack[mcp]"
claude mcp add domainhack -- uvx --from "domainhack[mcp]" domainhack-mcp   # Claude Code
```

For Claude Desktop, Cursor and other clients, add a stdio server that runs `domainhack-mcp` (or `uvx --from "domainhack[mcp]" domainhack-mcp`):

```json
{"mcpServers": {"domainhack": {"command": "uvx", "args": ["--from", "domainhack[mcp]", "domainhack-mcp"]}}}
```

The server takes the run options the CLI shares (`--delay`, `--parallel`, `--no-cache`, `--contact`) and reads the same [configuration file](#configuration-file). Only names with status `available` are recommended; the agent is told that "available" means "not registered" and to confirm with a registrar before buying. Details in the [docs](https://pabloalaniz.github.io/find-amazing-domains/mcp/).

## Weekly report

Every Tuesday a [workflow](.github/workflows/weekly-report.yml) checks the 5,086 distinct common English and Spanish words in [`data/wordlists/`](data/wordlists/) against every TLD domainhack can query and publishes the result in [`reports/latest.md`](https://github.com/PabloAlaniz/find-amazing-domains/blob/reports/reports/latest.md): the hacks that are available right now, the registered ones in redemption or pending delete (so they may be free soon), the names a registry calls available while DNS still delegates them, and a per-TLD breakdown. From the second run on it opens with what changed since the week before: the hacks that became available, the ones someone registered, and the ones that started dropping. An [Atom feed](https://raw.githubusercontent.com/PabloAlaniz/find-amazing-domains/reports/reports/feed.xml) has one entry per report. The [Browse page](https://pabloalaniz.github.io/find-amazing-domains/browse/) of the documentation site searches the latest report by word, TLD, language and length. Dated copies stay in the [`reports` branch](https://github.com/PabloAlaniz/find-amazing-domains/tree/reports/reports). It runs with the default pacing (one request per registry host at a time, one second apart) and reuses its result cache between runs, so taken names are not asked about again before they could have expired.

## Examples

| Word | Language | Domain | Meaning |
|------|----------|--------|---------|
| plato | Spanish | `pla.to` | plate / dish |
| grato | Spanish | `gra.to` | pleasant |
| abasto | Spanish | `abas.to` | supply |
| veto | English | `ve.to` | veto |
| gusto | English | `gus.to` | gusto |
| pinto | English | `pin.to` | pinto |

## How It Works

The tool has **three modes** to generate candidate domains, and then checks their availability against the registrar.

### Mode 1: Word list -- find domains that are real words

Feed a dictionary file. The tool finds words ending in a TLD suffix, splits them, and checks the resulting domain.

```
plato  -->  ends in "to"  -->  pla.to  -->  AVAILABLE / TAKEN
grato  -->  ends in "to"  -->  gra.to  -->  AVAILABLE / TAKEN
hello  -->  no TLD match  -->  (skipped)
```

This mode produces **memorable, meaningful domains** because every candidate is a real word.

### Mode 2: Brute-force -- try all letter combinations

Generate all combinations from `a` to `zzzzzz` (max length 6) and check each one as a domain. No dictionary needed.

```
a.to, b.to, ..., z.to, aa.to, ab.to, ..., zz.to, aaa.to, ...
```

This mode is useful for finding **short, available domains** regardless of whether they form real words.

### Mode 3: Pattern -- short names you can pronounce

Describe the shape of the name (`C` consonant, `V` vowel, `L` letter, `D` digit) and check only the names that fit.

```
CVCV  -->  baba.to, babe.to, babi.to, ..., zuzu.to
```

This mode finds **short, brandable domains** without wading through combinations like `qxzk`.

## Usage

### Filter only (no availability check)

List which words from a file would form domain hacks, without querying the registrar. Useful to preview candidates before a long check run.

```bash
# Spanish words ending in "to", at least 5 letters
domainhack --tld to filter data/words_es.txt --min-length 5

# English words ending in "in"
domainhack --tld in filter data/words_en.txt --min-length 4
```

### Check availability -- word list mode

Filter a word list **and** check each domain against the registrar in one step.

```bash
# Check Spanish .to domains with 1s delay between requests
domainhack --tld to check --file data/words_es.txt --delay 1.0

# Preview which domains would be checked (no HTTP requests)
domainhack --tld to check --file data/words_es.txt --dry-run

# Also show taken domains in the output
domainhack --tld to check --file data/words_es.txt --show-taken

# Show taken domains that are about to drop (redemption or pending delete)
domainhack --tld to check --file data/words_es.txt --show-dropping

# Only the 50 best candidates per TLD (shortest names first)
domainhack --tld to,it check --file data/words_es.txt --limit 50

# Check in word-list order instead
domainhack --tld to check --file data/words_es.txt --order input

# Most common words first: common words make the best hacks
domainhack --tld to check --file data/words_es.txt --frequency-list data/wordlists/es-common.txt
```

Taken names in their registry's redemption period or pending delete are "dropping": they may become free soon. They print as `TAKEN (dropping: pending delete, expires 2026-11-02): x.to` with `--show-taken` or `--show-dropping`, and the summary counts them (`12 taken (1 dropping)`). Statuses and expiry dates come from RDAP; for WHOIS TLDs they are read only when the reply uses EPP status codes and ISO dates (e.g. `.it`).

Other taken names print with what the registry says about them, so you can tell an active site from a name parked for sale:

```
  TAKEN:     sumanda.com (since 2015-11-12, expires 2026-11-12, parked: domainrecover)
```

Only the known parts are shown. `parked:` names the parking or aftermarket service the nameservers point to (`domainrecover`, `sedo`, `parkingcrew`, `bodis`, `afternic`, `dan.com`, `hugedomains`, `godaddy-parked`, `namebright`, ...; the curated list is in `domain/parking.py`). RDAP gives the registration date, registrar and nameservers; WHOIS gives the creation date and nameservers when the reply has `Creation Date:`/`Created:` in ISO form and `Name Server:`/`nserver:` lines.

Registries rightly limit how many automated lookups a client may make, so the best candidates are checked first. `--order` picks the order:

- `score` (default): shortest SLD first, then shortest full word, then alphabetical. The order is deterministic.
- `frequency`: most common words first. It needs `--frequency-list FILE`, a word list sorted most common first (the lists in [`data/wordlists/`](https://github.com/PabloAlaniz/find-amazing-domains/tree/main/data/wordlists) are); giving that option is enough to pick this order. Repeat it for several languages: a word takes its best rank. Words in no list are checked last, in `score` order.
- `alpha`: alphabetical by domain name.
- `input`: the order of the word list.

`--limit N` checks at most N domains per TLD, taken in that order. `--dry-run` lists exactly what a run with the same options would check, and the progress bar total accounts for the limit.

### Check availability -- brute-force mode

Generate all letter combinations up to a given length (max 6) and check each one.

```bash
# Try all 1-2 letter .to domains (26 + 676 = 702 combinations)
domainhack --tld to check --range-max 2

# Try up to 3 letters, but stop at "ba"
domainhack --tld to check --range-max 3 --range-end "ba"

# Preview combinations without checking
domainhack --tld to check --range-max 2 --dry-run

# Only the first 500 combinations per TLD
domainhack --tld to,io check --range-max 3 --limit 500
```

Combinations are generated shortest first, lazily (`--range-max 6` is over 300 million names), so range mode is never sorted and `--order` only accepts `input` there.

### Check availability -- pattern mode

Most letter combinations are unpronounceable. A pattern keeps the ones that read like a name: `CVCV` is 11,025 names where four free letters are 456,976.

```bash
# Consonant-vowel-consonant-vowel: baba.to, babe.to, ..., zuzu.to
domainhack --tld to check --pattern CVCV --limit 500

# Fixed letters with free positions: goba.io, gobe.io, ...
domainhack --tld io check --pattern goCV

# Preview without checking
domainhack --tld to check --pattern CVC --dry-run
```

A pattern has one character per position of the name:

| Character | Stands for |
|-----------|------------|
| `C` | a consonant (`y` counts as one) |
| `V` | a vowel (`a e i o u`) |
| `L` | any letter |
| `D` | a digit |
| lowercase letter, digit or `-` | itself |

Names come out in order, the first position changing slowest, and are never sorted (`--order` only accepts `input`). `--limit`, `--dry-run`, the estimate and the `--yes` threshold below work as in range mode.

Before a range or pattern run, domainhack prints the estimated number of queries and the minimum run time. The estimate accounts for TLDs that share a host and for each host's pacing:

```
Estimated 36,556 queries to 2 hosts (rdap.tonicregistry.to, rdap.identitydigital.services): at least 5 h 4 min at the current pacing.
```

Runs over 10,000 queries are refused with exit status 2 unless you pass `--yes`. Narrow the run with `--limit`, `--range-end` or a tighter pattern instead where you can: registry terms of use forbid bulk querying. `--dry-run` never queries anything, so it is exempt.

### Multiple TLDs

Pass a comma-separated list to `--tld`. Each TLD is routed to a registrar that supports it; unsupported TLDs are skipped with a warning.

```bash
# Words that form .to, .io or .it hacks (prints "word -> sld.tld")
domainhack --tld to,io,it filter data/words_en.txt

# Check all of them
domainhack --tld to,io,it check --file data/words_en.txt
```

Second-level suffixes work too: `--tld com.ar,com.mx,co.uk`. A word matches by plain concatenation, ignoring the suffix's dots (`fotocomar` -> `foto.com.ar`). Each one is checked against the registry of its top-level domain (`com.ar` -> `rdap.nic.ar`, `com.mx` -> `whois.mx`), with the whole name in the query.

#### DNS confirmation

```bash
domainhack --tld io,com.ar check --file words.txt --confirm-dns
```

`--confirm-dns` looks up every available or taken name in public DNS (NS, then A/AAAA, 3 s timeout, system resolver) as results come in. DNS is a second opinion, never the verdict. If the registry says a name is available but it has NS records, it prints as

```
  AVAILABLE? x.io -- registry says free but DNS has NS records
```

and the summary counts these names. Treat them as taken until a registrar confirms otherwise.

For a taken name that is delegated, the same lookup asks for a `_for-sale` TXT record: the way a holder says "this name can be bought" in DNS (an IETF draft, in use on `.nl` among others). When there is one, the name prints as `TAKEN: x.io (expires 2027-03-01, for sale: fval=EUR999)`, with whatever the record says after its version tag, or `for sale: listed` when it says nothing more. NXDOMAIN means "not in DNS". It is not an error, because many registered names are not delegated. Timeouts and SERVFAIL are recorded as DNS errors and never stop the run.

#### Unreachable registries

When a registry does not answer (timeouts, rate limits, an open circuit breaker), the summary names it, e.g. `warning: registry for .com.ar did not respond (3 errors); re-run later`. A TLD with only some failed checks gets `registry for .io failed 2 of 10 checks`. Errors are never cached, so the re-run checks only those names again.

#### Parallel checks

Different registry hosts are checked in parallel, each with one request in flight at a time. TLDs served by the same host share it: `.io`, `.sh`, `.ac` and `.me` (Identity Digital) are checked one after another, while `.to` and `.it` go ahead independently. One slow or unresponsive host (e.g. `whois.nic.it` at one query per 4 s) no longer holds up the others.

```bash
# Up to 8 hosts at once (default: 4)
domainhack --tld to,io,it,co,de check --file data/words_en.txt --parallel 8

# Strictly one domain at a time, in check order (the old behaviour)
domainhack --tld to,io,it check --file data/words_en.txt --parallel 1

# Parallel, but print and save results in check order
domainhack --tld to,io,it check --file data/words_en.txt --keep-order
```

- **Output order is completion order.** With `--parallel` above 1, results print (and are saved) as they come back, so domains from a fast host come out ahead of a slow one. Within one host they keep the check order. `--keep-order` holds results back to restore the check order (a slow host then delays the output, not the checks). `--parallel 1` checks in order, so output is in order too.
- **What gets checked does not change.** `--order` and `--limit` pick the domains before they are dispatched, and each host gets its share in that order.
- **Cache hits skip the queue.** Cached results are answered before dispatch and never wait for a host.
- **Memory stays bounded in range and pattern modes.** Candidates are generated lazily. Each host has a queue of at most 16 waiting domains; when it is full, generation pauses until that host catches up. At most `hosts x 16` candidates (plus their results) are in memory, however large `--range-max` is.
- **Ctrl-C** stops dispatching at once, gives checks in flight up to a second to finish (their results are kept), and exits with status 130 as before.

### Watching names

Some names are worth waiting for: the `.com` that expires next month, the hack that is in redemption. `watch` keeps a list of them and tells you what changed since the last time it looked.

```bash
domainhack watch add sumanda.com pla.to    # start watching (asks no registry)
domainhack watch run                       # check them all, print only the changes
domainhack watch list                      # what was last seen
domainhack watch remove pla.to
```

The first `watch run` records where each name stands. From then on a run prints one line per change, and nothing when nothing changed:

```
sumanda.com: is dropping: pending delete, expires 2026-11-12: it may be free soon
pla.to: is available now (was taken, expires 2026-11-02)
gus.to: expiry date moved: 2026-12-01 -> 2027-12-01
```

The changes it reports: a name became available, started dropping (redemption or pending delete), was registered, was restored by its holder, its expiry date moved, or its parking service changed. A name whose check fails keeps its last known state, so the change is still reported once the registry answers again.

- Every run asks the registries afresh; the result cache is not read. The pacing options of `check` apply (`--delay`, `--parallel`, `--contact`).
- `--format json` prints the changes as data (`changes`, `first_checks`, `not_checked`).
- `--exit-code` exits with status 3 when something changed, so a script can act on it:

  ```bash
  # crontab: every morning, mail me only when something changed
  0 8 * * *  domainhack watch run --exit-code --no-progress > /tmp/watch.txt; [ $? -eq 3 ] && mail -s "domain watch" you@example.com < /tmp/watch.txt
  ```

- The watchlist is a JSON file: `$XDG_DATA_HOME/domainhack/watchlist.json` (`~/.local/share/domainhack/watchlist.json`), or `$DOMAINHACK_WATCHLIST`, or `--watchlist FILE` to keep several.
- `watch add` refuses names under a TLD that cannot be checked, and says why.

### Saving results

```bash
# CSV (format inferred from the extension)
domainhack --tld to check --file data/words_es.txt --output results.csv

# JSON Lines
domainhack --tld to check --file data/words_es.txt --output results.jsonl
```

Rows are written as they are checked, so an interrupted run (Ctrl-C) keeps everything checked so far. Columns: `fqdn` (ASCII, what was queried), `display` (as written, e.g. `ñandú.de`), `word`, `sld`, `tld`, `availability`, `error_message`, `statuses`, `expires_at`, `registered_at`, `registrar`, `nameservers`, `parked_hint`, `dns_nameservers`, `dns_conflict`, `for_sale`. New columns are always appended, so existing positions stay stable.

- `statuses`: registry statuses of a taken name (RFC 9083 form, e.g. `client transfer prohibited`).
- `expires_at` / `registered_at`: expiration and registration dates in ISO 8601 UTC (`2026-11-30T07:38:29Z`).
- `registrar`: the sponsoring registrar's name (RDAP only).
- `nameservers`: NS hosts the registry lists, lowercase without the trailing dot.
- `parked_hint`: the parking/aftermarket service those nameservers belong to (e.g. `domainrecover`), empty if none is recognised.
- `dns_nameservers`: NS hosts public DNS returned, when DNS was consulted.
- `dns_conflict`: `true` when the registry said available but DNS shows a delegation (do not trust it as free), else `false`.
- `for_sale`: what the holder's `_for-sale` DNS record says (`listed` when it says nothing more); empty when there is none or DNS was not consulted.

Lists are joined with `;` in CSV and are arrays in JSON; unknown values are empty in CSV and `null` (dates) or empty in JSON.

Only results go to stdout; warnings, errors and the final summary go to stderr, so stdout can be piped.

### Name validation and international names

Candidates are validated before any query: letters, digits and hyphens only, 1-63 characters, plus per-TLD rules (e.g. `.it` requires at least 3 characters; `.com.ar` allows at most 50 and accepts `ñ` and accents, per NIC Argentina's rules). Names with accents or `ñ` are converted to their IDN form (`ñandú.de` -> `xn--and-6ma2c.de`) only for TLDs that accept IDNs; elsewhere they are skipped. Skipped candidates are counted on stderr.

Word lists are read as UTF-8; use `--encoding latin-1` for older lists.

### Progress and caching

- A progress bar is shown on stderr when running in a terminal. Disable it with `--no-progress`.
- Results are cached in SQLite (`$XDG_CACHE_HOME/domainhack/results.sqlite3`), so re-runs skip domains already checked. How long a result is reused depends on what it says:

  | Result | Reused for |
  |--------|------------|
  | Available | 24 hours (`--cache-ttl-available HOURS`): someone else can register it at any moment |
  | Taken, dropping (redemption / pending delete) | 24 hours |
  | Taken, expiration date known | until that date, at least 24 hours and at most 90 days |
  | Taken, no expiration date | 30 days |
  | Error | never cached |

  `--cache-ttl HOURS` caps all of these (e.g. `--cache-ttl 1` rechecks anything older than an hour). Use `--no-cache` to skip the cache and `--cache-path PATH` to move it. Cache files from older versions are upgraded in place. The cache keeps registration details (statuses, dates, registrar, nameservers, parking hint) but never DNS evidence, which is looked up fresh.

### Configuration file

Options you would otherwise repeat can live in a TOML file. Every table and key is optional:

```toml
[run]                      # for check, name and domainhack-mcp
delay = 2.0
parallel = 8
contact = "you@example.com"
cache_path = "~/domains/cache.sqlite3"

[presets]                  # your own presets for `name --tlds`
mine = ["com", "dev", "com.ar"]

[name]
tlds = "mine,io"           # the default of --tlds
prefixes = ["get", "try"]  # the default of --prefix
suffixes = ["hq", "labs"]  # the default of --suffix

[check]
frequency_list = ["~/words/en-common.txt"]   # the default of --frequency-list
```

domainhack uses the first of these that applies: `--config FILE`, the `DOMAINHACK_CONFIG` environment variable, `./domainhack.toml`, then `$XDG_CONFIG_HOME/domainhack/config.toml` (`~/.config/domainhack/config.toml`). `--no-config` ignores them all.

- A value on the command line always wins over the file, and `DOMAINHACK_CONTACT` wins over `contact`.
- A preset in `[presets]` with the name of a bundled one (`startup`, `latam`, `classic`) replaces it.
- `prefixes` and `suffixes` turn brand variants on for every `name` run, as `--prefix` and `--suffix` do.
- `frequency_list` only says where the lists are; the order is still `score` until you pass `--order frequency`.
- Relative paths are relative to the file, and `~` is your home directory.
- An unknown table or key, or a value of the wrong type, is an error (exit status 2) that names the file and the key, so a typo never silently does nothing.

## Supported TLDs

Availability is checked against the registry directly, with no captchas or API keys. Ask the tool what it covers; it answers offline:

```bash
domainhack tlds                    # summary + every country-code TLD
domainhack tlds cl es com.ar it    # how these are checked
domainhack tlds --unsupported      # what cannot be checked, and why
domainhack tlds --all --json       # every TLD, machine-readable
```

```
170 of 248 TLDs can be checked (102 rdap, 68 whois, 78 unsupported)
  .cl      whois        whois.nic.cl           2026-10-07
  .es      unsupported                         restricted: the registry provides automated lookups to accredited users only; ...
  .com.ar  rdap         rdap.nic.ar            2026-10-06
```

Every generic TLD (com, app, dev...) is covered through the IANA RDAP bootstrap. Registries missing from it live in `data/registry_sources.json`: RDAP endpoints, WHOIS servers with their "not found" patterns and pacing, and the TLDs that cannot be checked with the reason why (no published lookup service, lookups for accredited users only, terms of use that do not cover automated lookups...), plus a link to check by hand where there is one. Each supported entry was verified live with a registered and a random name, and `tests/fixtures/whois/` keeps the real replies so a wrong pattern (which would mean false "available" results) fails the tests. A weekly CI job re-checks a rotating sample against the live registries.

A snapshot of the IANA RDAP bootstrap ships with the package, so the backend chosen for each TLD is the same online and offline.

The package also bundles IANA's list of every TLD (`data/iana_tlds.txt`, snapshot with its `# Version` line) and a curated list of common registrable second-level suffixes, Latin America first (`data/second_level.json`, checked against the [Public Suffix List](https://publicsuffix.org/)). `adapters/iana_tlds.IanaTldList` answers "is this a public suffix?" and "which suffixes does this name end with?" (`plato` -> `to`, `fotocomar` -> `com.ar`, `ar`). Both lists are snapshot-only, so results are deterministic offline; refresh them by replacing the files.

Note: "available" means *not registered*. Premium or reserved names may still show as available; confirm with a registrar before buying.

### Being a good citizen

- Requests identify the tool: `User-Agent: domainhack/<version> (+repo URL)`. Add `--contact you@example.com` (or `DOMAINHACK_CONTACT`) to include a `From` header.
- Each registry host gets at most one request at a time from domainhack, however high `--parallel` is. Parallelism only goes across hosts (RFC 9112 §9.4 asks clients to limit simultaneous connections per server).
- Requests to each host are spaced (`--delay`, with stricter per-server minimums, e.g. 4 s for `whois.nic.it`). Each wait gets ±20% random jitter.
- The spacing adapts to each host (RFC 7480 §5.5). A 429, a 503, a timeout or WHOIS rate-limit text doubles that host's interval, up to 60 s. Each real answer shrinks it by 10%, back down to `--delay`.
- RDAP retries a 429, a 5xx, a timeout or a dropped connection at most twice. It waits for `Retry-After` when the server sends one (a 429 asking for more than 30 s is not retried, but the host is still held back, for up to 60 s). Otherwise it waits a "full jitter" exponential backoff, `random(0, min(30, 2 × 2^n))` seconds. WHOIS retries a timeout or a rate-limited reply only once.
- A check counts once, however many retries it takes: it slows its host down at most once and counts as at most one failure for the circuit breaker below.
- Checks go best-first, `--limit` caps them per TLD, and large range or pattern runs need `--yes` (see above).
- If a host stops responding (3 consecutive failures), its domains are skipped for 60 s instead of waiting on timeouts; skipped checks are reported as errors and re-checked on the next run.

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Completed, no errors |
| 1 | Completed with some failed checks, or a runtime error |
| 2 | Usage error |
| 3 | `watch run --exit-code`: something changed since the last run |
| 130 | Interrupted (Ctrl-C) |

## Word Lists

The sample files in `data/samples/` contain ~20 words each for quick testing, and [`data/wordlists/`](data/wordlists/) has the ~2,400-2,800 most common English and Spanish words (the input of the weekly report). For serious domain hunting, you'll need full dictionaries:

- **Spanish**: Any comprehensive Spanish word list (600K+ words recommended)
- **English**: `/usr/share/dict/words` on macOS/Linux, or download from open word list repositories

Place them as `data/words_es.txt` and `data/words_en.txt` (these paths are gitignored).

## Architecture

Built with **Clean Architecture** and **SOLID principles**:

```
domain/       Pure entities: TLD, DomainHack, Availability
ports/        Abstract interfaces: WordSource, RegistrarClient, ResultWriter,
              ResultCache, DnsLookup, KnownTlds
usecases/     Business logic: FilterWords, RangeCandidates, RankCandidates,
              EstimateRun, CheckDomains (sequential, or parallel lanes
              per registry host), ConfirmWithDns,
              BrandCandidates + BrandReport (`name`)
adapters/     Implementations: FileWordSource, RDAP/WHOIS registrars,
              RegistrarRouter, CachedRegistrar, Console/CSV/JSON writers, tqdm progress,
              DnsPythonLookup (dnspython), IanaTldList
cli/          Composition root: argparse + dependency injection
```

Adding support for a new TLD registrar or output format requires implementing a single interface -- zero changes to existing code.

## Why .to?

`.to` is the country code top-level domain (ccTLD) for Tonga. It's popular for domain hacks because many Spanish words end in "-to" (a common suffix in verb conjugations and nouns). English has plenty too: veto, photo, gusto, motto.

The tool is TLD-agnostic: see [Supported TLDs](#supported-tlds), or add a new source by implementing the `RegistrarClient` interface and registering it in `adapters/registrar_catalog.py`.

## Development

```bash
pip install -e ".[dev]"
pre-commit install              # enable git hooks
```

```bash
pytest                          # unit tests (hermetic, no network)
pytest -m integration           # live registry tests (network)
ruff check src tests            # linting
ruff format --check src tests   # formatting
mypy                            # strict type checking
pre-commit run --all-files      # exactly what CI runs for lint + types
```

The ruff and mypy revs in `.pre-commit-config.yaml` are the source of truth. The `dev` extra pins matching ranges, so update both together.

CI (GitHub Actions) does the following:

- runs pre-commit;
- runs the unit tests on Python 3.10 to 3.14, with a 95% coverage floor;
- builds the wheel and smoke-tests it in a fresh venv.

A separate weekly workflow runs the live `integration` tests against real registries. It is non-blocking and skips the `.it` case, because whois.nic.it throttles hard. See [CHANGELOG.md](CHANGELOG.md) for release notes.

## Contributing

Bug reports, new TLDs and WHOIS pattern fixes are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) has the setup and a step-by-step guide to adding a TLD; [SECURITY.md](SECURITY.md) says how to report a vulnerability privately.

## Roadmap

| Feature | Status |
|---------|--------|
| CSV/JSON ResultWriter | Done |
| Multi-TLD support (`--tld to,in,io`) | Done |
| Progress bar (tqdm) | Done |
| RDAP + WHOIS registrar adapters | Done |
| Result caching (decorator pattern) | Done |
| Best-first ordering (`--order`, `--limit`) | Done |
| Adaptive per-host backoff with jitter | Done |
| Parallel checks, one request per registry host (`--parallel`) | Done |
| DNS confirmation (`--confirm-dns`) | Done |
| Second-level suffixes (`com.ar`, `co.uk`) | Done |
| Registration details and parking detection for taken names | Done |
| `name` command: exact names, domain hacks, brand variants, recommendation | Done |
| `tlds` command and live-verified registry data (170 of 248 ccTLDs) | Done |
| Reference prices per TLD in the `name` report: a bundled, dated snapshot of a public registrar price list (registration and renewal), shown as a reference only; per-name premium pricing is out of scope | Done |
| `watch`: follow names and report what changed | Done |
| `_for-sale` DNS record of taken names | Done |
| `name --handles`: GitHub, npm and PyPI | Done |
| Weekly report: changes since the previous week, Atom feed | Done |
| Re-verify registries that did not answer during verification (e.g. .pt, .uy, .dk, .do) and the .my RDAP service | Planned |
| Word-frequency ordering (`--order frequency`) | Done |
| Pattern mode (`--pattern CVCV`) | Done |
| Your own prefixes and suffixes for `name`, subdomain hacks (`del.icio.us`) | Done |
| Configuration file (`domainhack.toml`): run options, presets, defaults | Done |
| Open zone files (.nu, .ch, .li) as an offline "registered" source | Planned |
| Async HTTP (`httpx.AsyncClient`) | Planned |

## License

MIT
