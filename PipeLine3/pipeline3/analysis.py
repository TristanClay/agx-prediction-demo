"""Deterministic numerical tools. Invalid inputs do not silently create metrics."""

import csv
import io
import math

import numpy as np


class DataProblem(ValueError):
    pass


def read_csv(content):
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise DataProblem("CSV_HEADER: missing or duplicate column names")
        rows = list(reader)
        if not rows or any(None in r for r in rows):
            raise DataProblem("CSV_ROWS: empty table or extra columns")
        return rows
    except (UnicodeDecodeError, csv.Error) as exc:
        raise DataProblem(f"CSV_ENCODING: {exc}") from exc


def number(value, field, row=None, *, positive=False, nonnegative=False):
    try:
        result = float(value)
        if not math.isfinite(result) or (positive and result <= 0) or (nonnegative and result < 0):
            raise ValueError
        return result
    except (TypeError, ValueError):
        raise DataProblem(f"{field} row={row}: finite {'positive' if positive else 'valid'} number required") from None


def unit_factor(unit, mapping, field):
    if unit not in mapping:
        raise DataProblem(f"UNKNOWN_UNIT {field}={unit!r}; allowed={list(mapping)}")
    return mapping[unit]


def window_mean(t, current, window):
    a, b = (number(v, "window") for v in window)
    if not t[0] <= a < b <= t[-1]:
        raise DataProblem(f"WINDOW_OUTSIDE_DATA: {window}")
    inside = (t > a) & (t < b)
    tx = np.r_[a, t[inside], b]
    iy = np.interp(tx, t, current)
    return float(np.trapezoid(iy, tx) / (b - a))


def analyze_ca(rows, metadata, parameters):
    tf = unit_factor(metadata.get("time_unit"), {"s": 1, "min": 60}, "time")
    cf = unit_factor(metadata.get("current_unit"), {"A": 1, "mA": .001, "uA": .000001}, "current")
    af = unit_factor(metadata.get("area_unit"), {"cm2": 1, "mm2": .01, "m2": 10000}, "area")
    area = number(metadata.get("area"), "area", positive=True) * af
    number(metadata.get("potential_V"), "potential_V")
    if metadata.get("potential_reference") not in {"RHE", "SHE", "Ag/AgCl", "Hg/HgO"}:
        raise DataProblem("POTENTIAL_REFERENCE_REQUIRED")
    t = np.array([number(r.get("time"), "time", i + 2) * tf for i, r in enumerate(rows)])
    current = np.array([number(r.get("current"), "current", i + 2) * cf for i, r in enumerate(rows)])
    if len(t) < 2 or np.any(np.diff(t) <= 0) or t[0] < 0:
        raise DataProblem("TIME_NOT_STRICTLY_INCREASING_OR_NEGATIVE")
    mean = window_mean(t, current, parameters["window_s"])
    early = window_mean(t, current, parameters["early_window_s"])
    late = window_mean(t, current, parameters["late_window_s"])
    issues = []
    decay = None
    if np.any(current > 0) and np.any(current < 0):
        issues.append("CURRENT_POLARITY_CROSSING: decay/recovery unavailable")
    elif abs(early) < 1e-12:
        issues.append("ZERO_DECAY_DENOMINATOR")
    else:
        decay = 1 - abs(late) / abs(early)
    return {"charge_C": float(np.trapezoid(current, t)), "area_cm2": area,
            "mean_current_A": mean, "mean_j_mA_cm2": mean / area * 1000,
            "early_j_mA_cm2": early / area * 1000, "late_j_mA_cm2": late / area * 1000,
            "decay_fraction": decay, "window_s": parameters["window_s"],
            "time_s": t.tolist(), "current_A": current.tolist(),
            "j_mA_cm2": (current / area * 1000).tolist(), "issues": issues}


def recovery_ratio(fresh, washed, fresh_context, washed_context):
    # Treatment/state intentionally differ; electrochemical conditions must match.
    fields = ["potential_V", "potential_reference", "electrolyte", "temperature_C", "reaction_window_id"]
    if any(fresh_context.get(k) is None or fresh_context.get(k) != washed_context.get(k) for k in fields):
        return None, "RECOVERY_CONDITIONS_MISMATCH"
    if fresh["window_s"] != washed["window_s"]:
        return None, "RECOVERY_WINDOWS_MISMATCH"
    if fresh["issues"] or washed["issues"]:
        return None, "RECOVERY_INPUT_QUALITY_LIMITED"
    if abs(fresh["mean_j_mA_cm2"]) < 1e-12:
        return None, "ZERO_RECOVERY_DENOMINATOR"
    return abs(washed["mean_j_mA_cm2"]) / abs(fresh["mean_j_mA_cm2"]), None


CONCENTRATION = {"mM": 1., "mmol/L": 1., "uM": .001, "mol/L": 1000.}


