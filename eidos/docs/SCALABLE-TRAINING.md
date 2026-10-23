# Train from larger owned datasets

`eidos.train_sparse` builds a new linear classifier from arbitrary, potentially uneven class counts. It uses only Python's standard library and exports the existing checked inference format. The earlier memory/GD/Adam trainers retain their exact 20-example-per-class contract. This is a new recipe with no inherited LAYA or JEV result.

From the repository's `eidos` directory, create an existing private parent and run:

```sh
mkdir -m 700 owned-registry
mkdir -m 700 owned-registry/models
python3 -S -B -m eidos.train_sparse \
  --input examples/owned-support-demo.jsonl \
  --provenance examples/owned-support-demo-provenance.json \
  --output owned-registry/models/owned-sparse \
  --model-id eidos-owned-demo-v1
python3 -S -B -m eidos \
  --registry owned-registry/models/owned-sparse \
  --tenant owned-training-demo < examples/owned-support-request.json
```

The small authored demo demonstrates the interface; supply your own authorized labeled file for a larger build. Use a new output name for every build. The registry also works with the existing local service and browser: follow the main README with a new private configuration pointing to `models/owned-sparse`. No serving model is selected or activated by training.

## Input and lineage

Each UTF-8 JSONL row has exactly `id`, `text`, `label`. The [identifier and four-field provenance declaration](TRAINING.md) are unchanged. IDs and normalized word sequences must be unique across all classes. No rows are cleaned, dropped, truncated or resampled. Provenance is a caller declaration, not independently verified rights, ground truth or exclusive ownership.

The new contract accepts 2–20,000 rows across 2–64 known labels, at least one row per label. Counts can differ; one example is permitted mechanically, not sufficient evidence of a useful class. Input is bounded to 32 MiB, 10 million total text characters and 12,000 characters per row. Blank/extra/duplicate JSON fields, malformed Unicode, empty word sequences, duplicate IDs and normalized-text duplicates are refused. It is bounded whole-file preparation, not streaming ingestion of an unlimited corpus.

Metadata and the distinct `eidos-owned-sparse-training-receipt-v1` record actual `training_rows` and `class_counts`, input/provenance hashes, recipe, seed, epochs, hyperparameters, work limits and training diagnostics. They never pretend that variable counts satisfy an older 20-example receipt. The model training tag is `eidos-caller-sparse-training-v1`.

## Sparse optimization

Training alone determines up to 10,000 Unicode word, bigram and character 3/4/5 TFIDF features, with log1p counts and unit L2 normalization. Vocabulary is ordered deterministically. Compact CSR arrays store row offsets, feature indices, float64 feature values and class indices, avoiding a separate Python tuple for every nonzero. There can be at most one million observed terms and ten million stored nonzeros; not every combination of maximum dimensions fits the work budget.

Weights and biases start at zero. The fixed default is three shuffled passes, seed 113, learning rate 0.5 and weight penalty 0.0001. Rows are first sorted by label and ID; a local seeded generator shuffles their indices each epoch. No development/test input or checkpoint selection is available. `--epochs` accepts 1–20 and `--seed` a bounded integer; choose both before examining evaluation results. The final epoch is exported.

For zero-based update number `t` and `N` training rows:

```text
eta_t = 0.5 / sqrt(1 + t/N)
p = softmax(W x + b)
W <- (1 - eta_t * 0.0001) W - eta_t (p - onehot(y)) x
b <- b - eta_t (p - onehot(y))
```

This is per-example SGD for mean cross-entropy plus half the weight penalty times the squared weight norm. Biases are unpenalized. Maximum-shifted softmax avoids overflowing exponentials. A shared scale applies weight decay lazily; only active feature columns need a gradient update. When the scale becomes small, a dense rescaling restores it to one. Independent dense numerical checks verify that these sparse updates implement the stated equation, including rescaling and unpenalized biases.

Initial and final objectives are computed on the training rows. They diagnose fitting and are neither held-out accuracy nor calibration. Training need not improve monotonically. Parameters use float64 arithmetic and the existing checked little-endian float32 export. On the tested runtime identical inputs/settings reproduce identical artifacts. Reordering input preserves numerical parameters but changes the recorded input-byte hash; cross-platform byte identity is not promised.

## Bounds, outputs and evaluation

A conservative work estimate must fit one billion arithmetic units before fitting. Training checks 600 process CPU seconds and 900 elapsed seconds cooperatively, including preparation. These checks are not aggregate process-tree controls or a hostile-caller sandbox. Use operating-system CPU, memory, elapsed-time and network controls for untrusted workloads. The documented maxima are refusal bounds, not latency or memory promises.

Output must be a new directory under an existing nonsymlink parent. It is private `0700`; `model.json`, `weights.f32`, `registry.json` and `TRAINING-RECEIPT.json` are `0400`. Export validation precedes a complete receipt. An I/O failure can leave an incomplete new directory; do not load or activate incomplete artifacts. No existing registry, configuration or acceptance policy is changed.

The [owned evaluation workflow](OWNED-EVALUATION.md) explicitly validates this new receipt and variable class counts against the actual supplied training bytes. Keep its historical `--support`/`--support-provenance` argument names, passing the complete new training files. Evaluation case, text, work and phase-time bounds remain unchanged. Prediction requires no gold; scoring requires no weights or case text. [Paired owned-model comparison](OWNED-COMPARISON.md) still requires matching training/provenance hashes and a predeclared case/group plan. None of these commands makes reused test data independent.

Every new profile is uncalibrated, consequential and review-only, with unit temperatures and disabled acceptance. It abstains, returns `value=null` and authorizes no actions. The implementation handles known trained classes in English. It does not supply multilingual, unseen-label, universal-recognition or out-of-scope guarantees. Vocabulary and weights can reveal training information; private file modes do not provide differential privacy or protection from a hostile same-UID process.

## Verification and next quality experiment

```sh
python3 -S -B -m unittest discover -s tests -p 'test_*.py' -v
python3 -S -B tools/smoke.py --trainer sparse
python3 -S -B tools/sparse_scale_smoke.py
```

The capacity script creates 20,000 original generated rows across 14 uneven classes, fits the fixed default, loads the exported model through the real Engine and checks review policy. It removes its temporary artifacts. Its repeated, simple sentences test bounded capacity and integration; they do not measure semantic generalization, general throughput, LAYA or JEV performance. Use external limits and a separate offline development workspace.

In the recorded Codex Cloud check, all 95 unit methods and 16 real CLI/service checks passed. The capacity run completed in 96.33 seconds elapsed / 86.72 CPU seconds, with 112,936 KiB peak self RSS on Linux, 10,000 selected features and 5,352,806 nonzeros. Four separately isolated evaluation CLI roles also passed; prediction had no gold and scoring had no weights or source case text. No unexpected IP connection occurred. These measurements apply to this generated fixture and VM. [Receipts and validation source](../evidence/sparse-training/) retain the exact tested-source bindings; later documentation and publication metadata are separate additions.

The next real quality experiment needs a newly sealed cohort, explicit exclusion of previously revealed cases before selection, independently planned development-only selection, a frozen reference adapter and a separately approved resource plan. No new real-corpus training, historical reference rerun or benchmark rescoring is authorized by the source/fixture milestone. The latest DBpedia result remains an accuracy tie with both superiority gates failed. Dataset/model redistribution obligations and production validation remain unresolved. `mission_complete=false`; `production_certified=false`.
