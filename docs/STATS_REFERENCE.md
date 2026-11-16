# Signal-check statistical references

The comparisons use observed, unadjusted synthetic data. Each row compares the latest cohort with the baseline cohort. The two-proportion interval treats those cohorts as independent and measures the difference in proportions. Multiply the difference and its endpoints by 100 for percentage points.

## Wilson score interval

For `k` events in `n>0` stays, set `p=k/n` and use `z=1.96` for a 95% interval. Inverting the binomial score test gives these endpoints. [Wilson (1927)](https://doi.org/10.1080/01621459.1927.10502953) · [NIST handbook and formula](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm)

```text
scale  = 1 + z²/n
center = (p + z²/(2n)) / scale
half   = z × √(p(1−p)/n + z²/(4n²)) / scale
[L, U] = [center − half, center + half]
```

For 20 readmissions in 200 stays, the observed rate is 10.0%. The Wilson center is approximately 0.10754 and the half-width is approximately 0.04187, yielding a 95% interval of 6.57% to 14.94%.

## Newcombe hybrid-score difference interval

Method 10 combines the uncorrected Wilson intervals for the two rates. Write the baseline rate and its endpoints as `p_1,L_1,U_1` and the latest ones as `p_2,L_2,U_2`. With `d=p_2-p_1`, the bounds are as follows. [Newcombe (1998), method 10 and Table II](https://www.researchgate.net/profile/Robert-Newcombe-2/publication/13687790_Interval_estimation_for_the_difference_between_independent_proportions_Comparison_of_eleven_methods/links/611a7ade0c2bfa282a4b7f2a/Interval-estimation-for-the-difference-between-independent-proportions-Comparison-of-eleven-methods.pdf#page=4) · [Publisher record](https://doi.org/10.1002/%28SICI%291097-0258%2819980430%2917%3A8%3C873%3A%3AAID-SIM779%3E3.0.CO%3B2-I)

```text
lower = d − √((p₂ − L₂)² + (U₁ − p₁)²)
upper = d + √((U₂ − p₂)² + (p₁ − L₁)²)
```

For 3/10 to 9/10, `d=0.60`. The Wilson intervals are about [0.10779, 0.60322] and [0.59585, 0.98212]. Substitution gives [0.17052, 0.80902], or +17.05 to +80.90 percentage points. Newcombe's Table II gives the same bounds rounded to four decimal places.

## Two-sided Fisher exact test

Condition on the row and column totals. Let `M=n_1+n_2`, `K=k_1+k_2`, and `X` be the number of events in the baseline row. Each feasible table has the following hypergeometric probability. The two-sided p-value sums the probabilities of all tables with probability at most the observed table's probability, allowing a small relative tolerance for the comparison. [SciPy reference implementation and definition](https://docs.scipy.org/doc/scipy-1.14.1/reference/generated/scipy.stats.fisher_exact.html)

```text
max(0, n₁ − (M − K)) ≤ x ≤ min(n₁, K)
P(X=x) = C(K, x) × C(M−K, n₁−x) / C(M, n₁)
p = ∑ P(X=x) over tables with P(X=x) ≤ P(X=k₁) within tolerance
```

For 2/6 to 0/6, the feasible baseline event counts are 0, 1 and 2. Their weights over the common denominator `C(12,6)=924` are 210, 504 and 210. The observed table has weight 210, so the two-sided p-value is `(210+210)/924=0.454545`.

## Independent Case B calculation

The cross-check read event and discharge totals from the synthetic SQLite fixture with the existing SQL measure. SciPy 1.14.1 in a scratch virtual environment supplied each Wilson interval via `binomtest(...).proportion_ci(method="wilson")` and each Fisher p-value via `fisher_exact(..., alternative="two-sided")`. The bounds were combined using the Newcombe formula and `math.hypot`. SciPy uses the full normal quantile `z ≈ 1.959964`. The difference from using 1.96 fits within the stated tolerances. [SciPy Wilson method](https://docs.scipy.org/doc/scipy-1.14.1/reference/generated/scipy.stats._result_classes.BinomTestResult.proportion_ci.html)

| Scope | Baseline events/stays | Latest events/stays | Change (pp) | 95% interval (pp) | Fisher p |
| --- | ---: | ---: | ---: | ---: | ---: |
| Overall | 20/200 | 32/200 | +6.0 | −0.637558 to +12.666017 | 0.101264 |
| Mesa | 10/100 | 20/100 | +10.0 | +0.014903 to +19.947387 | 0.073428 |
| Harbor | 10/100 | 12/100 | +2.0 | −6.961498 to +11.004066 | 0.821749 |

All values agree with the brief within ±0.05 percentage points for changes and intervals and ±0.002 for Fisher p-values. The dashboard's interpretation label follows the Newcombe interval. Fisher's conditional test uses its own probability rule, so Mesa's positive lower bound and Fisher p-value of 0.073428 can occur together.
