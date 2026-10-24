# Original question-conditioned EIDOS

EIDOS now has a separate trainable model path that scores **context, the actual question, and each caller-supplied answer description**. It returns structured probabilities for CHOICE, BINARY and anchored SCORE tasks. Its output size follows the offered candidates; it does not require their IDs to be pre-trained class names. There is no text-generation model or remote inference service.

This is original implementation, trained from scratch using only standard-library Python. The compact architecture is a research model, with no pretrained language knowledge, production calibration or established LAYA/JEV parity. The earlier 95.50% DBpedia result belongs to the separate frozen fixed-class model. Do not transfer that score to this new architecture.

## Train an owned artifact

From the repository's `eidos` directory:

```sh
mkdir -m 700 owned-decision
python3 -S -B -m eidos.train_decision \
  --input examples/decision-owned-demo/training.jsonl \
  --provenance examples/decision-owned-demo/provenance.json \
  --output owned-decision/model \
  --model-id owned-question-decision-demo-v1 --epochs 10
python3 -S -B -m eidos.decision \
  --registry owned-decision/model \
  < examples/decision-owned-demo/request-choice.json
```

Repeat the inference command with `request-binary.json` or `request-score.json` for the other types. These visible original mechanical examples are deliberately small and share templates. They demonstrate the workflow, not reliable answers or independent language generalization. The old version of this compact model completed the pipeline but correctly answered only 4/10 forced choice/binary smoke requests; that failed capability observation is retained. Subsequent representation work addresses independently identified information loss and must be evaluated separately.

Use your own authorized, independently labeled tasks for a useful model. The [data guide](DECISION-DATA.md) specifies the different training format: `request`, `target_option_id`, `source_group`, plus a four-field provenance declaration. It supports proprietary material; the field named `license` does not require an open-source license. Declarations are not independently verified rights. No blanket public license is granted to original EIDOS code by this workflow.

The trainer fits token representations and shared scorer parameters from zero-pretrained inputs, with fixed final-epoch Adam. Default settings are 20 epochs, seed 113, rate 0.005, global data-gradient norm clip 5 and coupled L2 0.00001 applied to all parameters, including biases. Calibration/model selection is not part of training. A bounded work estimate and cooperative 600 CPU / 900 elapsed-second checks can refuse a job; these are not hostile-caller or aggregate process-tree controls. Choose the recipe before examining independent evaluation outcomes. The documented v2 fixture uses 10 epochs: its pre-fit work estimate is 724,294,656 units; 20 would exceed the one-billion-unit bound at 1,382,324,736. That capacity decision preceded v2 fitting and was not selected on fixture answer quality. A later training-only optimization diagnostic compared predeclared rates 0.02/0.005/0.001, with no smoke/test inputs mounted. Rate 0.005 had the lowest final training cross-entropy (0.32118 versus 1.37752 and 1.26084) and became the default. These are fitting diagnostics, not generalization estimates; all earlier poor observations remain visible.

Output is a new private directory containing `model.json`, `weights.f64`, `registry.json` and `TRAINING-RECEIPT.json`. It refuses existing destinations and symlinks. Parameters use checked little-endian float64 without inference quantization. File hashes establish identity; retain an independently recorded registry checksum to authenticate a particular release. The complete receipt is required. Fitting never changes an existing serving profile.

## Run the local signed API and browser

After training, create a new local configuration and launch the service:

```sh
python3 -S -B -m eidos.decision_service --init-local \
  --config owned-decision/local-config.json \
  --registry owned-decision/model
python3 -S -B -m eidos.decision_service \
  --config owned-decision/local-config.json --port 8766
```

Open `http://127.0.0.1:8766/` on the same machine. Use key ID `decision-operator` and the operator key from the newly generated private config. The browser retains the imported key in tab memory, clears its input after connecting, and disconnects on reload. Do not publish the configuration. No secret value is printed by provisioning.

