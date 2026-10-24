# AAS Forge architecture

AAS Forge was designed by reverse engineering. We read the source of the main open-source AAS
implementations and ran them against a shared corpus. Then we built one system that combines their
best ideas and closes the gaps we measured. This document explains which ideas were taken from
which tool, what was fixed and the evidence for it, and how the AAS layer carries Digital Product
Passports.

**Sources**

- [BaSyx Java teardown](docs/teardown-basyx-java.md) (eclipse-basyx/basyx-java-server-sdk `5dec385`)
- [AASX Server and Package Explorer teardown](docs/teardown-aaspe.md)
- [Cross-tool compatibility study](../dpp-rea/aas-compat/README.md): BaSyx Python, AAS4J, aas-core
  3.0/3.1 and AASX Server, run over 65 IDTA, Catena-X and probe files
  ([matrix](../dpp-rea/aas-compat/MATRIX.md))
- The specifications:
  - AAS Part 1 metamodel 3.0.9 / 3.1.2 / 3.2.0
  - Part 2 API v3.1 / v3.2 (OpenAPI collection)
  - IDTA-01002 Query Language and IDTA-01004 Access Rules
  - IDTA 02099 DPP and 02035 Digital Battery Passport templates

## Layers

```
            HTTP (http.py: router, Result errors, SSE, multipart)            CLI (__main__.py)
  ┌───────────────────────────┬──────────────────────────────┬──────────────────────────┐
  │ AAS API Part 2 (api.py)   │ DPP service (dpp/service.py) │ GS1 resolver · MCP       │
  │ 235/235 v3.2 operations   │ /dpp/passports …             │ /01/… · /mcp             │
  ├───────────────────────────┴──────────────────────────────┴──────────────────────────┤
  │ security.py: authentication (JWT HS256/RS256, API keys) and IDTA-01004 access rules  │
  │ formula.py: one evaluator for Query Language and Access Rule FORMULA/FILTER         │
  ├──────────────────────────────────────────────────────────────────────────────────────┤
  │ repository.py: verification policy, import loss detection, auto registry/discovery │
  │ elements.py: idShortPath, ValueOnly, $metadata/$reference/$path, level/extent      │
  │ smt.py: template instantiate and check · signing.py: JWS · canonical.py: RFC 8785   │
  ├──────────────────────────────────────────────────────────────────────────────────────┤
  │ versioning.py: 3.0/3.1/3.2 detection, up/down conversion, 3.2 sidecar               │
  │ aasx.py: tolerant OPC reader with reported repairs, deterministic writer            │
  ├──────────────────────────────────────────────────────────────────────────────────────┤
  │ store.py: SQLite, revisions and history, events, attachments, descriptors,          │
  │ asset links, packages, operation handles                                            │
  └──────────────────────────────────────────────────────────────────────────────────────┘
```

**Canonical model.** Objects are held as AAS metamodel 3.1 JSON, verified by the released
`aas-core3.1` 1.0.0. Fields that exist only in another version are kept in a per-object sidecar
and merged back on export. That covers 3.2 `AssetKind.Batch`, `createdAt`/`updatedAt` and
`DataSpecificationUom`. So aasforge reads 3.0 and writes 3.1, as the DPP templates require, while
staying lossless for 3.2. Updates through 3.1 routes keep the stored sidecar. The `/api/v3.2` base
splits 3.2 bodies into 3.1 plus sidecar on writes and restores them on reads. On a copy pruned by
access-rule FILTERs, only the top-level fields are restored, because element indices may have
shifted.

## What came from where

