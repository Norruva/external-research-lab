# AAS tool compatibility deep dive (P3 → promoted)

The deep dive on the "AAS tools" idea in [`../IDEAS.md`](../IDEAS.md), run on 23 Oct 2026. It
asks whether a passport that Norruva writes as an Asset Administration Shell (AAS) will be read
correctly by the open-source tools customers and dataspaces actually run, and what breaks.

## Verdict

AAS works for DPP exchange today only if Norruva pins a version and a profile:

- **Pin AAS metamodel 3.1** (XML namespace `https://admin-shell.io/aas/3/1`, API `/api/v3.1`).
- **Don't use 3.2 features yet.** Not one tested reader accepts 3.2 `AssetKind.Batch`, and none
  keeps 3.2 `createdAt`/`updatedAt`.
- **Treat every reader as lossy until proven otherwise.**

The official IDTA DPP and battery passport templates mostly work. However, three of the eight
latest-version battery/DPP template packages (IDTA 02035-6 Material Composition, IDTA 02099
DPP Part 1, and IDTA 02035-4 Technical Data) can't be opened by at least one major tool
because of packaging defects. The IDTA 02023 Carbon Footprint 1.0.2 AASX has an XML typo that
AASX Server rejects.

## What was tested

| Tool | Version tested | Metamodel | How it was run |
|---|---|---|---|
| Eclipse AASX Server (`eclipse-aaspe/server`) | `b092dbe` (2026-10-14), vendored `AasCore.Aas3_1` 1.0.0-rc1 + NuGet `AasCore.Aas3_0` 1.0.4 | 3.1 (reads 3.0 via `ConvertFromV30`) | `dotnet-aasx/` console over the server's own `AasxCsharpLibrary` loader, plus its verifier |
| Eclipse AASX Package Explorer (`eclipse-aaspe/package-explorer`) | `87120be` (2026-07-06) | 3.1 | Same core library. REA showed its `AasCore.Aas3_1` is member-for-member identical to the server's (below), so server results apply |
| Eclipse BaSyx Python SDK | 2.2.0 (PyPI) | 3.1.2 | `run_python_tools.py`, strict (`failsafe=False`) and default modes, JSON/XML/AASX readers |
| Eclipse AAS4J (Java, used by BaSyx Java server SDK) | 1.0.5 and 2.0.2 (Maven Central) | 3.0 / 3.1 | `java-aas4j/` harness, JSON/XML/AASX deserializers |
| aas-core-works reference SDKs | `aas-core3.0` 1.1.4, `aas-core3.1` 1.0.0 (PyPI) | 3.0 / 3.1 | Deserialization + full constraint verification |
| IDTA JSON Schema / XSD | 3.0.9, 3.1.2, 3.2.0 (`admin-shell-io/aas-specs-metamodel`) | 3.0 / 3.1 / 3.2 | jsonschema (2019-09) and lxml XSD |

**Corpus.**
- 52 latest-version IDTA submodel templates relevant to passports (CC-BY-4.0, from
  `admin-shell-io/submodel-templates` `f379003`):
  - IDTA 02099 DPP Part 1
  - all IDTA 02035 Digital Battery Passport parts
  - Carbon Footprint, Digital Nameplate, Handover Documentation, Technical Data, Contact
    Information, Bills of Material and Plastic Waste
- The Catena-X `battery_pass` 6.0.0 generated AAS (XML and AASX).
- 11 generated **probes** (`probes/`, from `make_probes.py`), each changing one thing across a
  metamodel version boundary.

## Version differences that matter (schema diff, 3.0.9 → 3.1.2 → 3.2.0)

- **3.0 → 3.1**
  - The XML namespace changes `aas/3/0` → `aas/3/1`; JSON has no version marker at all.
  - `idShort` may contain hyphens.
  - String limits rise (2000 → 2048; `contentType` 100 → 128).
  - `AssetKind.Role` is added.
  - `File`/`Blob` `contentType` becomes optional.
  - `File.value` gains a URI-reference pattern, so paths with spaces become invalid.
  - Constraints AASd-090, AASd-120 and AASc-3a-002 are dropped, and AASc-002 is added.
- **3.1 → 3.2**
  - `AssetKind.Batch` is added (relevant to batch-level passports).
  - `AdministrativeInformation.createdAt`/`updatedAt` are added.
  - The `DataSpecificationUom` data specification is added.

## Results

Full per-file matrix: [`MATRIX.md`](MATRIX.md). Raw rows: `results/`.

### Probes: how each tool handles each boundary

