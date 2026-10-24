"""Original bounded evidence, prediction-set and information-value primitives.

These are pure finite mathematical operations. They do not extract claims from
text, establish that evidence is true, learn a classifier, calibrate probabilities,
verify sampling assumptions, identify causal effects or authorize actions.
Conformal sets can have a marginal coverage theorem under a fixed scoring rule,
disjoint calibration and exchangeable future examples; this code cannot establish
those premises. A caller-defined information model is not a causal/POMDP model.
"""
from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import math
import re

MAX_CALIBRATION = 100_000
MAX_OUTCOMES = 255
MAX_ACTIONS = 64
MAX_OBSERVATIONS = 512
MAX_WORK_UNITS = 2_000_000
MAX_LOSS = 1_000_000_000.0
TOLERANCE = 1e-12
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z', re.ASCII)
_SHA = re.compile(r'[0-9a-f]{64}\Z', re.ASCII)


class EpistemicError(ValueError):
    """Invalid finite caller declarations; errors do not contain their values."""


def _need(condition, reason):
    if not condition:
        raise EpistemicError(reason)


def _number(value, low, high, reason):
    _need(type(value) in (int, float), reason)
    try:
        number = float(value)
    except (OverflowError, ValueError):
        raise EpistemicError(reason) from None
    _need(math.isfinite(number) and low <= number <= high, reason)
    return number


def _identifier(value):
    return type(value) is str and _ID.fullmatch(value) is not None


def _review():
    return {'review_required': True, 'action_authority': False}


def evidence_state(positive, negative):
    """Represent explicit support flags, preserving missingness and contradiction.

    True/false label the support state, not independently established truth.
    Multiple copies of the same assertion do not turn these flags into stronger
    probabilities. No text is interpreted and no source is authenticated.
    """
    _need(type(positive) is bool and type(negative) is bool,
          'explicit_boolean_support_required')
    state = {(False, False): 'neither', (True, False): 'true',
             (False, True): 'false', (True, True): 'both'}[(positive, negative)]
    return {'format': 'eidos-four-valued-evidence-v1', 'state': state,
            'positive_support': positive, 'negative_support': negative,
            'support_truth_or_source_verified': False,
            **_review()}


def merge_evidence(supports):
    """Join bounded support pairs by information union, never majority voting."""
    _need(type(supports) in (list, tuple) and len(supports) <= 4096,
          'evidence_collection_bound')
    positive = negative = False
    for item in supports:
        _need(type(item) in (list, tuple) and len(item) == 2 and
              all(type(flag) is bool for flag in item),
              'explicit_boolean_support_pair_required')
        positive = positive or item[0]
        negative = negative or item[1]
    return evidence_state(positive, negative)


@dataclass(frozen=True)
class ConformalProfile:
    alpha: float
    calibration_count: int
    rank: int
    threshold: float | None
    calibration_scores_sha256: str

    def as_dict(self):
        _validate_profile(self)
        return {'format': 'eidos-split-conformal-score-profile-v1',
                'alpha': self.alpha, 'calibration_count': self.calibration_count,
                'rank': self.rank, 'threshold': self.threshold,
                'threshold_is_positive_infinity': self.threshold is None,
                'nonconformity': 'one_minus_offered_true_label_probability',
                'calibration_scores_sha256': self.calibration_scores_sha256,
                'sampling_assumptions_verified': False,
                'model_population_and_score_rule_binding_verified': False,
                'per_example_or_subgroup_coverage_guarantee': False,
                **_review()}


def _rank(count, alpha):
    # Use the exact rational value of the supplied float to avoid an
    # accidentally downward-rounded integer quantile rank.
    value = (count + 1) * (1 - Fraction.from_float(alpha))
    return (value.numerator + value.denominator - 1) // value.denominator


