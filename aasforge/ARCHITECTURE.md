# AAS Forge architecture

AAS Forge was designed by reverse engineering. We read the source of the main open-source AAS
implementations and ran them against a shared corpus. Then we built one system that combines their
best ideas and closes the gaps we measured. This document explains which ideas were taken from
which tool, what was fixed and the evidence for it, how the AAS layer carries Digital Product
Passports, and how it connects to factories (OPC UA, MQTT) and dataspaces (Catena-X).

**Sources**

- [BaSyx Java teardown](docs/teardown-basyx-java.md) (eclipse-basyx/basyx-java-server-sdk `5dec385`)
- [AASX Server and Package Explorer teardown](docs/teardown-aaspe.md)
- [Cross-tool compatibility study](../dpp-rea/aas-compat/README.md): BaSyx Python, AAS4J, aas-core
  3.0/3.1 and AASX Server, run over 65 IDTA, Catena-X and probe files
  ([matrix](../dpp-rea/aas-compat/MATRIX.md))
- Eclipse Tractus-X: the Digital Twin Registry access model
  (sldt-digital-twin-registry, `docs/architecture/6-crosscutting-concepts.md`) and the aspect models
  with their generated AAS output (sldt-semantic-models)
- The specifications:
  - AAS Part 1 metamodel 3.0.9 / 3.1.2 / 3.2.0
  - Part 2 API v3.1 / v3.2 (OpenAPI collection)
  - IDTA-01002 Query Language and IDTA-01004 Access Rules
  - IDTA 02099 DPP and 02035 Digital Battery Passport templates
  - OPC 30270 (I4AAS NodeSet)
  - SAMM 2.1
  - EDC Management API v3

## Layers

```
   HTTP (http.py)          OPC UA (opcua.py)        MQTT (mqtt.py)        CLI (__main__.py)
  ┌────────────────────────┬─────────────────────┬──────────────────────┬────────────────────┐
  │ AAS API Part 2 (api.py)│ DPP (dpp/service.py)│ Catena-X (catenax.py)│ Web editor (ui/)   │
  │ 235/235 v3.2 operations│ passports, resolver,│ DTR rules, DSP,      │ templates, forms,  │
  │ /templates             │ credential, MCP     │ EDC provisioning     │ history, query     │
  ├────────────────────────┴─────────────────────┴──────────────────────┴────────────────────┤
  │ security.py: JWT/API keys, IDTA-01004 rules · formula.py: Query + Access Rule evaluator   │
  │ sqlfilter.py: formulas compiled to SQL prefilters (superset; exact check stays in Python) │
  ├──────────────────────────────────────────────────────────────────────────────────────────┤
  │ repository.py: verification, validators, loss detection, auto registry/discovery         │
  │ elements.py · smt.py (templates) · samm.py / dictionary.py (semantics) · signing.py      │
  ├──────────────────────────────────────────────────────────────────────────────────────────┤
  │ versioning.py: 3.0/3.1/3.2, 3.2 sidecar · aasx.py: tolerant reader, deterministic writer │
  ├──────────────────────────────────────────────────────────────────────────────────────────┤
  │ store.py on db.py (SQLite | PostgreSQL): revisions and history, events, attachments,      │
  │ descriptors, asset links, packages, operations, element/shell indexes                      │
  └──────────────────────────────────────────────────────────────────────────────────────────┘
```

**Canonical model.** Objects are held as AAS metamodel 3.1 JSON, verified by the released
`aas-core3.1` 1.0.0. Fields that exist only in another version are kept in a per-object sidecar
and merged back on export. That covers 3.2 `AssetKind.Batch`, `createdAt`/`updatedAt` and
`DataSpecificationUom`. So aasforge reads 3.0 and writes 3.1, as the DPP templates require, while
staying lossless for 3.2. Updates through 3.1 routes keep the stored sidecar. The `/api/v3.2` base
splits 3.2 bodies into 3.1 plus sidecar on writes and restores them on reads.

**One view per caller, on every channel.** The access rules (and in DPP mode, the Battery
Regulation tiers) decide what each caller sees. That one decision is applied to:

- REST reads and queries (including the objects a query reaches through other objects);
- packages and serialisation, registry and discovery, signature verification and events;
- the passport view, the carbon roll-up, completeness and MCP;
- the OPC UA address space and MQTT payloads.

## What came from where

