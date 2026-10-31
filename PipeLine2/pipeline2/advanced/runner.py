"""Offline artifact pipeline shared by CLI and Streamlit; no process execution."""

import json
import platform
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

from pipeline2.contracts import RunConfig
from pipeline2.dft import VASPAdapter
from pipeline2.hackathon import decorate_bi, import_cards
from pipeline2.models import (
    DPA4Adapter,
    MACEMH1Adapter,
    MockModelAdapter,
    PredictionResult,
    UMAAdapter,
)
from pipeline2.orchestrator.budget import BudgetGuard
from pipeline2.orchestrator.gate import GateValidator
from pipeline2.properties import adsorption_energy
from pipeline2.registry import DataRegistry, content_hash, sha256_file, utc_now
from pipeline2.reports import software_hashes, write_csv, write_json
from pipeline2.safety import new_directory, protect_sources
from pipeline2.showcase import algorithm_demo
from pipeline2.structure import (
    InvalidStructure,
    ag111_template,
    canonical_structure,
    ff_demo,
    fixed_indices,
    place_demo_adsorbate,
    substitute,
    validate_structure,
    write_structure,
)

from .contracts import AdvancedConfig
from .dft import DFTResultValidator, TeachingPBSAdapter, prepare_teaching_job, scheduler_events
from .learning import run_learning, teaching_dataset


def model_statuses() -> list[dict]:
    return [a().capabilities() for a in (UMAAdapter, MACEMH1Adapter, DPA4Adapter)]


def doctor(config: AdvancedConfig) -> dict:
    return {
        "mode": config.mode,
        "data_origin": config.data_origin,
        "models": model_statuses(),
        "real_submission": "DISABLED_NO_EXECUTOR",
        "formal_budget": {"jobs": 0, "core_hours": 0.0},
        "gate": GateValidator().evaluate(config.task, config.gate_source).to_dict(),
    }


def semantic_payload(value):
    """Exclude measured clock fields only; coordinates, IDs, predictions remain bound."""
    if isinstance(value, dict):
        return {
            k: semantic_payload(v)
            for k, v in value.items()
            if k not in {"runtime_s", "elapsed_s", "generated_at", "total_elapsed_s"}
        }
    if isinstance(value, list):
        return [semantic_payload(v) for v in value]
    return value


def create_advanced(config: AdvancedConfig, output_dir: str | Path, *, config_path=None) -> dict:
    source_files = [Path(config_path).resolve()] if config_path else []
    if config.pipeline1_cards:
        source_files.append(Path(config.pipeline1_cards).expanduser().resolve(strict=True))
    if config.gate_source:
        roots = tuple(Path(p).resolve() for p in config.gate_source.model_dump().values())
    else:
        roots = ()
    # Validate/read the optional upstream snapshot before creating any artifacts.
    cards = import_cards(source_files[-1]) if config.pipeline1_cards else []
    source_hashes = {str(p): sha256_file(p) for p in source_files}
    with protect_sources((*roots, *source_files)):
        target = new_directory(output_dir)
        result = _generate(config, target, cards)
        if any(sha256_file(Path(p)) != digest for p, digest in source_hashes.items()):
            raise ValueError("Read-only input changed during generation")
        manifest = {
            "schema_version": "pipeline2-advanced-manifest-1",
            "status": "SYNTHETIC_DEMO_READY",
            "gate": result["gate"],
            "generated_at": result["generated_at"],
            "mode": "DEMO",
            "data_origin": "SYNTHETIC",
            "semantic_sha256": content_hash(semantic_payload(result)),
            "config": config.model_dump(),
            "source_hashes": source_hashes,
            "software_hashes": software_hashes(),
            "actual_dft_jobs": 0,
            "actual_core_hours": 0.0,
            "formal_feedback_written": False,
            "artifacts": {
                str(p.relative_to(target)): sha256_file(p)
                for p in sorted(target.rglob("*"))
                if p.is_file()
            },
        }
        write_json(target / "run_manifest.json", manifest)
        return manifest


