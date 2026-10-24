# Data for an original question-conditioned decision model

The new decision model learns compatibility between **context, question and caller-defined option descriptions**. It is separate from the historical fixed-class support/sparse trainers and their `id,text,label` format. Meaningful generalization to new questions/options must be measured on fresh appropriate data; accepting a richer JSON shape does not establish that ability.

## Training rows

Use UTF-8 JSONL with exactly these three outer fields:

```json
{"request":{"format":"eidos-decision-request-v2","request_id":"original-example-1","context":"Mira owns the red lantern. Noel owns the blue parcel.","prompt":"What color is the lantern?","kind":"CHOICE","options":[{"id":"c731","description":"blue"},{"id":"c208","description":"red"}]},"target_option_id":"c208","source_group":"authored-example-family-1"}
```

`target_option_id` must exactly name one supplied option. The label and `source_group` are training metadata, not request/model features. Group IDs must identify related source/template/customer/document/question families before any evaluation split. Distinct row IDs do not prove independence.

Option IDs identify outputs and must be opaque to semantic scoring. Supply meaningful natural-language `description` fields. The request has no pre-trained `class_id` mapping. Reordering an unchanged candidate set should only reorder the mapped results; adding candidates changes the outcome space and may legitimately change normalized probabilities.

Requests require exactly `format`, `request_id`, `context`, `prompt`, `kind` and `options`, with the score-only fields below. The version is `eidos-decision-request-v2`. Current contract limits include 2–255 options, 65,536 context **characters**, 4,096 prompt characters, 1,024 description characters per option, 131,072 total text characters and 1 MiB serialized request bytes. Those are input/refusal bounds, not token-context, throughput or semantic-quality promises; the trained model can have stricter supported processing limits. Text must be nonblank valid Unicode. Duplicate/unknown JSON fields, nonfinite numbers, unsupported kinds and malformed IDs are refused. See the [contract source](../eidos/decision_contract.py) for the exact checks.

## Question kinds

**CHOICE** uses options with exactly `id` and `description`. Any candidate may be correct in a new example; do not encode class identity or the answer in administrative IDs. Independently annotate the correct answer and include genuine none/multiple-answer cases under a separately defined unknown/ambiguity policy before a production evaluation. The small demonstration uses only unambiguous single-answer tasks.

**BINARY** supplies exactly two options, IDs `false` and `true`, with explicit descriptions of their meaning. For example:

```json
{"format":"eidos-decision-request-v2","request_id":"original-example-2","context":"The lantern is red.","prompt":"Is the lantern not red?","kind":"BINARY","options":[{"id":"true","description":"Yes. The proposition is supported by the stated facts."},{"id":"false","description":"No. The proposition is contradicted by the stated facts."}]}
```

The target for these declared facts is `false`. Understanding a proposition, its negation or insufficient evidence needs semantic supervision and fresh testing. An omitted fact must not automatically be labeled false; the task's unknown/insufficient-evidence handling must be defined.

**SCORE** adds root `minimum` and `maximum` and a numeric `value` on every option. Supply at least two distinct finite numeric anchors inside the increasing declared bounds, together with text descriptions and a clear prompt/rubric:

```json
{"format":"eidos-decision-request-v2","request_id":"original-example-3","context":"The lantern has status pending.","prompt":"Rate the lantern readiness using status: rejected means 0, pending means 1, approved means 2.","kind":"SCORE","minimum":0,"maximum":2,"options":[{"id":"c502","description":"approved","value":2},{"id":"c918","description":"rejected","value":0},{"id":"c247","description":"pending","value":1}]}
```

The labeled anchor is `c247`. A numeric expectation can be derived from the predicted anchor distribution. It is not a probability density at an arbitrary continuous point, and correct typed decoding does not prove understanding of the rubric. Numeric metadata supplies a scale; semantic model features should not leak a target or list position.

## Data provenance and ownership

Supply a separate JSON object with exactly `source`, `license`, `citation` and `rights_basis`, each a nonblank string of at most 1,000 characters. A proprietary-data declaration is permitted; the field name does not require an open-source license. These are caller assertions, not independently verified rights or ground truth. Record actual authorship/contributor assignment, authorization for training/model distribution, source, sensitivity, consent and retention in a separately governed rights ledger before any production release. Do not fabricate rights statements or rename licensed benchmark text as exclusively owned data.

The [authored demo](../examples/decision-owned-demo/) contains 144 training tasks and 12 separate smoke tasks, with transparent original authoring source and declared provenance. It applies no new CC0/public license grant. Its finite, repeated templates deliberately test mechanics. No real-world semantic, multilingual, broad-context, 255-option, calibration, LAYA/JEV or production claim follows from fitting it.

## Independent roles and reliability

Production research needs separate fitting, model-selection, calibration, policy-selection and sealed final evaluation roles, grouped by real source, question/template family, meaning/label family and time as appropriate. Hold out entire questions and domains when claiming question-conditioned transfer. Do not reuse the already revealed historical EIDOS benchmark cohorts. Authored source groups alone do not establish real-world independence.

Evaluate choice accuracy, binary probability quality, ordinal error, calibration, unknown false acceptance, accepted-answer error and automatic coverage separately. A normalized vector of probabilities is not calibrated confidence. The new owned profiles remain review-only until appropriate independent evidence supports a policy. Human review and abstention must not be counted as automatically correct answers, and zero automatic coverage must never count as a useful perfect-accuracy result.

Keep corpus text, labels, individual predictions and potentially revealing model artifacts under appropriate access controls. A model fitted on original material can still expose it; original provenance and private file modes do not provide a privacy guarantee. No serving activation, silent retraining, action authority or production certification is provided by preparing these data.
