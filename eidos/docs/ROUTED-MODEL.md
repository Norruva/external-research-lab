# Original question-routed model v4

This separately versioned original standard-library model extends contextual v3.1 with learned character features, question/current-state/hop-conditioned token keys and values, and question-dependent read-depth mixing. It addresses identified information-path/control limitations; it is not a pretrained language encoder or a guarantee of reliable decisions. Previous versions and their negative outcomes remain unchanged. See ROUTED-RESULT.md for observed outcomes once published.

For every normalized token, a learned32-bucket vector averages deterministic SHA256 features over ALL boundary-marked character unigrams/bigrams/trigrams. It is added to the learned word vector before nonlinear position/neighbor encoding. Unknown words can therefore differ without pretrained word embeddings, but hashing collides and spelling does not supply unknown meanings. No substring cutoff is used. Exact-spelling pointers separately preserve identity when copying through three memory reads.

At each read h, control c=tanh(question+current_query+hop_embedding) conditions diagonal token-state interactions and positional terms for both key and copied-value attention. Unlike v3.1, the key convention can change with the question/hop. A question-only softmax mixes three copied candidate masses. A stable softplus scale times log(1e-8+mixed_mass) contributes alongside the shared residual head. Candidate-dependent copied mass is a feature, not an independently normalized predictive probability; the final complete-option softmax is the returned distribution. No task parser, graph solver, hidden template keyword table, pretrained checkpoint or vendor call is used in inference.

## Generate and fit original tasks

Run from this repository's eidos directory, using NEW paths:

```sh
python3 -S -B -m eidos.routed_curriculum --output /tmp/eidos-routing-data
python3 -S -B -m eidos.train_routed --preflight-only \
  --input /tmp/eidos-routing-data/training.jsonl \
  --provenance /tmp/eidos-routing-data/provenance.json --epochs 16
```

The finite original curriculum contains paired five-node worlds, distinct points/cites cycles, rotated hues and same-context/same-choice questions requiring different directions, depths and relations. Training explicitly supervises all operator lexemes. New composition tests rearrange known words; this changes the target from the old unknown-wording challenge and cannot be called a matched performance gain. Default counts128 training and four32-row slices use four/two paired source groups respectively. Public ontology, lexicon and grammar families remain known; these are not pristine natural-domain or LAYA/JEV benchmarks. The optional --exclude-hashes reads a bounded regular JSON list of structural digests only; the recorded research control supplies prior exposure hashes without old rows or gold.

Preflight reads/encodes training only and performs no forward loss or fitting. Choose/freeze the recipe before quality. Fitting uses those arguments without --preflight-only, adding --output NEW_MODEL_DIRECTORY --model-id YOUR_MODEL_ID. The original Adam float64 trainer builds vocabulary on training only and uses the final checkpoint. Its v4 work estimate limit is12b, CPU600/wall900 seconds, with strict input/parameter/network/tape limits. The recorded experiment selects epochs by a predeclared resource rule, never quality. Caller provenance declares rights; it does not independently certify them.

## Query or serve a new artifact

The four private files are model.json, weights.f64, registry.json and TRAINING-RECEIPT.json. Distinct v4 formats/policies and complete source/receipt/weight identities are validated; older loaders refuse these weights. Keep a registry digest independently to authenticate a release.

```sh
python3 -S -B -m eidos.routed --registry NEW_MODEL_DIRECTORY < YOUR_REQUEST.json
python3 -S -B -m eidos.routed_service --init-local \
  --config NEW_PRIVATE_CONFIG.json --registry NEW_MODEL_DIRECTORY
python3 -S -B -m eidos.routed_service --config NEW_PRIVATE_CONFIG.json
```

The signed local service defaults to http://127.0.0.1:8768/ and reuses the original v2 wire/browser. Initialize creates a new private configuration and prints no secret; use routed-operator and the new config's key in the browser. No existing profile is activated or overwritten. Python3.12+/POSIX with standard SQLite/OpenSSL and browser WebCrypto are required; application inference needs no pip/npm package, remote model, CDN or internet. The isolated research harness additionally uses bwrap/strace; those are verification tooling, not inference dependencies.

## Limits

Current text bounds512 context tokens/64spans/48tokens per span,128 prompt tokens,64 tokens per option and2048 total option tokens, plus5m per-request work/500k gradient tape/120k parameter bounds. Full255-option and long-context quality are unproven; work bounds can refuse contract-valid requests. Inputs are fully processed or refused, never silently truncated. Hashed character features do not prove collision-free/injective representation or semantics.

CHOICE/BINARY/anchored SCORE output structured option-conditional probabilities. Every result remains uncalibrated, review_required=true,value=null,noactions. Numeric anchors are decoding metadata, not a learned arithmetic mechanism. Synthetic quality concerns CHOICE; binary/score transport checks alone are not semantic validation. The single-process loopback development server still needs production identity/TLS, persistent replay protection, CPU interruption/process isolation, recovery/load/drift and qualification. No blanket public license is newly granted. No broad95%, LAYA/JEV superiority, ownership certificate or production certification follows from implementation/mathematical tests.

Before the first scientific fit, the initial4m request preflight refused longer training requests. A recorded resource-only revision raises only v4 work/tape limits and serializes them strictly, under unchanged CPU600/wall900/768MiB process caps. A highest-work training-request gradient probe (no optimizer/quality output) and fixed two-times probe reserves additionally constrain epoch selection. The initial source packet/refusal remain retained; the exact generated data and model mathematics are unchanged.

The prefit implementation was then optimized with original fused sum/dot/triple-contraction autodiff primitives, retaining model equations and ordered floating-point forwards. Independent derivative tests guard the change. Both the earlier scalar resource probe and the explicitly declared optimized-backend probe are retained; neither performs an optimizer step or reports quality. The fixed resource-only epoch rule remains unchanged.
