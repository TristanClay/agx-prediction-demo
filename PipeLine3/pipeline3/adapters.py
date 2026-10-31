"""Read-only upstream mapping. No method can publish upstream."""

import csv
import io
import json
from pathlib import Path

from pipeline3.contracts import AdapterResult
from pipeline3.store import canonical, digest


def source(path):
    path = Path(path).resolve(strict=True)
    content = path.read_bytes()
    return content, {"path": str(path), "sha256": digest(content)}


class TaskAdapter:
    def load(self, path):
        content, ref = source(path)
        payload = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig")))) if Path(path).suffix == ".csv" else json.loads(content)
        result = AdapterResult(source_refs=[ref])
        if isinstance(payload, dict):
            if payload.get("schema_version") not in {"pipeline2-task-1", "pipeline3-task-fixture-1"}:
                result.issues.append("UNSUPPORTED_TASK_SCHEMA")
                return result
            rows = [payload]
        elif isinstance(payload, list):
            rows = payload
        else:
            result.issues.append("EXPECTED_TASK_OBJECT_OR_LIST")
            return result
        for row in rows:
            if not isinstance(row, dict) or not row.get("task_id"):
                result.issues.append("TASK_ID_MISSING")
                continue
            hypothesis = row.get("hypothesis_id", row.get("linked_hypothesis"))
            if not hypothesis:
                result.issues.append(f"{row['task_id']}:HYPOTHESIS_ID_MISSING")
            if not row.get("experiment") and not row.get("measurement") and not row.get("prediction_targets"):
                result.issues.append(f"{row['task_id']}:TASK_PURPOSE_MISSING")
            result.records.append({"external_id": row["task_id"], "hypothesis_id": hypothesis,
                "schema_version": row.get("schema_version", "p1-native-task-list-unversioned"),
                "gate_ref": row.get("gate_ref", row.get("execution_gate_status")),
                "artifact_refs": [ref], "title": row.get("experiment", row.get("measurement", row["task_id"])),
                "upstream_payload": row})
        result.issues.append("AUTHORITATIVE_GATE_UNRESOLVED: task fields are not release authority")
        return result


class CandidateAdapter:
    def load(self, directory):
        root = Path(directory).resolve(strict=True)
        content, ref = source(root / "run_manifest.json")
        manifest = json.loads(content)
        result = AdapterResult(source_refs=[ref])
        if manifest.get("schema_version") != "pipeline2-advanced-manifest-1":
            result.issues.append("UNSUPPORTED_CANDIDATE_MANIFEST_SCHEMA")
            return result
        hashes = manifest.get("artifacts", {})
        if not hashes or "advanced.json" not in hashes:
            result.issues.append("MISSING_ARTIFACT_HASHES")
            return result
        for relative, sha in hashes.items():
            file = (root / relative).resolve()
            if not file.is_relative_to(root) or not file.is_file() or digest(file.read_bytes()) != sha:
                result.issues.append(f"ARTIFACT_HASH_OR_PATH_INVALID:{relative}")
                return result
        data = json.loads((root / "advanced.json").read_bytes())
        if data.get("schema_version") != "pipeline2-advanced-data-1":
            result.issues.append("UNSUPPORTED_ADVANCED_DATA_SCHEMA")
            return result
        structures = {r["structure_id"]: r for r in data.get("structures", [])}
        predictions = {r["structure_id"]: r for r in data.get("predictions", [])}
        for row in data.get("candidates", []):
            cid, sid = row.get("candidate_id"), row.get("structure_id")
            structure, prediction = structures.get(sid), predictions.get(sid)
            issues = []
            if not cid or not structure or not prediction:
                issues.append("CANDIDATE_STRUCTURE_OR_PREDICTION_MISSING")
            if prediction and any(not prediction.get(k) for k in ["model_id", "model_version", "model_hash", "schema_version", "source_type"]):
                issues.append("MODEL_PROVENANCE_INCOMPLETE")
            if row.get("adsorption_energy_eV") is None:
                issues.append("DESCRIPTOR_VALUE_MISSING")
            validation_refs = row.get("validation_refs", [])
            if not validation_refs:
                issues.append("VALIDATION_REFS_MISSING: exploratory only")
            result.records.append({"external_id": cid, "schema_version": manifest["schema_version"],
                "structure_id": sid, "structure": structure, "prediction": prediction,
                "descriptor": {"name": "adsorption_energy_eV", "value": row.get("adsorption_energy_eV"),
                    "unit": "eV", "definition": "E(adsorbed)-E(reference_slab)-E(reference_molecule)",
                    "reference_ids": [row.get("slab_reference_id"), row.get("molecule_reference_id")]},
                "validation_refs": validation_refs, "gate_ref": manifest.get("gate"),
                "artifact_refs": [{"path": "advanced.json", "sha256": hashes["advanced.json"]}, ref],
                "source_type": row.get("source_type"), "data_origin": row.get("data_origin"),
                "issues": issues, "upstream_payload": row})
            result.issues.extend(f"{cid}:{v}" for v in issues)
        result.issues.append("AUTHORITATIVE_GATE_UNRESOLVED: no formal upstream write channel")
        return result


