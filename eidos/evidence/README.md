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

REA identity inspection does not certify semantic quality, security, data rights or production operation. Existing positive, negative, failed-gate and failure evidence is retained. The current comparison summary is [EVALUATION.md](../docs/EVALUATION.md).
