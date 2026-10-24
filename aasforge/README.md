# AAS Forge

An Asset Administration Shell (AAS) server and toolkit, built from what we learned by taking apart
the open-source AAS tools, with Digital Product Passports (DPP) built in.

- **The whole AAS API, version-tolerant.**
  - All 235 operations of the AAS API Part 2 v3.2 OpenAPI collection are routed
    ([coverage report](docs/API-COVERAGE.md)), under `/api/v3.1`, `/api/v3.0` and `/api/v3.2`.
  - Metamodel 3.0, 3.1 and 3.2 are read as JSON, XML and AASX.
  - 3.2-only fields (`Batch`, `createdAt`/`updatedAt`, …) are kept in a sidecar, so a 3.2 file goes
    out again intact. Under `/api/v3.2`, request and response bodies are metamodel 3.2.
- **Never loses data silently.** Each import counts objects before and after and reports any loss.
  The defects in the published IDTA templates (an `External` origin relationship, missing parts,
  stray XML text, `xsi:schemaLocation`) are repaired, and each repair is reported.
- **History, signatures, queries and access rules.**
  - Every write is stored as a revision (`$history?date=`, `$audit`, `$recent-changes`).
  - Signed exports are JWS RS256 with `x5c` (`$signed`).
  - The AAS Query Language (IDTA-01002) and Access Rule Language (IDTA-01004) share one evaluator,
    and rule `FILTER`s prune individual elements from responses.
  - Formulas are also compiled to SQL prefilters, which makes queries 21–42× faster on 5,000
    submodels with identical results ([benchmark](docs/BENCHMARK.md)).
- **Storage: SQLite or PostgreSQL, one process or many.** The whole test suite runs on both.
  `serve --workers N` pre-forks N processes on one socket. They share the database, and each follows
  the others' changes (event streams, bridges, rule caches).
- **Operations that run.** `invoke`, `invoke-async` and `$value` variants work. An operation is
  backed by a registered Python function, or forwarded to an allow-listed URL with an
  `invocationDelegation` qualifier.
- **Industrial integration.**
  - **OPC UA:** a live server and NodeSet2 export on the official I4AAS companion specification
    (OPC 30270). Subscriptions follow every change, and optional write-back is authorised by the
    access rules.
  - **MQTT:** a bridge that publishes change events on BaSyx-compatible topics and accepts telemetry
    ingest.
- **Dataspaces (Catena-X).**
  - The Digital Twin Registry access model: `externalSubjectId`, `PUBLIC_READABLE`, and the
    `Edc-Bpn` header from the EDC data plane.
  - DSP endpoints in submodel descriptors.
  - Provisioning through the EDC Management API v3: assets, policies and contracts. Each asset is
    confined to its own routes.
  - The consumer side: catalog, EDR negotiation, and fetching a partner's twins and submodels.
- **Registry federation.** Unknown descriptors are fetched from upstream registries or the BaSyx
  `registry.<host>` hierarchy, with SSRF guards.
- **Semantics.**
  - SAMM aspect models become submodel templates. They match the ESMF generator's own output path
    for path on the Catena-X BatteryPass and PCF models.
  - SAMM JSON payloads become submodel instances and convert back without loss.
  - ECLASS and IEC CDD property tables become IEC 61360 ConceptDescriptions.
- **Digital Product Passports.**
  - Passports are created from the IDTA 02099 / 02035 battery passport templates.
  - 90 of the 93 Battery Pass attributes are mapped to template elements.
  - Access tiers follow the Battery Regulation (public, legitimate interest, authority, economic
    operator) and are enforced everywhere: the passport view, the raw API, queries, packages,
    events, OPC UA and MQTT.
  - Other features: completeness scoring, a GS1 Digital Link resolver, a verifiable-credential
    export, the EU registry payload, a script-free HTML passport page, and MCP tools for LLM
    clients.
- **Web editor** at `/ui`. It browses and edits shells and submodels, creates submodels from forms
  generated from templates, checks them against their templates, and shows history, queries and
  events.
- **Security reviewed twice.** Two internal reviews found 16 and then 10 issues, all fixed.
  `tests/test_security.py` and `tests/test_security2.py` replay each attack.

The [architecture](ARCHITECTURE.md) explains which idea came from which tool, and the evidence for
each gap it fixes.

## Quickstart

```sh
python -m venv .venv && .venv/bin/pip install -r aasforge/requirements.txt
cd aasforge
../.venv/bin/python examples/battery_passport.py           # writes examples/battery-passport.aasx
../.venv/bin/python -m aasforge import examples/battery-passport.aasx
../.venv/bin/python -m aasforge serve --access-rules dpp --secret dev-secret
```