def fit_calibration(rows, metadata):
    factor = unit_factor(metadata.get("concentration_unit"), CONCENTRATION, "concentration")
    if not metadata.get("species") or not metadata.get("method_version") or metadata.get("area_unit") != "a.u.":
        raise DataProblem("CALIBRATION_SPECIES_METHOD_AREA_UNIT_REQUIRED")
    x = np.array([number(r.get("concentration"), "concentration", i + 2, nonnegative=True) * factor for i, r in enumerate(rows)])
    y = np.array([number(r.get("peak_area"), "peak_area", i + 2, nonnegative=True) for i, r in enumerate(rows)])
    if len(x) < 3 or len(set(x)) < 3:
        raise DataProblem("CALIBRATION_NEEDS_THREE_DISTINCT_STANDARDS")
    a, b = np.polyfit(x, y, 1)
    if a <= 0 or not np.isfinite([a, b]).all():
        raise DataProblem("CALIBRATION_NONPOSITIVE_SLOPE")
    residuals = y - (a * x + b)
    total = float(np.sum((y - y.mean()) ** 2))
    if total <= 0:
        raise DataProblem("CALIBRATION_NO_RESPONSE_VARIANCE")
    lod = metadata.get("lod")
    loq = metadata.get("loq")
    lod = number(lod, "lod", nonnegative=True) * factor if lod is not None else None
    loq = number(loq, "loq", nonnegative=True) * factor if loq is not None else None
    if lod is not None and loq is not None and lod > loq:
        raise DataProblem("LOD_EXCEEDS_LOQ")
    return {"slope": float(a), "intercept": float(b), "r_squared": 1 - float(np.sum(residuals ** 2)) / total,
            "residuals": residuals.tolist(), "range_mM": [float(x.min()), float(x.max())],
            "lod_mM": lod, "loq_mM": loq, "concentration_mM": x.tolist(),
            "peak_area": y.tolist(), "species": metadata["species"],
            "method_version": metadata["method_version"], "concentration_unit": "mM", "area_unit": "a.u."}


def analyze_hplc(rows, metadata, calibration, r2_min=.99):
    if calibration is None:
        raise DataProblem("MISSING_CALIBRATION")
    for field in ["species", "method_version", "area_unit"]:
        if metadata.get(field) != calibration[field]:
            raise DataProblem(f"CALIBRATION_METHOD_MISMATCH: {field}")
    unit_factor(metadata.get("time_unit"), {"s": 1}, "sampling_time")
    unit_factor(metadata.get("volume_unit"), {"mL": 1}, "volume")
    if metadata.get("sampling_mode") != "withdraw_no_replacement":
        raise DataProblem("UNSUPPORTED_SAMPLING_CORRECTION: replenishment/feed requires complete mass balance")
    volume = number(metadata.get("initial_volume_mL"), "initial_volume_mL", positive=True)
    initial = metadata.get("initial_concentration_mM")
    initial = number(initial, "initial_concentration_mM", nonnegative=True) if initial is not None else None
    previous_time, removed_volume, removed_amount = -1., 0., 0.
    cumulative_valid = True
    results = []
    for index, row in enumerate(rows, 2):
        t = number(row.get("sampling_time_s"), "sampling_time_s", index, nonnegative=True)
        if t <= previous_time:
            raise DataProblem(f"HPLC_SAMPLING_TIME_NOT_INCREASING row={index}; technical injections must be separate measurements")
        previous_time = t
        peak = number(row.get("peak_area"), "peak_area", index, nonnegative=True)
        dilution = number(row.get("dilution_factor"), "dilution_factor", index, positive=True)
        withdrawal = number(row.get("withdrawal_mL"), "withdrawal_mL", index, nonnegative=True)
        if removed_volume + withdrawal > volume or volume - removed_volume <= 0:
            raise DataProblem(f"IMPOSSIBLE_WITHDRAWAL_VOLUME row={index}")
        vial_c = (peak - calibration["intercept"]) / calibration["slope"]
        c = vial_c * dilution
        flags = []
        if vial_c < 0:
            flags.append("NEGATIVE_BACK_CALCULATION")
        elif calibration["lod_mM"] is not None and vial_c < calibration["lod_mM"]:
            flags.append("BELOW_LOD")
        elif calibration["loq_mM"] is not None and vial_c < calibration["loq_mM"]:
            flags.append("BELOW_LOQ")
        if not calibration["range_mM"][0] - 1e-10 <= vial_c <= calibration["range_mM"][1] + 1e-10:
            flags.append("OUTSIDE_CALIBRATION_RANGE")
        if calibration["lod_mM"] is None or calibration["loq_mM"] is None:
            flags.append("LOD_LOQ_NOT_PROVIDED")
        if calibration["r_squared"] < r2_min:
            flags.append(f"CALIBRATION_POOR_FIT_PROTOCOL_R2_LT_{r2_min}")
        valid = not flags
        cumulative_valid = cumulative_valid and valid
        accounted = c * (volume - removed_volume) + removed_amount if cumulative_valid else None
        results.append({"sampling_time_s": t, "vial_concentration_mM": vial_c,
                        "concentration_mM": c, "remaining_volume_mL": volume - removed_volume,
                        "accounted_amount_umol": accounted,
                        "net_formed_umol": accounted - initial * volume if accounted is not None and initial is not None else None,
                        "quantitation_status": "QUANTIFIED" if valid else "LIMITED", "issues": flags})
        removed_amount += c * withdrawal
        removed_volume += withdrawal
    return {"rows": results, "calibration": calibration,
            "issues": sorted({issue for r in results for issue in r["issues"]}),
            "mass_balance_basis": "before each withdrawal; C[mM]*V[mL]=amount[umol]; no replenishment/feed",
            "net_amount_note": "Initial concentration missing: net formation unavailable" if initial is None else "initial amount subtracted",
            "formal_FE": None}
