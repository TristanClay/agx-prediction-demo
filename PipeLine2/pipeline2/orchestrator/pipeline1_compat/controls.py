# Migrated from Pipeline1 without modifying its source.
from typing import Any, Dict, List

from .identifiers import element_candidate_id, stable_slug


def measurement_id(task_kind: str, measurement: str) -> str:
    return f"MEAS_{stable_slug(task_kind, 12)}_{stable_slug(measurement, 52)}_V1"


CONTROL_GATE_POLICIES: Dict[str, Dict[str, Any]] = {
    "baseline": {
        "element_candidate_id": element_candidate_id("ag_baseline", "Ag"),
        "groups": [
            {
                "measurement_group_id": "MG_AG_REPEATABILITY_V1",
                "required_measurement_ids": [measurement_id("experiment", "CA stability")],
                "minimum_replicates": 2,
            },
            {
                "measurement_group_id": "MG_AG_FUROATE_CAUSALITY_V1",
                "required_measurement_ids": [
                    measurement_id("experiment", "furoate-spike inhibition"),
                    measurement_id("experiment", "recovery test"),
                ],
                "minimum_replicates": 2,
            },
            {
                "measurement_group_id": "MG_AG_PRODUCT_INTEGRITY_V1",
                "required_measurement_ids": [measurement_id("experiment", "product analysis")],
                "minimum_replicates": 2,
            },
            {
                "measurement_group_id": "MG_AG_SURFACE_RESIDUE_V1",
                "required_measurement_ids": [measurement_id("experiment", "post-CA XPS/Raman")],
                "minimum_replicates": 2,
            },
        ],
    },
    "benchmark": {
        "element_candidate_id": element_candidate_id("moderate_furfural_adsorption", "Cu"),
        "groups": [
            {
                "measurement_group_id": "MG_AG_CU_ADSORPTION_V1",
                "required_measurement_ids": [
                    measurement_id("dft", "ΔG_furfural*"),
                    measurement_id("dft", "adsorption geometry"),
                ],
                "minimum_replicates": 1,
            },
            {
                "measurement_group_id": "MG_AG_CU_PERFORMANCE_V1",
                "required_measurement_ids": [
                    measurement_id("experiment", "CA stability"),
                    measurement_id("experiment", "product analysis"),
                ],
                "minimum_replicates": 2,
            },
            {
                "measurement_group_id": "MG_AG_CU_STRUCTURE_V1",
                "required_measurement_ids": [
                    measurement_id("experiment", "XPS O 1s/Cu core level"),
                ],
                "minimum_replicates": 1,
            },
        ],
    },
}


def _criterion_outcome(row: Dict[str, Any]) -> str:
    criterion = str(row.get("criterion_result", "not_assessed"))
    if criterion == "pass" and row.get("human_outcome") == "supports":
        return "pass"
    if criterion == "fail" or row.get("human_outcome") == "contradicts":
        return "fail"
    if criterion == "inconclusive" or row.get("human_outcome") == "inconclusive":
        return "inconclusive"
    return "not_assessed"


def _control_pack(rows: List[Dict[str, Any]], control: str) -> Dict[str, Any]:
    policy = CONTROL_GATE_POLICIES[control]
    qualifying = [
        row
        for row in rows
        if row.get("calibration_eligible")
        and row.get("element_candidate_id") == policy["element_candidate_id"]
    ]
    by_measurement: Dict[str, List[Dict[str, Any]]] = {}
    for row in qualifying:
        by_measurement.setdefault(str(row.get("measurement_id", "")), []).append(row)
    group_rows: List[Dict[str, Any]] = []
    for group in policy["groups"]:
        required = list(group["required_measurement_ids"])
        minimum_replicates = int(group.get("minimum_replicates", 1))
        selected = [
            row for measurement_key in required for row in by_measurement.get(measurement_key, [])
        ]
        observed = {str(row.get("measurement_id", "")) for row in selected}
        missing = [
            measurement_key for measurement_key in required if measurement_key not in observed
        ]
        replicate_counts = {
            measurement_key: max(
                [
                    int(row.get("replicate_count", 0))
                    for row in by_measurement.get(measurement_key, [])
                ]
                or [0]
            )
            for measurement_key in required
        }
        insufficient_replicates = [
            measurement_key
            for measurement_key in required
            if replicate_counts[measurement_key] < minimum_replicates
        ]
        outcomes = {_criterion_outcome(row) for row in selected}
        run_context_ids = {
            str(row.get("run_context_id", "")) for row in selected if row.get("run_context_id")
        }
        conditions_hashes = {
            str(row.get("conditions_hash", "")) for row in selected if row.get("conditions_hash")
        }
        comparability_scope_ids = {
            str(row.get("comparability_scope_id", ""))
            for row in selected
            if row.get("comparability_scope_id")
        }
        inconsistent_context = (
            len(run_context_ids) != 1
            or len(conditions_hashes) != 1
            or len(comparability_scope_ids) != 1
        )
        if "fail" in outcomes:
            status = "failed"
        elif (
            not missing
            and not insufficient_replicates
            and outcomes == {"pass"}
            and not inconsistent_context
        ):
            status = "passed"
        elif "inconclusive" in outcomes:
            status = "inconclusive"
        else:
            status = "missing"
        group_rows.append(
            {
                "measurement_group_id": group["measurement_group_id"],
                "required_measurement_ids": required,
                "observed_measurement_ids": sorted(observed),
                "missing_measurement_ids": missing,
                "minimum_replicates": minimum_replicates,
                "replicate_counts": replicate_counts,
                "insufficient_replicate_measurement_ids": insufficient_replicates,
                "status": status,
                "run_context_ids": sorted(run_context_ids),
                "conditions_hashes": sorted(conditions_hashes),
                "comparability_scope_ids": sorted(comparability_scope_ids),
                "context_consistent": not inconsistent_context,
            }
        )
    required_groups = [str(group["measurement_group_id"]) for group in policy["groups"]]
    passed_groups = [row["measurement_group_id"] for row in group_rows if row["status"] == "passed"]
    missing_groups = [
        row["measurement_group_id"] for row in group_rows if row["status"] == "missing"
    ]
    failed_groups = [row["measurement_group_id"] for row in group_rows if row["status"] == "failed"]
    inconclusive_groups = [
        row["measurement_group_id"] for row in group_rows if row["status"] == "inconclusive"
    ]
    complete = len(passed_groups) == len(required_groups)
    if failed_groups:
        state = f"{control}_failed"
    elif complete:
        state = f"{control}_established"
    elif inconclusive_groups:
        state = "inconclusive"
    else:
        state = "awaiting_feedback"
    return {
        "control": control,
        "element_candidate_id": policy["element_candidate_id"],
        "complete": complete,
        "state": state,
        "required_groups": required_groups,
        "passed_groups": passed_groups,
        "missing_groups": missing_groups,
        "failed_groups": failed_groups,
        "inconclusive_groups": inconclusive_groups,
        "groups": group_rows,
        "comparability_scope_ids": sorted(
            {scope for row in group_rows for scope in row.get("comparability_scope_ids", [])}
        ),
    }


