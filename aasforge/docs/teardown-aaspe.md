# Teardown: Eclipse AASX Server and AASX Package Explorer

Sources: `eclipse-aaspe/server` at `b092dbe` (2026-10-14) and `eclipse-aaspe/package-explorer` at
`87120be`, read statically. Their compiled core libraries were compared with REA in
`dpp-rea/aas-compat`.

## AASX Server
- **Pipeline.** ExceptionMiddleware → `UsePathBase("/api/v3.0")` → auth → CORS (any origin) →
  Swagger → Blazor UI, MVC controllers, optional GraphQL, and MCP endpoints (`/mcp`, `/mcp-basic`,
  `/mcp-simple`).
- **Persistence.**
  - EF Core 9 on SQLite (FTS5 trigram index for substring search) or PostgreSQL.
  - Submodel elements (SMEs) store a parent foreign key plus a denormalised, indexed `IdShortPath`.
  - Complex attributes are stored as JSON strings.
  - The old in-memory mode is unmaintained.
- **Write serialisation.** A queue served by 4 workers; writers wait for reads to drain using a
  `Thread.Sleep` poll.
- **Change tracking.** `TimeStampCreate`, `TimeStamp`, `TimeStampTree` and `TimeStampDelete` are
  patched into the vendored `AasCore.Aas3_1`. The tree timestamp is propagated to parents, which
  enables "changed since" queries. `DiaryData` is the older per-Referable journal.
- **Query.** The AAS Query Language JSON is compiled to SQL through an Irony grammar. Endpoints:
  `/query/shells|submodels|submodel-elements`, plus `$sql` and debug variants.
- **Security.** IDTA Access Rule Language (`ACL`/`OBJECTS`/`FORMULA`/`FILTER` with the `DEF*`
  reuse blocks). FORMULA and FILTER are compiled to SQL conditions and merged into every query,
  giving row-level, per-element filtering. JWTs are checked against trust lists.
- **API.** Labelled V3.1 at base `/api/v3.0`. All modifiers are supported, including
  `level`/`extent`. The cursor is a plain integer offset. Non-standard `$sign` (JWS RS256),
  `/events` sync and MCP tools (`aas_query`, `aas_find_product`, …).
- **Product carbon footprint roll-up** over the bill of materials (`AasxTaskService`); the UI is
  titled "Digital Product Passport Showcase".
- **Weaknesses.**
  - Operation `invoke`, `invoke-async` and results are all `NotImplementedException`.
  - About 175 TODOs; a 2.5k-line `Program.cs`; shared static state.
  - AllowAnyOrigin CORS; credentials accepted in the query string (`?bearer=`, `?_up=`).
  - Template listing only works with security off.

## AASX Package Explorer
- **UI abstraction.** AnyUi renders the same editors to WPF or Blazor HTML.
- **Plugins.** DLL plus a `*.plugin` tag file, loaded by reflection. Calls go through string
  actions; options are keyed by semanticId.
- **Viewers and forms.** DigitalNameplate, DocumentShelf (VDI 2770) and TechnicalData views.
  KnownSubmodels is a template catalogue; GenericForms builds forms from JSON form descriptions.
- **Import/export.**
  - AutomationML; ECLASS and IEC CDD dictionaries.
  - SAMM 1.0, 2.0 and 2.1 Turtle; BAMM RDF.
  - OPC UA nodesets; BMEcat; CSV; W3C Thing Description; JSON Schema from templates.
  - Table export to TSF, LaTeX, Word, Excel, Markdown and AsciiDoc.
- **SMT qualifiers.** `SMT/Cardinality`, `EitherOr`, `DefaultValue`, `ExampleValue`,
  `AllowedValue`, `AllowedRange`, `AllowedIdShort`, `RequiredLang`, `AccessMode` and
  `InitialValue`. They can be moved onto ConceptDescriptions as an `smt-attribute-set` extension.
- **Validation.** Assess SMT, Compare SMT, Check & Fix, sign and validate.
- **No undo stack found.**

## Ideas taken into aasforge
- One formula evaluator for both the Query Language and Access Rules (shared grammar); FILTER is
  applied to responses for row-level, per-element filtering.
- Server-maintained timestamps per identifiable, plus append-only history (instead of patching the
  metamodel types).
- `$signed` JWS (RS256 with `x5c`), as standardised in API 3.2.
- A template engine driven by SMT qualifiers: instantiate from a template, then check
  cardinality, allowed values and either-or groups.
- Product carbon footprint roll-up over the bill of materials for the passport.
- An MCP-style tool endpoint for LLM clients (read-only, access-rule-filtered).
- Avoided: an integer cursor (aasforge uses opaque keyset cursors), any-origin CORS by default,
  and credentials in the query string.
