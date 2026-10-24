# Original owned relational supervision

`eidos.decision_curriculum` generates original finite synthetic decision tasks without downloading data, importing a vendor model, training, scoring or changing a serving configuration. It gives the original question-conditioned trainer a reproducible source of explicit entity-bound supervision. It does not supply broad semantic knowledge or certify a useful LAYA/JEV replacement.

Create a new private corpus directory:

```sh
python3 -S -B -m eidos.decision_curriculum --output /tmp/eidos-relational-data
```

The default writes 360 training, 72 development and 72 evaluation tasks, plus provenance, an audit, a private oracle ledger and a file-hash manifest. JSONL rows use the existing trainer's exact `request`, `target_option_id`, `source_group` format. Keep development/evaluation rows and `ORACLE.json` out of training and inference mounts. `--training-worlds`, `--development-worlds`, `--evaluation-worlds` and `--seed` control bounded generation. Counts must be even because related worlds travel together. Existing outputs, symlinked ancestors and oversized settings are refused. New directories/files use private permissions.

Each four-person world supplies two tasks in each family:

| Family | Grounded question | Offered answers |
| --- | --- | --- |
| Property | An entity's stated color | Four colors |
| Binding | Which entity has a stated color | Four entity names |
| Negation | Positive or negated color proposition | True / false |
| Comparison | Which of two entities has greater/smaller mass | The two names |
| Two-step relation | Color after following two directed links | Four colors |
| Score | Which explicit mass interval contains an entity's mass | Four anchored intervals |

Labels are computed from typed facts. Every sentence states a complete entity's facts, avoiding the previous fixture's unbound pronouns and fragmented fields. Every offered property/name distractor also occurs in context. Candidate IDs/order are independently randomized and ignored by the scorer. Mass intervals are explicit; numeric anchors 0–3 denote ordinal bins, not a probability density over grams.

Worlds are paired: two entities exchange their colors and masses while names, edges, sentence order, prompt meanings and the context's word multiset stay fixed. Answers must follow the changed bindings. Both worlds share one `source_group`; never split a pair across roles. Defaults contain **15 training, 3 development and 3 evaluation groups**, not 360/72/72 independent observations. Two-step relations use only a four-cycle, not arbitrary graphs. On this cycle, following twice reaches the same node in either direction, so these questions cannot establish directional relation understanding.

Role-specific proper names, literal question templates and context templates are disjoint. Shared semantic vocabulary and the finite ontology remain common. Exact world/group/anonymized structural hashes are checked across roles; this does not prove semantic, template-family, domain or pretraining independence. The joint wording/name shift cannot isolate each source of error. Public code and known generation rules do not form a pristine hidden benchmark.

`AUDIT.json` records counts and correct answer/rank/description-length distributions by family, including exact binary truth/polarity balance. Randomization does not guarantee zero shortcuts or full finite-sample balance. `ORACLE.json` contains world facts, task specifications and labels for auditing only; none belongs in model features. Caller ownership/provenance declarations are not an independent legal certificate. No new blanket public-code/data license grant is added.

The current compact model has one attention read over independently summarized spans. It has no explicit relational join or contextual language pretraining. This corpus is useful even when it exposes failure: compare training success with fresh bindings/wording and report each family separately, without adapting to revealed evaluation outcomes. Keep all forecasts uncalibrated and review-only. A finite synthetic result cannot establish real-domain, multilingual, long-context, 255-option accuracy or LAYA/JEV superiority.

[Question-conditioned model](DECISION-MODEL.md) · [Task data contract](DECISION-DATA.md) · [Earlier build's critical result](DECISION-BUILD-RESULT.md).
