"""Seeded synthetic files, imported through the same service as user uploads."""

import csv
import io
import json
from pathlib import Path

import numpy as np

from pipeline3.service import DEFAULT_CONDITIONS, GROUPS
from pipeline3.store import digest


def csv_bytes(columns, rows):
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(rows)
    return out.getvalue().encode()


CA_META = {**DEFAULT_CONDITIONS, "time_unit": "s", "current_unit": "A", "area": 1., "area_unit": "cm2"}
CAL_META = {"species": "furoate", "method_version": "synthetic-HPLC-v1", "area_unit": "a.u.",
            "concentration_unit": "mM", "lod": .02, "loq": .05}
HPLC_META = {**DEFAULT_CONDITIONS, "species": "furoate", "method_version": "synthetic-HPLC-v1",
             "area_unit": "a.u.", "time_unit": "s", "volume_unit": "mL",
             "initial_volume_mL": 10., "initial_concentration_mM": 0.,
             "sampling_mode": "withdraw_no_replacement"}


def generate_fixtures(directory="data/samples"):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(20261029)
    files, entries = {}, []

    def add(name, data, kind, metadata, sample=None, group=None, expected="VALID", phase="supplement"):
        files[name] = data
        entries.append({"file": name, "kind": kind, "metadata": metadata, "sample": sample,
            "group": group, "expected_qc": expected, "phase": phase, "source_type": "synthetic",
            "generation": "NumPy seed=20261029; analytic current / linear confirmed-species peak areas",
            "sha256": digest(data)})

    add("hplc_calibration.csv", csv_bytes(["concentration", "peak_area"], [(0,10),(.1,110),(.5,510),(1,1010),(2,2010),(5,5010)]), "Calibration", CAL_META)
    times = np.arange(0., 601., 10.)
    for sample in [1, 2]:
        amplitudes = {"fresh": .02, "deactivated": .008, "washed": .018, "spike": .009, "solution_control": .0195}
        for group in GROUPS:
            noise = rng.normal(0, .00006, len(times))
            current = amplitudes[group] * (1 - .1 * times / 600) * (1 + (sample - 1) * .025) + noise
            add(f"ca_s{sample}_{group}.csv", csv_bytes(["time", "current"], zip(times, current)), "CA", {**CA_META, "group": group}, sample, group,
                phase="initial" if sample == 1 and group in {"fresh", "deactivated", "spike"} else "supplement")
            concentration = .5 if group in {"deactivated", "spike"} else 1.
            add(f"hplc_s{sample}_{group}.csv", csv_bytes(["sampling_time_s", "peak_area", "dilution_factor", "withdrawal_mL"],
                [(300, 10+1000*concentration*.5, 2, .2), (600, 10+1000*concentration, 2, .2)]), "HPLC", {**HPLC_META, "group": group}, sample, group,
                expected="MISSING_CALIBRATION initially; QUANTIFIED after calibration", phase="initial" if sample == 1 and group == "fresh" else "supplement")
        counter = .006 * (1 - .1 * times / 600) * (1 + .02 * sample) + rng.normal(0, .00004, len(times))
        add(f"ca_s{sample}_washed_counter.csv", csv_bytes(["time", "current"], zip(times, counter)), "CA", {**CA_META, "group": "washed"}, sample, "washed", phase="counter")
    add("xps_record.txt", b"SYNTHETIC attachment placeholder. No measured spectrum; no fitting implemented.\n", "XPS",
        {"method_version": "synthetic-XPS-record-v1", "operator": "demo operator", "acquired_at": "2026-10-29T09:00:00+08:00",
         "treatment_state": "deactivated", "interpretation_note": "Synthetic record only; no spectral/causal conclusion"}, 1, "deactivated")
    add("ca_bad_time.csv", b"time,current\n0,0.1\n10,0.1\n5,0.1\n", "CA", CA_META, expected="TIME_NOT_STRICTLY_INCREASING_OR_NEGATIVE", phase="anomaly")
    add("ca_missing_area.csv", b"time,current\n0,0.1\n600,0.1\n", "CA", {**CA_META, "area": None}, expected="area required", phase="anomaly")
    add("ca_etc_ag_example.csv", csv_bytes(["time", "current"], zip(times, .012 * (1 - .2 * times / 600))),
        "CA", {**CA_META, "material_label": "ETC-Ag SYNTHETIC"}, 99, "fresh", expected="VALID when imported into a separate ETC-Ag sample/task", phase="anomaly")
    add("hplc_outside_range.csv", b"sampling_time_s,peak_area,dilution_factor,withdrawal_mL\n600,10010,1,0.2\n", "HPLC", HPLC_META, expected="OUTSIDE_CALIBRATION_RANGE", phase="anomaly")
    add("upstream_task.json", json.dumps({"schema_version": "pipeline3-task-fixture-1", "task_id": "SYNTHETIC-P1-AG-RECOVERY",
        "hypothesis_id": "SYNTHETIC-operational-recoverability", "experiment": "Ag CA 失活／清洗恢复",
        "source_type": "fixture", "gate_ref": "unresolved"}, ensure_ascii=False, indent=2).encode(), "Task", {}, phase="initial")
    for name, data in files.items():
        path = root / name
        if path.exists() and path.read_bytes() != data:
            raise ValueError(f"Refusing to replace changed fixture: {path}")
        path.write_bytes(data)
    manifest = {"schema_version": "pipeline3-synthetic-manifest-1", "seed": 20261029,
        "materials": "Ag Foam synthetic paired-electrode study; separate ETC-Ag synthetic example, never pooled into that study",
        "warning": "All files synthetic; not scientific findings or real upstream feedback",
        "units": {"CA": "s,A,cm2,V vs RHE", "HPLC": "mM,a.u.,mL,s", "amount": "umol"}, "files": entries}
    path = root / "manifest.json"
    encoded = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    if path.exists() and path.read_text() != encoded:
        raise ValueError("Refusing to overwrite edited manifest")
    path.write_text(encoded)
    return manifest