| Probe | Schema | aas-core | BaSyx Py strict | BaSyx Py default | AAS4J 1.0.5 | AAS4J 2.0.2 | AASX Server |
|---|---|---|---|---|---|---|---|
| 3.0 XML namespace | ✓ | ✓ (3.0) | ✗ | **⚠ 0/2 kept** | ✓ | ✓ | ✓ |
| 3.2 XML namespace | ✓ | ✗ | ✗ | **⚠ 0/2 kept** | ✓ | ✓ | ✗ |
| `AssetKind.Role` (3.1) | ✓ | ✓ | ✓ | ✓ | ✗ | ✓ | ✓ |
| `File.value` with space (3.0-valid) | ✓ (3.0) | ✗ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `AssetKind.Batch` (3.2) | ✓ | ✗ | ✗ | **⚠ 1/2 kept** | ✗ | ✗ | ✗ |
| `createdAt`/`updatedAt` (3.2) | ✓ | ✗ | ✓ but drops them | ✓ but drops them | ✓ (field not in model) | ✓ but drops them | ✗ |

### Key findings

1. **Silent data loss in BaSyx Python's default mode.**
   - `read_aas_xml_file` and `AASXReader` default to `failsafe=True`. On a 3.0 or 3.2 XML
     document they report success and return **zero** objects.
   - Affected files include the Catena-X BatteryPass XML (0 of 194 kept) and nine 3.0 IDTA AASX
     templates (0 of 10–50 kept each).
   - An AAS with `assetKind: Batch` is dropped silently (1 of 2 kept).
   - Any importer built on BaSyx Python must pass `failsafe=False` or compare object counts.
2. **Version detection is the consumer's problem.** JSON carries no version marker, and the
   JSON Schemas allow unknown properties. So 3.2 `createdAt` validates against the 3.0 and 3.1
   schemas, yet aas-core and AASX Server reject it, and BaSyx and AAS4J drop it silently. Schema
   validation alone is not a compatibility check.
3. **Same file, different verdicts.**
   - AASX Server and Package Explorer verify with a vendored `AasCore.Aas3_1` **1.0.0-rc1**.
     That release candidate requires `File.value` to be an RFC 8089 `file:` URI and applies
     AASc-3a-010 differently from the released `aas-core3.1` 1.0.0.
   - Every IDTA template, and the minimal baseline probe (`/aasx/files/…pdf`), gets
     verification errors in AASX Server, while the released verifier passes them.
   - AAS4J does no constraint verification at all.
4. **Published template defects.**
   - **IDTA 02099 DPP Part 1 and IDTA 02035-6 Material Composition:** the root `aasx-origin`
     relationship is declared `TargetMode="External"`, so BaSyx can't find the package origin
     and rejects both. AAS4J and AASX Server tolerate it. 02099 also lists a supplementary file
     (`/aasx/files/title-page.png`) that isn't in the package.
   - **IDTA 02035-4 Technical Data:** references a thumbnail part missing from the package, so
     BaSyx rejects it in both modes.
   - **IDTA 02023 Carbon Footprint 1.0.2 (3.0 and 3.1 AASX):** contains
     `<value>OneToMany</value>value>`. XSD validation and AASX Server reject it; aas-core 3.1,
     BaSyx and AAS4J ignore the stray text.
   - **IDTA 02035-5 Product Condition and 02035-6:** fail 3.1 schema/verification (an empty
     `symbol`, an empty `preferredName` text, measures without a unit).
   - **Catena-X BatteryPass 6.0.0 AAS export (3.0):** carries `xsi:schemaLocation`, which
     strict aas-core 3.0 rejects. It also has 422 constraint findings in AASX Server, the first
     being AASd-131 (no global or specific asset ID).
   - These defects are worth reporting upstream (IDTA submodel-templates, Catena-X sldt).
5. **Round trips are not byte-stable.**
   - Re-serializing the JSON corpus produced several changes:
     - BaSyx Python rewrites `xs:double` and `xs:dateTime` lexical forms
       (`"1007"` → `"1007.0"`, `…Z` → `…+00:00`).
     - BaSyx removes `idShort` from children of `SubmodelElementList`. That enforces 3.0's
       AASd-120, which 3.1 removed, so 3.1-legal data is lost (IDTA 02035-5).
     - Both tools insert defaults such as `orderRelevant: true`.
     - Both drop 3.2 timestamps.
   - AAS4J changed no values.
   - Any signature, hash or "verified" badge over a passport must be computed over a canonical
     form, not over the bytes a given tool wrote.
6. **DPP-specific templates are 3.1-only.** IDTA 02099 and all of IDTA 02035 are published
   only for the `aas/3/1` namespace. Generic templates ship both 3.0 and 3.1 variants, and the
   Catena-X BatteryPass export is 3.0. A Norruva DPP stack therefore needs to read 3.0 and write
   3.1.
7. **Granularity has two homes.** IDTA 02099 `DppMetadata.granularity` expresses
   model/batch/item level as a property. Metamodel 3.2 adds `AssetKind.Batch`, which no tested
   reader accepts. Use the 02099 property until readers catch up.

## REA findings

