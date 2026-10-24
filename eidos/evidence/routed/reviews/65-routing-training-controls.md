# Training-only controls for question-conditioned routing

This is a design review only. It inspected the v3.1 source, the original generator and the published aggregate report. No model was executed, no fit/prediction/scoring occurred, no existing study was reopened and no app code was edited. The controls below are proposed original development fixtures, never independent quality evidence.

## What the previous experiment leaves unidentified

In `eidos/decision_contextual.py`, `_run` constructs `keys` before the candidate/read loop from context states and a global positional scalar. Thus the learned key convention itself cannot change with the question, candidate or hop. Fact attention and value attention do depend on query state, so this is not a proof that inverse/deep tasks are impossible. It is a specific information-path bottleneck worth testing. The initial exact-spelling query also includes every prompt spelling found anywhere in context, including common grammar words; a name is not automatically the dominant lookup key. The head can use all three reads without explicitly choosing a question-controlled stopping depth. Positional key/value initialization can reward location shortcuts.

The current generator changes both context wording and question wording together in `new_wording`/`joint`. Consequently those aggregate failures cannot separate question interpretation from context extraction. Its question families are generally evaluated with different query subjects and sometimes different candidate types. Include exact same-context/same-options query contrasts before interpreting broad accuracy as question use.

## Canonical original contrast fixtures

Use opaque request IDs/option IDs and shuffle option order independently of answer. IDs and source-group metadata must stay out of features. Every row in a contrast set has byte-identical context and option descriptions unless the variation is explicitly the thing being tested. The following names/text are newly authored proposals, not rows taken from any prior corpus.

**World R:**

```
Kora points to Luma. Luma points to Neri. Neri points to Pavo.
Pavo points to Varo. Varo points to Kora.
Kora has hue coral. Luma has hue denim. Neri has hue hazel.
Pavo has hue mauve. Varo has hue sepia.
```

Entity options are Kora/Luma/Neri/Pavo/Varo. Hue options are coral/denim/hazel/mauve/sepia. The five-cycle ensures forward-two and backward-two differ; do not substitute the ambiguous four-cycle.

| Contrast | Questions on the same context and option set | Gold sequence | Required distinction |
|---|---|---|---|
| Forward depth | `Starting at Kora, follow zero links. Which node is reached?`; same with `one`, `two` | Kora, Luma, Neri | Question-controlled stop/read depth |
| Inverse direction | `Starting at Kora, follow one outgoing link. Which node is reached?`; `Starting at Kora, follow one incoming link. Which node is reached?` | Luma, Varo | Reverse key/value roles |
| Two-step direction | Same pair with `two` | Neri, Pavo | Repeated direction, not one-step memorization |
| Property depth | `What hue does Kora have?`; `Follow one outgoing link from Kora. What hue is there?`; same with `two` | coral, denim, hazel | Traverse then change read type to property |
| Inverse property lookup | `Which node has hue coral?`; same with denim/hazel | Kora, Luma, Neri | Ground the hue mention, return a name |

All query words/operators used for learning must receive explicit authored supervision. A correct result here is a development fitting result, not a generalization claim.

**World T: competing relation labels.** Append to World R:

```
Kora cites Varo. Luma cites Kora. Neri cites Luma.
Pavo cites Neri. Varo cites Pavo.
```

With the same entity options, contrast `Follow one points-to link from Kora. Which node is reached?` (Luma) against `Follow one cites link from Kora. Which node is reached?` (Varo). With the same hue options, ask the hue after each relation (denim versus sepia). Two relations make node-name overlap and depth alone insufficient. Train the meaning of the two relation labels; do not infer that arbitrary unseen words have these meanings.

**World U: asymmetric non-cycle regression.** Use an authored directed graph with a unique incoming edge to the queried node and branching elsewhere. Ask unique forward/inverse queries only; do not label a multi-predecessor question as single-gold by silently picking one. A cycle-only learner can exploit algebraic shortcuts. This is a later training-only representation control, not required for the smallest initial fit.

## Controlled variation axes