def load_demo(service, namespace="demo:guided", phase="initial", directory="data/samples"):
    if not namespace.startswith("demo:") or phase not in {"initial", "supplement", "counter"}:
        raise ValueError("Demo namespace and known phase required")
    root = Path(directory)
    manifest = json.loads((root / "manifest.json").read_text())
    task = service.create_task(namespace, "Ag Foam 合成数据：失活与清洗恢复", source_type="synthetic",
        upstream={"external_id": "SYNTHETIC-P1-AG-RECOVERY", "hypothesis_id": "operational_recoverability", "schema_version": "pipeline3-task-fixture-1", "gate_ref": "unresolved"})
    protocol = service.protocol(namespace, task["id"])
    states = {}
    for i in [1, 2] if phase != "initial" else [1]:
        sample, fresh = service.create_sample(namespace, f"Ag-Foam-S{i:02d} (SYNTHETIC)", f"Synthetic-Batch-{i}", task["id"], source_type="synthetic")
        deact = service.treat(namespace, fresh["id"], "deactivated", "合成 CA 处理事件", event_key=f"synthetic-{i}-deact")
        washed = service.treat(namespace, deact["id"], "washed", "合成清洗事件", event_key=f"synthetic-{i}-wash")
        spike = service.treat(namespace, fresh["id"], "spike", "合成 furoate spike 分支", event_key=f"synthetic-{i}-spike")
        control = service.treat(namespace, fresh["id"], "solution_control", "合成匹配溶液对照分支", event_key=f"synthetic-{i}-control")
        states[i] = {"fresh": fresh, "deactivated": deact, "washed": washed, "spike": spike, "solution_control": control}
    for entry in manifest["files"]:
        kind = entry["kind"]
        if kind == "Task" or entry["phase"] == "anomaly":
            continue
        if phase == "initial" and entry["phase"] != "initial":
            continue
        if phase == "supplement" and entry["phase"] == "counter":
            continue
        if phase == "counter" and entry["kind"] == "CA" and entry["group"] == "washed" and entry["phase"] != "counter":
            continue
        data = (root / entry["file"]).read_bytes()
        if digest(data) != entry["sha256"]:
            raise ValueError("Synthetic fixture hash mismatch")
        if kind == "Calibration":
            service.import_calibration(namespace, data, entry["file"], entry["metadata"], source_type="synthetic")
        else:
            service.import_measurement(namespace, data, entry["file"], kind, states[entry["sample"]][entry["group"]]["id"],
                protocol["id"], entry["metadata"], source_type="synthetic")
    # A second actual trace on the same physical sample demonstrates technical replication.
    name = "ca_s1_fresh.csv"
    service.import_measurement(namespace, (root/name).read_bytes(), name, "CA", states[1]["fresh"]["id"], protocol["id"],
        {**CA_META, "group": "fresh", "technical_injection": 2}, replicate_type="technical", source_type="synthetic")
    return task
