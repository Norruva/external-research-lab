"""Aggregate closed-set metrics from completed numeric rows; no model or text."""
import math


def summarize(rows, labels):
    """Rows are validated/aligned by evaluate_owned; every offered class counts."""
    count = len(rows)
    if not count:
        raise ValueError('Nonempty completed rows required')
    index = {label: i for i, label in enumerate(labels)}
    matrix = [[0] * len(labels) for _ in labels]
    losses, briers = [], []
    bins = [[] for _ in range(10)]
    for row in rows:
        target, predicted = index[row['label']], index[row['predicted']]
        matrix[target][predicted] += 1
        logits, probabilities = row['logits'], row['probabilities']
        maximum = max(logits)
        losses.append(maximum - logits[target] + math.log(math.fsum(math.exp(z - maximum) for z in logits)))
        briers.append(math.fsum((p - (i == target)) ** 2 for i, p in enumerate(probabilities)))
        confidence = probabilities[predicted]
        bins[min(9, int(confidence * 10))].append((confidence, predicted == target))
    per_class = []
    for i, label in enumerate(labels):
        tp = matrix[i][i]
        support = sum(matrix[i])
        predicted = sum(row[i] for row in matrix)
        precision = tp / predicted if predicted else 0.0
        recall = tp / support if support else 0.0
        f1 = 2 * tp / (support + predicted) if support + predicted else 0.0
        per_class.append({'label': label, 'support': support, 'predicted': predicted,
                          'precision': precision, 'recall': recall, 'f1': f1})
    reliability = []
    for i, bucket in enumerate(bins):
        reliability.append({'lower': i / 10, 'upper': (i + 1) / 10, 'n': len(bucket),
                            'confidence': math.fsum(p for p, _ in bucket) / len(bucket) if bucket else None,
                            'accuracy': sum(correct for _, correct in bucket) / len(bucket) if bucket else None})
    correct = sum(matrix[i][i] for i in range(len(labels)))
    return {'n': count, 'correct': correct, 'candidate_accuracy': correct / count,
            'macro_f1_fixed_offered_labels': math.fsum(row['f1'] for row in per_class) / len(labels),
            'weighted_f1': math.fsum(row['f1'] * row['support'] for row in per_class) / count,
            'negative_log_likelihood': math.fsum(losses) / count,
            'multiclass_brier_sum': math.fsum(briers) / count,
            'ece_10_equal_width_bins': math.fsum(
                len(bucket) / count * abs(math.fsum(p for p, _ in bucket) / len(bucket) -
                                           sum(correct for _, correct in bucket) / len(bucket))
                for bucket in bins if bucket),
            'per_class': per_class, 'confusion': {'labels': labels, 'counts': matrix},
            'reliability': reliability,
            'accepted_answers': 0, 'accepted_coverage': 0.0, 'selective_accuracy': None,
            'policy': 'review_only_acceptance_disabled',
            'confidence_intervals': None,
            'uncertainty_scope': 'descriptive_sample_metrics_no_population_or_independence_guarantee'}
