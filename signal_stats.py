"""Intervals and an exact test for two independent binomial proportions."""

from math import comb, hypot, sqrt


def validate_arm(k, n):
    if not isinstance(k, int) or not isinstance(n, int) or not 0 <= k <= n or n == 0:
        raise ValueError("An arm needs integer counts with 0 ≤ events ≤ stays and stays > 0.")


def wilson_interval(k, n, z=1.96):
    """Return the two-sided Wilson score interval as proportions."""
    validate_arm(k, n)
    p = k / n
    scale = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / scale
    half_width = z * sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / scale
    return (0.0 if k == 0 else max(0.0, center - half_width),
            1.0 if k == n else min(1.0, center + half_width))


def newcombe_diff_interval(k1, n1, k2, n2):
    """Return the Newcombe method 10 interval for p2 minus p1."""
    lo1, hi1 = wilson_interval(k1, n1)
    lo2, hi2 = wilson_interval(k2, n2)
    p1, p2 = k1 / n1, k2 / n2
    change = p2 - p1
    return change - hypot(p2 - lo2, hi1 - p1), change + hypot(hi2 - p2, p1 - lo1)


def fisher_exact_two_sided(k1, n1, k2, n2):
    """Sum probabilities of fixed-margin tables at most as probable as observed."""
    validate_arm(k1, n1)
    validate_arm(k2, n2)
    events = k1 + k2
    stays = n1 + n2
    non_events = stays - events

    def weight(k):
        return comb(events, k) * comb(non_events, n1 - k)

    # All weights share a denominator. Apply a 1e-12 relative tolerance with
    # exact integer comparisons, including ties even in an extreme tail.
    comparison_scale = 10**12
    threshold = weight(k1) * (comparison_scale + 1)
    possible = range(max(0, n1 - non_events), min(n1, events) + 1)
    tail = sum(value for value in map(weight, possible) if value * comparison_scale <= threshold)
    return tail / comb(stays, n1)
