"""Original question-conditioned typed inference, with review-only decisions.

The neural scorer consumes context, prompt and candidate descriptions. Transport
IDs and numeric anchors are decoded after scoring and never supply model meaning.
"""
import argparse
import copy
import json
import math
import sys

from .decision_contract import (DecisionRequest, DecisionContractError,
                                MAX_REQUEST_BYTES, parse_request, validate_request)


class DecisionEngine:
    def __init__(self, model, identity, registry_sha256=None):
        self.model = model
        self.identity = copy.deepcopy(identity)
        self.registry_sha256 = registry_sha256

    @classmethod
    def from_registry(cls, directory, *, expected_registry_sha256=None):
        from .decision_artifact import load_artifact
        artifact = load_artifact(directory, expected_registry_sha256=expected_registry_sha256)
        return cls(artifact.model, artifact.identity, artifact.registry_sha256)

    def decide(self, request):
        if isinstance(request, DecisionRequest):
            request = validate_request(request.as_dict())
        elif type(request) is dict:
            request = validate_request(request)
        else:
            request = parse_request(request)
        prediction = self.model.forward(self.model.encode(request))
        logits = prediction['logits']
        probabilities = prediction['probabilities']
        if (len(logits) != len(request.options) or len(probabilities) != len(request.options)
                or any(type(z) not in (int, float) or not math.isfinite(z) for z in logits)
                or any(type(p) not in (int, float) or not math.isfinite(p) or not 0 <= p <= 1
                       for p in probabilities)
                or not math.isclose(math.fsum(probabilities), 1.0, abs_tol=1e-12, rel_tol=0)):
            raise DecisionContractError('invalid_model_distribution')
        best = max(range(len(probabilities)), key=lambda i: probabilities[i])
        uncertainty = {'entropy': -math.fsum(p * math.log(p) for p in probabilities if p)}
        if request.kind == 'SCORE':
            mean = math.fsum(p * option.value for p, option in zip(probabilities, request.options))
            candidate = min(request.maximum, max(request.minimum, mean))
            uncertainty['score_stddev'] = math.sqrt(math.fsum(
                p * (option.value - mean)**2 for p, option in zip(probabilities, request.options)))
        else:
            candidate = request.options[best].value
        distribution = [{'id': option.id, 'value': option.value,
                         'probability': float(p), 'logit': float(z)}
                        for option, p, z in zip(request.options, probabilities, logits)]
        result = {'format': 'eidos-decision-response-v2', 'request_id': request.request_id,
                  'kind': request.kind, 'status': 'review_required', 'value': None,
                  'candidate_value': candidate, 'distribution': distribution,
                  'confidence': max(probabilities), 'calibrated_confidence': None,
                  'probability_semantics': 'uncalibrated_conditional_on_offered_options',
                  'uncertainty': uncertainty, 'review_required': True,
                  'reasons': ['calibration_unfitted', 'automatic_acceptance_unvalidated'],
                  'model': copy.deepcopy(self.identity),
                  'processing': copy.deepcopy(prediction.get('accounting', {})),
                  'authorization': 'recommendation_only_no_action_authorized'}
        if request.kind == 'BINARY':
            result['probability_true'] = next(p for p, option in zip(probabilities, request.options)
                                              if option.id == 'true')
        json.dumps(result, allow_nan=False)
        return result


def main():
    parser = argparse.ArgumentParser(description='Original EIDOS question-conditioned local inference')
    parser.add_argument('--registry', required=True)
    parser.add_argument('--registry-sha256')
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise DecisionContractError('request_byte_budget_exceeded')
        engine = DecisionEngine.from_registry(args.registry,
                    expected_registry_sha256=args.registry_sha256)
        result = engine.decide(raw)
        sys.stdout.write(json.dumps(result, ensure_ascii=False, allow_nan=False,
                                    separators=(',', ':')) + '\n')
        return 0
    except (ValueError, OSError, UnicodeError, KeyError, TypeError, OverflowError):
        sys.stderr.write('{"error":"decision_refused","reason":"invalid_request_or_artifact"}\n')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
