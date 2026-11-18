# Browse available domain hacks

Every Tuesday a workflow checks about 5,000 common English and Spanish words against every
TLD domainhack can query. This page lets you search what it found: the hacks that were
**available** at the registry that day, and the registered ones that are **dropping**
(in redemption or pending delete, so they may be free soon).

<div id="hack-browser" markdown="0"
     data-source="https://raw.githubusercontent.com/PabloAlaniz/find-amazing-domains/reports/reports/available.json">
  <noscript>This page needs JavaScript. The same data is in the
  <a href="https://github.com/PabloAlaniz/find-amazing-domains/blob/reports/reports/latest.md">weekly report</a>.</noscript>
</div>

"Available" means the registry reported no registration when asked. Someone may have
registered the name since, premium and reserved names can still show as available, and
several country-code registries only accept local residents or companies. Check a name
yourself before you count on it:

```bash
uvx domainhack name plato
```

The full report, with the per-TLD breakdown and what changed since the week before, is in
[`reports/latest.md`](https://github.com/PabloAlaniz/find-amazing-domains/blob/reports/reports/latest.md);
an [Atom feed](https://raw.githubusercontent.com/PabloAlaniz/find-amazing-domains/reports/reports/feed.xml)
has one entry per week.