def _validate_profile(profile):
    _need(type(profile) is ConformalProfile, 'conformal_profile_required')
    alpha = _number(profile.alpha, 0, 1, 'alpha_outside_open_unit_interval')
    _need(0 < alpha < 1, 'alpha_outside_open_unit_interval')
    _need(type(profile.calibration_count) is int and
          1 <= profile.calibration_count <= MAX_CALIBRATION,
          'calibration_count_bound')
    _need(type(profile.rank) is int and
          profile.rank == _rank(profile.calibration_count, alpha),
          'conformal_rank_mismatch')
    if profile.rank > profile.calibration_count:
        _need(profile.threshold is None, 'infinite_quantile_required')
    else:
        _number(profile.threshold, 0, 1, 'conformal_threshold_bound')
    _need(type(profile.calibration_scores_sha256) is str and
          _SHA.fullmatch(profile.calibration_scores_sha256) is not None,
          'calibration_digest_required')


def fit_conformal(scores, alpha):
    """Fit the conservative split-conformal quantile of supplied 1-p_true scores.

    The model and scoring rule must be fixed before a disjoint calibration set;
    alpha must be chosen without selecting on calibration/test performance.
    Scores are declarations, not verified labels, independent samples or proof.
    If ceil((n+1)(1-alpha)) exceeds n, the threshold is +infinity and every
    offered candidate will be retained. None represents +infinity in finite JSON.
    """
    _need(type(scores) in (list, tuple) and 1 <= len(scores) <= MAX_CALIBRATION,
          'calibration_collection_bound')
    alpha = _number(alpha, 0, 1, 'alpha_outside_open_unit_interval')
    _need(0 < alpha < 1, 'alpha_outside_open_unit_interval')
    values = [_number(score, 0, 1, 'nonconformity_score_bound') for score in scores]
    rank = _rank(len(values), alpha)
    threshold = sorted(values)[rank - 1] if rank <= len(values) else None
    canonical = json.dumps(values, separators=(',', ':'), allow_nan=False).encode()
    profile = ConformalProfile(alpha, len(values), rank, threshold,
                               hashlib.sha256(canonical).hexdigest())
    _validate_profile(profile)
    return profile


def _probabilities(values, *, normalized=True, outcomes=None):
    _need(type(values) is dict and 2 <= len(values) <= MAX_OUTCOMES,
          'complete_outcome_probability_map_required')
    _need(all(_identifier(name) for name in values), 'outcome_identifier_refused')
    if outcomes is not None:
        _need(set(values) == set(outcomes), 'outcome_inventory_mismatch')
    result = {name: _number(value, 0, 1, 'probability_bound')
              for name, value in sorted(values.items())}
    if normalized:
        _need(math.isclose(math.fsum(result.values()), 1., rel_tol=0,
                           abs_tol=TOLERANCE), 'probabilities_must_sum_to_one')
    return result


def prediction_set(profile, probabilities_by_id):
    """Return complete-set candidate membership, with conservative <= ties.

    This is not a probability of correctness, an automatic accept rule or a
    guarantee for this particular input. Unseen classes, changed outcome spaces,
    distribution shift and scorer/calibration mismatch need separate treatment.
    Ordinary finite-precision score comparisons are used, not interval arithmetic.
    """
    _validate_profile(profile)
    probabilities = _probabilities(probabilities_by_id)
    selected = [name for name, probability in probabilities.items()
                if profile.threshold is None or 1. - probability <= profile.threshold]
    return {'format': 'eidos-conformal-prediction-set-v1', 'set': selected,
            'set_size': len(selected), 'offered_count': len(probabilities),
            'alpha': profile.alpha, 'nominal_marginal_coverage': 1. - profile.alpha,
            'calibration_count': profile.calibration_count,
            'threshold': profile.threshold,
            'threshold_is_positive_infinity': profile.threshold is None,
            'calibration_scores_sha256': profile.calibration_scores_sha256,
            'probability_semantics': 'set_membership_not_per_answer_probability',
            'sampling_assumptions_verified': False,
            'model_population_and_score_rule_binding_verified': False,
            'per_example_or_subgroup_coverage_guarantee': False,
            **_review()}


