# Public passport viewer teardown (P1, first pass)

The first iteration of the "public passport pages → field-coverage matrix" idea in
[`../IDEAS.md`](../IDEAS.md), run with REA (`rea-agents` 6.7.0) on 23 Oct 2026.

**Scope.** Two open-source viewers were analysed statically, and four public sample passports
were captured live. The live captures were passive: page load only, no clicking, typing or
sign-in. Every target was explicitly published as a demo or example. robots.txt allowed
access where present.

| Target | Kind | How REA saw it |
|---|---|---|
| [eclipse-tractusx/digital-product-pass](https://github.com/eclipse-tractusx/digital-product-pass) `b3d0f2c` (Apache-2.0) | Catena-X reference battery passport viewer (Vue) | `analyze-javascript-application` on `dpp-frontend`; source reading of `.vue` templates REA does not parse |
| [DavidTidy/battery-passport-prototype](https://github.com/DavidTidy/battery-passport-prototype) `f1e0eff` (no licence; described, not copied) | LMT e-bike battery demo (vanilla JS) | `analyze-javascript-application`; loopback live capture |
| `thebatterypass.io` sample passport | Battery Pass consortium demonstrator | live `capture-browser-scenario`; SSR payload inspection |
| `seqo-pass.com/passport/gg-ess-004821.html` | Vendor sample (stationary LFP) | live `capture-browser-scenario` |
| `portal.circuland.co.uk/en/battery/m/…` | Vendor "live" public model passport | live `capture-browser-scenario` |
| `passportcraft.com/01/05701234000013` | Vendor textile example via GS1 Digital Link | live `capture-browser-scenario`; resolver header checks |

The required-field baseline is the Battery Pass consortium's
[Data Attribute Longlist v1.2](https://github.com/batterypass/BatteryPassDataModel)
(Jan 2025, CC-BY-4.0). It maps 93 attributes to Regulation (EU) 2023/1542 references, access
tiers and battery categories; the trimmed copy is in `battery-pass-attributes-v1.2.json`.

## Files

- `MATRIX.md`: the 93-attribute coverage matrix for the two open-source viewers (generated).
- `mapping.json`: hand-reviewed attribute → field mapping with Y/P/N per viewer.
- `build_matrix.py`: regenerates `MATRIX.md` (`python3 build_matrix.py > MATRIX.md`).
- `capture_live.sh`: REA live-capture runbook (Chromium over loopback CDP, connect mode).
- `summarize_capture.py`: turns a capture directory into a receipt without bodies, header
  values or query values.
- `receipts/*.json`: per-target receipts with REA evidence IDs, request inventory and visible text.

## Coverage results (open-source viewers)

| Mandatory attributes | Tractus-X viewer (EV) | LMT prototype |
|---|---|---|
| All tiers | 49 of 70 full, 21 partial, 0 missing | 30 of 79 full, 20 partial, 29 missing |
| Public tier | 35 of 53 full, 18 partial | 21 of 52 full, 15 partial, 16 missing |

- **Tractus-X / Catena-X `battery_pass` 6.0.0.**
  - Every mandatory attribute has a home.
  - The partial rows are mostly:
    - recycled content with no pre-/post-consumer split (rows 40–47)
    - dynamic-data fields that are document blobs rather than counters (time at extreme
      temperature, deep-discharge and overcharge events)
    - manufacturer contact details: there is only a BPN ID, with no postal, web or e-mail contact
  - The viewer is model-driven: each top-level payload key becomes a tab. That's why its
    coverage equals the data model's.
- **LMT prototype.**
  - Strong on the consumer UX: plain-language end-of-life guidance and a role-based tier
    switcher.
  - Weak on electrochemical data: no round-trip efficiency, internal resistance, power or
    C-rate fields.

## Findings

1. **The Catena-X reference viewer has no public tier.**
   - Every passport sits behind Keycloak sign-in plus a BPN/role check
     (`src/services/Authentication.js`), then EDC contract negotiation. The UI reads ODRL
     `permission`, `constraint` and `leftOperand` fields (REA endpoint candidates in
     `src/utils/edcUtil.js`).
   - Annex XIII point 1 data must be public, so this viewer can't serve a QR scan on its own.
   - **Norruva opportunity:** a public-tier front door that resolves the QR and shows Annex
     XIII(1) fields, while handing restricted tiers to a dataspace connector.
2. **Server-side tier filtering is done right on the Battery Pass demonstrator.**
   - The public page is server-rendered (Next.js `__NEXT_DATA__`), and the page makes no
     client-side data API calls.
   - Restricted fields (negative events, dismantling documentation, test reports, detailed
     composition) are present as keys but are `null` in the public payload.
   - The payload does expose backend topology: `dataSource.name` and `dataSource.instanceUrl`
     (`provider-bmw.thebatterypass.io/passport-instances/…`). This is a design lesson: keep
     provider hostnames out of public payloads.
   - It ships a `validation {hash, signature, isValid}` block and per-datapoint
     `dataPointsVerifications`. That's an "integrity in the passport" pattern worth matching.
3. **Identifier schemes differ.**
   - Battery Pass uses W3C DIDs (`did:web:acme.battery.pass:<uuid>`). The sample host
     `acme.battery.pass` does not resolve, so the DID cannot be resolved independently and the
     "Verified" badge rests on the viewer alone.
   - PassportCraft uses GS1 Digital Link:
     - `/01/<GTIN>` returns a 307 redirect to an internal `/passport/<uuid>`.
     - It advertises `rel="linkset"`.
     - It returns `application/linkset+json` both for `Accept: application/linkset+json` and
       for `?linkType=all`.
     - `/01/<GTIN>/21/<serial>` for an unknown serial redirects to `/passport/unavailable`.
       That's a good resolver baseline to test Norruva against.
4. **Tier placement errors in demos.**
   - Seqo's public page shows battery status, cycles completed and remaining capacity. The
     longlist assigns all three to persons with a legitimate interest.
   - The LMT prototype puts the carbon-footprint study in the authority-only tier, but the
     regulation makes the public study link public (row 25).
   - Lesson: tiers need to be enforced from the attribute list, not chosen by page designers.
5. **Third-party tracking on a public passport page.**
   - Circuland's public passport loaded 19 third-party origins: Google Analytics (3 tags),
     Google Ads/DoubleClick, LinkedIn Insight, Microsoft Clarity session recording, Bing and
     an OpenAI ads pixel.
   - All of this happened in a fresh browser profile while its "We use cookies — Opt Out /
     Got it" notice was still unanswered.
   - That's a privacy and trust risk for a page every product owner is sent to by QR code;
     it's worth a privacy review before copying the pattern.
   - Seqo uses only Plausible. Battery Pass and PassportCraft load nothing from third-party
     analytics.
6. **Document persistence.** PassportCraft serves the EU Declaration of Conformity through a
   Supabase signed URL that expires after 3600 s, so a saved or shared link to the declaration
   stops working after an hour. Persistent document URLs (or a resolver link) are the safer
   pattern.

## REA notes and limits

- `analyze-javascript-application` parsed the 18 `.js` files in the Tractus-X frontend but
  skipped the 61 `.vue` single-file components, so field rendering was confirmed by reading
  source.
  - Its 19 "endpoint" hits were mostly low-confidence JSON getters.
  - It missed the real backend calls, which are template-literal URLs through a wrapper in
    `BackendService.js` (`/api/contract/*`, `/api/data`, `/api/irs/*`, `/api/verification/verify`).
- Live capture needs connect mode: REA's launch mode has no proxy option, and in this
  environment that meant DNS failures.
- `analyze-web-bundle` ran out of Node heap (about 2 GB) on the Battery Pass, Circuland and
  PassportCraft bundles; only Seqo completed. Retry with a larger `--max-old-space-size`.
- The Circuland capture only sees the first section of its tabbed layout. Others need
  scripted tab clicks.

## Next steps

- Add the four live passports as matrix columns: Battery Pass from its SSR keys, Seqo from
  visible text, and Circuland after scripted tab captures.
- Re-run bundle analysis with more heap and source maps.
- Turn finding 3 into a resolver conformance suite for Norruva.
