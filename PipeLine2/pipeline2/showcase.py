"""Reproducible software demonstrations, isolated from scientific admission."""

import json
from pathlib import Path

import numpy as np
from ase.data import chemical_symbols

from pipeline2.active_learning import sample
from pipeline2.contracts import AcquisitionConfig, RunConfig, ScreeningConfig
from pipeline2.dft import VASPAdapter
from pipeline2.models import MockModelAdapter, PredictionResult
from pipeline2.orchestrator import BudgetGuard
from pipeline2.orchestrator.runner import run_demo
from pipeline2.properties import adsorption_energy
from pipeline2.properties.screening import screen
from pipeline2.registry import DataRegistry, content_hash, sha256_file, utc_now
from pipeline2.reports import software_hashes, write_csv, write_json
from pipeline2.safety import new_directory
from pipeline2.structure import InvalidStructure, ag111_template, validate_structure

HEIGHTS = [2.5, 3.0, 3.5, 4.0]
SITES = {18: "次表面替位", 27: "表面替位"}


def demo_config(material: str, seed: int) -> RunConfig:
    return RunConfig.model_validate(
        {
            "task": {
                "task_id": f"showcase-{material}",
                "source_pipeline": "pipeline_1",
                "hypothesis_id": "SYNTHETIC_DEMO_NOT_FORMAL_EVIDENCE",
                "evidence_refs": [],
                "substrate": "Ag",
                "surface": "111",
                "dopants": [] if material == "ag" else ["Cu"],
                "dopant_sites": [] if material == "ag" else list(SITES),
                "adsorbates": ["FF"],
                "coverage": 1 / 9,
                "prediction_targets": ["adsorption_energy"],
                "reference_profile_id": "mock-neutral-v1",
                "model_candidates": ["mock-v1"],
                "dft_budget_jobs": 0,
                "dft_budget_core_hours": 0.0,
                "gate_status": "PENDING_GATE",
                "human_review_required": True,
                "allow_real_vasp_submit": False,
                "dry_run": True,
                "data_source": "fixture",
            },
            "structure": {"heights_A": HEIGHTS},
            "mock_seed": seed,
            "acquisition": {"seed": seed},
        }
    )


def algorithm_fixture() -> list[dict]:
    """Declared dimensionless examples, not estimated chemical properties."""
    values = [
        (0.10, 0.90),
        (0.20, 0.70),
        (0.30, 0.55),
        (0.40, 0.40),
        (0.55, 0.30),
        (0.70, 0.20),
        (0.90, 0.10),
        (0.65, 0.65),
        (0.80, 0.80),
        (1.10, 0.25),
        (0.25, 1.10),
        (0.95, 0.95),
    ]
    features = np.array(values)
    standardized = (features - features.mean(axis=0)) / features.std(axis=0)
    return [
        {
            "candidate_id": f"F{i + 1:02d}",
            "properties": {"demo_a": a, "demo_b": b},
            "comparison_key": "dimensionless-algorithm-fixture-v1",
            "status": "EXPLORATORY",
            "source_type": "fixture",
            "gate_status": "PENDING_GATE",
            "split": "exploration",
            "features": [a, b],
            "potential": 1 - a,
            "uncertainty": None,
            "diversity": min(
                float(np.linalg.norm(standardized[i] - standardized[j]))
                for j in range(len(values))
                if j != i
            ),
            "estimated_cost": float([8, 12, 16][i % 3]),
            "unit": "dimensionless",
            "origin": "declared synthetic software fixture",
        }
        for i, (a, b) in enumerate(values)
    ]


