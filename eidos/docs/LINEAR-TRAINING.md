# Train an owned linear classifier

For variable counts and larger datasets, see the separate [sparse SGD trainer](SCALABLE-TRAINING.md). The full-batch GD/Adam methods on this page retain their original exact-20 contract.

The original `eidos.train_linear_support` command fits a new linear registry using Python's standard library. It shares the existing supervised input contract and inference format. It reads only the labeled input and provenance files you supply. No old/pretrained weights, remote model, benchmark output, evaluation selection, calibration, activation, or action execution is involved.

From the repository's `eidos` directory, first create a private `owned-registry/models` parent as described in the main README, then run:

```sh
python3 -S -B -m eidos.train_linear_support \
  --input examples/owned-support-demo.jsonl \
  --provenance examples/owned-support-demo-provenance.json \
  --output owned-registry/models/owned-linear \
  --model-id eidos-owned-demo-v1
python3 -S -B -m eidos \
  --registry owned-registry/models/owned-linear \
  --tenant owned-training-demo < examples/owned-support-request.json
```

This example reuses the demo's logical profile ID in a separate registry, so the same request works. Its artifact hash and method differ from the memory registry. Give different real profiles distinct IDs. To serve this linear registry, follow the main README with a new `local-linear` configuration and `--release-path models/owned-linear`; stop an existing service before reusing its port. The same browser and signed API load the configured inventory dynamically. Nearest-support explanations apply only to memory profiles.

## Method

The labeled supports alone determine a maximum 10,000-feature Unicode word/bigram/character 3/4/5 TFIDF transform. Counts use log1p and vectors are normalized to unit L2 norm. Classes, IDs and vocabulary are ordered deterministically. The transform matches the existing memory trainer on identical supports.

Both optimizers start from zero weights and biases. The default uses full-batch gradient descent for a fixed 64 epochs, learning rate 1 and weight penalty 0.0001. Biases are unpenalized. For support examples `(x_i, y_i)`, the objective is:

```text
z_ic = W_c · x_i + b_c
L = mean_i [logsumexp_c(z_ic) - z_i,y_i] + 0.0001 / 2 * sum(W²)
```

Gradients use a stable maximum-shifted softmax and float64 standard-library arithmetic. Exported parameters use the existing checked little-endian float32 format. Metadata records the recipe, fixed epoch count, input/provenance hashes and initial/final support-fit objective. Those objective values are training diagnostics, not held-out accuracy, calibration, or evidence of superiority.

An optional `--epochs` accepts 1–200 fixed passes, selected before evaluation. No development/test path or evaluation-based epoch selection exists. The default recipe and its artifact bytes remain unchanged from the first owned linear trainer.

## Adam training option

Use `--optimizer adam` to train with an original standard-library implementation of full-batch Adam. Its fixed defaults are 120 epochs, learning rate 0.12, weight penalty 0.00002, first/second moment factors 0.9/0.999 and epsilon 1e-8. Biases remain unpenalized. The gradient includes coupled L2; this is Adam, not decoupled AdamW. Starting with zero moments, each step uses:

```text
m_t = 0.9 m_(t-1) + 0.1 g_t
v_t = 0.999 v_(t-1) + 0.001 g_t²
theta_t = theta_(t-1) - 0.12 [m_t/(1-0.9^t)] / [sqrt(v_t/(1-0.999^t)) + 1e-8]
```

The usual bias corrections and epsilon outside the square root are explicit. The method follows the public mathematical algorithm in Kingma and Ba, *Adam: A Method for Stochastic Optimization*, arXiv:1412.6980; no third-party implementation is imported or vendored.

```sh
python3 -S -B -m eidos.train_linear_support \
  --input examples/owned-support-demo.jsonl \
  --provenance examples/owned-support-demo-provenance.json \
  --output owned-registry/models/owned-adam \
  --model-id eidos-owned-demo-v1 \
  --optimizer adam
python3 -S -B tools/smoke.py --trainer adam
```

Use the same local-service instructions with a new configuration and `--release-path models/owned-adam`. The [owned evaluation workflow](OWNED-EVALUATION.md) accepts this registry without an adapter. Metadata and the training receipt identify the optimizer, hyperparameters, fixed final-epoch policy and additional work estimate. Adam uses float64 parameters/moments during fitting and checked float32 export. Optimizer choice is explicit, never selected from evaluation outcomes. A lower training objective alone does not establish better generalization or calibrated probabilities; Adam need not decrease the objective at every step.

These mathematical settings resemble the project's historical research optimizer, but this implementation is **not** the frozen model that outscored LAYA: that experiment used float32 NumPy/SciPy training and development-selected checkpoints. This option uses float64 standard-library arithmetic and the fixed final epoch, with no development data. It neither reconstructs the historical checkpoint nor inherits its score. A new, frozen recipe needs independently planned evaluation on eligible data before any superiority claim. Existing revealed benchmarks are not eligible for later recipe selection.

## Limits and artifacts

The [existing training data format](TRAINING.md) applies: exactly 20 distinct supports per class, 2–256 classes, strict id/text/label JSONL and an explicit caller-supplied provenance declaration. The same input, aggregate character, observed-term and 500,000-nonzero bounds apply. Selected feature strings must fit the inference format's 512-character bound.

A conservative arithmetic work estimate must fit 250,000,000 units; Adam adds 20 units per parameter per update to the existing gradient estimate. Larger inputs or epoch counts are refused before output creation; class-count support does not guarantee every maximum-size workload fits this budget. Adam's two float64 moment arrays add 16 bytes per parameter. Training checks 60 process CPU seconds and 90 elapsed seconds cooperatively, including Adam update chunks, and refuses a timed-out fit before publication. These are per-invocation training checks, not an aggregate process-tree/cgroup or hostile-caller guarantee; use external process isolation for untrusted workloads. Limits do not imply latency or semantic quality.

Output must be a new directory under an existing nonsymlink parent. It is private `0700`, with `0400` model.json, weights.f32, registry.json and TRAINING-RECEIPT.json. The existing writer checks exported weights/metadata before registry publication. Existing outputs are refused. Filesystem failure can leave a new incomplete directory; do not activate incomplete artifacts. Repeated successful builds with identical bytes, IDs, epochs and tested runtime produce identical artifact and receipt bytes. Row reordering preserves numerical parameters while changing the recorded input-byte hash. No cross-platform bit identity is claimed.

Every new profile always abstains, returns `value=null`, requires review and has no action authority. Candidate probabilities are uncalibrated; all 2-through-k class subsets are supported with invariant raw per-class logits. Vocabulary and parameters can disclose support information; no privacy guarantee or hostile same-UID filesystem assurance is claimed. Real data require independently designed evaluation and calibration.

The authored demo and numerical tests establish gradients, serialization, bounds, ownership mechanics and serving integration. They do not establish benchmark, general semantic or JEV performance. The previous revealed evaluations remain consumed and were not rerun. TypeSafe/JEV service access and model extraction remain outside the authorized scope. `mission_complete=false`; `production_certified=false`.
