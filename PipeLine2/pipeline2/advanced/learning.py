"""Reproducible teaching AL. No foundation-model training or scientific claims."""

import math
from pathlib import Path
from typing import Literal

import numpy as np

from pipeline2.active_learning import sample
from pipeline2.contracts import AcquisitionConfig
from pipeline2.orchestrator.budget import BudgetGuard
from pipeline2.registry import DataRegistry, content_hash

from .contracts import AdvancedConfig

STRATEGIES = ("random", "uncertainty", "diversity", "cost_aware")


def teaching_dataset(config: AdvancedConfig) -> list[dict]:
    """Features and targets have no elemental/activity/DFT interpretation."""
    rng = np.random.default_rng(config.seed)
    total = config.initial_size + config.pool_size + config.test_size
    rows = []
    for i, x in enumerate(rng.uniform(-1, 1, (total, 3))):
        target = float(np.sin(2.4 * x[0]) + 0.6 * x[1] ** 2 - 0.4 * x[2])
        baseline = float(0.45 * x[0] - 0.15 * x[1] + 0.1 * x[2])
        ensemble = [
            baseline + float(0.2 * np.sin(x[0] + x[1])),
            baseline - float(0.25 * x[2]),
            baseline + float(0.15 * x[1] ** 2),
        ]
        split = (
            "train"
            if i < config.initial_size
            else "pool"
            if i < config.initial_size + config.pool_size
            else "blind"
        )
        row = {
            "candidate_id": f"TEACH_{i:03d}",
            "features": x.tolist(),
            "split": split,
            "baseline": baseline,
            "teaching_ensemble": ensemble,
            "label": target,
            "estimated_cost": float(8 + 4 * (i % 4)),
            "source_type": "fixture",
            "data_origin": "SYNTHETIC",
            "lineage_source_types": ["fixture"],
            "training_allowed": split != "blind",
            "status": "EXPLORATORY",
            "target_name": "dimensionless_teaching_response",
            "unit": "dimensionless",
            "label_status": "SYNTHETIC_TEACHING_LABEL",
        }
        row["record_hash"] = content_hash(row)
        rows.append(row)
    return rows


def eligible_training_rows(rows: list[dict], *, teaching: bool) -> list[dict]:
    """Fail closed at the fitting boundary, not merely at the UI selector."""
    seen = set()
    for row in rows:
        if row["candidate_id"] in seen:
            raise ValueError("Duplicate training label")
        seen.add(row["candidate_id"])
        if row.get("split") != "train" or row.get("training_allowed") is not True:
            raise ValueError("Independent test/pool/forbidden label cannot enter training")
        if teaching:
            if row.get("source_type") != "fixture" or row.get("data_origin") != "SYNTHETIC":
                raise ValueError("Teaching fit requires isolated synthetic labels")
        else:
            # Formal fitting is intentionally not exposed by this teaching engine.
            raise ValueError("Formal calibration not implemented; synthetic data forbidden")
        if not math.isfinite(row["label"]):
            raise ValueError("Non-finite training target")
    return rows