def _normalized_measurement(value: str) -> str:
    return " ".join(str(value or "").replace("−", "-").replace("–", "-").lower().split())


def _baseline_furoate_causality(baseline_pack: Dict[str, Any]) -> bool:
    required = {"MG_AG_FUROATE_CAUSALITY_V1", "MG_AG_SURFACE_RESIDUE_V1"}
    return required <= set(baseline_pack.get("passed_groups", []))


def _ni_low_cost_gate(rows: List[Dict[str, Any]]) -> bool:
    required_comparators = {"SM_AG_CLEAN_SURFACE_V1", "SM_AG_CU_SURFACE_SUB_V1"}
    by_structure: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        if (
            row.get("calibration_eligible")
            and _criterion_outcome(row) == "pass"
            and str(row.get("task_kind", "")).lower() == "dft"
        ):
            by_structure.setdefault(str(row.get("structure_model_id", "")), []).append(row)
    for structure_id, structure_rows in by_structure.items():
        if structure_id not in {"SM_AG_NI_SURFACE_SUB_V1", "SM_AG_NI_HYDROXIDE_INTERFACE_V1"}:
            continue
        normalized = [_normalized_measurement(row.get("measurement", "")) for row in structure_rows]
        has_structure = any(
            "segregation" in value or "interface formation" in value for value in normalized
        )
        has_oh = any("oh*" in value or "dg_oh" in value or "δg_oh" in value for value in normalized)
        has_o_guardrail = any(
            ("o*" in value or "dg_o" in value or "δg_o" in value)
            and "oh*" not in value
            and "dg_oh" not in value
            and "δg_oh" not in value
            for value in normalized
        )
        comparators = {
            token.strip()
            for row in structure_rows
            for token in str(row.get("comparator_structure_model_id", "")).split(";")
            if token.strip()
        }
        contexts = {str(row.get("run_context_id", "")) for row in structure_rows}
        condition_hashes = {str(row.get("conditions_hash", "")) for row in structure_rows}
        if (
            has_structure
            and has_oh
            and has_o_guardrail
            and required_comparators <= comparators
            and len(contexts) == 1
            and "" not in contexts
            and len(condition_hashes) == 1
            and "" not in condition_hashes
        ):
            return True
    return False


RESULT_CONTRACT_VERSION = "validation-result-contract-v2.3-1"

STRUCTURE_MODEL_CATALOG: Dict[str, Dict[str, str]] = {
    "SM_AG_CLEAN_SURFACE_V1": {
        "model": "Ag_clean_surface",
        "element_candidate_id": element_candidate_id("ag_baseline", "Ag"),
    },
    "SM_AG_FUROATE_COVERED_SURFACE_V1": {
        "model": "Ag_furoate_covered_surface",
        "element_candidate_id": element_candidate_id("ag_baseline", "Ag"),
    },
    "SM_AG_CU_SURFACE_SUB_V1": {
        "model": "Cu_sub@Ag_surface",
        "element_candidate_id": element_candidate_id("moderate_furfural_adsorption", "Cu"),
    },
    "SM_AG_CU_OXIDE_INTERFACE_V1": {
        "model": "Ag|CuOx(OH)y_interface",
        "element_candidate_id": element_candidate_id("moderate_furfural_adsorption", "Cu"),
    },
    "SM_AG_NI_SURFACE_SUB_V1": {
        "model": "Ni_sub@Ag_surface",
        "element_candidate_id": element_candidate_id("OH_affinity_site", "Ni"),
    },
    "SM_AG_NI_HYDROXIDE_INTERFACE_V1": {
        "model": "Ag|Ni(OH)x_interface",
        "element_candidate_id": element_candidate_id("OH_affinity_site", "Ni"),
    },
    "SM_AG_BI_SURFACE_DECORATION_V1": {
        "model": "Bi_decorated@Ag_surface",
        "element_candidate_id": element_candidate_id("coverage_induced_poisoning", "Bi"),
    },
    "SM_AG_BI_OXIDE_INTERFACE_V1": {
        "model": "Ag|BiOx_interface",
        "element_candidate_id": element_candidate_id("coverage_induced_poisoning", "Bi"),
    },
}
