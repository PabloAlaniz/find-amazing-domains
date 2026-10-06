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
```

### Check availability -- brute-force mode

Generate all letter combinations up to a given length (max 6) and check each one.

```bash
# Try all 1-2 letter .to domains (26 + 676 = 702 combinations)
domainhack --tld to check --range-max 2

# Try up to 3 letters, but stop at "ba" (useful to resume interrupted runs)
domainhack --tld to check --range-max 3 --range-end "ba"

# Preview combinations without checking
domainhack --tld to check --range-max 2 --dry-run
```

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

Rows are written as they are checked, so an interrupted run keeps everything checked so far.

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

Note: "available" means *not registered*. Premium or reserved names may still show as available; confirm with a registrar before buying.

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
usecases/     Business logic: FilterWords, CheckDomains
adapters/     Implementations: FileWordSource, RDAP/WHOIS/Tonic registrars,
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
pytest                          # 285 tests, 99% coverage
pytest -m integration           # live registry tests (network)
ruff check src tests            # linting
ruff format --check src tests   # formatting
mypy                            # strict type checking
```

CI runs lint, type-check, and tests on Python 3.10/3.11/3.12 via GitHub Actions.

## Roadmap

| Feature | Status |
|---------|--------|
| CSV/JSON ResultWriter | Done |
| Multi-TLD support (`--tld to,in,io`) | Done |
| Progress bar (tqdm) | Done |
| RDAP + WHOIS registrar adapters | Done |
| Result caching (decorator pattern) | Done |
| DNS pre-filter (skip domains with NS records) | Planned |
| Porkbun API adapter (confirm hits, premium pricing) | Planned |
| Async HTTP (`httpx.AsyncClient`) | Planned |

## License

MIT