- `rea compare-managed-members` on Package Explorer's and AASX Server's compiled
  `AasCore.Aas3_1.dll` found 6,316 of 6,316 members unchanged (3,884 with exact IL). The
  20k-line source diff between the two vendored copies is formatting, so the two Eclipse tools
  verify identically.
  - REA also surfaced that the vendored "aas-core 1.0.0-rc1" is patched with an extra
    `AdminShellNS.DiaryData` type. It is a fork, not the upstream release candidate.
  - Evidence ID: `ev_3e96b9e288229052cd279cf3e483b95f9d0ff41d8410fc96b69eb27e5b9c8e17`.
- `rea inspect-managed-members` user-string inventories gave the constraint IDs each .NET
  verifier embeds: 37 for `AasCore.Aas3_0` 1.0.4 and 35 for vendored `AasCore.Aas3_1` rc1. Both
  match the Python aas-core sets for the same version (`results/constraint-coverage.json`). The
  rc1 vs. release differences are therefore in the rule bodies, not the constraint list.
- `rea inspect-artifact` on AASX packages only inventories the ZIP. It has no OPC/AAS
  semantics, so relationship and `TargetMode` checks come from `fingerprint.py`.

## DPP field mapping (where battery passport data lives in AAS)

| Battery Regulation content | IDTA template | Submodel `idShort` |
|---|---|---|
| Passport ID, product ID, granularity, status, last update, operator/facility IDs | 02099-1 DPP Part 1 | `DppMetadata` |
| Manufacturer, serial, dates, facility, lifecycle stage, category, mass | 02035-1 | `BatteryNameplate` |
| EU DoC, test reports, manuals | 02035-2 | `HandoverDocumentation` |
| Carbon footprint (per lifecycle stage) | 02035-3 (uses 02023) | `CarbonFootprint` |
| Rated capacity, voltage, power, efficiency, resistance, lifetime | 02035-4 | `TechnicalData` |
| State of charge, full cycles, SOCE, remaining energy/capacity, negative events, accidents | 02035-5 | `ProductCondition` |
| Material composition, hazardous substances | 02035-6 | (template `MaterialComposition`) |
| Dismantling, spare parts, recycled/renewable content, safety, end of life | 02035-7 | `Circularity` |

## Norruva import/export checklist

1. **Write** AAS 3.1: XML namespace `aas/3/1`, IDTA 02099 + 02035 semantic IDs, AASX with a
   root `aasx-origin` relationship that is *internal* (no `TargetMode="External"`), and every
   referenced supplementary part present in the package.
2. **Read** 3.0 and 3.1. Detect the version from the XML namespace, or for JSON try 3.1 then 3.0
   with aas-core. Up-convert 3.0 to 3.1 (as AASX Server's `ConvertFromV30` does), and refuse
   3.2 until a reference SDK exists.
3. **Fail loudly.** Never use BaSyx Python default `failsafe=True` for imports. Always compare
   identifiable counts before and after reading.
4. **Verify** with the released `aas-core3.1` (Python, or the C# 1.0.0 release, not rc1) and
   report AASX Server rc1 findings separately as "may warn in AASX Server / Package Explorer".
5. **Canonicalize before signing.** Normalize numeric and dateTime lexical forms and element
   order before hashing or signing passport content.
6. **Avoid fragile constructs:** spaces in `File.value`, `idShort` on `SubmodelElementList`
   children (BaSyx strips them), 3.2 fields, and `xsi:schemaLocation` on the root element.
7. **Regression suite:** run `run_python_tools.py`, `java-aas4j` and `dotnet-aasx` on every
   export, and re-run when any tool releases.

## Reproduce

```bash
# corpus: IDTA templates (submodel-templates published/), Catena-X gen/BatteryPass*.{xml,aasx}, probes
python3 make_probes.py corpus/probes          # needs aas-core3.0
python3 fingerprint.py corpus > results/fingerprints.json
python3 run_python_tools.py corpus schemas > results/results-python.json
#   schemas/{v3.0.9,v3.1.2,v3.2.0}/{aas.json,AAS.xsd} from admin-shell-io/aas-specs-metamodel tags
(cd java-aas4j && mvn -Daas4j.version=2.0.2 package dependency:copy-dependencies -DoutputDirectory=target/lib)
java -cp "java-aas4j/target/aas4j-compat-0.1.0.jar:java-aas4j/target/lib/*" norruva.aascompat.Main corpus 2.0.2 rt-aas4j
dotnet build dotnet-aasx -c Release -p:AasxLibrary=<server>/src/AasxCsharpLibrary/AasxCsharpLibrary.csproj
dotnet dotnet-aasx/bin/Release/net9.0/AasxCompat.dll corpus aasx-server-lib-b092dbe
python3 roundtrip_diff.py basyx-write corpus rt-basyx
python3 roundtrip_diff.py diff corpus rt-basyx basyx-python-2.2.0
python3 build_matrix.py results > MATRIX.md
```

Limits:
- Readers were tested as libraries, not as running REST servers (no Docker daemon here), so
  API Part 2 behaviour is not covered.
- Package Explorer itself is a Windows WPF application; it was covered through its shared core
  library and REA comparison, not by opening its UI.
