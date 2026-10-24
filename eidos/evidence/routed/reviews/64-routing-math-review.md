# Question-conditioned routing review (source only)

Scope: review of the original v3.1 source at public commit `db067f0e3d14fd1b315d44e524fca562e697866f` and its authored generator. No fitting, model loading, prediction, scoring, tests, downloads, credential or service access was performed for this review. Earlier experiments remain consumed and unchanged. These recommendations are hypotheses, not quality evidence.

## Actual information paths and limitations

In `eidos/eidos/decision_contextual.py`, key-token attention is computed once before the candidate/read loops. Its logits use only the contextual token state and one global position coefficient. The question and current read state do not choose the key role. Value-copy logits have a global token scorer, global position coefficient, and one shared scalar multiplying a token-state/current-query dot product. All three reads share these parameters. The head sees every read in a fixed slot and can learn some question/depth interaction; it would be incorrect to claim that v3.1 categorically cannot represent inverse or multi-hop questions. Nevertheless, it must learn role changes and stopping indirectly, with a strong globally shared positional prior.

Exact-spelling distributions already preserve unseen entity identity and candidate key/value masses already expose that identity to the head. Preserve both. Unknown tokens still share an embedding, so spelling identity does not provide the meaning of an unseen instruction word. The generator's shifted prompts introduce such words as `shade`, `onward`, `immediate`, and `twice`, and shifted facts introduce a leading `Node` and different predicate word order. These source changes expose both lexical-supervision and positional-shortcut risks. The observed failure does not isolate either as the sole cause.

## Small original next parameterization

Retain the current local nonlinear encoder, exact spelling representation, artifact/transport bounds and complete option normalization. Recompute token-key and token-value attention inside each read. Let token state be `s_st`, token position `rho_st`, question vector `q`, current recurrent query `u_h`, and a learned small hop vector `e_h`. Set `c_h = tanh(q + u_h + e_h)`.

Use diagonal bilinear control, avoiding full matrices:

```
key_logit(st,h) = a_k dot s_st
                + (b_k * c_h) dot s_st
                + (beta_k + d_k dot c_h) * rho_st
value_logit(st,h) = a_v dot s_st
                  + (b_v * c_h) dot s_st
                  + (beta_v + d_v dot c_h) * rho_st
```

`*` between vectors is coordinate multiplication. Shared vectors plus hop embeddings are the minimal version; separate per-hop vector/position blocks are an optional larger alternative. Every control term varies across tokens. A question-only additive scalar would cancel in a token softmax and add no capacity. Keep the original differentiable spelling-copy recurrence, so a copied OOV successor can address another fact without a learned word embedding.

Make read-depth selection explicit: `pi = softmax_h(g_h dot q + b_h)`. For candidate `j`, retain per-read features and form `M_j = sum_h pi_h * copied_mass_h(candidate_j)`. Add `lambda * log(epsilon + M_j)` to the residual head's option logit before the final full-set softmax. This gives an auditable direct spelling-evidence path and a question-controlled distribution over stopping depths. If routing still depends on candidate text/current query, the different `M_j` do not necessarily sum to one: describe them as scoring features, not a separately normalized predictive distribution. Epsilon is required when an offered description does not literally occur in context. This feature does not create language semantics for nonliteral options.

A positive lambda avoids learning an anti-copy shortcut; a fixed coefficient of one is the simplest first choice, or learn a scalar through a stable softplus. An unconstrained coefficient is legal but can undo the desired bias. Implementing softplus as a naive exponential over the whole allowed parameter range would introduce overflow. A stable branch or a bounded scalar is required. Existing finite/refusal controls remain mandatory.

## Initialization and independent mechanical checks

Initialize new query/position interaction blocks close to zero, depth logits at zero (uniform mixture), and modest nonzero hop vectors. Retain an active copy path. Large key/value/depth logits can saturate the softmax and weaken useful gradients. Equal hop parameters need not remain symmetric because the pointer recurrence differs by depth, but do not assume that this guarantees learning. Never interpret a valid derivative as proof of adequate optimization.

Before any fresh diagnostic experiment, independently verify scalar forward results and all-coordinate numerical gradients through query-dependent token softmax, recurrence, depth mixture, positive scale (if learned), and the floored log readout. Include zero copied mass and nonliteral candidates; the residual head must still receive gradients. Use training-only authored controls with the same fact text but conflicting forward/reverse and one-/two-step questions. Also test neutral prefixes, field-order changes, OOV renaming, option order/ID invariance, and an irrelevant fact. Hand-constructed parameters may demonstrate representational paths, but must never be reported as learned quality. A small supervised fit of these controls is a development diagnostic, not an independent evaluation.

Broader owned paraphrase supervision is necessary to test lexical meaning; question routing alone does not justify expecting unfamiliar synonyms to work. Freeze architecture, data/exposure exclusions, fitting recipe and success criteria before a separate fresh complete prediction/scoring pass. Preserve all failed attempts. A synthetic 95% pass, if eventually obtained, would still not establish real-domain 95%, calibration, production readiness, or LAYA/JEV superiority.