1. **Context extraction only:** hold each question byte-identical; render the facts as separate short sentences, then joined property/relation clauses in both field orders. Add the neutral prefix `Record:` to each fact. Shuffle sentence order. Gold and full option set must stay fixed. Use neutral tokens in training; an entirely unseen prefix tests robustness but not a proven invariant of tokenization/spans.
2. **Question interpretation only:** keep context/options byte-identical, use supervised paraphrases such as `outgoing/incoming`, `successor/predecessor` and `zero/one/two`. Include all operator meanings in training. Later reserve combinations or independently authored forms, not merely a synonymous token whose meaning has never been grounded.
3. **Exact-name identity:** bijectively rename all entities across context/question/options together, preserving edges. A hand-parameter or explicitly UNK-embedding mechanical test can prove the spelling-pointer path distinguishes arbitrary names. A fitted test on reserved names measures limited OOV transfer and must be reported as development if it guides code. Do not run the old novel-name slices again.
4. **Value identity:** similarly rename property values to opaque symbols while preserving answer equality. This separates copying from memorizing five hue embeddings.
5. **Option invariance:** permute all options and opaque IDs, retaining semantic text; compare probability by semantic description. Add a supported unrelated distractor only under a separately stated probability-set expectation: normalization changes, so exact old probabilities are not invariant.
6. **Question necessity:** a context/options-only oracle cannot answer all rows of each depth/direction/relation contrast because gold differs while those inputs are identical. Report the fraction of full contrast sets all correct, not just row accuracy. Record paired answer changes and both-correct counts.

## Small bounded development campaign

Start with two newly authored worlds, two context renderings and six questions per world/rendering: 24 original rows in grouped contrasts. Questions should cover outgoing-one/two, incoming-one/two, direct property and one relation-selector contrast. Hue and entity questions use separate complete candidate sets, while each decisive contrast keeps its set fixed. Fix all rows, settings, seed and a final checkpoint before running. The purpose is to see whether the implementation can fit and respond to the QUESTION; it is allowed to be a deliberately visible training fixture.

Before any fitting, require independent scalar math, all-coordinate tiny-model gradients and hand-set question-routing witnesses. Training-only preflight must include any added routing parameters and context/read cost in work/tape estimates. Use the existing 600 CPU/900 elapsed seconds and 768 MiB per-process ceiling with a parent watchdog; a scoped protocol should define one recipe/attempt rather than quality-selected restarts. This small corpus should fit well within the previous resource envelope, but that is an expectation, not a measured guarantee. If a fit cannot learn the visible contrasts, fix the mechanism/optimization before spending a fresh semantic challenge.

An optional generic training mechanism is lexical-identity dropout: on a fixed declared schedule, mask a spelling's learned embedding consistently across context/question/options while retaining its exact-spelling channel. It must be implemented generically, not by a hidden graph/name parser, and have independently checked gradients and reproducibility. This can discourage memorized-name shortcuts; it cannot supply unknown operator meanings. Do not silently reinterpret encoded records to violate the current word-ID validation contract.

After development, preserve all visible controls as exposed. Freeze the next architecture, recipe, 95% criterion and a new exposure-disjoint grouped challenge before measuring quality. Exclude both prior world/sequence hashes and any new development world/structure/group hashes. Keep target/oracle sidecars out of fitting and prediction. Hash disjointness is not semantic/template independence. New natural-domain or LAYA/JEV claims still need different evidence.

## Unknown-word semantics is a real identifiability limit

Suppose an operator token appears only in a future question and has never received meaning-bearing training/context. Two interpretations that swap that token's meaning produce identical training data and likelihood but different correct answers. Without an external semantic prior or a meaning declaration, neither copying exact spelling nor a binomial equation identifies which interpretation is intended. Known-language pretrained models have such priors; this from-scratch 48-word model does not. This explains why novel entity *identity* and novel operator *meaning* must be treated separately. It does not prove which factor caused the measured v3.1 failures.

Use owned paraphrase/operator supervision and explicit meaning-bearing context to reduce this ambiguity. Do not hardcode the old generator's template strings in inference, remap exposed held-out vocabulary into a new score on the old cohort, or call memorized synthetic performance general language understanding.
