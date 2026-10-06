# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Parallel checks across registry hosts: `check --parallel N` (default 4)
  checks up to N hosts at once, never more than one request in flight per
  host. TLDs that share a host (`.io`/`.sh`/`.ac`/`.me`) share its lane, so
  per-host pacing, adaptive slow-down and the circuit breaker work as before.
  A slow or hung host no longer stalls the other TLDs. `--parallel 1` keeps
  the old one-at-a-time behaviour.
- `--keep-order` prints and saves parallel results in check order.

- Registration details for taken names: RDAP `status` values and the
  `expiration` event (WHOIS: EPP status codes and ISO expiry dates, best
  effort). Malformed parts are ignored and never turn a taken name into an error.
- "Dropping" names (redemption period or pending delete) print as
  `TAKEN (dropping: pending delete, expires 2026-11-02): x.to` with
  `--show-taken`, or alone with the new `--show-dropping`. The summary counts them.
- CSV and JSON Lines output gain `statuses` and `expires_at` columns, appended
  at the end so existing column positions are unchanged.
- `--cache-ttl-available HOURS` (default 24).

- `check --output FILE` with `--format {csv,json}` (CSV or JSON Lines), inferred
  from the `.csv` / `.json` / `.jsonl` extension. Results also go to the console.
- Multi-TLD support: `--tld` accepts a comma-separated list (`to,io,in`). A TLD
  router sends each domain to the registrar for its TLD and skips unsupported
  TLDs with a warning.
- tqdm progress bar for `check` on stderr, hidden automatically when stderr is not
  a TTY. `--no-progress` turns it off.
- SQLite result cache for AVAILABLE/TAKEN results, on by default. ERROR results are
  never cached. Options: `--no-cache`, `--cache-ttl HOURS`, `--cache-ttl-available
  HOURS` and `--cache-path`.
- RDAP and WHOIS registrar adapters with a per-TLD catalog. Lookups try RDAP first
  (IANA bootstrap plus overrides), then fall back to port-43 WHOIS with per-TLD
  "not found" patterns. Requests are paced per host.
- The IANA RDAP bootstrap ships as package data, so routing works offline and
  gives the same result as a live lookup. A failed refresh falls back to the
  bundled snapshot.
- Per-host circuit breaker. After 3 consecutive failures, domains for that host
  are skipped until a 60 s cooldown passes. WHOIS servers can set a minimum query
  interval (whois.nic.it: 4 s).
- Domain label validation: LDH syntax, length, hyphen rules and per-TLD rules.
  IDN support converts names with IDNA2008, and the console shows both the
  Unicode and the A-label form. Invalid candidates are skipped and counted.
- Documented exit codes: 0 ok, 1 some checks errored or the run failed, 2 usage
  error, 130 interrupted. Ctrl-C keeps partial output.
- Errors print as one line without a traceback. Results go to stdout; warnings,
  errors and the summary go to stderr.
- `--encoding` for word lists, and `--contact EMAIL` (or `$DOMAINHACK_CONTACT`) to
  send a `From` header with RDAP requests.
- `domainhack --version` and `python -m domainhack`.
- PEP 561 `py.typed` marker, full project metadata, and this changelog.
- CI runs on Python 3.10 to 3.14, with pre-commit lint and type-check, a coverage
  floor, a wheel build smoke test, a weekly live-registry check and Dependabot.
- Best candidates are checked first. `check --order {score,alpha,input}` sets the
  order; the default in word-list mode is `score`: shortest SLD, then shortest full
  word, then alphabetical (deterministic). Range mode keeps its lazy shortest-first
  generation. A `Scorer` hook in `RankCandidatesUseCase` leaves room for
  frequency-based ranking.
- `check --limit N` checks at most N domains per TLD, after ordering. The progress
  total and `--dry-run` reflect it. Sorted word lists now get an exact progress
  total.
- Brute-force guardrail. A `--range-max` run first prints its estimated query count
  and minimum duration per host. Runs over 10,000 queries exit with status 2 unless
  `--yes` is given. `--dry-run` is exempt.
- Adaptive per-host throttling (RFC 7480 §5.5). A 429, 503, timeout or WHOIS
  rate-limit reply doubles the host's interval (cap 60 s); each real answer shrinks
  it by 10% back toward `--delay`. Every wait gets ±20% jitter.

### Changed

- With the default `--parallel 4`, results print and are saved in completion
  order rather than check order (pass `--keep-order` or `--parallel 1` for
  the old order). Cache lookups and writes happen on the main thread, before
  dispatch, so cache hits never wait behind a slow host. An unexpected
  exception inside one check is reported as an ERROR for that domain instead
  of ending the run. The brute-force estimate notes how many hosts run in
  parallel.
- Split cache TTLs instead of one 7-day TTL: available names 24 h, dropping names
  24 h, taken names until their expiration date (at most 90 days; 30 days when
  unknown). `--cache-ttl HOURS` now caps all of them and has no default. The
  cache stores statuses and expiration; older cache files are migrated in place
  (`ALTER TABLE ADD COLUMN`, `PRAGMA user_version = 1`).
- The cache commits in batches (every 25 rows or 10 s, and on close) instead of
  once per row.
- Honest User-Agent:
  `domainhack/<version> (+https://github.com/PabloAlaniz/find-amazing-domains)`.
  It is built from `domainhack.__version__`, which is the single source of the
  version.
- `.to` domains are checked through the official Tonic RDAP endpoint.
- The ruff and mypy versions are pinned in the `dev` extra and match
  `.pre-commit-config.yaml`.
- RDAP retries a 429 (with or without `Retry-After`), a 5xx, a timeout or a dropped
  connection up to 2 times. Without `Retry-After` it waits a full-jitter
  exponential backoff (`random(0, min(30, 2·2^n))` s). `Retry-After` defers the
  whole host, including other TLDs that share it. WHOIS retries a timeout or a
  rate-limited reply once at most.
- A check counts once for the circuit breaker and slows its host down at most once,
  however many retries it makes.

### Removed

- `TonicRegistrarClient`, which scraped the tonic.to web form with a spoofed
  browser User-Agent, and the unused `beautifulsoup4` dependency.

## [0.1.0]

The version in the initial commit. It was never tagged or published.

### Added

- Initial release: `filter` finds words that end in a TLD, and `check` tests
  domain availability from a word list or a generated letter range.

[Unreleased]: https://github.com/PabloAlaniz/find-amazing-domains/commits/main
[0.1.0]: https://github.com/PabloAlaniz/find-amazing-domains/commit/4af9a40