| Idea | Taken from | In aasforge |
|---|---|---|
| Feature decorators: registry, discovery and events wrap the repository | BaSyx Java `BaSyxFeature` | `Repository` with `auto_register`, so a shell write keeps its descriptor and asset links in the same transaction (BaSyx deletes the shell again when registration fails; aasforge has nothing to undo) |
| Operation delegation by qualifier | BaSyx Java `invocationDelegation` | `api._run_operation`: delegation to allow-listed URLs only, or a registered local function; sync, async and `$value` |
| Keyset paging with an opaque cursor | BaSyx Java | `store.list`; AASX Server's integer offset was avoided |
| Change events on MQTT topics | BaSyx MQTT feature | `events` table (same transaction as the change), `/events` SSE, and `mqtt.py` on BaSyx-style topics |
| Access Rule Language with FILTER as row and element filtering | AASX Server | `security.AccessRules` (DEF*/USE* reuse, union of FILTERs), `restrict_submodel` |
| Query Language and access rules compiled to SQL | AASX Server (Irony grammar to SQL) | `sqlfilter.py`. It compiles to a *superset* condition, so exactness never depends on SQL; 21–42× faster ([benchmark](docs/BENCHMARK.md)) |
| Server-maintained timestamps and diary | AASX Server (patched metamodel types) | Kept outside the model, in `store` revisions and append-only history, which back `$history`, `$recent-changes` and `$audit` |
| `$sign` JWS | AASX Server; standardised as API 3.2 `$signed` | `signing.py`: RS256 with `x5c`, `sigT`, `sid` over the RFC 8785 canonical form |
| OPC UA server for AAS | AASX Server's OPC UA (I4AAS) | `opcua.py`. It uses the official NodeSet, follows the store live, filters by tier, and has authorised write-back |
| MCP tools for LLM clients | AASX Server `/mcp` | `/mcp` JSON-RPC; tools are read-only and filtered by access rules |
| Product carbon footprint roll-up over the bill of materials | AASX Server `AasxTaskService` | `passport.carbon_rollup`, computed on the caller's view |
| SMT qualifiers drive template forms and checks | Package Explorer (KnownSubmodels, GenericForms, Assess SMT) | `smt.instantiate`/`smt.check`, `/templates/*`, and the forms of the web editor |
| ECLASS / IEC CDD dictionary import | Package Explorer importers | `dictionary.py` (CSV exports, multilingual merge, unit IRDIs) |
| SAMM aspect model to AAS | ESMF aspect model generator | `samm.py`. Its output matches the generator's for BatteryPass 6.1.0 and PCF 10.0.0, and it adds payload ↔ instance conversion |
| Strict object-count comparison on import | Lesson from BaSyx Python `failsafe=True` dropping everything | `Repository.import_bytes` returns `lost` and `expected` vs `stored` |

## Gaps fixed, with evidence

