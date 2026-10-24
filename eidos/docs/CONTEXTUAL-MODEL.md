# Original contextual pointer-memory model

The v3.1 research candidate replaces v2's first-moment token pooling with an ordered nonlinear local encoder in **context, question and candidate descriptions**. Exact normalized-spelling pointers preserve the identity of an unseen name even when its learned word vector is UNK. Three learned reads can copy a retrieved value spelling into the next fact-key query. This supports a binding information path; it does not supply pretrained meanings or guarantee correct reasoning.

For each token, learned depthwise neighbor weights at offsets −2, −1, +1, +2 and a position vector are combined with its word embedding and passed through tanh. Learned key attention and value-copy attention operate on these contextual states. Each read combines question/candidate-conditioned fact attention with the current spelling distribution, retrieves a vector, copies a new spelling distribution and updates the neural query. A shared nonlinear candidate head uses all three reads, exact-match support, and candidate-specific key/copy probability masses, followed by a complete-set softmax. There are no task-specific hue/graph parsers or reference-oracle calls in inference.

This removes the old architecture's parameter-independent `a b b a .` / `b a a b .` witness: fixed v3 parameters can distinguish it. A finite local encoder and learned memory are still compressed representations. Sensitivity, valid gradients and correct transport do not prove learned semantics or injectivity.

## Create an original corpus and check a fitting recipe

From the repository's `eidos` directory, using new paths:

```sh
python3 -S -B -m eidos.contextual_challenge --output /tmp/eidos-v3-data
python3 -S -B -m eidos.train_contextual --preflight-only \
  --input /tmp/eidos-v3-data/training.jsonl \
  --provenance /tmp/eidos-v3-data/provenance.json --epochs 16
```

Preflight parses and encodes training only; it does not run a forward loss or fit. For a recipe within its reported bound, fitting uses the same arguments plus `--output NEW_MODEL_DIRECTORY --model-id YOUR_MODEL_ID` and omits `--preflight-only`. Fix settings before inspecting evaluation quality. The implementation uses standard-library Python, Adam float64, training-only vocabulary and the final checkpoint, with no pretrained artifact or third-party Python ML runtime. Provenance remains a caller declaration, not a rights certificate.

The distinct v3.1 four-file artifact includes `model.json`, `weights.f64`, `registry.json` and a complete training receipt. New directories/files are private; symlinked paths and existing outputs are refused. Hashes check bytes; retain the registry digest independently to pin a release. The v2 loader refuses this format rather than reinterpreting weights.

```sh
python3 -S -B -m eidos.contextual --registry NEW_MODEL_DIRECTORY < YOUR_REQUEST.json
python3 -S -B -m eidos.contextual_service --init-local \
  --config NEW_PRIVATE_CONFIG.json --registry NEW_MODEL_DIRECTORY
python3 -S -B -m eidos.contextual_service --config NEW_PRIVATE_CONFIG.json
```

The local service defaults to loopback port 8767 and reuses the existing signed v2 wire/browser. Only the explicit contextual entry point selects the new loader. Initialization prints no operator secret. Use the key from your newly created private configuration in the local browser; the assistant's checks create and remove only fresh fixture keys. No hosted model, CDN, install or model download is required.

## Bounds and interpretation

Current explicit model limits are 512 context tokens, 64 context spans of at most 48 tokens, 128 prompt tokens, 64 tokens per option and 2,048 total option tokens. The contract allows up to 255 options, but the smaller work/tape bounds can refuse such requests. Refusal never means silently dropping options or truncating context. This prototype does not demonstrate long-context or 255-option semantic quality. Reverse-mode differentiation has a bounded original scalar tape; inference uses ordinary floats.

CHOICE, BINARY and anchored SCORE decoding return structured probabilities with `review_required=true`, `value=null`, no calibrated confidence and no action authority. All are conditional on the offered options. Numeric anchors are decoding metadata, not a learned arithmetic mechanism. The fresh semantic challenge evaluates CHOICE binding/order tasks; binary/score checks establish typed mechanics only.

The challenge separates fresh-world, novel-name, new-wording and joint shifts within one finite public ontology. Counterfactual variants stay grouped; relation cycles use three/five nodes so the two-link direction is distinguishable. Ordering patterns remain deliberately shared regression families. None is a pristine real-domain, multilingual or LAYA/JEV benchmark. A synthetic 95% milestone would require broader independent validation before supporting product quality claims.

The HTTP server remains a single-process loopback development service. Its socket watchdog does not interrupt CPU work; production identity/TLS, process isolation, persistent replay protection, recovery/load/drift and promotion qualification remain open. No existing serving profile is changed by creating or evaluating this candidate.

The initial v3 source preserved exact name pointers internally but omitted their candidate-specific readout, leaving equal-count unknown candidate names indistinguishable. v3.1 adds each candidate spelling's key and copied-value attention mass at every read, including the final read, and uses distinct architecture/model identifiers. Initial v3 weights are refused; this is a separate experiment. See the contextual experiment report for observed outcomes, not a capability guarantee.
