"""Constraint filtering and deterministic Pareto layers in a compatible group."""

import math

from pipeline2.contracts import ScreeningConfig
from pipeline2.registry import canonical_json


def screen(candidates: list[dict], policy: ScreeningConfig) -> dict:
    if not policy.objectives:
        raise ValueError("At least one objective required")
    ids = [c["candidate_id"] for c in candidates]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate candidate id")
    # Relative properties also need compatible provenance; never rank raw totals.
    contexts = {
        canonical_json(c["comparison_key"])
        for c in candidates
        if c.get("status") in {"EXPLORATORY", "VALIDATED"}
    }
    if len(contexts) > 1:
        raise ValueError("Screen separately by compatible property/model/reference group")
    accepted: list[dict] = []
    rejected: list[dict] = []
    for original in candidates:
        candidate = {**original}
        props = candidate.get("properties", {})
        reasons = []
        if candidate.get("status") not in {"EXPLORATORY", "VALIDATED"}:
            reasons.append("property_not_eligible")
        if candidate.get("status") == "VALIDATED" and candidate.get("source_type") in {
            "mock",
            "fixture",
        }:
            reasons.append("mock_or_fixture_cannot_be_validated")
        required = {o.property for o in policy.objectives} | {
            b.property for b in policy.constraints
        }
        for prop in sorted(required):
            value = props.get(prop)
            if (
                isinstance(value, bool)
                or not isinstance(value, (float, int))
                or not math.isfinite(value)
            ):
                reasons.append(f"missing_or_nonfinite:{prop}")
        if not reasons:
            for bound in policy.constraints:
                value = props[bound.property]
                if bound.minimum is not None and value < bound.minimum:
                    reasons.append(f"below_minimum:{bound.property}")
                if bound.maximum is not None and value > bound.maximum:
                    reasons.append(f"above_maximum:{bound.property}")
        candidate["filter_reasons"] = reasons
        (rejected if reasons else accepted).append(candidate)

    def vector(c):
        return tuple(
            c["properties"][o.property] * (1 if o.direction == "min" else -1)
            for o in policy.objectives
        )

    def dominates(a, b):
        av, bv = vector(a), vector(b)
        return all(x <= y for x, y in zip(av, bv)) and any(x < y for x, y in zip(av, bv))

    remaining, ranked, layer = list(accepted), [], 0
    while remaining:
        front = [c for c in remaining if not any(dominates(other, c) for other in remaining)]
        front.sort(key=lambda c: (vector(c), c["candidate_id"]))
        for candidate in front:
            candidate["pareto_rank"] = layer
            candidate["ranking_reason"] = (
                "Pareto layer; configured objective order then candidate_id breaks ties"
            )
            ranked.append(candidate)
        front_ids = {c["candidate_id"] for c in front}
        remaining = [c for c in remaining if c["candidate_id"] not in front_ids]
        layer += 1
    return {
        "ranked": ranked,
        "top_k": ranked[: policy.top_k],
        "rejected": sorted(rejected, key=lambda c: c["candidate_id"]),
    }
