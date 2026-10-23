# EIDOS

EIDOS is an original, locally controlled classifier and decision research application. It provides Python inference and training, a local browser, a signed loopback API, review history, audit records, and backup/restore.

This source checkout runs without pip, npm, a hosted model, or a CDN. It requires Python 3.12 or later on POSIX, including the standard SQLite/OpenSSL modules. Browser use requires WebCrypto. No installation command or model download is needed for the authored demo below.

## Train and query the demo

From the repository root:

```sh
cd eidos
mkdir -m 700 owned-registry
mkdir -m 700 owned-registry/models
python3 -S -B -m eidos.train_support \
  --input examples/owned-support-demo.jsonl \
  --provenance examples/owned-support-demo-provenance.json \
  --output owned-registry/models/owned-demo \
  --model-id eidos-owned-demo-v1
python3 -S -B -m eidos \
  --registry owned-registry/models/owned-demo \
  --tenant owned-training-demo < examples/owned-support-request.json
python3 -S -B -m eidos.explain_support \
  --registry owned-registry/models/owned-demo \
  --tenant owned-training-demo < examples/owned-support-request.json
```

The 40 original, synthetic CC0 examples demonstrate mechanics only. To train on your own authorized data, use exactly 20 distinct examples per class, 2–256 classes, and an explicit provenance declaration. See [the complete format and limits](docs/TRAINING.md). Output must be a new directory; the trainer refuses an existing output and does not activate or overwrite configuration.

The command above trains the support-memory method. A new [standard-library linear trainer](docs/LINEAR-TRAINING.md) is also available through `python3 -S -B -m eidos.train_linear_support`, with the same input/provenance/output arguments and optional fixed `--epochs`. It trains a new zero-initialized softmax model; the measured linear EIDOS model in the LAYA comparison remains a different, frozen artifact. Neither command makes new models benchmark winners. Every new model abstains, returns `value=null`, requires human review, and has no action authority. Its candidate distribution is uncalibrated.

The linear trainer now also accepts `--optimizer adam`, with bias-corrected moments and a fixed 120-epoch default; ordinary gradient descent remains the default. Both methods use only standard-library Python and the caller's supports. See [optimizer settings and scope](docs/LINEAR-TRAINING.md#adam-training-option). Its optimizer checks establish mechanics; benchmark results depend on the exact trained artifact and evaluation.

For larger datasets, the new [sparse SGD trainer](docs/SCALABLE-TRAINING.md) accepts up to 20,000 labeled rows across 2–64 uneven classes. It uses compact sparse vectors, deterministic shuffled optimization and the same self-contained inference format. Its distinct receipt records actual class counts; owned evaluation verifies that lineage. This new recipe has no LAYA or JEV score.

## Start the local application

After training the demo, still inside this directory:

```sh
mkdir -m 700 local-owned
python3 -S -B -m eidos.manage init-local \
  --config local-owned/config.json \
  --state-dir local-owned/state \
  --registry-root "$PWD/owned-registry" \
  --release-path models/owned-demo \
  --tenant owned-training-demo
python3 -S -B -m eidos.service --config local-owned/config.json
```

The service binds to loopback port 8765. Open its local browser on the same machine. Use tenant `owned-training-demo`, key ID `demo-operator`, and the operator key from the newly created private configuration. Do not put key values in shell arguments, logs, Git, or persistent browser storage. The application does not publish itself or provide production identity/TLS configuration. Stop it with Ctrl-C.

With the service running, a second terminal in this directory can make a signed request:

```sh
python3 -S -B -m eidos.manage call \
  --config local-owned/config.json \
  --path /v1/decisions < examples/owned-support-request.json
```

Read [operations](docs/OPERATIONS.md) before backup/restore or deployment. Its historical preinstalled-profile examples require the separately packaged model artifacts; the new owned-data workflow above needs only this checkout. Backups are authenticated plaintext and exclude configuration/keys. Production identity, encryption/offsite recovery, load/drift and independent security review remain work to complete.

## Evaluate separate labeled data

The [owned evaluation workflow](docs/OWNED-EVALUATION.md) provides explicit `prepare`, `predict`, and `score` commands for the owned trainers. It checks exact support/evaluation ID and normalized-text overlap, binds code/model/data files by hash, and requires a complete prediction stream before writing aggregate accuracy, F1 and probability metrics. Prediction needs no gold file; scoring needs no source case text or model weights. The portable commands use logical file roles; operating-system isolation is external.

