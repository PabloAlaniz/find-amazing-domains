"""Generate docs/tlds.md from the bundled registry data (`domainhack tlds --all --json`).

Run from the repository root: ``python tools/gen_tlds_doc.py``. The docs
workflow runs it before building the site, so the published table always
matches the data shipped in the package.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

OUT = Path("docs/tlds.md")

INTRO = """# Supported TLDs

domainhack checks availability against the registry itself: RDAP (RFC 9082/9083)
where the registry offers it, port-43 WHOIS otherwise. No API keys, no captchas.

Every **generic TLD** (`com`, `app`, `dev`, `io`...) is covered through the
[IANA RDAP bootstrap](https://data.iana.org/rdap/dns.json), a snapshot of which
ships with the package. The **country-code TLDs** below were each verified live
with a registered name and a random one; the real WHOIS replies are kept as test
fixtures so a wrong "not found" pattern (which would report false "available"
results) fails the build.

Ask the tool itself, offline:

```bash
domainhack tlds                 # summary + every country-code TLD
domainhack tlds cl es com.ar    # how these are checked
domainhack tlds --unsupported   # what cannot be checked, and why
domainhack tlds --all --json    # every TLD, machine-readable
```

"Available" means *not registered*. Premium or reserved names may still show as
available; confirm with a registrar before buying.

Missing or wrong? [Open a TLD issue](https://github.com/PabloAlaniz/find-amazing-domains/issues/new?template=tld_request.yml)
or follow "Adding or fixing a TLD" in the [contributing guide](contributing.md).
"""


def main() -> int:
    raw = subprocess.run(
        [sys.executable, "-m", "domainhack", "tlds", "--all", "--json"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    entries = json.loads(raw)
    cc = [e for e in entries if len(e["tld"]) == 2]
    counts = Counter(e["kind"] for e in cc)
    supported = counts["rdap"] + counts["whois"]

    lines = [INTRO]
    lines.append(
        f"## Country-code TLDs\n\n{supported} of {len(cc)} can be checked "
        f"({counts['rdap']} over RDAP, {counts['whois']} over WHOIS, "
        f"{counts['unsupported']} unsupported).\n"
    )
    lines.append("| TLD | Checked over | Registry host | Notes |\n|---|---|---|---|")
    for e in sorted(cc, key=lambda e: e["tld"]):
        host = f"`{e['host']}`" if e.get("host") else ""
        detail = (e.get("detail") or "").replace("|", "\\|")
        lines.append(f"| `.{e['tld']}` | {e['kind']} | {host} | {detail} |")

    generic = [e for e in entries if len(e["tld"]) > 2]
    gcounts = Counter(e["kind"] for e in generic)
    lines.append(
        f"\n## Generic TLDs\n\n{len(generic)} generic TLDs are known from the bundled IANA list; "
        f"{gcounts['rdap']} resolve to an RDAP server through the IANA bootstrap"
        + (f", {gcounts['whois']} use WHOIS" if gcounts["whois"] else "")
        + (f" and {gcounts['unsupported']} cannot be checked" if gcounts["unsupported"] else "")
        + ". Run `domainhack tlds --all` for the full list."
    )
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(cc)} ccTLDs, {len(generic)} gTLDs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