def algorithm_demo(seed: int) -> dict:
    policy = ScreeningConfig.model_validate(
        {
            "objectives": [
                {"property": "demo_a", "direction": "min"},
                {"property": "demo_b", "direction": "min"},
            ],
            "constraints": [
                {"property": "demo_a", "maximum": 1.0},
                {"property": "demo_b", "maximum": 1.0},
            ],
            "top_k": 7,
        }
    )
    fixture = algorithm_fixture()
    screened = screen(fixture, policy)
    scenarios = [
        ("normal", "正常模拟额度", 3, 48.0),
        ("tight", "紧张模拟额度", 3, 16.0),
        ("zero", "零模拟额度", 0, 0.0),
    ]
    rounds = []
    for scenario, label, jobs, hours in scenarios:
        for strategy in ["random", "diversity", "score"]:
            result = sample(
                screened["ranked"],
                AcquisitionConfig.model_validate(
                    {"strategy": strategy, "seed": seed, "batch_size": 3}
                ),
                remaining_jobs=jobs,
                remaining_core_hours=hours,
                round_id=f"fixture-{scenario}-{strategy}",
            )
            rounds.append(
                {
                    "scenario": scenario,
                    "scenario_label": label,
                    "max_jobs": jobs,
                    "max_core_hours": hours,
                    "quota_scope": "synthetic_algorithm_only",
                    "gate_status": "PENDING_GATE",
                    "source_type": "fixture",
                    **result,
                }
            )
    return {
        "source_type": "fixture",
        "policy": policy.model_dump(),
        "candidates": fixture,
        "screening": screened,
        "rounds": rounds,
    }


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def guardrail_demo(config: RunConfig, run: Path) -> list[dict]:
    bad = ag111_template().atoms
    bad.positions[1] = bad.positions[0]
    try:
        validate_structure(bad)
        raise AssertionError("Collision was not rejected")
    except InvalidStructure as exc:
        collision: dict = {
            "case_id": "collision",
            "title": "重复原子被阻断",
            "source_type": "fixture",
            "operation": "validate_structure",
            "status": exc.status,
            "reason": str(exc),
        }
    invalid_prediction = MockModelAdapter().predict(bad, task="energy_forces")
    collision["model_status"] = invalid_prediction.prediction_status
    collision["energy_eV"] = invalid_prediction.energy_eV
    registry = DataRegistry(run / "registry.sqlite")
    all_predictions = registry.records("Prediction")
    by_id = {
        p["structure_id"]: PredictionResult.model_validate(
            {k: p[k] for k in PredictionResult.model_fields}
        )
        for p in all_predictions
    }
    prop = _load(run / "properties.json")[0]
    combined, slab, ads = [by_id[sid] for sid in prop["reference_structure_ids"]]
    charged = ads.model_copy(update={"charge": -1})
    rejected = adsorption_energy(combined, slab, charged)
    budget = BudgetGuard(registry, config.task.task_id, 0, 0.0)
    check = VASPAdapter(config, budget).validate_gate_and_budget()
    return [
        collision,
        {
            "case_id": "charged_reference",
            "title": "带电参考态被拒绝",
            "source_type": "fixture",
            "operation": "adsorption_energy",
            "status": rejected.status,
            "reason": rejected.error_message,
            "value": rejected.value,
            "perturbation": "fixture only: isolated reference charge changed to -1",
        },
        {
            "case_id": "pending_gate",
            "title": "待门控与零预算阻止 DFT",
            "source_type": "fixture",
            "operation": "validate_gate_and_budget",
            "status": check["gate"]["status"],
            "reason": "; ".join(check["blockers"]),
            "allowed": check["allowed"],
            "executed": False,
        },
    ]


def _structure_rows(directory: Path) -> tuple[list[dict], list[dict]]:
    structures = {p.stem: _load(p) for p in (directory / "structures").glob("*.json")}
    predictions = {
        p["structure_id"]: p
        for p in DataRegistry(directory / "registry.sqlite").records("Prediction")
    }
    material = "Ag" if directory.name == "ag" else "Ag–Cu"
    candidates, atoms = [], []
    for prop in _load(directory / "properties.json"):
        sid = prop["candidate_id"]
        structure = structures[sid]
        clean = structures[structure["parent_structure_id"]]
        site = clean.get("dopant_site")
        layer = SITES.get(site, "Ag 基线")
        label = f"{material} / {layer} / {structure['height_A']:.1f} Å"
        prediction = predictions[sid]
        artifact_paths = [
            f"{directory.name}/properties.json",
            f"{directory.name}/predictions.csv",
            f"{directory.name}/run_manifest.json",
            f"{directory.name}/structures/{sid}.json",
        ]
        candidates.append(
            {
                "candidate_id": sid,
                "label": label,
                "material": material,
                "layer": layer,
                "site_index": site,
                "height_A": structure["height_A"],
                "adsorption_energy_eV": prop["value"],
                "source_type": "mock",
                "status": prop["status"],
                "gate_status": "PENDING_GATE",
                "calibration_status": "UNCALIBRATED",
                "model_id": prediction["model_id"],
                "model_hash": prediction["model_hash"],
                "model_branch": prediction["model_branch"],
                "reference_profile_id": prediction["reference_profile_id"],
                "structure_hash": structure["structure_hash"],
                "cell_hash": prediction["cell_hash"],
                "reference_structure_ids": prop["reference_structure_ids"],
                "artifact_refs": artifact_paths,
            }
        )
        geometry = structure["geometry"]
        for i, (z, xyz) in enumerate(zip(geometry["numbers"], geometry["positions_A"])):
            atoms.append(
                {
                    "candidate_id": sid,
                    "atom_index": i,
                    "element": chemical_symbols[z],
                    "x_A": xyz[0],
                    "y_A": xyz[1],
                    "z_A": xyz[2],
                    "fixed": i in geometry["fixed_indices"],
                    "source_type": "fixture",
                }
            )
    return candidates, atoms


