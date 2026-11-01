# How domainhack compares

Looking for a domain hack finder, a domain availability checker for the terminal, or an
alternative to a web-based generator? This page compares domainhack with the tools people
usually weigh it against: [domaingrep](https://github.com/ysm-dev/domaingrep),
[tldx](https://github.com/brandonyoungdev/tldx) and [DomainHacks.info](https://domainhacks.info/).
The table only states what each project documents in its README or on its site; if a cell is
wrong, [open an issue](https://github.com/PabloAlaniz/find-amazing-domains/issues) and it will
be fixed.


| | domainhack | [domaingrep](https://github.com/ysm-dev/domaingrep) | [tldx](https://github.com/brandonyoungdev/tldx) | [DomainHacks.info](https://domainhacks.info/) |
|---|---|---|---|---|
| Finds domain hacks (word split at a TLD) | yes | yes | no: prefix/suffix permutations | yes |
| Availability source | registry RDAP + WHOIS | DNS | RDAP | registry lookups (about 900 of 1466 TLDs) |
| Cross-checks "available" against DNS | yes | DNS is the source | not documented | not documented |
| Taken-name details (expiry, registrar, parking, dropping) | yes | not documented | `_for-sale` TXT record | not documented |
| Second-level suffixes (`com.ar`, `co.uk`) and IDN | yes | not documented | not documented | not documented |
| Country-code TLDs you can check | 170 of 248, each verified live | "every TLD", list not published | not published | about 900 TLDs |
| Runs as | CLI, Python 3.10+ | CLI, Rust | CLI, Go | website |
| License | MIT | MIT | Apache-2.0 | proprietary |

"Not documented" means the project's README or site did not say so in October 2026; corrections are welcome in an issue.

## What the differences mean in practice

- **Registry vs DNS as the source of truth.** A DNS lookup tells you whether a name is
  *delegated*, not whether it is *registered*. Many registered names have no nameservers
  (parked without delegation, reserved, in redemption), so a DNS-only checker calls them
  free. domainhack asks the registry and uses DNS only as a second opinion, to flag the
  opposite case: a registry that says "free" for a name that is clearly in use.
- **Details of taken names.** Knowing that a name is taken is the start. Knowing that it
  expires next month, is parked at an aftermarket service or is in pending delete tells you
  whether to wait, to make an offer or to move on. domainhack reads statuses, dates, registrar
  and nameservers from RDAP (and best-effort from WHOIS).
- **Verified ccTLDs.** Country-code registries differ wildly: some run RDAP, some a WHOIS
  with a unique "not found" text, some forbid automated lookups. domainhack ships the result
  of probing each one (see [Supported TLDs](tlds.md)) and says *why* a TLD cannot be checked.
- **Politeness.** Registries are shared infrastructure. domainhack keeps one request in
  flight per host, backs off on 429/503, honours `Retry-After`, adds jitter and trips a
  circuit breaker when a host stops answering (see "Being a good citizen" in [Usage](usage.md)).
