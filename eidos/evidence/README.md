# Evidence index

These are completed cloud-run receipts and aggregate results, imported by hash. They contain no raw benchmark cases, gold rows, per-case predictions, model weights, existing credentials or application state. The authored synthetic demo lives separately in `../examples`.

- [Source provenance](../SOURCE-PROVENANCE.json): copied file paths, sizes, hashes and source archive identity.
- [Historical application test](owned-training/application-v1/execution-summary.json): fresh owned-data training, 18 API / 11 real Chromium / 9 recovery checks, preserved profile checks and namespace/network receipts.
- [Training unit result](owned-training/unit-v1/result.json): 12 authored-fixture methods.
- [Legacy wrapper interpretation](owned-training/application-evidence-interpretation-v1.json): two new synthetic registries were built; preinstalled benchmark models were not refitted.
- [Final remaining-cohort review](remaining-review/independent-aggregate-review-v1.json): aggregate arithmetic and recorded bindings; no quality rerun or bootstrap regeneration.
- [Training kit REA review](owned-training/rea-integrity-review-v1.json): all 35 source-kit member hashes/sizes matched.
- [Comparison packet REA review](remaining-review/rea-integrity-review-v1.json): all 32 packet member hashes/sizes matched.
- [Original comparison packet](comparison/README.md): exact 32-member tree, including controls, scope-specific approval pins, plans, aggregate scores and pilot evidence. Its MANIFEST binds that nested tree only.
- [Historical release receipts](releases): omitted binary/application archives, not GitHub releases or proof they were uploaded.

Historical absolute paths and some snapshot links refer to the original cloud workspace. Do not automatically run its reference/fit/control scripts or treat old resource approvals as permission to repeat consumed evaluations. GitHub delivery checks are recorded separately in `repository-validation.json` after the source checkout is exercised.

Latest implementation: [owned-linear validation](owned-linear/validation-result.json), with 24 unit methods, all nine finite-difference coordinates, 17 memory and 16 linear real-service smoke checks. [Linear training documentation](../docs/LINEAR-TRAINING.md). All 84 imported source members and six compared-model files remain unchanged. These authored-fixture checks establish implementation mechanics, not a new accuracy or rival result. The earlier `repository-validation.json` remains the historical initial source import.

REA also passively inspected the [new 52-member linear-training source kit](owned-linear/source-kit-receipt.json); [independent hash/size review](owned-linear/rea-integrity-review.json) matched all members with complete coverage and no contradictions. [REA execution receipt](owned-linear/rea-summary.json). No target member was executed. The kit's report is a snapshot before this final index/update record; it is inspection material rather than a complete application checkout. The complete application source is the repository itself.

Latest workflow: [owned-evaluation validation](owned-evaluation/validation-result.json), with 40 unit methods and six actual CLI stages across memory and linear profiles. Each CLI stage ran in its own offline namespace; prediction had no gold files and scoring had no model weights or source case text. [Role-stage receipts](owned-evaluation/roles-execution-summary.json). These tests deliberately fit/predict/score authored fixtures and check hand-calculated metrics; the legacy unit wrapper's no-quality field refers to historical/real/rival benchmark experiments, which were not repeated. Completed, correctly typed training receipts are required. Initial pre-hardening results are retained separately. No raw stage inputs/gold/predictions/model artifacts are imported into this evidence directory. The role control source has absolute cloud paths and is inspection material, not an automatic launch command.

The final [owned-evaluation source kit](owned-evaluation/source-kit-receipt.json) has 64 members. [REA hash/size review](owned-evaluation/rea-integrity-review.json) matched every member with complete coverage and no contradictions; no member was executed. The kit includes explicitly authored labeled demo examples, but no real/benchmark/private cases or generated stage gold/predictions/model artifacts. Earlier source-kit and validation receipts are retained as first-pass history. Identity inspection does not certify quality, security, data rights or independence.

REA identity inspection does not certify semantic quality, security, data rights or production operation. Existing positive, negative, failed-gate and failure evidence is retained. The current comparison summary is [EVALUATION.md](../docs/EVALUATION.md).
