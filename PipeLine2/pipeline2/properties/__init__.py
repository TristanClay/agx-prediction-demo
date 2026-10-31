"""Relative energies are only meaningful within an explicitly compatible reference."""

from dataclasses import asdict, dataclass, field

from pipeline2.models import PredictionResult


@dataclass
class PropertyResult:
    property_name: str
    value: float | None
    status: str
    source_type: str
    reference_structure_ids: list[str] = field(default_factory=list)
    reference_profile_id: str = ""
    error_message: str | None = None
    unit: str = "eV"

    def to_dict(self):
        return asdict(self)


def adsorption_energy(
    combined: PredictionResult | None,
    slab: PredictionResult | None,
    adsorbate: PredictionResult | None,
) -> PropertyResult:
    result = PropertyResult("adsorption_energy_eV", None, "INCOMPATIBLE_REFERENCE", "predicted")
    if combined is None or slab is None or adsorbate is None:
        result.error_message = "Missing combined/slab/adsorbate reference"
        return result
    rows = [combined, slab, adsorbate]
    result.source_type = (
        "mock"
        if any(r.source_type == "mock" for r in rows)
        else ("fixture" if any(r.source_type == "fixture" for r in rows) else combined.source_type)
    )
    result.reference_structure_ids = [r.structure_id for r in rows]
    result.reference_profile_id = combined.reference_profile_id
    compatible = (
        "model_id",
        "model_version",
        "model_hash",
        "model_branch",
        "task_name",
        "reference_profile_id",
        "comparison_context_id",
        "source_type",
    )
    reason = None
    if any(r.prediction_status != "SUCCESS" or r.energy_eV is None for r in rows):
        reason = "Reference prediction failed or energy missing"
    elif any(r.charge != 0 for r in rows):
        reason = "Charged reference requires an electrochemical reference model (not implemented)"
    elif any(len({getattr(r, name) for r in rows}) != 1 for name in compatible):
        reason = "Mixed model/task/theory/source/comparison context"
    elif (
        not combined.cell_hash
        or not slab.cell_hash
        or combined.cell_hash != slab.cell_hash
        or combined.pbc != [True, True, False]
        or slab.pbc != combined.pbc
        or adsorbate.pbc != [False, False, False]
        or combined.fixed_indices != slab.fixed_indices
    ):
        reason = "Slab/combined cell, periodicity or fixed-atom reference mismatch"
    else:
        elements = set(combined.composition) | set(slab.composition) | set(adsorbate.composition)
        if any(
            combined.composition.get(e, 0)
            != slab.composition.get(e, 0) + adsorbate.composition.get(e, 0)
            for e in elements
        ):
            reason = "Reference stoichiometry mismatch"
    if reason:
        result.error_message = reason
        return result
    assert (
        combined.energy_eV is not None
        and slab.energy_eV is not None
        and adsorbate.energy_eV is not None
    )
    result.value = combined.energy_eV - slab.energy_eV - adsorbate.energy_eV
    result.status = "EXPLORATORY"
    return result


def doping_energy(*args, **kwargs) -> PropertyResult:
    return PropertyResult(
        "doping_energy_eV",
        None,
        "NOT_IMPLEMENTED",
        "predicted",
        error_message="Chemical-potential reference not defined",
    )


def segregation_energy(*args, **kwargs) -> PropertyResult:
    return PropertyResult(
        "segregation_energy_eV",
        None,
        "NOT_IMPLEMENTED",
        "predicted",
        error_message="Matched surface/subsurface reference not defined",
    )
