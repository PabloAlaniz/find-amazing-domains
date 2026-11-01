# Architecture


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

The tool is TLD-agnostic: see [Supported TLDs](tlds.md), or add a new source by implementing the `RegistrarClient` interface and registering it in `adapters/registrar_catalog.py`.

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

A separate weekly workflow runs the live `integration` tests against real registries. It is non-blocking and skips the `.it` case, because whois.nic.it throttles hard. See [CHANGELOG.md](https://github.com/PabloAlaniz/find-amazing-domains/blob/main/CHANGELOG.md) for release notes.
