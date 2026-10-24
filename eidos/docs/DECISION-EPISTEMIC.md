# Evidence, prediction sets and investigation value

The original `eidos.decision_epistemic` module implements three finite, independently testable components from the supplied architecture direction. They sit alongside semantic prediction. They do not supply language understanding, improve classifier accuracy by themselves, identify causal effects or execute actions.

## Preserve uncertainty and contradictions

`evidence_state(positive, negative)` records two explicit Boolean support flags. The four results are `neither`, `true`, `false` and `both`. These describe declared support, not verified world truth. Missing support is not negative evidence. `merge_evidence` joins the information: repeating one assertion does not vote away a conflicting assertion.

```python
from eidos.decision_epistemic import evidence_state, merge_evidence

conflicting = merge_evidence([(True, False), (False, True)])
assert conflicting['state'] == 'both'
assert conflicting['action_authority'] is False
```

The module does not extract facts from text or authenticate evidence sources. A future typed evidence store needs actual source versions, authenticated references, time/applicability and independent checks; a model's probability is not such a proof.

## Split-conformal candidate sets

Given a fixed scorer and separate calibration outcomes, define a nonconformity score `1 - p_true` for each labeled calibration example. `fit_conformal(scores, alpha)` uses rank `ceil((n+1)*(1-alpha))`. If that rank exceeds the calibration sample count, the conservative threshold is positive infinity, represented explicitly as `None` in finite JSON, retaining every offered option. Otherwise use the corresponding ordered score. `prediction_set` includes candidates with scores at or below the threshold, keeping ties conservative.

```python
from eidos.decision_epistemic import fit_conformal, prediction_set

# Invented arithmetic fixture only; not real model calibration.
profile = fit_conformal([0.0, 0.25, 0.5], alpha=0.5)
example = prediction_set(profile, {'answer-a': 0.75, 'answer-b': 0.25})
assert example['set'] == ['answer-a']
assert example['review_required'] is True
```

A split-conformal coverage statement is marginal under the required fixed-rule, disjoint-calibration and exchangeability premises. This implementation cannot establish those premises, model/population applicability, semantic independence or correctness of supplied labels. Its profiles explicitly mark these bindings as unverified. Production use needs a release-bound calibration artifact and fresh evaluation of the actual candidate/context regime. A singleton set is neither a per-example probability nor permission to act; an empty or large set does not become a forced answer. No real EIDOS calibration was fitted for this extension.

`assess_forecast` accepts a complete review-only v2 forecast and explicit support flags, optionally with a supplied profile. It validates finite typed outcome rows, preserves the model/probability identity, and returns evidence/set diagnostics. It leaves probabilities unchanged and never enables acceptance:

```python
from eidos.decision import DecisionEngine
from eidos.decision_epistemic import assess_forecast

# Use an explicitly supplied local registry and a valid caller request.
# forecast = DecisionEngine.from_registry(registry_path).decide(request)
# assessment = assess_forecast(forecast, evidence_positive=True, evidence_negative=True)
```

The caller's evidence flags and model hash are not independently authenticated by this operation. Every assessment remains review-required, including one with positive evidence or a singleton set.

## One-step value of information

`value_of_information(prior, losses, joint, cost)` evaluates an explicitly supplied finite investigation model:

```text
risk_before = min_action sum_y prior[y] * loss[action,y]
risk_after  = sum_observation min_action sum_y joint[observation,y] * loss[action,y]
net_value   = risk_before - risk_after - investigation_cost
```

The joint table contains `P(observation, outcome)`, not a table of conditional probabilities. Its marginal must match the prior, and every action/observation must cover the complete outcome inventory. Zero-probability observations need no undefined posterior division. Review is an ordinary action with supplied costs, not an assumed perfect oracle.

The result reports the declared net value and remains advisory. It does not establish that observation probabilities, losses or costs are correct, choose a real tool, authorize an experiment, solve a sequential POMDP or identify a causal intervention. The existing separate robust-decision research explores an uncertainty-set loss criterion; it is not silently presented as a new causal solver.

## Implemented scope

The new mathematical checks use exact small cases, independent rational arithmetic, conservative quantile/tie cases, all four support states, and malformed-input/refusal checks. They do not run a vendor model or confer a classifier quality score. The larger design's causal, sequential planning, temporal-proof, multiscale and stability claims still require explicit models, measurements and suitable implementations. Combining names of mathematical fields is not a proof that the composed system has their guarantees.

No probability, evidence assertion, predicted set or positive investigation value overrides EIDOS's review-only policy. The model, evidence verification and action authority remain separate engineering concerns. `mission_complete=false`; `production_certified=false`.
