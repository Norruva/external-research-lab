# AAS Forge

An Asset Administration Shell (AAS) server and toolkit, built from what we learned by taking apart
the open-source AAS tools, with Digital Product Passports (DPP) built in.

- **The whole AAS API, version-tolerant.** All 235 operations of the AAS API Part 2 v3.2 OpenAPI
  collection are routed ([coverage report](docs/API-COVERAGE.md)), under `/api/v3.1`, `/api/v3.0` and
  `/api/v3.2`. Metamodel 3.0, 3.1 and 3.2 are read as JSON, XML and AASX. 3.2-only fields (`Batch`,
  `createdAt`/`updatedAt`, …) are kept in a sidecar, so a 3.2 file goes out again intact. Under
  `/api/v3.2`, request and response bodies are metamodel 3.2.
- **Never loses data silently.** Each import counts objects before and after and reports any loss.
  The defects in the published IDTA templates (an `External` origin relationship, missing parts,
  stray XML text, `xsi:schemaLocation`) are repaired, and each repair is reported.
- **History, signatures, queries and access rules.** Every write is stored as a revision
  (`$history?date=`, `$audit`, `$recent-changes`). Signed exports are JWS RS256 with `x5c`
  (`$signed`). The AAS Query Language (IDTA-01002) and Access Rule Language (IDTA-01004) share one
  evaluator, and rule `FILTER`s prune individual elements from responses.
- **Operations that run.** `invoke`, `invoke-async` and `$value` variants work. An operation is
  backed by a registered Python function or forwarded with an `invocationDelegation` qualifier.
- **Digital Product Passports.**
  - Passports are created from the IDTA 02099 / 02035 battery passport templates.
  - The 93 attributes of the Battery Pass longlist are mapped to template elements (90 of them so
    far).
  - Access tiers follow the Battery Regulation (public, legitimate interest, authority, economic
    operator) and are enforced on both the passport view and the raw AAS API.
  - Other features: completeness scoring, a GS1 Digital Link resolver, a verifiable-credential
    export, the EU registry payload, a script-free HTML passport page, and MCP tools for LLM
    clients.

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
curl localhost:8081/api/v3.1/description
curl localhost:8081/dpp/passports                                     # passports, public view
curl -i localhost:8081/01/04012345000016/21/VX86-2026-000417          # GS1 Digital Link → 307 to the passport
curl -H 'Accept: application/linkset+json' localhost:8081/01/04012345000016/21/VX86-2026-000417
TOKEN=$(../.venv/bin/python -m aasforge token --secret dev-secret --claims '{"sub":"inspector","dpp_tier":"authority"}')
curl -H "Authorization: Bearer $TOKEN" localhost:8081/dpp/passports/<base64url aas id>
```

Open `/dpp/passports/<id>?format=html` in a browser for the human-readable passport page.

## Command line

| Command | What it does |
|---|---|
| `serve` | HTTP server: AAS API, `/dpp`, `/01` resolver, `/mcp` |
| `import FILE...` | Import AASX, JSON or XML (3.0, 3.1 or 3.2) and print the loss report; exits 2 if anything was lost |
| `export --format json\|xml\|aasx --version 3.0\|3.1\|3.2` | Write the environment (with `--aas ID` to limit it) |
| `verify FILE` | Repairs, detected version and constraint findings (released aas-core 3.1 verifier) |
| `token --secret S --claims JSON` | Issue a development HS256 bearer token |
| `sign FILE` / `verify-signature FILE --trust cert.pem` | Sign an AAS object as JWS, or verify one |
| `dpp-create SPEC.json` | Create a passport from a spec (see `examples/battery_passport.py`) |
| `access-rules` | Print the generated IDTA-01004 tier rules for the passports in the store |

Global options: `--data-dir` (default `var`) and `--database` (a SQLite path, or `:memory:`).

## Configuration

These environment variables override the defaults in `aasforge/app.py`:

| Variable | Meaning |
|---|---|
| `AASFORGE_DATA_DIR`, `AASFORGE_DATABASE` | Storage location |
| `AASFORGE_HOST`, `AASFORGE_PORT` | Listen address (default `127.0.0.1:8081`) |
| `AASFORGE_PUBLIC_URL` | External base URL, used in descriptors, passport links and resolver redirects |
| `AASFORGE_VERIFICATION` | `reject`, `warn` (default) or `off`: what to do with objects that fail aas-core verification |
| `AASFORGE_ACCESS_RULES` | An IDTA-01004 JSON rule file, `dpp` (generated tier rules), or unset (open server) |
| `AASFORGE_HS256_SECRET`, `AASFORGE_ISSUER`, `AASFORGE_AUDIENCE` | Bearer token checks |
| `AASFORGE_API_KEYS` | JSON object mapping each API key to its claims, e.g. `{"k1": {"sub": "erp", "dpp_tier": "economic-operator"}}` |

Credentials are accepted only in headers, never in the query string. CORS is off unless origins are
listed in the configuration.

## Tests

```sh
cd aasforge/tests && ../../.venv/bin/python -m unittest discover -s .
```

There are 54 tests:

- **Versioning:** 3.0, 3.1 and 3.2 detection, XML namespaces, and the 3.2 sidecar round trip.
- **AASX:** repairs, lossless import of real IDTA and Catena-X packages, and deterministic writing.
- **Elements and templates:** idShortPath and modifiers, and SMT template instantiation.
- **Query and security:** the Query Language spec examples, access rules with `FILTER`, tokens and
  JWS.
- **API:** the full API end to end over HTTP.
- **DPP:** tier filtering on the passport view and the raw API, the resolver, the credential, MCP
  and completeness.

To regenerate the API coverage report, pass the OpenAPI file from
[admin-shell-io/aas-specs-api](https://github.com/admin-shell-io/aas-specs-api):

```sh
python tools/api_coverage.py path/to/Entire-API-Collection/V3.2.yaml -o docs/API-COVERAGE.md
```

## Layout

```
aasforge/            the package (≈5.6k lines, standard library + aas-core + cryptography + jsonschema)
  versioning.py aasx.py canonical.py store.py repository.py elements.py smt.py
  formula.py security.py signing.py http.py api.py app.py __main__.py
  dpp/               catalog, tiers, passport, gs1, credential, viewer, service (routes, resolver, MCP)
  data/              Query Language JSON schema, Battery Pass attributes, attribute map, IDTA templates
docs/                teardowns of BaSyx Java, AASX Server and Package Explorer; API coverage
examples/            fictional Voltaris EV-86 battery passport
tests/               unittest suite and CC-BY-4.0 fixtures (see tests/fixtures/README.md)
tools/               build_dpp_data.py (template and attribute bundle), api_coverage.py
```

## Data and licences

- `aasforge/data/templates/` are IDTA submodel templates (CC-BY-4.0, admin-shell-io/submodel-templates).
  Provenance is recorded in `manifest.json`.
- `battery-attributes.json` is derived from the Battery Pass Data Attribute Longlist (CC-BY-4.0).
- `query-json-schema.json` comes from admin-shell-io/aas-specs-api.
- The Voltaris company and its product in the example are fictional.

## Status

This is a research implementation that has not been security-reviewed. Storage is SQLite and runs
in a single process; the [roadmap](ARCHITECTURE.md#roadmap) lists what production use needs.