| Idea | Taken from | In aasforge |
|---|---|---|
| Feature decorators: registry, discovery and events wrap the repository | BaSyx Java `BaSyxFeature` | `Repository` with `auto_register`, so a shell write keeps its descriptor and asset links in the same transaction (BaSyx deletes the shell again when registration fails; aasforge has nothing to undo) |
| Operation delegation by qualifier | BaSyx Java `invocationDelegation` | `api._run_operation`: delegation, or a registered local function; sync, async and `$value` |
| Keyset paging with an opaque cursor | BaSyx Java | `store.list`; AASX Server's integer offset was avoided |
| Change events | BaSyx MQTT/Kafka topics | `events` table and `/events` SSE stream, written in the same transaction as the change (no lost events) |
| Access Rule Language with FILTER as row and element filtering | AASX Server | `security.AccessRules` (DEF*/USE* reuse, union of FILTERs), `restrict_submodel` |
| Query Language JSON | AASX Server (compiled to SQL), BaSyx (Elasticsearch) | `formula.py` evaluator, tested against the spec examples, shared with access rules |
| Server-maintained timestamps and diary | AASX Server (patched metamodel types) | Kept outside the model, in `store` revisions and append-only history, which back `$history`, `$recent-changes` and `$audit` |
| `$sign` JWS | AASX Server; standardised as API 3.2 `$signed` | `signing.py`: RS256 with `x5c`, `sigT`, `sid` over the RFC 8785 canonical form |
| MCP tools for LLM clients | AASX Server `/mcp` | `/mcp` JSON-RPC; tools are read-only and filtered by access rules |
| Product carbon footprint roll-up over the bill of materials | AASX Server `AasxTaskService` | `passport.carbon_rollup` |
| SMT qualifiers drive template forms and checks | Package Explorer (KnownSubmodels, GenericForms, Assess SMT) | `smt.instantiate` and `smt.check` (cardinality, allowed values, either-or, examples pruned) |
| Strict object-count comparison on import | Lesson from BaSyx Python `failsafe=True` dropping everything | `Repository.import_bytes` returns `lost` and `expected` vs `stored` |

## Gaps fixed, with evidence

