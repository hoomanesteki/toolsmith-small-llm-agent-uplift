"""Statistics: confidence intervals, paired tests, and multiplicity control.

A matrix of point estimates is a table of anecdotes. Three things turn it into
evidence, and all three are implemented here rather than gestured at.

**Paired bootstrap.** Every configuration runs the *same* tasks, so the
comparison is paired and the resampling must be too: resample tasks, not runs.
An unpaired interval on paired data is wider than the truth and hides real
differences.

**McNemar's exact test.** For two systems on the same items, only the
disagreements carry information. A and B both passing 900 of 1,000 tells you
nothing about which is better; the 60 items where exactly one passed tells you
everything. Exact binomial rather than the chi-square approximation, because the
discordant counts here are often small.

**Holm-Bonferroni.** Fifteen configurations is 105 pairwise comparisons. At
alpha 0.05 you expect five spurious wins by chance alone. Holm controls the
family-wise error rate and is uniformly more powerful than Bonferroni, at no
cost in assumptions.

Everything is seeded. ``results.jsonl`` records the seed, so a published
interval is reproducible to the digit.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np

DEFAULT_RESAMPLES = 10_000
DEFAULT_SEED = 20260726


@dataclass(slots=True)
class Interval:
    """A point estimate with a bootstrap confidence interval."""

    estimate: float
    low: float
    high: float
    n: int
    resamples: int = DEFAULT_RESAMPLES
    level: float = 0.95

    @property
    def width(self) -> float:
        return self.high - self.low

    def __str__(self) -> str:
        return f"{self.estimate:.3f} [{self.low:.3f}, {self.high:.3f}]"

    def to_dict(self) -> dict[str, float | int]:
        return {
            "estimate": round(self.estimate, 6),
            "ci_low": round(self.low, 6),
            "ci_high": round(self.high, 6),
            "n": self.n,
            "resamples": self.resamples,
            "level": self.level,
        }


def bootstrap_mean(
    values: list[float],
    resamples: int = DEFAULT_RESAMPLES,
    level: float = 0.95,
    seed: int = DEFAULT_SEED,
) -> Interval:
    """Percentile bootstrap over the mean of one sample."""
    if not values:
        return Interval(0.0, 0.0, 0.0, 0, resamples, level)
    data = np.asarray(values, dtype=float)
    if len(data) == 1:
        return Interval(float(data[0]), float(data[0]), float(data[0]), 1, resamples, level)

    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(data), size=(resamples, len(data)))
    means = data[draws].mean(axis=1)
    alpha = (1.0 - level) / 2.0
    return Interval(
        estimate=float(data.mean()),
        low=float(np.quantile(means, alpha)),
        high=float(np.quantile(means, 1.0 - alpha)),
        n=len(data),
        resamples=resamples,
        level=level,
    )


def paired_bootstrap_difference(
    left: list[float],
    right: list[float],
    resamples: int = DEFAULT_RESAMPLES,
    level: float = 0.95,
    seed: int = DEFAULT_SEED,
) -> Interval:
    """Interval on ``mean(left) - mean(right)``, resampling task indices.

    Both arguments must be aligned: element i of each is the same task. That
    alignment is the whole point, and :func:`align` exists to guarantee it.
    """
    if len(left) != len(right):
        raise ValueError(
            f"paired comparison needs aligned samples, got {len(left)} and {len(right)}"
        )
    if not left:
        return Interval(0.0, 0.0, 0.0, 0, resamples, level)

    a = np.asarray(left, dtype=float)
    b = np.asarray(right, dtype=float)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(a), size=(resamples, len(a)))
    diffs = a[draws].mean(axis=1) - b[draws].mean(axis=1)
    alpha = (1.0 - level) / 2.0
    return Interval(
        estimate=float(a.mean() - b.mean()),
        low=float(np.quantile(diffs, alpha)),
        high=float(np.quantile(diffs, 1.0 - alpha)),
        n=len(a),
        resamples=resamples,
        level=level,
    )


# ------------------------------------------------------------- McNemar ------


@dataclass(slots=True)
class McNemarResult:
    """Only the disagreements carry information."""

    n: int
    left_only: int
    right_only: int
    both: int
    neither: int
    p_value: float
    test: Literal["exact"] = "exact"

    @property
    def discordant(self) -> int:
        return self.left_only + self.right_only

    def to_dict(self) -> dict[str, float | int | str]:
        return {
            "n": self.n,
            "left_only": self.left_only,
            "right_only": self.right_only,
            "both": self.both,
            "neither": self.neither,
            "discordant": self.discordant,
            "p_value": round(self.p_value, 8),
            "test": self.test,
        }


def _binomial_two_sided(k: int, n: int, p: float = 0.5) -> float:
    """Exact two-sided binomial p-value.

    Written out rather than pulled from scipy: it is fifteen lines, it removes a
    heavy dependency from a project whose argument is about counting costs
    honestly, and the arithmetic is checkable by eye.
    """
    if n == 0:
        return 1.0

    def pmf(i: int) -> float:
        return math.comb(n, i) * (p**i) * ((1 - p) ** (n - i))

    observed = pmf(k)
    # Floating-point slack, so a value that should be exactly equal is counted.
    tolerance = observed * (1 + 1e-9)
    return min(1.0, sum(pmf(i) for i in range(n + 1) if pmf(i) <= tolerance))


def mcnemar(left: list[bool], right: list[bool]) -> McNemarResult:
    """Paired test for two systems on identical items."""
    if len(left) != len(right):
        raise ValueError("McNemar needs aligned samples")
    both = sum(a and b for a, b in zip(left, right, strict=True))
    neither = sum((not a) and (not b) for a, b in zip(left, right, strict=True))
    left_only = sum(a and not b for a, b in zip(left, right, strict=True))
    right_only = sum(b and not a for a, b in zip(left, right, strict=True))
    p = _binomial_two_sided(left_only, left_only + right_only)
    return McNemarResult(
        n=len(left),
        left_only=left_only,
        right_only=right_only,
        both=both,
        neither=neither,
        p_value=p,
    )


# ------------------------------------------------------ multiple testing ----


def holm_bonferroni(
    p_values: dict[str, float], alpha: float = 0.05
) -> dict[str, dict[str, float | bool]]:
    """Family-wise error control over every pairwise comparison.

    Fifteen configurations is 105 comparisons; at alpha 0.05 you expect five
    spurious wins from noise alone. Holm is a step-down procedure: sort
    ascending, compare the i-th to alpha/(m-i), and stop at the first failure.
    Uniformly more powerful than Bonferroni and assumes no less.
    """
    if not p_values:
        return {}
    ordered = sorted(p_values.items(), key=lambda kv: kv[1])
    m = len(ordered)
    out: dict[str, dict[str, float | bool]] = {}
    still_rejecting = True
    for index, (key, p) in enumerate(ordered):
        threshold = alpha / (m - index)
        if still_rejecting and p > threshold:
            still_rejecting = False
        out[key] = {
            "p_value": round(p, 8),
            "threshold": round(threshold, 8),
            "significant": bool(still_rejecting),
            "rank": index + 1,
        }
    return out


# ---------------------------------------------------------------- helpers ---


def align(
    left: dict[str, float], right: dict[str, float]
) -> tuple[list[float], list[float], list[str]]:
    """Restrict two keyed samples to their shared tasks, in a stable order.

    Comparing configurations on different task sets is the easiest way to
    publish a difference that is not there. This function makes that impossible
    to do by accident.
    """
    shared = sorted(set(left) & set(right))
    return [left[k] for k in shared], [right[k] for k in shared], shared


def cohens_kappa(a: Sequence[str], b: Sequence[str]) -> float:
    """Agreement above chance. The number a judge is worthless without."""
    if len(a) != len(b) or not a:
        return 0.0
    labels = sorted(set(a) | set(b))
    index = {label: i for i, label in enumerate(labels)}
    matrix = np.zeros((len(labels), len(labels)))
    for x, y in zip(a, b, strict=True):
        matrix[index[x], index[y]] += 1
    total = matrix.sum()
    observed = np.trace(matrix) / total
    expected = float((matrix.sum(axis=0) * matrix.sum(axis=1)).sum()) / (total**2)
    if expected >= 1.0:
        return 1.0
    return float((observed - expected) / (1 - expected))


def confusion_matrix(a: Sequence[str], b: Sequence[str]) -> dict[str, dict[str, int]]:
    labels = sorted(set(a) | set(b))
    out = {row: dict.fromkeys(labels, 0) for row in labels}
    for x, y in zip(a, b, strict=True):
        out[x][y] += 1
    return out


def wilson_interval(successes: int, n: int, level: float = 0.95) -> tuple[float, float]:
    """Closed-form interval for a proportion.

    Used where a bootstrap would be silly (a single rate over a few hundred
    trials) and where the normal approximation would be wrong (rates near 0
    or 1, which is exactly where the safety metrics live).
    """
    if n == 0:
        return (0.0, 0.0)
    z = 1.959963984540054 if level == 0.95 else abs(_probit((1 - level) / 2))
    phat = successes / n
    denominator = 1 + z**2 / n
    centre = (phat + z**2 / (2 * n)) / denominator
    margin = z * math.sqrt(phat * (1 - phat) / n + z**2 / (4 * n**2)) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def _probit(p: float) -> float:
    """Inverse normal CDF, Acklam's rational approximation. Accurate to ~1e-9."""
    a = [-39.696830, 220.946098, -275.928510, 138.357751, -30.664798, 2.506628]
    b = [-54.476098, 161.585836, -155.698979, 66.801311, -13.280681]
    c = [-0.007784894002, -0.32239645, -2.400758, -2.549732, 4.374664, 2.938163]
    d = [0.007784695709, 0.32246712, 2.445134, 3.754408]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if p > phigh:
        return -_probit(1 - p)
    q = p - 0.5
    r = q * q
    return (
        (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
        * q
        / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    )
