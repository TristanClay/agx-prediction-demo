"""Sampling demonstrations: output proposals, never execute DFT or train models."""

import random

import numpy as np

from pipeline2.contracts import AcquisitionConfig


def sample(
    candidates: list[dict],
    config: AcquisitionConfig,
    *,
    remaining_jobs: int,
    remaining_core_hours: float,
    round_id: str = "round-1",
) -> dict:
    if remaining_jobs < 0 or not np.isfinite(remaining_core_hours) or remaining_core_hours < 0:
        raise ValueError("Invalid remaining budget")
    ordered = sorted(candidates, key=lambda c: c["candidate_id"])
    if len({c["candidate_id"] for c in ordered}) != len(ordered):
        raise ValueError("Duplicate sampling candidate")
    pool, rejected = [], []
    for c in ordered:
        if c.get("split") == "blind":
            rejected.append({"candidate_id": c["candidate_id"], "reason": "blind_set_locked"})
        elif c.get("status") not in {"EXPLORATORY", "VALIDATED"}:
            rejected.append({"candidate_id": c["candidate_id"], "reason": "invalid_candidate"})
        else:
            cost = c.get("estimated_cost")
            if (
                isinstance(cost, bool)
                or not isinstance(cost, (int, float))
                or not np.isfinite(cost)
                or cost <= 0
            ):
                raise ValueError(
                    "Every sampling candidate needs a positive estimated core-hour cost"
                )
            pool.append(c)
    indices = list(range(len(pool)))
    scores: dict[int, float] = {}
    if config.strategy == "random":
        random.Random(config.seed).shuffle(indices)
    elif config.strategy == "score":
        scores = dict.fromkeys(indices, 0.0)
        for name, weight in config.weights.items():
            if weight == 0:
                continue
            values = [c.get(name) for c in pool]
            if any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not np.isfinite(v)
                for v in values
            ):
                raise ValueError(
                    f"Missing acquisition component:{name}; supply it or set its weight to zero"
                )
            if not values:
                continue
            array = np.array(values, dtype=float)
            span = np.ptp(array)
            norm = (array - array.min()) / span if span else np.zeros(len(array))
            for i, value in enumerate(norm):
                scores[i] += float(value) * weight * (-1 if name == "estimated_cost" else 1)
        indices.sort(key=lambda i: (-scores[i], pool[i]["candidate_id"]))
    else:
        if pool:
            try:
                features = np.array([c["features"] for c in pool], dtype=float)
            except (KeyError, ValueError) as exc:
                raise ValueError(
                    "Diversity sampling requires equal-length numeric features"
                ) from exc
            if features.ndim != 2 or not features.shape[1] or not np.isfinite(features).all():
                raise ValueError("Invalid diversity features")
            scale = np.std(features, axis=0)
            normalized = (features - np.mean(features, axis=0)) / np.where(scale > 0, scale, 1)
            first = random.Random(config.seed).randrange(len(pool))
            indices, todo = [first], set(range(len(pool))) - {first}
            while todo:
                next_i = min(
                    todo,
                    key=lambda i: (
                        -min(float(np.linalg.norm(normalized[i] - normalized[j])) for j in indices),
                        pool[i]["candidate_id"],
                    ),
                )
                indices.append(next_i)
                todo.remove(next_i)
    selected: list[dict] = []
    spent = 0.0
    for i in indices:
        c = pool[i]
        if (
            len(selected) >= min(config.batch_size, remaining_jobs)
            or spent + c["estimated_cost"] > remaining_core_hours
        ):
            rejected.append({"candidate_id": c["candidate_id"], "reason": "batch_or_budget_limit"})
            continue
        spent += c["estimated_cost"]
        selected.append(
            {
                **c,
                "selection_reason": f"{config.strategy}; seed={config.seed}; cost within proposal budget",
                "acq_score": scores.get(i),
                "uncertainty_status": "UNCALIBRATED",
            }
        )
    return {
        "round_id": round_id,
        "strategy": config.strategy,
        "seed": config.seed,
        "weights": config.weights,
        "selected": selected,
        "rejected": rejected,
        "proposed_jobs": len(selected),
        "proposed_core_hours": spent,
        "actual_jobs": 0,
        "actual_core_hours": 0.0,
        "uncertainty_status": "UNCALIBRATED",
    }