def create_showcase(output_dir: str | Path, *, seed: int = 42) -> dict:
    directory = new_directory(output_dir)
    source_hashes = software_hashes()
    recipe = {
        "schema_version": "pipeline2-showcase-recipe-1",
        "seed": seed,
        "heights_A": HEIGHTS,
        "cu_site_indices": list(SITES),
        "candidate_counts": {"Ag": 4, "Ag-Cu": 8},
        "formal_budget_jobs": 0,
        "formal_budget_core_hours": 0.0,
        "dry_run": True,
        "allow_real_vasp_submit": False,
        "simulation_quotas_are_not_scientific_authorization": True,
    }
    write_json(directory / "recipe.json", recipe)
    manifests, rows, atom_rows = [], [], []
    for material in ["ag", "ag_cu"]:
        config = demo_config(material, seed)
        path = directory / f"{material}_config.json"
        write_json(path, config.model_dump())
        manifest = run_demo(config, path, directory / material)
        if manifest["status"] != "COMPLETED_OFFLINE":
            raise ValueError(f"Showcase generation failed:{manifest['error']}")
        manifests.append(
            {
                "run": material,
                "status": manifest["status"],
                "gate_status": manifest["gate"]["status"],
                "actual_dft_jobs": manifest["actual_dft_jobs"],
                "manifest_ref": f"{material}/run_manifest.json",
            }
        )
        c, a = _structure_rows(directory / material)
        rows.extend(c)
        atom_rows.extend(a)
    rows.sort(key=lambda row: (row["material"], row["layer"], row["height_A"]))
    algorithms = algorithm_demo(seed)
    guards = guardrail_demo(demo_config("ag", seed), directory / "ag")
    # Opening registry for guardrail checks is idempotent and does not alter prior records.
    provenance = [
        {"artifact_ref": str(p.relative_to(directory)), "sha256": sha256_file(p)}
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    ]
    history_path = Path(__file__).resolve().parents[1] / "docs/pipeline2/validation_results.json"
    history = None
    if history_path.exists():
        old = _load(history_path)
        history = {
            "as_of": old["verification_date"],
            "record_type": "historical_test_receipt",
            "pipeline2_tests_passed": old["pipeline2_tests_passed"],
            "pipeline1_tests_passed": old["pipeline1_tests_passed"],
        }
        source_hashes[str(history_path)] = sha256_file(history_path)
    semantic = {
        "recipe": recipe,
        "structure_candidates": rows,
        "atom_rows": atom_rows,
        "algorithms": algorithms,
        "guardrail_cases": guards,
    }
    bundle = {
        "schema_version": "pipeline2-showcase-1",
        "created_at": utc_now(),
        "status": "COMPLETED_OFFLINE",
        "gate_status": "PENDING_GATE",
        "semantic_sha256": content_hash(semantic),
        **semantic,
        "data_sources": ["mock", "fixture"],
        "run_summaries": manifests,
        "historical_validation": history,
        "source_artifacts": provenance,
        "actual_dft_jobs": 0,
        "actual_dft_core_hours": 0.0,
    }
    write_json(directory / "showcase.json", bundle)
    write_csv(directory / "candidates.csv", rows)
    write_json(directory / "algorithm_fixture.json", algorithms)
    write_json(directory / "guardrail_cases.json", guards)
    if not all(sha256_file(path) == digest for path, digest in source_hashes.items()):
        raise RuntimeError("Software/source changed during showcase generation")
    manifest = {
        "schema_version": "pipeline2-showcase-manifest-1",
        "status": "COMPLETED_OFFLINE",
        "created_at": utc_now(),
        "gate_status": "PENDING_GATE",
        "semantic_sha256": bundle["semantic_sha256"],
        "source_hashes": source_hashes,
        "actual_dft_jobs": 0,
        "actual_dft_core_hours": 0.0,
        "artifacts": {
            str(p.relative_to(directory)): sha256_file(p)
            for p in sorted(directory.rglob("*"))
            if p.is_file()
        },
    }
    write_json(directory / "showcase_manifest.json", manifest)
    return manifest