def _generate(config, target, cards):
    started = perf_counter()
    registry = DataRegistry(target / "demo_registry.sqlite")
    formal = BudgetGuard(registry, "formal-zero", 0, 0.0)
    registry.append("Task", config.task.task_id, config.task.model_dump(), "fixture")
    gate = GateValidator().evaluate(config.task, config.gate_source).to_dict()
    events: list[dict] = []

    def event(stage, inputs, outputs, elapsed):
        row = {
            "step": len(events) + 1,
            "stage": stage,
            "inputs": inputs,
            "outputs": outputs,
            "elapsed_s": elapsed,
            "execution_kind": "deterministic_python_tool",
            "data_origin": "SYNTHETIC",
        }
        events.append(row)
        registry.append("Event", f"tool:{row['step']}", row, "fixture")

    source_start = perf_counter()
    event(
        "Pipeline 1 context / TaskSpec",
        {"candidate_cards_provided": bool(cards)},
        {
            "family_ids": [c["candidate_family_id"] for c in cards],
            "gate": gate,
            "is_gate_evidence": False,
        },
        perf_counter() - source_start,
    )
    slab, molecule = ag111_template(), ff_demo()
    slabs = [
        ("Ag", "clean", slab),
        ("Cu", "surface_substitution", substitute(slab, "Cu", 27)),
        ("Ni", "surface_substitution", substitute(slab, "Ni", 27)),
        ("Ni", "subsurface_substitution", substitute(slab, "Ni", 18)),
        ("Bi", "fcc_decoration", decorate_bi(slab, "fcc")),
        ("Bi", "hcp_decoration", decorate_bi(slab, "hcp")),
    ]
    adapter = MockModelAdapter(seed=config.seed)
    structures: list[dict] = []
    predictions: list[dict] = []
    prediction_by_id: dict[str, PredictionResult] = {}
    structure_dir = target / "structures"

    def save(record, role, element, site):
        if record.structure_id in prediction_by_id:
            return prediction_by_id[record.structure_id]
        tick = perf_counter()
        payload = write_structure(record, structure_dir)
        # Dataset-relative artifact paths make bundles portable and semantic hashes stable.
        for artifact in payload["artifacts"].values():
            artifact["path"] = str(Path(artifact["path"]).relative_to(target))
        payload.update(role=role, element=element, site=site, data_origin="SYNTHETIC")
        structures.append(payload)
        registry.append("Structure", record.structure_id, payload, "fixture")
        event(
            "Structure validation",
            {"structure_id": record.structure_id},
            {"status": "VALID_GEOMETRY_ONLY", "role": role},
            perf_counter() - tick,
        )
        prediction = adapter.predict(record.atoms, task="energy_forces")
        if prediction.prediction_status != "SUCCESS":
            raise ValueError(prediction.error_message)
        row = {
            **prediction.model_dump(),
            "mode": "DEMO",
            "data_origin": "SYNTHETIC",
            "display_model": "Analytic teaching pair potential (not UMA/MACE/DPA4)",
            "theory": "No DFT theory: analytic gaussian test potential",
            "device": "cpu",
            "stress_eV_per_A3": None,
            "calibration_status": "UNCALIBRATED",
        }
        predictions.append(row)
        prediction_by_id[record.structure_id] = prediction
        registry.append("Prediction", record.structure_id, row, "mock")
        event(
            "Teaching potential",
            {"structure_id": record.structure_id},
            {"model_id": prediction.model_id, "prediction_status": prediction.prediction_status},
            prediction.runtime_s,
        )
        return prediction

    mol_prediction = save(molecule, "reference_molecule", "FF", "isolated")
    candidates, dft_jobs, trajectories = [], [], []
    for element, site, surface in slabs:
        surf_prediction = save(surface, "reference_slab", element, site)
        tick = perf_counter()
        task = config.task.model_copy(
            update={
                "task_id": f"advanced-{element}-{site}",
                "dopants": [] if element == "Ag" else [element],
                "dopant_sites": []
                if element == "Ag"
                else [
                    int(next(i for i, atom in enumerate(surface.atoms) if atom.symbol == element))
                ],
            }
        )
        material_gate = GateValidator().evaluate(task, config.gate_source).to_dict()
        vasp = VASPAdapter(RunConfig(task=task, gate_source=config.gate_source), formal)
        job = prepare_teaching_job(
            surface, target / "dft_preview" / f"{element}_{site}", material_gate
        )
        job["proposal"] = vasp.propose(surface.structure_id)
        dft_jobs.append(job)
        event(
            "DFT input preview",
            {"structure_id": surface.structure_id},
            {"status": job["status"], "scientific_gate": material_gate["status"]},
            perf_counter() - tick,
        )
        for height in config.heights_A:
            combined = place_demo_adsorbate(surface, molecule, height)
            combined_prediction = save(combined, "adsorbed", element, site)
            prop = adsorption_energy(combined_prediction, surf_prediction, mol_prediction)
            if prop.value is None:
                raise ValueError("Teaching references unexpectedly incompatible")
            cid = "ADV_" + combined.structure_id[2:18]
            candidates.append(
                {
                    "candidate_id": cid,
                    "structure_id": combined.structure_id,
                    "element": element,
                    "site": site,
                    "height_A": height,
                    "slab_reference_id": surface.structure_id,
                    "molecule_reference_id": molecule.structure_id,
                    "adsorption_energy_eV": prop.value,
                    "property_status": prop.status,
                    "source_type": "mock",
                    "data_origin": "SYNTHETIC",
                    "status": "EXPLORATORY",
                    "uncertainty_status": "UNCALIBRATED",
                    "gate_status": material_gate["status"],
                    "gate_blockers": material_gate["blockers"],
                    "family_id": next(
                        (c["candidate_family_id"] for c in cards if c["element"] == element), None
                    ),
                }
            )
            if site in {"clean", "surface_substitution"} and height == config.heights_A[0]:
                tick = perf_counter()
                working = combined.atoms.copy()
                frames = [canonical_structure(working)]
                outcomes = []
                for _ in range(3):
                    relaxation = adapter.relax(working, fmax=0.005, max_steps=1)
                    if relaxation.prediction is None:
                        raise ValueError("Teaching optimizer failed")
                    working = relaxation.atoms
                    validate_structure(working, expected_fixed=fixed_indices(combined.atoms))
                    frames.append(canonical_structure(working))
                    outcomes.append(relaxation.prediction_status)
                trajectories.append(
                    {
                        "candidate_id": cid,
                        "frames": frames,
                        "step_statuses": outcomes,
                        "model_id": "mock-v1",
                        "data_origin": "SYNTHETIC",
                        "algorithm": "3 one-step ASE BFGS runs on analytic test potential",
                        "status": outcomes[-1],
                        "not_a_relaxed_scientific_reference": True,
                    }
                )
                event(
                    "Teaching geometry steps",
                    {"candidate_id": cid},
                    {"frames": len(frames), "status": outcomes[-1]},
                    perf_counter() - tick,
                )

    tick = perf_counter()
    fixture = teaching_dataset(config)
    learning_dir = new_directory(target / "teaching_learning")
    learning = run_learning(config, learning_dir, fixture)
    event(
        "Active learning",
        {"seed": config.seed, "rows": len(fixture)},
        {
            "strategies": [r["strategy"] for r in learning],
            "rounds": config.rounds,
            "target": "dimensionless_teaching_response",
        },
        perf_counter() - tick,
    )
    schedules = [
        e
        for run in learning
        for r in run["rounds"]
        for selected in r["selected"]
        for e in scheduler_events(
            selected["candidate_id"], round_id=f"{run['strategy']}:{r['round'] + 1}", gate=gate
        )
    ]
    collision = slab.atoms.copy()
    collision.positions[1] = collision.positions[0]
    try:
        validate_structure(collision)
        raise AssertionError("Collision validator failed")
    except InvalidStructure as exc:
        collision_case = {"case": "collision", "status": exc.status, "reason": str(exc)}
    charged = mol_prediction.model_copy(update={"charge": -1})
    incompatible = adsorption_energy(
        prediction_by_id[candidates[0]["structure_id"]],
        prediction_by_id[candidates[0]["slab_reference_id"]],
        charged,
    )
    guards = [
        collision_case,
        {
            "case": "charged_reference",
            "status": incompatible.status,
            "reason": incompatible.to_dict(),
        },
        {"case": "scientific_gate", "status": gate["status"], "reason": gate["blockers"]},
        {
            "case": "zero_formal_budget",
            **VASPAdapter(RunConfig(task=config.task), formal).submit({}),
        },
        {
            "case": "submit_even_if_requested",
            **TeachingPBSAdapter().submit(dry_run=False, allow_real_vasp_submit=True),
        },
    ]
    result = {
        "schema_version": "pipeline2-advanced-data-1",
        "generated_at": utc_now(),
        "mode": "DEMO",
        "data_origin": "SYNTHETIC",
        "seed": config.seed,
        "config": config.model_dump(),
        "status": "SYNTHETIC_DEMO_READY",
        "gate": gate,
        "models": model_statuses(),
        "structures": structures,
        "predictions": predictions,
        "candidates": candidates,
        "trajectories": trajectories,
        "pipeline1_cards": cards,
        "dft_jobs": dft_jobs,
        "scheduler_events": schedules,
        "dft_validation": DFTResultValidator().validate(
            None, reference_profile_id=config.task.reference_profile_id
        ),
        "algorithm_fixture": algorithm_demo(config.seed),
        "teaching_dataset": fixture,
        "learning": learning,
        "guardrails": guards,
        "tool_events": events,
        "formal_budget": formal.snapshot(),
        "actual_dft_jobs": 0,
        "actual_core_hours": 0.0,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "versions": {name: version(name) for name in ("ase", "numpy", "scikit-learn")},
        },
        "recipe": {
            "slab": slab.metadata,
            "heights_A": config.heights_A,
            "height_definition": "FF centroid above highest slab/adatom atom",
            "synthetic_labels": "sin(2.4*x0) + 0.6*x1^2 - 0.4*x2; x ~ uniform[-1,1]^3",
            "pool_not_mapped_to_material_performance": True,
            "dft_cost": "8/12/16/20 simulated core-hours cycling by fixture index",
        },
        "total_elapsed_s": perf_counter() - started,
    }
    from .guards import run_guardrail

    result["guardrails"] = [run_guardrail(row["case"], result) for row in guards]
    write_json(target / "advanced.json", result)
    write_json(target / "predictions.json", predictions)
    write_json(target / "recipe.json", result["recipe"])
    write_json(target / "scheduler_events.json", schedules)
    write_json(target / "teaching_labels.json", fixture)
    write_csv(target / "candidates.csv", candidates)
    write_csv(target / "predictions.csv", predictions)
    write_csv(
        target / "learning_metrics.csv",
        [
            {
                "strategy": run["strategy"],
                **{
                    k: r[k]
                    for k in (
                        "round",
                        "test_mae",
                        "test_rmse",
                        "coverage",
                        "calibration_status",
                        "metrics_scope",
                    )
                },
            }
            for run in learning
            for r in run["rounds"]
        ],
    )
    write_new_report(target, result)
    return result


