# Security Policy

## Supported versions

Only the latest release on PyPI receives fixes.

## Reporting a vulnerability

Please do not open a public issue for security problems. Use GitHub's private
vulnerability reporting instead:

https://github.com/PabloAlaniz/find-amazing-domains/security/advisories/new

You should get an acknowledgement within a few days. Once a fix is released the
report is credited in the changelog unless you prefer otherwise.

## Scope

domainhack is a command-line client that sends RDAP (HTTPS) and WHOIS (port 43)
queries to domain registries and DNS queries to the system resolver, and keeps
a local SQLite cache. Things we consider security issues include:

- a registry reply or word list that can crash the tool or make it run
  arbitrary code;
- the cache or output files being written outside the paths the user chose;
- requests leaking data the user did not ask to send (the only identifying
  headers are the User-Agent and the optional `--contact` address).

Rate limiting or blocking by a registry is not a vulnerability; see "Being a
good citizen" in the README.