`POST /v2/decisions` accepts the versioned JSON request. `GET /v2/model` returns the pinned model identity and policy. Both require the same HMAC message convention as the original EIDOS local API: key ID, timestamp, nonce, method, exact path and SHA256 of exact body bytes separated by newlines, under the four `X-Eidos-*` headers. Exact framing, host/origin, bounded size, timestamp, replay and rate checks apply. The development server binds to loopback only, retains replay state in memory and provides no cross-process tenancy or production TLS/load/recovery guarantee. An HTTP socket watchdog is not a model CPU interrupt.

The legacy service remains `eidos.service` on its own configuration and `/v1` routes. The new artifact format and service are separate; they do not reinterpret or activate the old benchmark models.

## Structured semantics and review

- **CHOICE:** probabilities over exactly the supplied candidate meanings, mapped to supplied opaque IDs.
- **BINARY:** explicit `true` and `false` descriptions; response includes `probability_true`.
- **SCORE:** probabilities over explicitly described numeric anchors; the candidate score is their weighted expectation, with an anchor-distribution standard deviation. This is not a continuous point probability or proof of rubric understanding.

Every result has `status=review_required`, `value=null`, `review_required=true`, uncalibrated probability semantics and no action authority. `candidate_value` and `distribution` are diagnostics. Normalization, concentrated probabilities, typed JSON and a matching checksum do not establish calibrated confidence, correctness or permission to act. All candidate probabilities are conditional on the caller's offered answer set; missing correct options require a separately validated unknown/abstention policy.

Contract limits include 255 candidates and 65,536 context **characters**, with stricter explicit tokenizer/span/work bounds inside the model. Processing accounts for all accepted context or refuses; it never silently clips context or drops offered options. These are capacity/refusal limits, not measured 64k-token semantic context or useful 255-option accuracy. Actual latency depends on hardware and workload; no vendor latency figure is inherited.

## Architecture and limitations

The common public architectural observation from LAYA is joint conditioning on instructions, answer meanings and context, followed by a shared candidate scorer. REA authenticated the inspected public source; no implementation, model weights or vendor outputs were copied into this model. JEV's private implementation was not accessed.

Architecture v2 learns shared word and UTF-8 byte representations, within-word/sequence position moments, a global span-position feature, context attention and a shared nonlinear option function. Byte composition applies to every token and preserves unfamiliar spellings; it does not supply pretrained meaning. First-moment encodings are still compact summaries with possible collisions, not a contextual foundation encoder. Listwise cross-entropy competes over every supplied option. Different questions and option descriptions genuinely reach scoring; opaque IDs and numeric anchor values supply no semantic features. This closes the former fixed-taxonomy interface gap while leaving broad semantic competence as a training/representation/evaluation problem.

The small original fixtures do not establish semantic generalization, multilingual use, unseen-domain competence, unknown detection, calibration, broad-context reasoning, LAYA/JEV parity or production readiness. A source-level audit identified information loss in the first compact model's question/option order, global span order and unknown words. The current architecture version must be stated in each artifact and validation receipt; old weights must never be silently loaded under changed equations.

## Verify mechanics

```sh
python3 -S -B -m unittest discover -s tests -p 'test_decision*.py' -v
python3 -S -B tools/decision_smoke.py --output /absolute/new/private/mechanical-run
```

The second command deliberately fits the 144-task original fixture, queries its model through the real CLI and signed loopback service, exercises all typed outputs and 255-option mechanics, then stops the service and deletes generated keys. It retains the mechanical model and aggregate result in the new output directory. It reports visible fixture outcomes honestly; they are not a fresh quality estimate. Run with external limits in a separate offline development environment.

An optional real-browser control is provided separately when Chromium is already installed; it is a test dependency, not an inference requirement. Serving HTML/JavaScript assets alone is not browser automation evidence. No library installation, existing credential, proprietary service, old benchmark rescore or production activation is required by the authored workflow.

Independent, rights-reviewed multi-task supervision and pristine source/question/label-family holdouts remain necessary before making the intended product claims. See the current evidence for exact executed checks and architecture identity. `mission_complete=false`; `production_certified=false`.
