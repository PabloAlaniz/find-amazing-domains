# Supported TLDs

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

## Country-code TLDs

170 of 248 can be checked (102 over RDAP, 68 over WHOIS, 78 unsupported).

| TLD | Checked over | Registry host | Notes |
|---|---|---|---|
| `.ac` | rdap | `rdap.identitydigital.services` | 2026-10-06 |
| `.ad` | rdap | `rdap.nic.ad` | IANA RDAP bootstrap |
| `.ae` | unsupported |  | unavailable: the registry's lookup service did not answer automated checks when we last verified it (2026-10); availability can be checked at https://www.aeda.ae |
| `.af` | rdap | `rdap.nic.af` | 2026-10-07 |
| `.ag` | rdap | `rdap.identitydigital.services` | 2026-10-07 |
| `.ai` | rdap | `rdap.identitydigital.services` | IANA RDAP bootstrap |
| `.al` | unsupported |  | unavailable: the registry does not publish an automated lookup service; availability can be checked at https://akep.al |
| `.am` | whois | `whois.amnic.net` | 2026-10-06 |
| `.ao` | unsupported |  | unavailable: the registry does not publish an automated lookup service; availability can be checked at https://www.dns.ao |
| `.aq` | unsupported |  | unavailable: .aq is reserved for organisations working in Antarctica |
| `.ar` | rdap | `rdap.nic.ar` | 2026-10-06 |
| `.as` | rdap | `rdap.nic.as` | IANA RDAP bootstrap |
| `.at` | whois | `whois.nic.at` | 2026-10-06 |
| `.au` | rdap | `rdap.cctld.au` | IANA RDAP bootstrap |
| `.aw` | rdap | `rdap.nic.aw` | 2026-10-07 |
| `.ax` | whois | `whois.ax` | 2026-10-07 |
| `.az` | unsupported |  | unavailable: the registry does not publish an automated lookup service; availability can be checked at https://whois.az |
| `.ba` | unsupported |  | unavailable: the registry does not publish an automated lookup service; availability can be checked at https://nic.ba |
| `.bb` | unsupported |  | unavailable: the registry offers a web lookup only; availability can be checked at http://www.whois.telecoms.gov.bb/ |
| `.bd` | unsupported |  | unavailable: the registry does not publish an automated lookup service; availability can be checked at https://bdia.btcl.com.bd |
| `.be` | whois | `whois.dns.be` | 2026-10-06 |
| `.bf` | whois | `whois.registre.bf` | 2026-10-07 |
| `.bg` | whois | `whois.register.bg` | 2026-10-07 |
| `.bh` | rdap | `rdap.bhregistry.bh` | IANA RDAP bootstrap |
| `.bi` | whois | `whois1.nic.bi` | 2026-10-07 |
| `.bj` | whois | `whois.nic.bj` | 2026-10-07 |
| `.bm` | rdap | `rdap.identitydigital.services` | IANA RDAP bootstrap |
| `.bn` | unsupported |  | unavailable: the registry's lookup service did not answer automated checks when we last verified it (2026-10); availability can be checked at https://www.bnnic.bn |
| `.bo` | unsupported |  | unavailable: the registry's lookup replies do not include an explicit 'not registered' answer this tool can rely on; availability can be checked at https://nic.bo |
| `.br` | rdap | `rdap.registro.br` | IANA RDAP bootstrap |
| `.bs` | unsupported |  | unavailable: the registry does not publish an automated lookup service; availability can be checked at https://www.nic.bs |
| `.bt` | unsupported |  | unavailable: the registry does not publish an automated lookup service; availability can be checked at https://www.nic.bt |
| `.bv` | unsupported |  | unavailable: .bv is not open for registration |
| `.bw` | unsupported |  | unavailable: the registry's lookup replies do not include an explicit 'not registered' answer this tool can rely on; availability can be checked at https://nic.net.bw |
| `.by` | whois | `whois.cctld.by` | 2026-10-07 |
| `.bz` | unsupported |  | unavailable: the registry does not publish an automated lookup service |
| `.ca` | rdap | `rdap.ca.fury.ca` | IANA RDAP bootstrap |
| `.cc` | rdap | `tld-rdap.verisign.com` | IANA RDAP bootstrap |
| `.cd` | unsupported |  | unavailable: the registry's lookup service did not answer automated checks when we last verified it (2026-10); availability can be checked at https://nic.cd |
| `.cf` | unsupported |  | unavailable: the registry's lookup service did not answer automated checks when we last verified it (2026-10) |
| `.cg` | unsupported |  | unavailable: the registry does not publish an automated lookup service; availability can be checked at https://www.nic.cg |
| `.ch` | rdap | `rdap.nic.ch` | 2026-10-06 |
| `.ci` | rdap | `rdap.nic.ci` | 2026-10-07 |
| `.ck` | unsupported |  | unavailable: the registry does not publish an automated lookup service; .ck mostly registers third-level names |
| `.cl` | whois | `whois.nic.cl` | 2026-10-07 |
| `.cm` | rdap | `rdap.nic.cm` | IANA RDAP bootstrap |
| `.cn` | whois | `whois.cnnic.cn` | 2026-10-07 |
| `.co` | rdap | `rdap.registry.co` | 2026-10-06 |
| `.cr` | rdap | `rdap.nic.cr` | IANA RDAP bootstrap |
| `.cu` | unsupported |  | unavailable: the registry does not publish an automated lookup service; availability can be checked at https://www.nic.cu |
| `.cv` | rdap | `rdap.nic.cv` | IANA RDAP bootstrap |
| `.cw` | unsupported |  | unavailable: the registry does not publish an automated lookup service |
| `.cx` | rdap | `rdap.nic.cx` | IANA RDAP bootstrap |
| `.cy` | unsupported |  | unavailable: the registry does not publish an automated lookup service; availability can be checked at https://www.nic.cy |
| `.cz` | rdap | `rdap.nic.cz` | IANA RDAP bootstrap |
| `.de` | rdap | `rdap.denic.de` | 2026-10-06 |
| `.dj` | unsupported |  | unavailable: the registry does not publish an automated lookup service |
| `.dk` | unsupported |  | unavailable: the registry's lookup service did not answer automated checks when we last verified it (2026-10); availability can be checked at https://punktum.dk |
| `.dm` | unsupported |  | unavailable: the registry's lookup service did not answer automated checks when we last verified it (2026-10); availability can be checked at https://www.dmdomains.dm |
| `.do` | unsupported |  | unavailable: the registry's lookup service did not answer automated checks when we last verified it (2026-10); availability can be checked at https://nic.do |
| `.dz` | whois | `whois.nic.dz` | 2026-10-07 |
| `.ec` | rdap | `rdap.registry.ec` | IANA RDAP bootstrap |
| `.ee` | whois | `whois.tld.ee` | 2026-10-07 |
| `.eg` | unsupported |  | unavailable: the registry does not publish an automated lookup service |
| `.er` | unsupported |  | unavailable: the registry does not publish an automated lookup service |
| `.es` | unsupported |  | restricted: the registry provides automated lookups to accredited users only; availability can be checked at https://www.dominios.es |
| `.et` | unsupported |  | unavailable: the registry does not publish an automated lookup service |
| `.eu` | whois | `whois.eu` | 2026-10-07 |
| `.fi` | rdap | `rdap.fi` | IANA RDAP bootstrap |
| `.fj` | rdap | `www.rdap.fj` | IANA RDAP bootstrap |
| `.fk` | unsupported |  | unavailable: the registry does not publish an automated lookup service; .fk registers third-level names only |
| `.fm` | rdap | `rdap.centralnic.com` | 2026-10-06 |
| `.fo` | rdap | `rdap.centralnic.com` | IANA RDAP bootstrap |
| `.fr` | rdap | `rdap.nic.fr` | IANA RDAP bootstrap |
| `.ga` | rdap | `rdap.nic.ga` | 2026-10-07 |
| `.gb` | unsupported |  | unavailable: .gb is not open for registration |
| `.gd` | rdap | `rdap.centralnic.com` | IANA RDAP bootstrap |
| `.ge` | whois | `whois.nic.ge` | 2026-10-07 |
| `.gf` | whois | `whois.mediaserv.net` | 2026-10-07 |
| `.gg` | whois | `whois.gg` | 2026-10-06 |
| `.gh` | whois | `whois.nic.gh` | 2026-10-07 |
| `.gi` | rdap | `rdap.identitydigital.services` | 2026-10-07 |
| `.gl` | whois | `whois.nic.gl` | 2026-10-07 |
| `.gm` | unsupported |  | unavailable: the registry does not publish an automated lookup service |
| `.gn` | whois | `whois.ande.gov.gn` | 2026-10-07 |
| `.gp` | unsupported |  | unavailable: the registry's lookup service did not answer automated checks when we last verified it (2026-10) |
| `.gq` | unsupported |  | unavailable: the registry's lookup service is not currently available |
| `.gr` | unsupported |  | unavailable: the registry does not publish an automated lookup service |
| `.gs` | rdap | `rdap.nic.gs` | IANA RDAP bootstrap |
| `.gt` | unsupported |  | unavailable: the registry does not publish an automated lookup service |
| `.gu` | unsupported |  | unavailable: the registry does not publish an automated lookup service |
| `.gw` | unsupported |  | unavailable: the registry does not publish an automated lookup service |
| `.gy` | rdap | `rdap.registry.gy` | IANA RDAP bootstrap |
| `.hk` | whois | `whois.hkirc.hk` | 2026-10-07 |
| `.hm` | unsupported |  | unavailable: the registry's lookup service did not answer automated checks when we last verified it (2026-10) |
| `.hn` | rdap | `rdap.nic.hn` | IANA RDAP bootstrap |
| `.hr` | whois | `whois.dns.hr` | 2026-10-07 |
| `.ht` | rdap | `rdap.nic.ht` | IANA RDAP bootstrap |
| `.hu` | whois | `whois.nic.hu` | 2026-10-07 |
| `.id` | rdap | `rdap.pandi.id` | IANA RDAP bootstrap |
| `.ie` | whois | `whois.weare.ie` | 2026-10-07 |
| `.il` | unsupported |  | unavailable: the registry's lookup replies do not include an explicit 'not registered' answer this tool can rely on; availability can be checked at https://www.isoc.org.il |
| `.im` | whois | `whois.nic.im` | 2026-10-06 |
| `.in` | rdap | `rdap.nixiregistry.in` | 2026-10-06 |
| `.io` | rdap | `rdap.identitydigital.services` | 2026-10-06 |
| `.iq` | unsupported |  | unavailable: the registry's lookup service did not answer automated checks when we last verified it (2026-10) |
| `.ir` | whois | `whois.nic.ir` | 2026-10-07 |
| `.is` | rdap | `rdap.isnic.is` | 2026-10-06 |
| `.it` | whois | `whois.nic.it` | 2026-10-06 |
| `.je` | whois | `whois.je` | 2026-10-07 |
| `.jm` | unsupported |  | unavailable: the registry does not publish an automated lookup service |
| `.jo` | unsupported |  | unavailable: the registry's lookup service did not answer automated checks when we last verified it (2026-10) |
| `.jp` | whois | `whois.jprs.jp` | 2026-10-07 |
| `.ke` | rdap | `rdap.kenic.or.ke` | IANA RDAP bootstrap |
| `.kg` | rdap | `rdap.cctld.kg` | IANA RDAP bootstrap |
| `.kh` | unsupported |  | unavailable: the registry does not publish an automated lookup service |
| `.ki` | rdap | `rdap.nic.ki` | 2026-10-07 |
| `.km` | unsupported |  | unavailable: the registry does not publish an automated lookup service |
| `.kn` | rdap | `rdap.nic.kn` | 2026-10-07 |
| `.kp` | unsupported |  | unavailable: the registry does not publish an automated lookup service |
| `.kr` | rdap | `rdap.nic.or.kr` | IANA RDAP bootstrap |
| `.kw` | whois | `whois.nic.kw` | 2026-10-07 |
| `.ky` | rdap | `whois.kyregistry.ky` | IANA RDAP bootstrap |
| `.kz` | rdap | `rdap.nic.kz` | 2026-10-07 |
| `.la` | whois | `whois.nic.la` | 2026-10-06 |
| `.lb` | rdap | `rdap.lbdr.org.lb` | IANA RDAP bootstrap |
| `.lc` | unsupported |  | unavailable: the registry does not publish an automated lookup service |
| `.li` | rdap | `rdap.nic.ch` | 2026-10-06 |
| `.lk` | unsupported |  | unavailable: the registry does not publish an automated lookup service |
| `.lr` | unsupported |  | unavailable: the registry does not publish an automated lookup service |
| `.ls` | whois | `whois.nic.ls` | 2026-10-07 |
| `.lt` | whois | `whois.domreg.lt` | 2026-10-07 |
| `.lu` | whois | `whois.dns.lu` | 2026-10-07 |
| `.lv` | whois | `whois.nic.lv` | 2026-10-07 |
| `.ly` | rdap | `rdap.nic.ly` | 2026-10-06 |
| `.ma` | whois | `whois.registre.ma` | 2026-10-06 |
| `.mc` | whois | `whois.nic.mc` | 2026-10-07 |
| `.md` | whois | `whois.nic.md` | 2026-10-07 |
| `.me` | rdap | `rdap.identitydigital.services` | 2026-10-06 |
| `.mg` | rdap | `rdap.nic.mg` | IANA RDAP bootstrap |
| `.mh` | unsupported |  | unavailable: the registry does not publish an automated lookup service |
| `.mk` | whois | `whois.marnet.mk` | 2026-10-07 |
| `.ml` | rdap | `rdap.nic.ml` | IANA RDAP bootstrap |
| `.mm` | whois | `whois.registry.gov.mm` | 2026-10-07 |
| `.mn` | whois | `whois.nic.mn` | 2026-10-07 |
| `.mo` | whois | `whois.monic.mo` | 2026-10-07 |
| `.mp` | unsupported |  | unavailable: the registry does not publish an automated lookup service |
| `.mq` | whois | `whois.mediaserv.net` | 2026-10-07 |
| `.mr` | rdap | `rdap.nic.mr` | 2026-10-07 |
| `.ms` | rdap | `rdap.nic.ms` | IANA RDAP bootstrap |
| `.mt` | unsupported |  | unavailable: the registry does not publish an automated lookup service |
| `.mu` | rdap | `rdap.identitydigital.services` | IANA RDAP bootstrap |
| `.mv` | unsupported |  | unavailable: the registry does not publish an automated lookup service |
| `.mw` | unsupported |  | unavailable: the registry's lookup service did not answer automated checks when we last verified it (2026-10) |
| `.mx` | whois | `whois.mx` | 2026-10-06 |
| `.my` | whois | `whois.mynic.my` | 2026-10-07 |
| `.mz` | rdap | `rdap.nic.mz` | 2026-10-07 |
| `.na` | rdap | `keetmans.omadhina.co.na` | IANA RDAP bootstrap |
| `.nc` | whois | `whois.nc` | 2026-10-07 |
| `.ne` | unsupported |  | unavailable: the registry does not publish an automated lookup service |
| `.nf` | rdap | `rdap.nic.nf` | IANA RDAP bootstrap |
| `.ng` | rdap | `rdap.nic.net.ng` | IANA RDAP bootstrap |
| `.ni` | unsupported |  | unavailable: the registry offers a web lookup only; availability can be checked at https://nic.ni |
| `.nl` | rdap | `rdap.sidn.nl` | IANA RDAP bootstrap |
| `.no` | rdap | `rdap.norid.no` | IANA RDAP bootstrap |
| `.np` | unsupported |  | unavailable: the registry offers web forms only; availability can be checked at https://register.com.np |
| `.nr` | unsupported |  | unavailable: the registry does not publish an automated lookup service |
| `.nu` | whois | `whois.iis.nu` | 2026-10-06 |
| `.nz` | whois | `whois.irs.net.nz` | 2026-10-07 |
| `.om` | unsupported |  | unavailable: the registry's lookup service could not be reached over a verified secure (TLS) connection from this tool; availability can be checked at https://www.registry.om |
| `.pa` | unsupported |  | unavailable: the registry offers a web lookup only; availability can be checked at https://nic.pa |
| `.pe` | whois | `kero.yachay.pe` | 2026-10-06 |
| `.pf` | unsupported |  | unavailable: the registry's lookup service did not answer automated checks when we last verified it (2026-10) |
| `.pg` | rdap | `rdap.nic.pg` | IANA RDAP bootstrap |
| `.ph` | unsupported |  | unavailable: the registry offers a web lookup only; availability can be checked at https://dot.ph |
| `.pk` | whois | `whois.pknic.net.pk` | 2026-10-07 |
| `.pl` | rdap | `rdap.dns.pl` | IANA RDAP bootstrap |
| `.pm` | rdap | `rdap.nic.pm` | IANA RDAP bootstrap |
| `.pn` | rdap | `rdap.nominet.uk` | IANA RDAP bootstrap |
| `.pr` | rdap | `rdap.identitydigital.services` | 2026-10-07 |
| `.ps` | unsupported |  | unavailable: the registry's lookup service did not answer automated checks when we last verified it (2026-10) |
| `.pt` | unsupported |  | unavailable: the registry's lookup service did not answer automated checks when we last verified it (2026-10); availability can be checked at https://www.pt.pt |
| `.pw` | rdap | `rdap.radix.host` | IANA RDAP bootstrap |
| `.py` | unsupported |  | unavailable: the registry offers a web lookup only; availability can be checked at https://www.nic.py |
| `.qa` | whois | `whois.registry.qa` | 2026-10-07 |
| `.re` | rdap | `rdap.nic.re` | 2026-10-06 |
| `.ro` | unsupported |  | restricted: the registry's terms of use do not allow automated availability lookups; availability can be checked at https://www.rotld.ro |
| `.rs` | whois | `whois.rnids.rs` | 2026-10-07 |
| `.ru` | whois | `whois.tcinet.ru` | 2026-10-07 |
| `.rw` | rdap | `rdap.ricta.org.rw` | IANA RDAP bootstrap |
| `.sa` | whois | `whois.nic.net.sa` | 2026-10-07 |
| `.sb` | rdap | `rdap.nic.sb` | 2026-10-07 |
| `.sc` | rdap | `rdap.identitydigital.services` | 2026-10-07 |
| `.sd` | rdap | `rdap.nic.sd` | IANA RDAP bootstrap |
| `.se` | whois | `whois.iis.se` | 2026-10-07 |
| `.sg` | rdap | `rdap.sgnic.sg` | IANA RDAP bootstrap |
| `.sh` | rdap | `rdap.identitydigital.services` | 2026-10-06 |
| `.si` | rdap | `rdap.register.si` | IANA RDAP bootstrap |
| `.sj` | unsupported |  | unavailable: .sj is not open for registration |
| `.sk` | whois | `whois.sk-nic.sk` | 2026-10-07 |
| `.sl` | whois | `whois.nic.sl` | 2026-10-07 |
| `.sm` | whois | `whois.nic.sm` | 2026-10-07 |
| `.sn` | rdap | `rdap.nic.sn` | IANA RDAP bootstrap |
| `.so` | rdap | `rdap.nic.so` | 2026-10-06 |
| `.sr` | rdap | `whois.sr` | IANA RDAP bootstrap |
| `.ss` | rdap | `rdap.nic.ss` | IANA RDAP bootstrap |
| `.st` | whois | `whois.nic.st` | 2026-10-06 |
| `.su` | whois | `whois.tcinet.ru` | 2026-10-07 |
| `.sv` | unsupported |  | unavailable: the registry offers a web lookup only; availability can be checked at https://svnet.sv |
| `.sx` | whois | `whois.sx` | 2026-10-07 |
| `.sy` | rdap | `rdap.nic.sy` | 2026-10-07 |
| `.sz` | unsupported |  | unavailable: the registry does not publish an automated lookup service |
| `.tc` | whois | `whois.nic.tc` | 2026-10-07 |
| `.td` | rdap | `rdap.nic.td` | 2026-10-07 |
| `.tf` | rdap | `rdap.nic.tf` | IANA RDAP bootstrap |
| `.tg` | whois | `whois.nic.tg` | 2026-10-07 |
| `.th` | rdap | `rdap.thains.co.th` | IANA RDAP bootstrap |
| `.tj` | unsupported |  | unavailable: the registry offers a web lookup only; availability can be checked at https://nic.tj |
| `.tk` | unsupported |  | unavailable: the registry's lookup service is not currently available |
| `.tl` | rdap | `rdap.nic.tl` | 2026-10-07 |
| `.tm` | whois | `whois.nic.tm` | 2026-10-07 |
| `.tn` | whois | `whois.ati.tn` | 2026-10-07 |
| `.to` | rdap | `rdap.tonicregistry.to` | 2026-10-06 |
| `.tr` | whois | `whois.trabis.gov.tr` | 2026-10-07 |
| `.tt` | unsupported |  | unavailable: the registry offers a web lookup only; availability can be checked at https://nic.tt |
| `.tv` | rdap | `rdap.nic.tv` | 2026-10-06 |
| `.tw` | rdap | `ccrdap.twnic.tw` | IANA RDAP bootstrap |
| `.tz` | rdap | `rdap.tznic.or.tz` | IANA RDAP bootstrap |
| `.ua` | rdap | `rdap.hostmaster.ua` | IANA RDAP bootstrap |
| `.ug` | whois | `whois.co.ug` | 2026-10-07 |
| `.uk` | rdap | `rdap.nominet.uk` | IANA RDAP bootstrap |
| `.us` | rdap | `rdap.nic.us` | 2026-10-07 |
| `.uy` | unsupported |  | unavailable: the registry's lookup service did not answer automated checks when we last verified it (2026-10); availability can be checked at https://nic.uy |
| `.uz` | rdap | `rdap.cctld.uz` | IANA RDAP bootstrap |
| `.va` | unsupported |  | unavailable: .va is not open for public registration |
| `.vc` | rdap | `rdap.identitydigital.services` | 2026-10-07 |
| `.ve` | whois | `whois.nic.ve` | 2026-10-07 |
| `.vg` | rdap | `rdap.centralnic.com` | IANA RDAP bootstrap |
| `.vi` | rdap | `rdap.nic.vi` | IANA RDAP bootstrap |
| `.vn` | unsupported |  | unavailable: the registry offers a web lookup only; availability can be checked at https://vnnic.vn |
| `.vu` | rdap | `rdap.dnrs.vu` | 2026-10-07 |
| `.wf` | rdap | `rdap.nic.wf` | IANA RDAP bootstrap |
| `.ws` | rdap | `rdap.website.ws` | 2026-10-06 |
| `.ye` | rdap | `rdap.y.net.ye` | IANA RDAP bootstrap |
| `.yt` | rdap | `rdap.nic.yt` | IANA RDAP bootstrap |
| `.za` | unsupported |  | unavailable: names are registered under second-level zones such as co.za, not directly under .za |
| `.zm` | rdap | `rdap.nic.zm` | IANA RDAP bootstrap |
| `.zw` | unsupported |  | unavailable: the registry does not publish an automated lookup service |

## Generic TLDs

1038 generic TLDs are known from the bundled IANA list; 1035 resolve to an RDAP server through the IANA bootstrap and 3 cannot be checked. Run `domainhack tlds --all` for the full list.

