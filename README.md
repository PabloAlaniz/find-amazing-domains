# domainhack

[![CI](https://github.com/PabloAlaniz/find-amazing-domains/actions/workflows/lint-typecheck-test.yml/badge.svg)](https://github.com/PabloAlaniz/find-amazing-domains/actions/workflows/lint-typecheck-test.yml)

**Find domain hacks hiding in real words.**

Some words have a secret: they end in a top-level domain. The Spanish word *plato* (plate) becomes `pla.to`. *Abasto* (supply) becomes `abas.to`. *Grato* (pleasant) becomes `gra.to`. This tool finds those words and checks if the domains are actually available.

## Examples

| Word | Language | Domain | Meaning |
|------|----------|--------|---------|
| plato | Spanish | `pla.to` | plate / dish |
| grato | Spanish | `gra.to` | pleasant |
| abasto | Spanish | `abas.to` | supply |
| veto | English | `ve.to` | veto |
| gusto | English | `gus.to` | gusto |
| pinto | English | `pin.to` | pinto |

## Requirements

- Python 3.10+

## Quick Start

```bash
# Install
pip install .

# Find words that form .to domains
domainhack --tld to filter data/samples/sample_es_5.txt

# Preview domains without checking availability
domainhack --tld to check --file data/samples/sample_es_5.txt --dry-run

# Check domain availability (with rate limiting)
domainhack --tld to check --file data/samples/sample_es_5.txt --delay 1.5
```

## How It Works

The tool has **two modes** to generate candidate domains, and then checks their availability against the registrar.

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

# Only the 50 best candidates per TLD (shortest names first)
domainhack --tld to,it check --file data/words_es.txt --limit 50

# Check in word-list order instead
domainhack --tld to check --file data/words_es.txt --order input
```

Registries throttle hard (whois.nic.it stopped answering after about 50 queries in a test run), so the best candidates are checked first. `--order` picks the order:

- `score` (default): shortest SLD first, then shortest full word, then alphabetical. The order is deterministic.
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

Before a brute-force run, domainhack prints the estimated number of queries and the minimum run time. The estimate accounts for TLDs that share a host and for each host's pacing:

```
Estimated 36,556 queries to 2 hosts (rdap.tonicregistry.to, rdap.identitydigital.services): at least 5 h 4 min at the current pacing.
```

Runs over 10,000 queries are refused with exit status 2 unless you pass `--yes`. Narrow the run with `--range-end` or `--limit` instead where you can: registry terms of use forbid bulk querying. `--dry-run` never queries anything, so it is exempt.

### Multiple TLDs

Pass a comma-separated list to `--tld`. Each TLD is routed to a registrar that supports it; unsupported TLDs are skipped with a warning.

```bash
# Words that form .to, .io or .it hacks (prints "word -> sld.tld")
domainhack --tld to,io,it filter data/words_en.txt

# Check all of them
domainhack --tld to,io,it check --file data/words_en.txt
```

### Saving results

```bash
# CSV (format inferred from the extension)
domainhack --tld to check --file data/words_es.txt --output results.csv

# JSON Lines
domainhack --tld to check --file data/words_es.txt --output results.jsonl
```

Rows are written as they are checked, so an interrupted run (Ctrl-C) keeps everything checked so far. Columns: `fqdn` (ASCII, what was queried), `display` (as written, e.g. `ñandú.de`), `word`, `sld`, `tld`, `availability`, `error_message`.

Only results go to stdout; warnings, errors and the final summary go to stderr, so stdout can be piped.

### Name validation and international names

Candidates are validated before any query: letters, digits and hyphens only, 1-63 characters, plus per-TLD rules (e.g. `.it` requires at least 3 characters). Names with accents or `ñ` are converted to their IDN form (`ñandú.de` -> `xn--and-6ma2c.de`) only for TLDs that accept IDNs; elsewhere they are skipped. Skipped candidates are counted on stderr.

Word lists are read as UTF-8; use `--encoding latin-1` for older lists.

### Progress and caching

- A progress bar is shown on stderr when running in a terminal. Disable it with `--no-progress`.
- Results are cached in SQLite (`$XDG_CACHE_HOME/domainhack/results.sqlite3`, default 7 days). Re-runs skip domains already checked. Use `--no-cache`, `--cache-ttl HOURS` or `--cache-path PATH` to control it. Errors are never cached.

## Supported TLDs

Availability is checked against the registry directly, with no captchas or API keys:

| Source | TLDs |
|--------|------|
| RDAP (registry endpoints, incl. manual overrides) | to, io, sh, ac, me, co, de, ch, li, so, ws |
| RDAP (via the IANA bootstrap) | ai, in, is, ly, fm, tv, cc, pw, re, fr, nl, uk, ar, ... and all gTLDs |
| WHOIS (port 43) | it, am, at, be, gg, im, la, ma, mx, nu, pe, st |
| Not supported | es, al |

A snapshot of the IANA RDAP bootstrap ships with the package, so the backend chosen for each TLD is the same online and offline.

Note: "available" means *not registered*. Premium or reserved names may still show as available; confirm with a registrar before buying.

### Being a good citizen

- Requests identify the tool: `User-Agent: domainhack/<version> (+repo URL)`. Add `--contact you@example.com` (or `DOMAINHACK_CONTACT`) to include a `From` header.
- Requests to each host are spaced (`--delay`, with stricter per-server minimums, e.g. 4 s for `whois.nic.it`). Each wait gets ±20% random jitter.
- The spacing adapts to each host (RFC 7480 §5.5). A 429, a 503, a timeout or WHOIS rate-limit text doubles that host's interval, up to 60 s. Each real answer shrinks it by 10%, back down to `--delay`.
- RDAP retries a 429, a 5xx, a timeout or a dropped connection at most twice. It waits for `Retry-After` when the server sends one (a 429 asking for more than 30 s is not retried, but the host is still held back, for up to 60 s). Otherwise it waits a "full jitter" exponential backoff, `random(0, min(30, 2 × 2^n))` seconds. WHOIS retries a timeout or a rate-limited reply only once.
- A check counts once, however many retries it takes: it slows its host down at most once and counts as at most one failure for the circuit breaker below.
- Checks go best-first, `--limit` caps them per TLD, and large brute-force runs need `--yes` (see above).
- If a host stops responding (3 consecutive failures), its domains are skipped for 60 s instead of waiting on timeouts; skipped checks are reported as errors and re-checked on the next run.

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Completed, no errors |
| 1 | Completed with some failed checks, or a runtime error |
| 2 | Usage error |
| 130 | Interrupted (Ctrl-C) |

## Word Lists

The sample files in `data/samples/` contain ~20 words each for quick testing. For serious domain hunting, you'll need full dictionaries:

- **Spanish**: Any comprehensive Spanish word list (600K+ words recommended)
- **English**: `/usr/share/dict/words` on macOS/Linux, or download from open word list repositories

Place them as `data/words_es.txt` and `data/words_en.txt` (these paths are gitignored).

## Architecture

Built with **Clean Architecture** and **SOLID principles**:

```
domain/       Pure entities: TLD, DomainHack, Availability
ports/        Abstract interfaces: WordSource, RegistrarClient, ResultWriter
usecases/     Business logic: FilterWords, RangeCandidates, RankCandidates,
              EstimateRun, CheckDomains
adapters/     Implementations: FileWordSource, RDAP/WHOIS registrars,
              RegistrarRouter, CachedRegistrar, Console/CSV/JSON writers, tqdm progress
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
| Word-frequency scoring (`Scorer` hook) | Planned |
| DNS pre-filter (skip domains with NS records) | Planned |
| Porkbun API adapter (confirm hits, premium pricing) | Planned |
| Async HTTP (`httpx.AsyncClient`) | Planned |

## License

MIT
