# Reverse engineering for Digital Product Passports (DPP) with REA

Research backlog for using [REA — Reverse Engineer Anything](https://github.com/morluto/rea)
(`rea-agents` 6.7.0) to learn how the DPP ecosystem works on the wire, in files and on
devices. The goal is interoperability and product learning for Norruva, not copying anyone.

## Ground rules (apply to every idea)

- **Targets are public, open-source, or ours/authorized in writing.** Public web pages and
  their shipped JavaScript, public resolvers, open-source reference stacks, published
  standards artifacts, public blockchain bytecode, and Norruva or partner artifacts covered
  by an agreement.
- **Passive first.** Prefer REA's static and recorded-capture commands (`analyze-*`,
  `inspect-*`, HAR/pcap replay-free inspection) over runtime capture. REA does not execute
  targets in its static paths.
- **No circumvention.** No bypassing auth, paywalls, rate limits, DRM, licence checks or
  ToS. No credential use against third-party systems. Competitor mobile/desktop apps are
  out of scope for this backlog.
- **Interoperability basis.** Where decompilation of a licensed program is needed for
  interop, get legal sign-off first (EU Software Directive 2009/24/EC Art. 6).
- **Output is evidence, not certification.** As in `eidos/evidence/README.md`, REA proves
  what bytes and observations were seen, not quality, security or rights.

## Why now: regulatory drivers

| Driver | What it forces | RE angle |
|---|---|---|
| ESPR, Regulation (EU) 2024/1781 | DPP per product group via delegated acts; data carrier, unique IDs, EU DPP registry, access rights by actor | How live passports encode IDs, carriers, access tiers |
| Battery Regulation (EU) 2023/1542 | Battery passport for EV, LMT and industrial >2 kWh batteries from 18 Feb 2027; QR code; state of health | BMS data, carrier payloads, reference data models |
| CEN/CENELEC JTC 24 standards | Harmonised standards for identifiers, carriers, interoperability, APIs | Compare real implementations against draft requirements |
| GS1 Digital Link / IEC 61406 / ISO/IEC 15459 | Resolvable identifiers on labels | Resolver behaviour, link types, redirects |
| UN Transparency Protocol (UNTP), W3C VC | Passports and conformity claims as verifiable credentials | Credential shapes, signatures, status lists |
| Catena-X / Eclipse Tractus-X, IDTA AAS | Dataspace exchange of passports (EDC, digital twin registry, AAS submodels) | Connector handshakes and submodel schemas |

## Ideas, prioritized

Priority = impact on Norruva × ease with REA today. **P1** = start now, **P2** = next, **P3** = opportunistic.

### P1 — Public passport viewer teardown → ESPR field-coverage matrix

**Status:** first pass done, see [`passport-viewers/`](passport-viewers/README.md).

- **Target:** Publicly reachable passport pages that brands and DPP vendors publish behind
  on-pack QR codes (battery, textile, electronics, furniture pilots).
- **REA:** `inspect-web-page`, `analyze-web-bundle --fetch-source-maps`,
  `export-web-scripts` → `analyze-javascript-application`, `discover-webmcp-tools`,
  `compare-web-captures` across vendors.
- **Learn:** Front-end frameworks, JSON-LD / schema.org / GS1 vocab used, API endpoint
  shapes and versioning, how they render access tiers (consumer vs repairer vs authority),
  i18n, accessibility, offline behaviour.
- **Deliverable:** Field-by-field matrix (ESPR Annex III + Battery Annex XIII fields) of
  who exposes what, and a "patterns to adopt / avoid" note for Norruva's viewer.

### P1 — Data-carrier and resolver behaviour (GS1 Digital Link, IEC 61406)
- **Target:** Public GS1 Digital Link resolvers and brand resolvers reached from QR codes;
  our own test labels.
- **REA:** `capture-browser-scenario` (scan URL → follow redirects), HAR export into
  `inspect-web-network-capture`, `compare-web-captures` for content negotiation
  (`Accept: application/linkset+json` vs HTML).
- **Learn:** Link-type sets, linkset JSON, redirect chains, cache headers, language and
  role-based routing, how resolvers behave when a passport moves or a company dissolves
  (ESPR backup-copy requirement).
- **Deliverable:** Conformance test suite for Norruva's resolver; a list of real-world
  deviations from GS1 Digital Link 1.x.

### P1 — Tractus-X / Catena-X Battery Pass interoperability map
- **Target:** Open-source Eclipse Tractus-X components (EDC connector, digital twin
  registry, BPN discovery, product passport consumer app, Battery Pass submodels).
- **REA:** Build artifacts we compile ourselves, then `analyze-javascript-application`
  (consumer frontend), `inspect-binary-layout` / `inspect-go-binary` where relevant,
  `inspect-web-network-capture` on HAR/mitmproxy captures of our own local test
  dataspace.
- **Learn:** Exact catalog → negotiation → transfer sequence, policy (ODRL) shapes, submodel
  semantic IDs, which fields consumers actually read.
- **Deliverable:** Sequence diagram and a minimal Norruva connector adapter spec.

### P2 — Verifiable-credential passports (UNTP / W3C VC / EUDI wallet)
- **Target:** UNTP sample credentials and test vectors; open-source EUDI wallet reference
  apps (public APKs built from source); our own signed passport bundles.
- **REA:** `inspect-android-package`, `search-android-classes`,
  `inspect-android-method` (on the open-source wallet APK we build), `inspect-signature`,
  `inspect-artifact` for JWT/JSON-LD bundles.