def write_new_report(target, data):
    lines = [
        "# Pipeline 2 Advanced Demo",
        "",
        f"生成时间：{data['generated_at']}。DEMO / SYNTHETIC / EXPLORATORY / UNCALIBRATED。",
        "",
        f"科学门控：{data['gate']['status']}；实际 DFT 作业 0，实际核心小时 0。",
        "",
        f"{len(data['candidates'])} 个吸附候选、{len(data['structures'])} 个结构，"
        f"{len(data['teaching_dataset'])} 条独立无量纲教学记录。",
        "",
        "UMA / MACE-MH-1 / DPA4 均 NOT_CONFIGURED。结构预测来自解析测试势；"
        "主动学习标签来自显式数学函数。它们没有材料性能或真实模型精度含义。",
        "",
        "## 合成教学对照",
        "",
        "| 策略 | 最后轮教学 MAE | 模拟任务 | 模拟核心小时 |",
        "|---|---:|---:|---:|",
    ]
    for run in data["learning"]:
        b = run["simulated_budget"]
        lines.append(
            f"| {run['strategy']} | {run['rounds'][-1]['test_mae']:.6f} | "
            f"{b['reserved_jobs']} | {b['reserved_core_hours']} |"
        )
    lines += [
        "",
        "以上误差只评价固定的合成测试集；不同策略允许消耗不同模拟资源，"
        "应结合预算曲线阅读。不代表材料预测性能或实际节约。",
        "",
        "输入、结构、模型、来源及产物哈希见 run_manifest.json。",
        "",
        "真实结果解析仍使用原有保守接口；本展示没有正式 DFT 验收功能。",
    ]
    from pipeline2.safety import write_new

    write_new(target / "report.md", "\n".join(lines) + "\n")


def load_verified_bundle(directory: str | Path) -> dict:
    root = Path(directory).expanduser().resolve(strict=True)
    manifest = json.loads((root / "run_manifest.json").read_text())
    if manifest.get("schema_version") != "pipeline2-advanced-manifest-1":
        raise ValueError("Not an Advanced Demo bundle")
    for name, digest in manifest["artifacts"].items():
        path = (root / name).resolve(strict=True)
        if root not in path.parents or sha256_file(path) != digest:
            raise ValueError(f"Artifact missing, outside bundle, or hash mismatch: {name}")
    data = json.loads((root / "advanced.json").read_text())
    if content_hash(semantic_payload(data)) != manifest["semantic_sha256"]:
        raise ValueError("Semantic digest mismatch")
    if data.get("data_origin") != "SYNTHETIC" or data.get("mode") != "DEMO":
        raise ValueError("Teaching replay rejects undeclared source/mode")
    return data
