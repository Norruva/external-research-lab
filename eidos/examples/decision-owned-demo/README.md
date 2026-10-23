# Original decision-model mechanics fixtures

These are small, original assistant-authored synthetic examples for developing the question-conditioned implementation. They contain no benchmark cases, private material, vendor/model outputs or copied corpus. They are not a production corpus, independent semantic benchmark, calibration set or evidence of LAYA/JEV parity.

`training.jsonl` contains 144 tasks from 24 two-entity scenarios: 96 choice tasks, 24 binary tasks and 24 ordinal score tasks. Each scenario supplies the same context to six different questions. Questions ask about colors, owners, locations and processing status; binary tasks include positive and negated propositions; score tasks specify a 0/1/2 status rubric. All facts and answer assignments are explicit in `author_fixtures.py`.

`smoke.jsonl` contains 12 additional tasks from two separately authored scenarios, using the same finite templates and attribute vocabulary. These are visible, authored integration fixtures. Their source groups differ from training, but shared templates/vocabulary and tiny size mean they are not an independent language-understanding estimate. Do not tune a recipe on their outcomes and then describe them as an untouched test. A successful input/output smoke check is distinct from correct semantic answers.

Each row has exactly `request`, `target_option_id` and `source_group`. Request fields use the `eidos-decision-request-v2` contract; see [the data guide](../../docs/DECISION-DATA.md). Opaque option IDs and input ordering vary independently of the target. A target points to the supplied option ID; it is never passed as a model input. The source-group value identifies related tasks and must also remain outside model features.

The four-field `provenance.json` declares original mechanical-fixture origin and does not grant a new public license, remove any existing rights, or certify exclusive legal ownership. It does not inherit dataset terms from any third-party corpus because none was used to author these fixtures. The earlier CC0 fixed-class demo elsewhere in this repository is unchanged.

`author_fixtures.py` is transparent original fixture-authoring source. Its only outputs are `training.jsonl`, `smoke.jsonl` and `provenance.json` beside itself; it refuses existing outputs. It performs no training, inference, scoring, installation or network request. Existing files are already materialized, so running it again in this directory will refuse rather than overwrite them. Read its source when auditing provenance; it is not a service startup command.
