"""Paired whole-group bootstrap for completed closed-set predictions.

Caller-defined groups and their exchangeability are not independently verified.
Intervals are individual approximate percentile intervals, not certification.
"""
import collections
import math
import random


def percentile(values, probability):
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (position - low) * (ordered[high] - ordered[low])


def blocks(left, right, groups, labels):
    """Preaggregate counts so a large sampled cluster is never row-expanded."""
    label_index = {label: i for i, label in enumerate(labels)}
    grouped = {}
    paired = {'both_correct': 0, 'left_only_correct': 0, 'right_only_correct': 0, 'both_wrong': 0}
    for a, b, group in zip(left, right, groups):
        if a['id'] != b['id'] or a['label'] != b['label']:
            raise ValueError('unaligned_paired_rows')
        gold, lp, rp = (label_index[x] for x in (a['label'], a['predicted'], b['predicted']))
        # count; gold, left predicted, right predicted, left TP, right TP counters
        item = grouped.setdefault(group, [0, *[collections.Counter() for _ in range(5)]])
        item[0] += 1
        item[1][gold] += 1
        item[2][lp] += 1
        item[3][rp] += 1
        if gold == lp:
            item[4][gold] += 1
        if gold == rp:
            item[5][gold] += 1
        key = ('both_correct' if gold == lp else 'right_only_correct') if gold == rp else (
            'left_only_correct' if gold == lp else 'both_wrong')
        paired[key] += 1
    return [grouped[key] for key in sorted(grouped)], paired


def aggregate(items, multiplicities, classes):
    count = 0
    counters = [[0] * classes for _ in range(5)]
    for group, multiplier in multiplicities.items():
        item = items[group]
        count += item[0] * multiplier
        for total, contribution in zip(counters, item[1:]):
            for label, value in contribution.items():
                total[label] += value * multiplier
    gold, left_pred, right_pred, left_tp, right_tp = counters
    values = []
    for predicted, tp in ((left_pred, left_tp), (right_pred, right_tp)):
        correct = sum(tp)
        values.append({'n': count, 'correct': correct, 'candidate_accuracy': correct / count,
                       'macro_f1_fixed_offered_labels': math.fsum(
                           2 * tp[i] / (gold[i] + predicted[i]) if gold[i] + predicted[i] else 0.
                           for i in range(classes)) / classes})
    return values


def bootstrap(left, right, groups, labels, replicates, seed, check_budget):
    if not (len(left) == len(right) == len(groups)) or not left or len(set(groups)) < 2:
        raise ValueError('complete_pairs_and_at_least_two_groups_required')
    items, paired = blocks(left, right, groups, labels)
    point = aggregate(items, dict.fromkeys(range(len(items)), 1), len(labels))
    metrics = ('candidate_accuracy', 'macro_f1_fixed_offered_labels')
    draws = {metric: [] for metric in metrics}
    rng = random.Random(seed)
    for _ in range(replicates):
        sample = collections.Counter(rng.randrange(len(items)) for _ in items)
        a, b = aggregate(items, sample, len(labels))
        for metric in metrics:
            draws[metric].append(a[metric] - b[metric])
        check_budget()
    differences = {metric: {'left_minus_right': point[0][metric] - point[1][metric],
                           'individual_approximate_95_percent_interval': [
                               percentile(draws[metric], .025), percentile(draws[metric], .975)]}
                   for metric in metrics}
    gates = {metric: differences[metric]['individual_approximate_95_percent_interval'][0] > 0
             for metric in metrics}
    return {'left': point[0], 'right': point[1], 'differences': differences,
            'paired_correctness_counts': paired, 'numeric_gates': {**gates, 'both_pass': all(gates.values())},
            'groups': len(items), 'minimum_group_rows': min(item[0] for item in items),
            'maximum_group_rows': max(item[0] for item in items)}