Its authored four-case demo is for mechanics only. Exact overlap refusal does not verify source/paraphrase independence, unseen holdout status, calibrated confidence or semantic quality. Reports retain zero accepted coverage for these review-only models. No existing revealed benchmark was rerun for this feature.

[Paired comparison](docs/OWNED-COMPARISON.md) now freezes two owned models against the same cases, labels and labeled supports before prediction, then scores complete streams using whole caller-declared groups. It reports accuracy/macro-F1 differences and individual approximate bootstrap intervals. Group independence and untouched outcomes are not verified; the numeric rule does not promote a model or establish general superiority.

## Verify the checkout

From this directory:

```sh
python3 -S -B -m unittest discover -s tests -p 'test_*.py' -v
python3 -S -B tools/smoke.py
python3 -S -B tools/smoke.py --trainer linear
python3 -S -B tools/smoke.py --trainer adam
python3 -S -B tools/smoke.py --trainer sparse
python3 -S -B tools/sparse_scale_smoke.py
```

The unit suite uses authored fixtures only, including finite-difference gradient checks and deterministic artifact tests for the linear trainer. Each smoke invocation creates a new temporary demo registry and configuration for its selected method, queries the real CLI and loopback service, verifies review/idempotency behavior and served browser assets, then stops the service and removes its temporary state. Memory-specific neighbor explanations are explicitly inapplicable to the linear run. No existing configurations, external services, benchmark data, or reference model are accessed. These are functional checks, not quality evaluations or headless-browser tests. Run them in an isolated development environment.

The separate sparse capacity check trains on 20,000 generated mechanical examples and loads the result through the real Engine. It checks capacity and artifact integration, not semantic quality or a rival comparison.

The preceding cloud package also passed 18 API, 11 real Chromium and 9 backup/restore checks. Those historical receipts are preserved separately from checkout-specific verification in [evidence](evidence/README.md).

## Measured results and scope

The newer 280-case, 14-class DBpedia comparison **tied accuracy at 86.79% (243/280 each)**. EIDOS macro-F1 was 86.66%, versus LAYA 84.22%, but both individual paired interval gates failed. This does **not** establish that EIDOS beats LAYA. It tested the actual stdlib Adam trainer at a fixed 12 epochs, selected only on separate development data, using 20 labeled supports/class versus zero-shot LAYA. Every article and all 14 choices fit without truncation. [Complete result and limitations](docs/DBPEDIA-RESULT.md) · [Aggregate evidence](evidence/dbpedia-study/score.json). The test cohort is now revealed and consumed; no new model was activated.

The earlier SNIPS result below concerns a different frozen artifact and cohort:

On the same 1,821 seven-choice cases, frozen linear EIDOS scored **94.18% accuracy / 93.00% macro-F1**, versus public zero-shot LAYA's **92.48% / 89.97%**. Both predefined cohort-scoped numeric gates passed. The support-memory secondary arm failed its paired gates. This outcome-informed, exploratory comparison uses supervised EIDOS versus zero-shot LAYA; it does not establish matched-adaptation, general, strongest-LAYA, JEV or production superiority. See [the complete comparison and limitations](docs/EVALUATION.md).

This checkout contains original implementation and aggregate evidence. The five preinstalled benchmark model binaries and third-party implementations/weights are omitted. Their original release receipts and source hashes are retained for provenance; this source tree is not the complete historical application archive. The demo builds a fresh registry, so its documented workflow requires no binary release asset. No GitHub binary release has been created as part of this source import.

No private corpus, raw benchmark cases, benchmark answer keys/per-case predictions, existing credentials, or runtime state are included. The labeled demonstration files are explicitly original authored fixtures. Public licensed data are not exclusive property. See [attribution and changes](docs/ATTRIBUTION-AND-CHANGES.md), the included dataset license notices, [original source provenance](SOURCE-PROVENANCE.json), and [the current change record](CHANGESET-PROVENANCE.json). TypeSafe/JEV service access and model extraction remain outside this project's authorized research scope. `mission_complete=false`; `production_certified=false`.