| Gap observed | Evidence | aasforge behaviour |
|---|---|---|
| BaSyx Python returns **zero objects** for 3.0 and 3.2 XML while reporting success | aas-compat finding 1 (Catena-X BatteryPass XML: 0 of 194 kept) | Version detected by namespace and features; 3.0 is up-converted; loss reported, and the CLI exits non-zero |
| JSON has no version marker, and the schemas allow unknown properties | aas-compat finding 2 | `detect_json_version` picks the lowest version whose features the file uses; 3.2 fields go to the sidecar |
| AASX Server and Package Explorer vendor `AasCore.Aas3_1` **1.0.0-rc1**, which rejects valid files | aas-compat finding 3 | The released `aas-core3.1==1.0.0` is pinned; `verification=reject\|warn\|off` |
| IDTA template packaging defects: `External` origin, missing parts, stray XML text, `xsi:schemaLocation` | aas-compat finding 4 | Tolerated, and each repair is reported; the writer refuses dangling `File` parts |
| Round trips not byte-stable (`1007` → `1007.0`, `Z` → `+00:00`, `orderRelevant` inserted) | aas-compat finding 5 | Lexical values are stored as given; signatures and digests use RFC 8785 canonical JSON; the AASX writer is deterministic |
| BaSyx Java implements API 3.0 only: no `$reference`/`$path`, and `level`/`extent` ignored | BaSyx teardown | Every modifier is implemented ([coverage](docs/API-COVERAGE.md)) |
| No async operations (BaSyx TODO #566); AASX Server operations are `NotImplementedException` | Both teardowns | `invoke`, `invoke-async`, status and results, `$value` variants, and OPC UA Methods |
| BaSyx: RBAC only, later rules overwrite earlier ones, exact path matching only | BaSyx teardown | IDTA-01004 ABAC; rules are combined; element-scoped grants are enforced on writes |
| BaSyx: search, Kafka and registry sync run inline, so a failure leaves components inconsistent | BaSyx teardown | One transaction covers the object, history, event, indexes, descriptor and asset links; bridges consume committed events |
| AASX Server: any-origin CORS; credentials accepted in the query string | AASX Server teardown | CORS is off unless configured; credentials are read from headers only |
| ESMF generator: list items carry idShorts (AASd-120 in 3.0), and collections have no semanticId | Generated `BatteryPass-aas.xml` | No idShorts on list items; every element has its property semanticId |
| 3.2 `createdAt`/`updatedAt` dropped by every tested reader | aas-compat finding 5 | Kept in the sidecar; also derived from the store when exporting 3.2 |
| 16 issues found by an internal security review, among them query probing of hidden values, ReDoS, unchecked element grants and PUT authorisation, SSRF through delegation, FILTER bypass in packages, header injection, zip bombs and token expiry | `tests/test_security.py` replays each attack | All fixed. `$regex` uses size caps, timeouts and a query budget; delegation needs an allow-list; packages are rebuilt for filtered readers; JWTs must expire |
| A second review (10 issues): hidden Catena-X asset ids on shell output paths, templates read without FILTERs, OPC UA writes accepted with writes disabled, ingest thread per message, an over-broad data-plane scope, plaintext OPC UA passwords, export amplification, unbounded file handles, SAMM path traversal | `tests/test_security2.py` | One shell view on every output path; templates read through FILTERs; writes refused unless enabled; one ordered ingest worker; each EDC asset confined to its routes; OPC UA Basic256Sha256 with encrypted passwords; exports cached and serialised; handles capped; namespaces confined to the models root |
| Found while building the integrations: rows read from a shared database cursor under concurrency, and case-sensitive header names | `test_core.StoreConcurrencyTests`; Catena-X tests through urllib | Rows are fetched under the connection lock; request headers are case-insensitive |

## How the AAS layer carries Digital Product Passports

A passport is an ordinary AAS. The DPP layer adds a profile, an access model and the touchpoints a
passport needs: resolver, credential and registry.

1. **Structure.** `passport.create` builds a Shell for the product instance. Its submodels are
   instantiated from the IDTA templates bundled in `data/templates`:
   - IDTA 02099 `DppMetadata`: EN 18223 passport identifier, granularity, status, last update
     and economic operator.
   - IDTA 02035 parts 1–7: nameplate, handover documentation, carbon footprint, technical data,
     product condition, material composition and circularity.

   Granularity (model, batch or item) is written to `DppMetadata.granularity`. A batch passport is
   also exported as 3.2 `AssetKind.Batch` (from the sidecar).
2. **Regulatory meaning.** Every attribute of the Battery Pass longlist (93 in total) records its
   Battery Regulation article, DIN DKE SPEC 99100 chapter, battery categories and access tier.
   `data/attribute-map.json` links 90 of them to template elements. `passport.completeness` scores
   a passport against the mandatory attributes for its battery category.
3. **Access tiers to IDTA-01004 rules.** Tiers come from Art. 77 and Annex XIII (public,
   legitimate interest, authority, economic operator).
   - A tier is resolved for each element in this order: a marker on the element (an extension, or a
     supplemental semanticId `…/accessTier/<tier>`), the attribute map, the parent's tier, the
     submodel's marker.
   - `tiers.access_rules()` generates a standard Access Rule document in which each tier reads
     nothing above itself. Other servers can load the same rule file. On this server, the exact
     tier filter is additionally applied to every submodel read.
   - Only the `dpp_tier` claim of a JWT or API key counts.
4. **Finding a passport.** A GS1 Digital Link `/01/{GTIN}[/10/{lot}][/21/{serial}]` resolves as
   follows:
   - The check digit and qualifier order are validated.
   - The request redirects (307) to the most specific visible passport and reports
     `X-DPP-Granularity`.
   - With `Accept: application/linkset+json`, the response is an RFC 9264 linkset.
5. **Trust.**
   - `/credential` returns a W3C VC 2.0 credential as `vc+jwt`, aligned with UNTP DPP and signed
     with the server's X.509 key.
   - `/signatures/verify` checks signatures and reports `matchesCurrent` only to callers who may
     read the object.
   - `/registry-payload` produces the ESPR Art. 13 registry record.
6. **People and machines.**
   - The `?format=html` passport page has no JavaScript and is served under
     `Content-Security-Policy: default-src 'none'`.
   - The `/ui` editor is for operators.
   - `/mcp` serves LLM agents, and OPC UA and MQTT serve machines; all of them use the same tier
     filtering.

## Factory and dataspace connections

- **OPC UA (I4AAS).** `opcua.py` maps metamodel 3.1 onto the official OPC 30270 ObjectTypes. That
  NodeSet was written for metamodel 2.0, so the deviations are listed in the module docstring.
  - Values have typed DataTypes, `semanticId`s become `HasDictionaryEntry` references to a
    ConceptDictionary, and Blobs and attachments are readable through OPC UA `FileType`.
  - The live server writes changed values in place and rebuilds only the submodels whose structure
    changed, keeping NodeIds stable, so subscriptions survive.
  - Clients log in with an API key or token as the password. No session may add or delete nodes,
    and value writes go through the access rules and the repository's validation.
- **MQTT.** `mqtt.py` publishes BaSyx-style topics, plus a retained ValueOnly per submodel for
  dashboards. Nothing is published when only hidden data changed. Optional telemetry ingest uses
  `…/submodelElements/<path>/set`, authorised like a `PATCH $value`.
- **Catena-X.** `catenax.py` installs the Digital Twin Registry semantics on registry, discovery and
  shells, using extension points in the API:
  - `specificAssetIds` are visible per BPN through `externalSubjectId`.
  - `PUBLIC_READABLE` applies only to `manufacturerPartId`/`assetLifecyclePhase`, with a reduced
    view.
  - `Edc-Bpn` is trusted only from the EDC data plane's identity.

  Submodel descriptors carry the DSP endpoint (`subprotocolBody: id=…;dspEndpoint=…`).
  `EdcClient.provision` registers submodels and the registry as EDC assets, with BPN or membership
  policies and contract definitions. Each data address names its asset (`header:X-AASForge-Asset`),
  and requests from the data plane are confined to that asset's routes.

  On the consumer side, `PartnerTwins` uses the consumer's EDC to query a partner's catalog,
  negotiate an EDR, look twins up in the partner's DTR and fetch submodels through the descriptors'
  DSP endpoints.
- **Federation.** `federation.py` asks upstream registries, or the BaSyx `registry.<host>`
  hierarchy, for descriptors this registry lacks. Only allow-listed hosts on public addresses are
  contacted, over https, and results are cached but never stored.
- **Semantics.** `samm.py` turns aspect models into templates and payloads into instances (and
  back); `dictionary.py` turns ECLASS and IEC CDD tables into IEC 61360 ConceptDescriptions. With
  both, a Catena-X BatteryPass payload becomes a verified AAS submodel whose elements resolve to
  concept descriptions.

## Design choices

- **Standard library HTTP server.** It keeps the core dependencies to aas-core, cryptography,
  jsonschema and regex. OPC UA, MQTT, PostgreSQL and SAMM are optional. The router is thin, so the
  API could move onto ASGI without changing the handlers.
- **One database adapter.** The store writes portable SQL (upserts, `RETURNING`), and `db.py`
  translates it per dialect. Text columns use the "C" collation on PostgreSQL, so ordering and
  comparisons match SQLite and the Python evaluator. One connection serves all threads; every
  statement fetches its rows under the lock.
- **Exact semantics in Python, speed from SQL.** SQL prefilters may return extra candidates but
  never miss one. A property test with 600 generated queries over the corpus checks this, and a
  planted off-by-one in the compiler makes it fail.
- **Python dict model plus aas-core verification.** Working on JSON dicts keeps both unknown
  fields and lexical forms. aas-core is the referee for constraints, not the storage model.

## Roadmap

- **Scale-out.** A connection pool and multiple worker processes. Events across processes would
  come from PostgreSQL `LISTEN/NOTIFY` instead of in-process subscribers.
- **DTR "granular" access rules** alongside the classic `externalSubjectId` model.
- **Checking EDC agreements.** Verify `Edc-Contract-Agreement-Id` against the provider EDC, and
  refresh EDR tokens on the consumer side.
- **More integration.** A Kafka bridge, and ECLASS XML (ontoml) in addition to the CSV exports.
- **More DPP product groups** beyond batteries (textiles, iron and steel), once their delegated acts
  and IDTA templates are published.