def value_of_information(prior_by_id, loss_by_action, joint_by_observation, cost):
    """Finite one-step Bayes information value under explicit caller declarations.

    joint_by_observation[o][y] is P(observation=o,outcome=y), not P(y|o).
    It must match the supplied prior marginal and complete outcome inventory.
    Expected posterior Bayes risk is sum_o min_a sum_y P(o,y)L(a,y), so zero
    probability observations require no undefined posterior division. All actions,
    including review, have their supplied costs; review is not presumed perfect.
    A positive net value recommends no real experiment or action automatically.
    This operation does not solve a sequential POMDP or identify a causal effect.
    """
    prior = _probabilities(prior_by_id)
    outcomes = tuple(prior)
    cost = _number(cost, 0, MAX_LOSS, 'investigation_cost_bound')
    _need(type(loss_by_action) is dict and 1 <= len(loss_by_action) <= MAX_ACTIONS and
          all(_identifier(action) for action in loss_by_action), 'action_inventory_bound')
    losses = {}
    for action, row in sorted(loss_by_action.items()):
        _need(type(row) is dict and set(row) == set(outcomes), 'complete_loss_matrix_required')
        losses[action] = {outcome: _number(row[outcome], 0, MAX_LOSS, 'loss_bound')
                          for outcome in outcomes}
    _need(type(joint_by_observation) is dict and
          1 <= len(joint_by_observation) <= MAX_OBSERVATIONS and
          all(_identifier(observation) for observation in joint_by_observation),
          'observation_inventory_bound')
    _need(len(outcomes) * len(losses) * len(joint_by_observation) <= MAX_WORK_UNITS,
          'information_value_work_bound')
    joint = {observation: _probabilities(row, normalized=False, outcomes=outcomes)
             for observation, row in sorted(joint_by_observation.items())}
    marginal = {outcome: math.fsum(row[outcome] for row in joint.values())
                for outcome in outcomes}
    _need(all(math.isclose(marginal[name], prior[name], rel_tol=0, abs_tol=TOLERANCE)
              for name in outcomes), 'joint_marginal_must_match_prior')
    _need(math.isclose(math.fsum(marginal.values()), 1., rel_tol=0,
                       abs_tol=TOLERANCE), 'joint_probabilities_must_sum_to_one')
    # Use the checked joint marginal coherently for both sides of the identity.
    # It can differ from the supplied prior only by declared float tolerance;
    # no probabilities are silently renormalized.
    action_risks = {action: math.fsum(marginal[y] * row[y] for y in outcomes)
                    for action, row in losses.items()}
    before = min(action_risks.values())
    best = [action for action, risk in action_risks.items() if risk == before]
    observation_rows = []
    weighted_risks = []
    for observation, row in joint.items():
        mass = math.fsum(row.values())
        if mass == 0:
            observation_rows.append({'id': observation, 'probability': 0.,
                'posterior': None, 'best_actions': [], 'minimum_posterior_loss': None})
            weighted_risks.append(0.)
            continue
        weighted = {action: math.fsum(row[y] * loss[y] for y in outcomes)
                    for action, loss in losses.items()}
        minimum = min(weighted.values())
        weighted_risks.append(minimum)
        observation_rows.append({'id': observation, 'probability': mass,
            'posterior': {y: row[y] / mass for y in outcomes},
            'best_actions': [action for action, risk in weighted.items() if risk == minimum],
            'minimum_posterior_loss': minimum / mass})
    after = math.fsum(weighted_risks)
    raw_gross = before - after
    # With coherent finite joint/loss declarations, information can be ignored;
    # any tiny negative gross value is an ordinary rounding artifact.
    gross = max(0., raw_gross)
    net = gross - cost
    return {'format': 'eidos-one-step-information-value-v1',
            'minimum_prior_loss': before, 'best_prior_actions': best,
            'prior_action_losses': action_risks,
            'expected_minimum_posterior_loss': after,
            'gross_information_value': gross, 'investigation_cost': cost,
            'net_information_value': net, 'positive_declared_net_value': net > 0,
            'rounding_nonnegative_clamp_applied': raw_gross < 0,
            'coherent_prior_from_joint': marginal,
            'observations': observation_rows,
            'probability_loss_and_cost_models_verified': False,
            'causal_or_sequential_planning_claimed': False,
            **_review()}