| Gap observed | Evidence | aasforge behaviour |
|---|---|---|
| BaSyx Python returns **zero objects** for 3.0 and 3.2 XML while reporting success | aas-compat finding 1 (Catena-X BatteryPass XML: 0 of 194 kept) | Version detected by namespace and features; 3.0 is up-converted; loss reported, and the CLI exits non-zero |
| JSON has no version marker, and the schemas allow unknown properties | aas-compat finding 2 | `detect_json_version` picks the lowest version whose features the file uses; 3.2 fields go to the sidecar |
| AASX Server and Package Explorer vendor `AasCore.Aas3_1` **1.0.0-rc1**, which rejects valid files | aas-compat finding 3 | The released `aas-core3.1==1.0.0` is pinned; `verification=reject\|warn\|off` |
| IDTA 02099 / 02035-6 declare the `aasx-origin` relationship `TargetMode="External"` | aas-compat finding 4 | Accepted, and reported as a repair |
| Supplementary or thumbnail parts referenced but missing (IDTA 02035-4) | aas-compat finding 4 | Reported as a repair; the writer refuses dangling `File` parts |
| Stray XML text `</value>value>` (IDTA 02023) and `xsi:schemaLocation` (Catena-X 6.0.0) | aas-compat finding 4 | Tolerated and noted; test `test_reads_stray_text_and_30_packages_losslessly` |
| Round trips not byte-stable (`1007` → `1007.0`, `Z` → `+00:00`, `orderRelevant` inserted) | aas-compat finding 5 | Lexical values are stored as given; signatures and digests use RFC 8785 canonical JSON; the AASX writer is deterministic |
| BaSyx Java implements API 3.0 only: no `$reference`/`$path`, and `level`/`extent` ignored | BaSyx teardown | Every modifier is implemented for submodels, elements and lists ([coverage](docs/API-COVERAGE.md)) |
| No async operations (BaSyx TODO #566); AASX Server operations are `NotImplementedException` | Both teardowns | `invoke`, `invoke-async`, `operation-status`, `operation-results` and `$value` variants |
| BaSyx: RBAC only, later rules overwrite earlier ones, exact path matching only | BaSyx teardown | IDTA-01004 ABAC; rules are combined, not overwritten; idShortPath prefix and wildcard objects |
| BaSyx: search, Kafka and registry sync run inline, so a failure leaves components inconsistent | BaSyx teardown | One SQLite transaction covers the object, history, event, descriptor and asset links |
| AASX Server: any-origin CORS; credentials accepted in the query string | AASX Server teardown | CORS is off unless configured; credentials are read from headers only |
| ValueOnly writes accept values that don't fit `valueType` | Found by `test_api` while building aasforge | `elements._lexical` checks XSD lexical form with aas-core and returns 400 |
| 3.2 `createdAt`/`updatedAt` dropped by every tested reader | aas-compat finding 5 | Kept in the sidecar; also derived from the store when exporting 3.2 |

## How the AAS layer carries Digital Product Passports

A passport is an ordinary AAS. The DPP layer adds a profile, an access model and the touchpoints a
passport needs: resolver, credential and registry.

1. **Structure.** `passport.create` builds a Shell for the product instance. Its submodels are
   instantiated from the IDTA templates bundled in `data/templates`:
   - IDTA 02099 `DppMetadata`: EN 18223 passport identifier, granularity, status, last update
     and economic operator.
   - IDTA 02035 parts 1–7: nameplate, handover documentation, carbon footprint, technical data,
     product condition, material composition and circularity.

   Granularity (model, batch or item) is written to `DppMetadata.granularity`, which today's
   readers understand. It is also written as 3.2 `AssetKind.Batch` in the sidecar, so a 3.2
   reader receives it.
2. **Regulatory meaning.** Every attribute of the Battery Pass longlist (93 in total) records its
   Battery Regulation article, DIN DKE SPEC 99100 chapter, battery categories and access tier.
   `data/attribute-map.json` links 90 of them to template elements. `passport.completeness` scores
   a passport against the mandatory attributes for its battery category.
3. **Access tiers to IDTA-01004 rules.** Tiers come from Art. 77 and Annex XIII (public,
   legitimate interest, authority, economic operator).
   - A tier is resolved for each element in this order: an element extension, the attribute map,
     the parent's tier, the submodel extension.
   - `tiers.access_rules()` generates a standard Access Rule document from these tiers. The raw
     AAS API, the passport view and MCP all enforce the same rules. A customer who runs
     AASX Server instead can load the same rule file.
   - The tier comes from the `dpp_tier` claim of a JWT or API key.
4. **Finding a passport.** A GS1 Digital Link `/01/{GTIN}[/10/{lot}][/21/{serial}]` resolves as
   follows:
   - The check digit and qualifier order are validated.
   - The request redirects (307) to the most specific passport and reports `X-DPP-Granularity`.
   - With `Accept: application/linkset+json`, the response is an RFC 9264 linkset that uses GS1
     link types.
   - The specific asset IDs `gtin`, `serialNumber` and `batchId` are also registered in AAS
     discovery (`/lookup/shells`).
5. **Trust.**
   - `/dpp/passports/{id}/credential` returns a W3C VC 2.0 credential as `vc+jwt`, aligned with
     UNTP DPP and signed with the server's X.509 key.
   - `/signatures/verify` checks a credential, reports whether it matches the current revision,
     and the revision history shows what changed since signing.
   - `/registry-payload` produces the ESPR Art. 13 registry record (unique product, operator and
     facility identifiers, plus the passport link).
6. **People and machines.**
   - The `?format=html` passport page has no JavaScript and is served with
     `Content-Security-Policy: default-src 'none'`. It uses the same tier filtering.
   - `/mcp` exposes `find_passport`, `get_passport`, `passport_completeness`, `query_submodels`
     and `list_attributes` to LLM agents.

## Design choices

- **Standard library HTTP server.** It keeps the dependency surface to aas-core, cryptography and
  jsonschema, so the whole request path can be audited. The router is a thin layer, so the API
  could be moved onto ASGI without changing the handlers.
- **SQLite with history tables.** Revisions use validity intervals, which gives point-in-time
  reads without event-sourcing every element. The tables are plain, so the schema can move to
  PostgreSQL.
- **Python dict model plus aas-core verification.** Working on JSON dicts keeps both unknown
  fields and lexical forms. aas-core is the referee for constraints, not the storage model.
- **Access rules evaluated in Python.** This avoids compiling them to SQL as AASX Server does.
  It is simpler and exact for FILTER semantics, but slower on large collections (see the
  roadmap).

## Roadmap

- PostgreSQL backend, with Query Language and Access Rule formulas compiled to SQL (the
  AASX Server approach) for large repositories.
- OPC UA companion specification mapping (I4AAS) and MQTT/Kafka event bridges.
- Eclipse Dataspace Connector / Catena-X connector, so passports can be exchanged under usage
  policies.
- ECLASS, IEC CDD and SAMM import for ConceptDescriptions (Package Explorer has importers for
  these).
- A web editor built on the SMT form model; registry federation via `registry.<host>`
  (BaSyx hierarchy).
- More DPP product groups beyond batteries (textiles, iron and steel) once their delegated acts
  and IDTA templates are published.