def calibrate(train: list[dict], queries: list[dict], min_labels: int = 8) -> dict:
    """Fixed-kernel GP delta learning; no tuning on test data and no CI claim."""
    eligible_training_rows(train, teaching=True)
    base = np.array([r["baseline"] for r in queries])
    disagreement = np.array([np.std(r["teaching_ensemble"]) for r in queries])
    fallback = {
        "status": "Not Enough Validated Data",
        "scope": "SYNTHETIC DEMO; threshold is an engineering minimum",
        "mean": base.tolist(),
        "uncertainty": disagreement.tolist(),
        "ood_distance": [None] * len(queries),
        "gp_std": [None] * len(queries),
        "model_disagreement": disagreement.tolist(),
        "training_ids": [r["candidate_id"] for r in train],
        "uncertainty_status": "UNCALIBRATED",
    }
    if len(train) < min_labels or not queries:
        return fallback
    # Optional demo dependencies are imported only when a fit is possible.
    from sklearn.gaussian_process import GaussianProcessRegressor
    from sklearn.gaussian_process.kernels import RBF, WhiteKernel
    from sklearn.preprocessing import StandardScaler

    x = np.array([r["features"] for r in train])
    if len(np.unique(x, axis=0)) < 2:
        return fallback
    scaler = StandardScaler().fit(x)
    tx = scaler.transform(x)
    qx = scaler.transform([r["features"] for r in queries])
    residuals = np.array([r["label"] - r["baseline"] for r in train])
    gp = GaussianProcessRegressor(
        kernel=RBF(length_scale=1.0) + WhiteKernel(noise_level=1e-4),
        optimizer=None,
        alpha=1e-8,
        normalize_y=False,
    ).fit(tx, residuals)
    correction, std = gp.predict(qx, return_std=True)
    distances = np.linalg.norm(qx[:, None, :] - tx[None, :, :], axis=-1).min(axis=1)
    return {
        **fallback,
        "status": "FITTED_TEACHING_ONLY",
        "mean": (base + correction).tolist(),
        "uncertainty": std.tolist(),
        "gp_std": std.tolist(),
        "ood_distance": distances.tolist(),
        "scaler_mean": scaler.mean_.tolist(),
        "kernel": str(gp.kernel_),
        "model_id": "teaching-gp-delta-"
        + content_hash(
            {"train": train, "kernel": str(gp.kernel_), "scaler": scaler.mean_.tolist()}
        ),
    }


def acquire(pool, fit, strategy, config, round_index, remaining_jobs, remaining_hours):
    if strategy not in STRATEGIES:
        raise ValueError("Unknown acquisition strategy")
    rows = []
    for i, row in enumerate(pool):
        uncertainty = fit["uncertainty"][i]
        if not math.isfinite(uncertainty):
            raise ValueError("Missing/non-finite uncertainty component")
        rows.append({**{k: v for k, v in row.items() if k != "label"}, "uncertainty": uncertainty})
    weights = {"potential": 0.0, "uncertainty": 0.0, "diversity": 0.0, "estimated_cost": 0.0}
    sampler_strategy: Literal["random", "diversity", "score"] = "score"
    if strategy == "random":
        sampler_strategy = "random"
    elif strategy == "diversity":
        sampler_strategy = "diversity"
    if strategy == "uncertainty":
        weights["uncertainty"] = 1.0
    elif strategy == "cost_aware":
        # Utility/cost, not a claim of scientific value per core hour.
        for row in rows:
            row["potential"] = row["uncertainty"] / row["estimated_cost"]
        weights["potential"] = 1.0
    result = sample(
        rows,
        AcquisitionConfig(
            strategy=sampler_strategy,
            batch_size=config.batch_size,
            seed=config.seed + round_index,
            weights=weights,
        ),
        remaining_jobs=min(remaining_jobs, config.teaching_budget.per_round_jobs),
        remaining_core_hours=min(remaining_hours, config.teaching_budget.per_round_core_hours),
        round_id=f"{strategy}-{round_index}",
    )
    for selected in result["selected"]:
        selected["selection_reason"] = (
            f"{strategy}; seed={config.seed + round_index}; "
            "synthetic pool only; simulated job/core-hour limits checked"
        )
        # Labels are not visible to the acquisition output.
        selected.pop("label", None)
    return {**result, "strategy": strategy, "scope": "SYNTHETIC DEMO"}


