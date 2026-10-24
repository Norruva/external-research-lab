# Test fixtures

Unmodified copies of published packages used as regression inputs, with the defects aasforge repairs:

| File | Source | Defect exercised |
|---|---|---|
| `idta-02099-1-dpp.aasx` | IDTA 02099-1 Digital Product Passport Part 1 v1.0.2, `admin-shell-io/submodel-templates` `f379003` | aasx-origin relationship `TargetMode="External"`; missing `/aasx/files/title-page.png` |
| `idta-02035-6-material-composition.aasx` | IDTA 02035-6 DBP Material Composition v1.0.2 | aasx-origin `TargetMode="External"` |
| `idta-02023-carbon-footprint-v31.aasx` | IDTA 02023 Carbon Footprint 1.0.2 (for metamodel 3.1) | stray text `value>` inside a qualifier |
| `catenax-batterypass-6.0.0.aasx` | Catena-X `io.catenax.battery.battery_pass` 6.0.0 generated AASX, `eclipse-tractusx/sldt-semantic-models` `e3d6c3e` | AAS 3.0 XML namespace |

All are licensed CC-BY-4.0 by their publishers (IDTA; Catena-X Automotive Network e.V. / Eclipse Tractus-X contributors).
The version-boundary probes are read from `dpp-rea/aas-compat/probes`.

`samm/` holds SAMM aspect models from
[eclipse-tractusx/sldt-semantic-models](https://github.com/eclipse-tractusx/sldt-semantic-models)
(CC-BY-4.0, see `samm/LICENSE`): BatteryPass 6.1.0 and PCF 10.0.0 with the models they import,
the example payloads (`gen/*.json`) and the AAS output of the ESMF generator (`gen/*-aas.xml.gz`,
gzipped, used as the reference for the SAMM to AAS mapping).