- **Learn:** Which proof formats and status-list mechanisms wallets accept; how a consumer
  wallet could store or present a product passport or conformity credential.
- **Deliverable:** Compatibility table (proof type × wallet × status method) and a
  recommendation for Norruva's credential issuance format.

### P2 — Battery state-of-health pipeline (own and partner hardware)
- **Target:** BMS firmware images and diagnostic tools supplied by partners under NDA;
  CAN/UDS and BLE captures from our own test packs.
- **REA:** `inspect-firmware-regions` / `extract-firmware`, `inspect-binary-layout`,
  `inspect-pcap-capture` / `inspect-packet-capture`, `decode-signal-recording` (sigrok)
  for bench recordings; `inspect-sqlite-database` for vendor tool caches.
- **Learn:** Where SoH, cycle count, capacity fade and event logs live; how to pull them
  into the passport's dynamic fields that Battery Regulation Annex XIII expects.
- **Deliverable:** Ingestion mapping per BMS family plus a passive capture runbook.

### P2 — On-chain passport and NFT-based authenticity schemes
- **Target:** Public smart contracts used by blockchain-anchored DPP and authenticity
  schemes (bytecode is public on-chain).
- **REA:** `inspect-evm-interface` on fetched bytecode.
- **Learn:** Token standards, ownership-transfer and revocation models, what's on-chain vs
  off-chain, GDPR exposure of holder data.
- **Deliverable:** Short brief on whether and how Norruva should anchor passport hashes.

### P2 — NFC / RFID tag payloads on physical products
- **Target:** Tags on products we buy, our own encoded tags, and public datasheets for
  tag ICs. Read-only NDEF.
- **REA:** `decode-signal-recording` for bench SDR/logic-analyzer captures,
  `inspect-artifact` on NDEF dumps, `inspect-pcap-capture` for reader traffic.
- **Learn:** URL templates, signed-tag or counter schemes (anti-cloning), how passports
  survive tag-only vs QR-only scanning.
- **Deliverable:** Carrier recommendation per product category (QR, NFC, both).

### P3 — AAS tooling compatibility (IDTA Asset Administration Shell)

**Status:** deep dive done, see [`aas-compat/`](aas-compat/README.md).
**Follow-on:** [AAS Forge](../aasforge/README.md) is an AAS server and DPP layer built from the
teardowns and compatibility findings. It routes all 235 v3.2 API operations, imports the corpus
without loss, and enforces Battery Regulation tiers as IDTA-01004 rules
([architecture](../aasforge/ARCHITECTURE.md)).

- **Target:** Open-source AASX Package Explorer (.NET) and AAS server builds we compile.
- **REA:** `inspect-managed-artifact`, `inspect-managed-members`,
  `compare-managed-members` between releases.
- **Learn:** Which submodel templates and serialisations are actually supported, and
  breaking changes between versions.
- **Deliverable:** AASX import/export compatibility checklist for Norruva.

### P3 — EU DPP registry and customs integration (once a public sandbox exists)
- **Target:** Public specifications, sandbox portals and SDKs the Commission publishes for
  the DPP registry and the customs single-window link.
- **REA:** `inspect-web-page`, `export-web-scripts`, HAR into
  `inspect-web-network-capture`, `compare-web-captures` between spec versions.
- **Learn:** Registration payloads, identifier rules, error contracts.
- **Deliverable:** Registry client stub and change-watch diff on each release.

### P3 — Self-audit of Norruva and EIDOS surfaces
- **Target:** Our own web clients (e.g. `eidos/web`), desktop builds, APKs and containers.
- **REA:** `analyze-javascript-application`, `inspect-electron-fuses`, `inspect-artifact`,
  `compare-source-to-bundle` (shipped bundle vs committed source).
- **Learn:** What a competitor or auditor would see when they do these teardowns to us:
  leaked endpoints, source maps, debug flags, third-party SDKs.
- **Deliverable:** Pre-release "outside-in" report per release. Already smoke-tested on
  `eidos/web` (see below).

## Session setup used

This cloud session only; nothing here is installed by repository hooks.

```bash
npm install -g n && n 24                 # REA needs Node >=22.23.4 / >=24.22.1
export PATH="/usr/local/bin:$PATH"       # Node 24 ahead of /opt/node22
npm install --global rea-agents@6.7.0
rea setup --client claude_code --yes     # MCP server "rea" + reverse-engineer-anything skill
export REA_BROWSER_EXECUTABLE=/opt/pw-browsers/chromium REA_BROWSER_NO_SANDBOX=1
rea doctor --client claude_code --skill
```

The `rea` MCP registration in `~/.claude.json` sets `PATH` (Node 24 first),
`REA_BROWSER_EXECUTABLE` and `REA_BROWSER_NO_SANDBOX` so the server runs on the right Node
and can drive the preinstalled Chromium.

**Verified:** `claude mcp list` shows `rea … Connected`; `rea doctor` is healthy for Node
24.22.1 on Ubuntu 24.04; `rea analyze-javascript-application eidos/web` completed and
parsed all four files.

**Not yet available:**
- Native engines (Ghidra, Hopper, IDA, JADX) aren't installed. They're only needed for
  deep native or firmware decompilation (P2 battery ideas).
- Live web capture works through Chromium and CDP, but this environment's network policy
  denies public DPP hosts. `ref.gs1.org`, `www.gs1.org`, `eclipse-tractusx.github.io`,
  `uncefact.github.io` and `thebatterypass.eu` were refused by the egress proxy. Add
  them under the environment's allowed domains to run the P1 web ideas.