class FeedbackAdapter:
    def export(self, store, namespace, analysis_id, review_id):
        analysis = store.get(namespace, "AnalysisRun", analysis_id)
        review = store.get(namespace, "ReviewEvent", review_id)
        if not analysis or not review or review["payload"].get("analysis_id") != analysis_id:
            raise ValueError("Matching analysis and simulated review required")
        payload = {"schema_version": "pipeline3-feedback-1", "namespace": namespace,
            "analysis_id": analysis_id, "analysis": analysis,
            "observations": analysis["payload"]["results"], "conditions": analysis["payload"]["input_snapshot"],
            "interpretation": analysis["payload"]["interpretation"],
            "limitations": ["Software demo; synthetic records are not experimental validation",
                "No formal P1 receipt or candidate unlock", "No MLIP energy/force labels from experimental current",
                "GC/XPS records only; no spectral fitting; no formal FE"],
            "missing_items": analysis["payload"]["evidence"], "review": review,
            "next_tasks": analysis["payload"]["recommendations"],
            "source_type": analysis["source_type"], "validation_status": analysis["validation_status"],
            "gate_status": "unresolved", "feedback_status": "STAGED", "official_publish": False,
            "upstream_write_performed": False}
        package_id = "feedback_" + digest([analysis_id, review_id])[:24]
        with store.transaction():
            package = store.add(namespace, "FeedbackPackage", payload, key=package_id,
                source_type=analysis["source_type"], execution_status="COMPLETED", feedback_status="STAGED")
        report = self.report(payload)
        files = {"feedback.json": (json.dumps(package, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode(),
                 "analysis_report.md": report.encode()}
        manifest = {"schema_version": "pipeline3-export-manifest-1", "package_id": package_id,
                    "official_publish": False, "files": {name: digest(content) for name, content in files.items()}}
        files["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
        return package, files

    @staticmethod
    def report(payload):
        analysis = payload["analysis"]["payload"]
        lines = ["# Pipeline 3 实验分析与暂存反馈", "", "STAGED · official_publish=false · simulated review",
                 "", f"来源：{payload['source_type']}；namespace：{payload['namespace']}",
                 f"分析：{payload['analysis_id']}；规则运行，无真实 LLM 调用。", "",
                 "## 条件性解释", analysis["interpretation"], "", "## 观察与条件", ""]
        for r in analysis["results"]:
            metrics = {k: v for k, v in r.get("metrics", {}).items() if k not in {"time_s", "current_A", "j_mA_cm2", "calibration"}}
            lines += [f"- {r['measurement_id']} / {r['kind']} / {r['status']}", f"  - {canonical(metrics)}", f"  - 条件：{canonical(r['metadata'])}"]
        lines += ["", "## 缺项与边界", canonical(analysis["evidence"]), "", "## 下一步实验"]
        for card in analysis["recommendations"]:
            lines += [f"- {card['title']}：{card['purpose']}（规则 {card['rule_id']}）"]
        lines += ["", "## 审核", canonical(payload["review"]["payload"]), "", "## 限制"]
        lines += [f"- {v}" for v in payload["limitations"]]
        lines += ["", "## 输入和软件追溯", f"代码 SHA-256：{analysis['code_sha256']}",
                  f"参数：{canonical(analysis['parameters'])}", f"输入：{canonical(analysis['input_snapshot'])}"]
        return "\n".join(lines) + "\n"
