"""CPU-only CLI; scheduler commands are never executed."""

import argparse
import json
import re
import sys
from pathlib import Path

from pydantic import ValidationError

from pipeline2.contracts import Contract, load_config, load_payload
from pipeline2.models import PredictionResult
from pipeline2.orchestrator.gate import GateValidator
from pipeline2.orchestrator.runner import run_demo
from pipeline2.properties import adsorption_energy
from pipeline2.registry import sha256_file
from pipeline2.reports import finish_report, software_hashes, write_json
from pipeline2.safety import new_directory, protect_sources


class BenchmarkCase(Contract):
    case_id: str
    combined: PredictionResult
    slab: PredictionResult
    adsorbate: PredictionResult
    expected_adsorption_energy_eV: float


class BenchmarkFixture(Contract):
    schema_version: str
    source_type: str
    description: str
    cases: list[BenchmarkCase]


def run_benchmark(fixture: Path, output_dir: Path, *, formal: bool = False) -> dict:
    data = BenchmarkFixture.model_validate(load_payload(fixture))
    if formal:
        raise ValueError(
            "FORMAL_BENCHMARK_NOT_IMPLEMENTED: mock/fixture data cannot enter formal evaluation"
        )
    if data.schema_version != "pipeline2-benchmark-fixture-1" or data.source_type != "fixture":
        raise ValueError("This benchmark is an explicitly labelled software fixture only")
    if any(
        p.source_type not in {"fixture", "mock"}
        for c in data.cases
        for p in [c.combined, c.slab, c.adsorbate]
    ):
        raise ValueError("Fixture benchmark cannot mix real or undeclared model data")
    if not data.cases or len({c.case_id for c in data.cases}) != len(data.cases):
        raise ValueError("Nonempty, unique fixture cases required")
    directory = new_directory(output_dir)
    results: list[dict] = []
    predictions: list[dict] = []
    for case in data.cases:
        prop = adsorption_energy(case.combined, case.slab, case.adsorbate)
        passed = (
            prop.value is not None and abs(prop.value - case.expected_adsorption_energy_eV) <= 1e-10
        )
        results.append(
            {"case_id": case.case_id, "software_arithmetic_passed": passed, **prop.to_dict()}
        )
        predictions.extend(
            {"case_id": case.case_id, **p.model_dump()}
            for p in [case.combined, case.slab, case.adsorbate]
        )
    write_json(directory / "fixture_checks.json", results)
    write_json(directory / "dft_proposals.json", [])
    return finish_report(
        directory,
        command="benchmark",
        status="FIXTURE_CHECKS_PASSED"
        if all(r["software_arithmetic_passed"] for r in results)
        else "FIXTURE_CHECKS_FAILED",
        gate={"status": "PENDING_GATE", "blockers": ["fixture_is_not_scientific_evaluation"]},
        predictions=predictions,
        screening={},
        source_hashes={**software_hashes(), str(fixture.resolve()): sha256_file(fixture)},
        details={"software_cases": len(results), "scientific_metrics_computed": False},
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ag-X Pipeline 2 offline MVP (no real VASP/PBS backend)"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "demo", "propose-dft"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--config", required=True, type=Path)
        if name != "validate":
            cmd.add_argument("--output-dir", type=Path)
        if name == "propose-dft":
            cmd.add_argument("--dry-run", action="store_true")
    benchmark = commands.add_parser("benchmark")
    benchmark.add_argument("--fixture", type=Path, required=True)
    benchmark.add_argument("--output-dir", type=Path, required=True)
    benchmark.add_argument("--formal", action="store_true")
    showcase = commands.add_parser(
        "showcase", help="Generate isolated mock/fixture presentation data"
    )
    showcase.add_argument("--output-dir", type=Path, required=True)
    showcase.add_argument("--seed", type=int, default=42)
    hackathon = commands.add_parser(
        "hackathon", help="Generate evidence-bound Ni/Bi competition data"
    )
    hackathon.add_argument("--pipeline1-source", type=Path, required=True)
    hackathon.add_argument("--output-dir", type=Path, required=True)
    hackathon.add_argument("--seed", type=int, default=42)
    advanced = commands.add_parser("advanced", help="Isolated synthetic Advanced Demo")
    advanced_commands = advanced.add_subparsers(dest="advanced_command", required=True)
    for name in ("doctor", "run"):
        cmd = advanced_commands.add_parser(name)
        cmd.add_argument("--config", required=True, type=Path)
        if name == "run":
            cmd.add_argument("--output-dir", required=True, type=Path)
            cmd.add_argument(
                "--pipeline1-source",
                type=Path,
                help="Optional read-only candidate_cards.json context; not gate evidence",
            )
    args = parser.parse_args(argv)
    try:
        if args.command == "advanced":
            from pipeline2.advanced.contracts import load_advanced_config
            from pipeline2.advanced.runner import create_advanced, doctor

            advanced_config = load_advanced_config(args.config)
            if args.advanced_command == "doctor":
                print(json.dumps(doctor(advanced_config), ensure_ascii=False, indent=2))
                return 0
            if args.pipeline1_source:
                advanced_config = advanced_config.model_copy(
                    update={"pipeline1_cards": str(args.pipeline1_source.resolve())}
                )
            manifest = create_advanced(advanced_config, args.output_dir, config_path=args.config)
        elif args.command == "hackathon":
            from pipeline2.hackathon import create_hackathon

            manifest = create_hackathon(args.pipeline1_source, args.output_dir, seed=args.seed)
            manifest["gate"] = {"status": manifest["gate_status"]}
        elif args.command == "showcase":
            from pipeline2.showcase import create_showcase

            manifest = create_showcase(args.output_dir, seed=args.seed)
            manifest["gate"] = {"status": manifest["gate_status"]}
        elif args.command == "benchmark":
            manifest = run_benchmark(args.fixture, args.output_dir, formal=args.formal)
        else:
            config = load_config(args.config)
            if args.command == "propose-dft" and args.dry_run:
                config.task.dry_run = True
            gate = GateValidator().evaluate(config.task, config.gate_source)
            if args.command == "validate":
                print(
                    json.dumps(
                        {
                            "config_valid": True,
                            "gate": gate.to_dict(),
                            "real_submission_backend": "NOT_IMPLEMENTED",
                            "declared_gate_status_is_advisory_only": True,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 0
            roots = (
                tuple(Path(p) for p in config.gate_source.model_dump().values())
                if config.gate_source
                else ()
            )
            safe_id = re.sub(r"[^A-Za-z0-9_-]+", "_", config.task.task_id)
            output = args.output_dir or Path("runs") / f"{args.command}_{safe_id}"
            with protect_sources(roots):
                manifest = run_demo(config, args.config, output, command=args.command)
        print(
            json.dumps(
                {
                    "status": manifest["status"],
                    "gate_status": manifest["gate"]["status"],
                    "actual_dft_jobs": 0,
                },
                ensure_ascii=False,
            )
        )
        return (
            0 if manifest["status"] not in {"INVALID_OUT_OF_SCOPE", "FIXTURE_CHECKS_FAILED"} else 2
        )
    except (ValidationError, ValueError, OSError, ImportError) as exc:
        print(
            json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False), file=sys.stderr
        )
        return 2