Then:

```sh
open http://localhost:8081/ui/                                         # web editor
curl localhost:8081/api/v3.1/description
curl localhost:8081/dpp/passports                                     # passports, public view
curl -i localhost:8081/01/04012345000016/21/VX86-2026-000417          # GS1 Digital Link → 307 to the passport
TOKEN=$(../.venv/bin/python -m aasforge token --secret dev-secret --claims '{"sub":"inspector","dpp_tier":"authority"}')
curl -H "Authorization: Bearer $TOKEN" localhost:8081/dpp/passports/<base64url aas id>
```

To add PostgreSQL, OPC UA and MQTT:

```sh
../.venv/bin/python -m aasforge --database postgresql://user:pass@db/aasforge serve --access-rules dpp \
    --secret dev-secret --opcua opc.tcp://0.0.0.0:4840/aasforge/ --mqtt mqtt://broker:1883
```

## Command line

| Command | What it does |
|---|---|
| `serve [--workers N] [--opcua ENDPOINT] [--opcua-writable] [--mqtt URL]` | HTTP server: AAS API, `/dpp`, `/01` resolver, `/mcp`, `/ui`; optionally several worker processes, OPC UA and MQTT |
| `import FILE...` | Import AASX, JSON or XML (3.0, 3.1 or 3.2) and print the loss report; exits 2 if anything was lost |
| `export --format json\|xml\|aasx\|nodeset --version 3.0\|3.1\|3.2` | Write the environment (`nodeset`: OPC UA NodeSet2 on I4AAS) |
| `verify FILE` | Repairs, detected version and constraint findings (released aas-core 3.1 verifier) |
| `import-samm TTL --models-root DIR [--payload JSON]` | SAMM aspect model → submodel template + ConceptDescriptions (and an instance) |
| `import-dictionary FILE... [--units FILE]` | ECLASS / IEC CDD property tables (CSV) → ConceptDescriptions |
| `catenax-provision --edc URL --edc-key K --dataplane-key K [--bpn BPN...]` | Register submodels and the registry as EDC assets with contracts |
| `catenax-fetch --edc URL --edc-key K --dsp URL --partner BPN --asset-id name=value [--semantic-id S] [--store]` | Fetch a partner's twin (and submodel) through EDC |
| `token --secret S --claims JSON` | Issue a development HS256 bearer token |
| `sign FILE` / `verify-signature FILE --trust cert.pem` | Sign an AAS object as JWS, or verify one |
| `dpp-create SPEC.json` | Create a passport from a spec (see `examples/battery_passport.py`) |
| `access-rules` | Print the generated IDTA-01004 tier rules for the passports in the store |

Global options: `--data-dir` (default `var`) and `--database` (a SQLite path, `:memory:`, or a
`postgresql://` URL).

## Configuration

These environment variables override the defaults in `aasforge/app.py`:

| Variable | Meaning |
|---|---|
| `AASFORGE_DATA_DIR`, `AASFORGE_DATABASE` | Storage location (SQLite file or `postgresql://` URL) |
| `AASFORGE_HOST`, `AASFORGE_PORT` | Listen address (default `127.0.0.1:8081`) |
| `AASFORGE_PUBLIC_URL` | External base URL, used in descriptors, passport links and resolver redirects |
| `AASFORGE_VERIFICATION` | `reject`, `warn` (default) or `off`: what to do with objects that fail aas-core verification |
| `AASFORGE_ACCESS_RULES` | An IDTA-01004 JSON rule file, `dpp` (generated tier rules), or unset (open server) |
| `AASFORGE_HS256_SECRET`, `AASFORGE_ISSUER`, `AASFORGE_AUDIENCE` | Bearer token checks (tokens must carry `exp`) |
| `AASFORGE_API_KEYS` | JSON object mapping each API key to its claims, e.g. `{"k1": {"sub": "erp", "dpp_tier": "economic-operator"}}` |
| `AASFORGE_OPERATION_DELEGATION` | Comma-separated URL prefixes that `invocationDelegation` may call (default: none) |
| `AASFORGE_MAX_BODY`, `AASFORGE_MAX_PACKAGE` | Request body limit (64 MB) and total uncompressed AASX limit (512 MB) |
| `AASFORGE_OPCUA_ENDPOINT`, `AASFORGE_OPCUA_WRITABLE` | Serve OPC UA (I4AAS), optionally with authorised write-back |
| `AASFORGE_MQTT_URL`, `AASFORGE_MQTT_REPOSITORY`, `AASFORGE_MQTT_INGEST_CLAIMS` | MQTT bridge; the claims that telemetry writes are authorised with |
| `AASFORGE_CATENAX` | JSON `{"bpn": ..., "dataplane_url": ..., "dsp_url": ...}`: Catena-X DTR mode |
| `AASFORGE_FEDERATION` | JSON `{"upstreams": [...], "hierarchy_domains": [...]}`: registry federation |