def run_learning(config: AdvancedConfig, directory: Path, dataset: list[dict]) -> list[dict]:
    """One independent simulation ledger per strategy. Actual costs are never recorded."""
    initial = [r for r in dataset if r["split"] == "train"]
    blind = [r for r in dataset if r["split"] == "blind"]
    original_pool = [r for r in dataset if r["split"] == "pool"]
    outcomes = []
    for strategy in STRATEGIES:
        registry = DataRegistry(directory / f"{strategy}.sqlite")
        guard = BudgetGuard(
            registry,
            f"teaching-only:{strategy}",
            config.teaching_budget.jobs,
            config.teaching_budget.core_hours,
        )
        train, pool = list(initial), list(original_pool)
        rounds: list[dict] = []
        previous_top: set[str] = set()
        for index in range(config.rounds + 1):
            all_fit = calibrate(train, original_pool + blind, config.min_training_labels)
            pool_mean = all_fit["mean"][: len(original_pool)]
            test_mean = np.array(all_fit["mean"][len(original_pool) :])
            test_labels = np.array([r["label"] for r in blind])
            ranking = sorted(
                [
                    {
                        "candidate_id": r["candidate_id"],
                        "prediction": value,
                        "gp_std": all_fit["gp_std"][i],
                        "model_disagreement": all_fit["model_disagreement"][i],
                        "ood_distance": all_fit["ood_distance"][i],
                        "uncertainty_status": "UNCALIBRATED",
                    }
                    for i, (r, value) in enumerate(zip(original_pool, pool_mean))
                ],
                key=lambda r: (-r["prediction"], r["candidate_id"]),
            )
            top = {r["candidate_id"] for r in ranking[: config.top_k]}
            state = {
                "round": index,
                "data_origin": "SYNTHETIC",
                "calibration_status": all_fit["status"],
                "uncertainty_status": "UNCALIBRATED",
                "training_ids": [r["candidate_id"] for r in train],
                "test_ids": [r["candidate_id"] for r in blind],
                "ranking": ranking,
                "test_mae": float(np.mean(np.abs(test_mean - test_labels))),
                "test_rmse": float(np.sqrt(np.mean((test_mean - test_labels) ** 2))),
                "raw_baseline_test_mae": float(
                    np.mean(np.abs(np.array([r["baseline"] for r in blind]) - test_labels))
                ),
                "coverage": (len(original_pool) - len(pool)) / len(original_pool),
                "top_k_new_entries": len(top - previous_top) if index else None,
                "simulated_budget": guard.snapshot(),
                "selected": [],
                "metrics_scope": "SYNTHETIC DEMO; fixed held-out teaching cases",
            }
            if index < config.rounds:
                fit = calibrate(train, pool, config.min_training_labels)
                budget = guard.snapshot()
                selection = acquire(
                    pool,
                    fit,
                    strategy,
                    config,
                    index + 1,
                    config.teaching_budget.jobs - budget["reserved_jobs"],
                    config.teaching_budget.core_hours - budget["reserved_core_hours"],
                )
                selected_ids = {r["candidate_id"] for r in selection["selected"]}
                state["next_selection"] = selection
                state["selected"] = selection["selected"]
                for row in pool:
                    if row["candidate_id"] in selected_ids:
                        guard.reserve(row["candidate_id"], row["estimated_cost"])
                        train.append({**row, "split": "train"})
                        registry.append(
                            "Event",
                            f"teaching-label:{row['candidate_id']}",
                            {
                                "kind": "synthetic_label_revealed",
                                "candidate_id": row["candidate_id"],
                                "round": index + 1,
                                "label": row["label"],
                                "source_type": "fixture",
                                "formal_eligible": False,
                            },
                            "fixture",
                        )
                pool = [r for r in pool if r["candidate_id"] not in selected_ids]
            registry.append("Decision", f"round:{index}", state, "fixture")
            if "model_id" in all_fit:
                registry.append(
                    "ModelVersion",
                    f"round:{index}",
                    {k: all_fit[k] for k in ("model_id", "training_ids", "kernel", "scaler_mean")},
                    "fixture",
                )
            rounds.append(state)
            previous_top = top
        outcomes.append(
            {
                "strategy": strategy,
                "rounds": rounds,
                "simulated_budget": guard.snapshot(),
                "actual_dft_jobs": 0,
                "actual_core_hours": 0.0,
                "scope": "SYNTHETIC DEMO",
                "formal_feedback_written": False,
            }
        )
    return outcomes