def assess_forecast(forecast, *, evidence_positive=False, evidence_negative=False,
                    conformal_profile=None):
    """Attach bounded declared-evidence/optional-set diagnostics to a v2 forecast.

    This does not upgrade uncalibrated probabilities, authenticate a model or
    establish that a calibration population and offered-option regime apply.
    Even a singleton set and positive evidence retain mandatory review and no
    action authority. Four-valued support comes from explicit caller assertions;
    it is never inferred from the model's confidence or copied into probabilities.
    """
    _need(type(forecast) is dict and
          forecast.get('format') == 'eidos-decision-response-v2' and
          forecast.get('kind') in ('CHOICE', 'BINARY', 'SCORE'),
          'v2_decision_forecast_required')
    _need(forecast.get('status') == 'review_required' and
          forecast.get('review_required') is True and forecast.get('value') is None and
          forecast.get('authorization') == 'recommendation_only_no_action_authorized',
          'review_only_forecast_required')
    request_id = forecast.get('request_id')
    _need(_identifier(request_id), 'forecast_request_identifier_refused')
    model = forecast.get('model')
    _need(type(model) is dict and type(model.get('sha256')) is str and
          _SHA.fullmatch(model['sha256']) is not None, 'forecast_model_digest_required')
    rows = forecast.get('distribution')
    _need(type(rows) is list and 2 <= len(rows) <= MAX_OUTCOMES,
          'complete_forecast_distribution_required')
    probabilities = {}
    score_values = set()
    for row in rows:
        _need(type(row) is dict and set(row) == {'id', 'value', 'probability', 'logit'},
              'forecast_distribution_fields_mismatch')
        name = row['id']
        _need(_identifier(name) and name not in probabilities,
              'forecast_duplicate_or_invalid_option_identifier')
        _number(row['logit'], -math.inf, math.inf, 'finite_forecast_logit_required')
        value = row['value']
        if forecast['kind'] == 'CHOICE':
            _need(value == name and type(value) is str, 'forecast_choice_value_mismatch')
        elif forecast['kind'] == 'BINARY':
            _need(name in ('false', 'true') and type(value) is bool and
                  value is (name == 'true'), 'forecast_binary_value_mismatch')
        else:
            value = _number(value, -MAX_LOSS, MAX_LOSS, 'forecast_score_anchor_bound')
            _need(value not in score_values, 'forecast_duplicate_score_anchor')
            score_values.add(value)
        probabilities[name] = row['probability']
    probabilities = _probabilities(probabilities)
    if forecast['kind'] == 'BINARY':
        _need(set(probabilities) == {'false', 'true'}, 'forecast_binary_inventory_mismatch')
    evidence = evidence_state(evidence_positive, evidence_negative)
    selected = prediction_set(conformal_profile, probabilities) if conformal_profile is not None else None
    bound = {'request_id': request_id, 'model_sha256': model['sha256'],
             'kind': forecast['kind'], 'probabilities': probabilities}
    raw = json.dumps(bound, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return {'format': 'eidos-epistemic-forecast-assessment-v1',
            'request_id': request_id, 'model_sha256': model['sha256'],
            'forecast_probability_binding_sha256': hashlib.sha256(raw).hexdigest(),
            'four_valued_evidence': evidence, 'prediction_set': selected,
            'evidence_changes_model_probabilities': False,
            'forecast_model_and_evidence_verified': False,
            'calibration_regime_applies_to_this_forecast_verified': False,
            'probability_semantics': 'caller_forecast_and_optional_set_not_verified_truth',
            'authorization': 'recommendation_only_no_action_authorized',
            **_review()}
