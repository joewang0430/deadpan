"""Statistics for the evaluation report: bootstrap confidence intervals and judge agreement."""
from __future__ import annotations

import numpy as np

N_BOOT = 10_000


def bootstrap(num, den=None, n_boot: int = N_BOOT, seed: int = 0) -> tuple[float, float, float]:
    """Estimate sum(num) / sum(den) with a 95% percentile bootstrap CI over items.

    With den=None this is the plain mean of `num`. A ratio is for IFEval instruction-level
    accuracy, where an item (prompt) carries several instructions: resampling whole prompts
    keeps instructions of the same prompt together. For a paired difference between two
    systems, pass the per-item differences as `num`."""
    num = np.asarray(num, dtype=float)
    den = np.ones_like(num) if den is None else np.asarray(den, dtype=float)
    idx = np.random.default_rng(seed).integers(0, len(num), size=(n_boot, len(num)))
    sums = den[idx].sum(axis=1)
    reps = num[idx].sum(axis=1)[sums > 0] / sums[sums > 0]  # drop the rare resample with no instructions
    lo, hi = np.percentile(reps, [2.5, 97.5])
    return float(num.sum() / den.sum()), float(lo), float(hi)


def _ranks(x) -> np.ndarray:
    """Ranks with ties given their average rank."""
    x = np.asarray(x, dtype=float)
    ranks = np.empty(len(x))
    ranks[x.argsort(kind="stable")] = np.arange(len(x))
    for v in np.unique(x):
        ranks[x == v] = ranks[x == v].mean()
    return ranks


def spearman(a, b) -> float:
    return float(np.corrcoef(_ranks(a), _ranks(b))[0, 1])


def cohen_kappa(a, b) -> float:
    """Agreement of two binary raters beyond chance."""
    a, b = np.asarray(a, dtype=bool), np.asarray(b, dtype=bool)
    observed = float((a == b).mean())
    chance = a.mean() * b.mean() + (1 - a.mean()) * (1 - b.mean())
    return 1.0 if chance == 1 else float((observed - chance) / (1 - chance))