Credentials are accepted only in headers, never in the query string. CORS is off unless origins are
listed in the configuration.

## Tests

```sh
cd aasforge/tests && ../../.venv/bin/python -m unittest discover -s .
AASFORGE_TEST_DATABASE=postgresql://user:pass@localhost/db ../../.venv/bin/python -m unittest discover -s .
```

There are 140 tests:

- **Versioning and AASX:** version detection, the 3.2 sidecar, repairs, lossless import of real IDTA
  and Catena-X packages, and deterministic writing.
- **Elements and templates:** idShortPath and modifiers, and SMT template instantiation.
- **Query and security:** the Query Language spec examples, access rules with `FILTER`, JWT and JWS,
  and the replayed security-review attacks.
- **SQL prefilter:** 600 generated queries that must never lose a match.
- **API:** the full API end to end over HTTP.
- **DPP:** tier filtering everywhere, the resolver, the credential, MCP and completeness.
- **OPC UA:** the export loads next to the official I4AAS NodeSet; live subscriptions, authorised
  writes, FileType reads and operations.
- **MQTT:** against a real mosquitto broker.
- **Catena-X:** DTR visibility, data-plane scoping and EDC provisioning. A dataspace test fetches a
  partner twin through a consumer EDC and a provider data plane (both simulated).
- **Federation:** upstream registries and the SSRF guards.
- **Importers:** SAMM templates against the ESMF generator's output, payload round trips, ECLASS and
  CDD tables.
- **Web editor:** a browser test with Playwright and Chromium.
- **Workers:** three processes on SQLite and PostgreSQL. Concurrent writes give gap-free
  revisions, and an event stream on one worker sees every worker's changes.

Tests needing optional parts (asyncua, paho-mqtt with mosquitto, rdflib, Node Playwright) are
skipped when those parts are missing. `tools/api_coverage.py` regenerates the coverage report from
the OpenAPI file in [admin-shell-io/aas-specs-api](https://github.com/admin-shell-io/aas-specs-api).
`tools/bench_query.py` reruns the benchmark.

## Layout

```
aasforge/            the package (≈9.7k lines)
  versioning.py aasx.py canonical.py db.py store.py sqlfilter.py repository.py elements.py smt.py
  formula.py security.py signing.py http.py api.py app.py __main__.py
  opcua.py mqtt.py catenax.py federation.py samm.py dictionary.py
  dpp/               catalog, tiers, passport, gs1, credential, viewer, service (routes, resolver, MCP)
  ui/                web editor (index.html, app.js, style.css)
  data/              Query Language schema, Battery Pass attributes, IDTA templates, I4AAS NodeSet, SAMM units
docs/                teardowns of BaSyx Java, AASX Server and Package Explorer; API coverage; benchmark
examples/            fictional Voltaris EV-86 battery passport
tests/               unittest suite and fixtures (IDTA, Catena-X; see tests/fixtures/README.md)
tools/               build_dpp_data.py, build_samm_units.py, api_coverage.py, bench_query.py
```

## Data and licences

- `aasforge/data/templates/` are IDTA submodel templates (CC-BY-4.0, admin-shell-io/submodel-templates).
  Provenance is recorded in `manifest.json`.
- `battery-attributes.json` is derived from the Battery Pass Data Attribute Longlist (CC-BY-4.0).
- `query-json-schema.json` comes from admin-shell-io/aas-specs-api.
- `opcua/Opc.Ua.I4AAS.NodeSet2.xml` is from OPCFoundation/UA-Nodeset (MIT).
- `samm-units.json` holds symbols from the ESMF SAMM unit catalogue (MPL-2.0).
- The test fixtures under `samm/` are Eclipse Tractus-X aspect models (CC-BY-4.0).
- The Voltaris company and its product in the example are fictional.

## Status

This is a research implementation. It has been security-reviewed twice, and every finding is
covered by a test, but it has had no third-party audit or production hardening. The
[roadmap](ARCHITECTURE.md#roadmap) lists what remains.
