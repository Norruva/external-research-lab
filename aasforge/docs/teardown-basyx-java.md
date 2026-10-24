# Teardown: Eclipse BaSyx Java server SDK

Source: `eclipse-basyx/basyx-java-server-sdk` at `5dec385` (2026-10-22), read statically. The
project README now points users to "BaSyx Go" for ready-to-run servers.

## Structure
- Each component ships as `-core` (interfaces), `-backend` (Spring `CrudRepository`), `-backend-inmemory` / `-backend-mongodb`,
  `-http`, `-feature-*` (decorators), `.component` (Spring Boot app), `-client` and `-tck`.
- Components:
  - aasservice / submodelservice (single resource; idShortPath parsing; ValueOnly mappers)
  - aasrepository, submodelrepository, conceptdescriptionrepository
  - aasenvironment (`/serialization`, `/upload`, preconfiguration loading)
  - aasregistry / submodelregistry (OpenAPI-generated, separate code style, log or Kafka events)
  - aasdiscoveryservice
  - aasxfileserver
  - aasdigitaltwinregistry (registry plus discovery in one app)

## Ideas worth taking
- **Feature decorators.** `BaSyxFeature.decorate(Factory)`: authorization, MQTT, Kafka, registry
  integration, discovery integration, search and operation delegation each wrap the repository
  interface, enabled per component or globally by properties.
- **Registry integration with compensation.** Creating a shell registers its descriptor; if that
  fails, the shell is deleted again.
- **Hierarchy federation.** An unknown ID is forwarded to a registry derived from the ID's host
  (`registry.<host>`).
- **Operation delegation.** A Qualifier `invocationDelegation` names the URL that `/invoke` is
  forwarded to.
- **Keyset pagination.** The cursor is base64url of the last ID.
- **Zip-bomb protection** on environment import.
- **Events.** MQTT topics such as `sm-repository/<repo>/submodels/<b64id>/submodelElements/<path>/updated`;
  Kafka envelopes such as `SME_UPDATED` carrying the full object.
- **Query Language** (IDTA JSON form) through an experimental Elasticsearch indexer.

## Gaps found (and fixed in aasforge)
- **API version.** Implements API V3.0/3.0.1 only. `$reference`/`$path` are missing, and
  `level`/`extent` are accepted but ignored.
- **No async operations** (TODO #566).
- **RBAC only, no ABAC.** Rules are keyed by (role, action, type), so later rules overwrite earlier
  ones silently. idShortPath matching is exact (no prefix). Search filters are lost when the
  caller lacks wildcard read.
- **Access-rule (IDTA-01004) model classes exist but nothing uses them.**
- **Inconsistency on failure.** Search indexing, the Kafka send and registry sync run inline with
  no outbox, so a failure leaves components inconsistent. Locks are JVM-local.
- **No DPP, battery or Catena-X specifics.**
