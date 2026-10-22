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

The own-data command currently trains the support-memory method. The measured linear EIDOS model in the LAYA comparison is a different, frozen artifact. Adding this command does not make its new models benchmark winners. Every new model abstains, returns `value=null`, requires human review, and has no action authority. Its candidate distribution is uncalibrated.

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

## Verify the checkout

From this directory:

```sh
python3 -S -B -m unittest discover -s tests -p 'test_*.py' -v
python3 -S -B tools/smoke.py
```

The unit suite uses authored fixtures only. The smoke test creates a new temporary demo registry and configuration, queries the real CLI and loopback service, verifies review/idempotency behavior and served browser assets, then stops the service and removes its temporary state. It does not access existing configurations, external services, benchmark data, or a reference model. It is a functional check, not a quality evaluation or a headless-browser test. Run it in an isolated development environment.

The preceding cloud package also passed 18 API, 11 real Chromium and 9 backup/restore checks. Those historical receipts are preserved separately from checkout-specific verification in [evidence](evidence/README.md).

## Measured results and scope

On the same 1,821 seven-choice cases, frozen linear EIDOS scored **94.18% accuracy / 93.00% macro-F1**, versus public zero-shot LAYA's **92.48% / 89.97%**. Both predefined cohort-scoped numeric gates passed. The support-memory secondary arm failed its paired gates. This outcome-informed, exploratory comparison uses supervised EIDOS versus zero-shot LAYA; it does not establish matched-adaptation, general, strongest-LAYA, JEV or production superiority. See [the complete comparison and limitations](docs/EVALUATION.md).

This checkout contains original implementation and aggregate evidence. The five preinstalled benchmark model binaries and third-party implementations/weights are omitted. Their original release receipts and source hashes are retained for provenance; this source tree is not the complete historical application archive. The demo builds a fresh registry, so its documented workflow requires no binary release asset. No GitHub binary release has been created as part of this source import.

No private corpus, raw benchmark cases, answer keys, per-case predictions, existing credentials, or runtime state are included. Public licensed data are not exclusive property. See [attribution and changes](docs/ATTRIBUTION-AND-CHANGES.md), the included dataset license notices, and [source provenance](SOURCE-PROVENANCE.json). TypeSafe/JEV service access and model extraction remain outside this project's authorized research scope. `mission_complete=false`; `production_certified=false`.
