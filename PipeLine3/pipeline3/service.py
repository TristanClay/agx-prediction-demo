"""Shared application/CLI service; Streamlit session state is never the database."""

from pathlib import Path
from time import perf_counter
from uuid import uuid4

from pipeline3 import analysis as numeric
from pipeline3.adapters import FeedbackAdapter
from pipeline3.planner import RuleBasedPlanner
from pipeline3.store import Store, digest, now, stable_id

DEFAULT_PARAMETERS = {"window_s": [0., 600.], "early_window_s": [0., 60.], "late_window_s": [540., 600.]}
DEFAULT_CONDITIONS = {"potential_V": 1.2, "potential_reference": "RHE", "electrolyte": "demo KOH 1M",
                      "temperature_C": 25., "reaction_window_id": "0-600s"}
GROUPS = ["fresh", "deactivated", "washed", "spike", "solution_control"]


class Service:
    def __init__(self, root="data/local"):
        self.store = Store(root)

    def create_task(self, namespace, title, *, source_type="experimental", upstream=None):
        upstream = upstream or {}
        with self.store.transaction():
            task = self.store.add(namespace, "Task", {"title": title,
                "external_id": upstream.get("external_id"),
                "hypothesis_id": upstream.get("hypothesis_id", "operational_recoverability"),
                "hypothesis": "匹配条件下清洗能否使失活 Ag 的电流恢复至新鲜样品的80%以上？",
                "mechanism_question": "furoate 是否与失活因果相关？操作性恢复不足以回答该问题。",
                "gate_status": "unresolved", "upstream": upstream}, source_type=source_type, execution_status="PLANNED")
            protocol = self.store.add(namespace, "Protocol", {"task_id": task["id"], "version": "1.0",
                "conditions": DEFAULT_CONDITIONS, "min_independent": 2, "groups": GROUPS,
                "recovery_threshold": .8, "repeat_spread_limit": .2,
                "analysis_parameters": DEFAULT_PARAMETERS, "hplc_r2_min": .99,
                "criterion_note": "演示协议预设门槛，不是材料领域的通用验收标准"}, source_type=source_type)
            self.store.add(namespace, "ExperimentPlan", {"task_id": task["id"], "protocol_id": protocol["id"],
                "execution_mode": "manual experiment / file import", "required_measurements": ["CA", "HPLC", "XPS record"],
                "formal_gate_authority": False}, source_type=source_type, execution_status="PLANNED")
        return task

    def protocol(self, namespace, task_id):
        rows = [r for r in self.store.list(namespace, "Protocol") if r["payload"]["task_id"] == task_id]
        if not rows:
            raise ValueError("Task protocol missing")
        return rows[-1]

    def revise_protocol(self, namespace, task_id, settings):
        previous = self.protocol(namespace, task_id)
        allowed = {"conditions", "min_independent", "groups", "recovery_threshold", "repeat_spread_limit", "analysis_parameters", "hplc_r2_min"}
        if set(settings) != allowed:
            raise ValueError("Protocol settings must contain exactly: " + ", ".join(sorted(allowed)))
        if set(settings["conditions"]) != set(DEFAULT_CONDITIONS):
            raise ValueError("Protocol requires all five comparison condition fields")
        for name in ["potential_V", "temperature_C"]:
            numeric.number(settings["conditions"][name], name)
        if settings["conditions"]["potential_reference"] not in {"RHE", "SHE", "Ag/AgCl", "Hg/HgO"}:
            raise ValueError("Unknown potential reference")
        if not settings["conditions"]["electrolyte"] or not settings["conditions"]["reaction_window_id"]:
            raise ValueError("Electrolyte and reaction window identity required")
        if type(settings["min_independent"]) is not int or settings["min_independent"] < 2:
            raise ValueError("Protocol requires at least two independent samples")
        if settings["groups"] != GROUPS:
            raise ValueError("V1 recovery protocol requires the five matched groups")
        for field in ["recovery_threshold", "repeat_spread_limit", "hplc_r2_min"]:
            numeric.number(settings[field], field, positive=True)
        if settings["hplc_r2_min"] > 1:
            raise ValueError("R2 threshold cannot exceed 1")
        parameters = settings["analysis_parameters"]
        if set(parameters) != set(DEFAULT_PARAMETERS):
            raise ValueError("Three CA windows required")
        for window in parameters.values():
            if len(window) != 2 or not 0 <= numeric.number(window[0], "window") < numeric.number(window[1], "window"):
                raise ValueError("Window must contain increasing nonnegative endpoints")
        with self.store.transaction():
            return self.store.add(namespace, "Protocol", {**previous["payload"], **settings,
                "version": str(float(previous["payload"]["version"]) + 1), "parent_protocol_id": previous["id"]}, source_type=previous["source_type"])

    def create_sample(self, namespace, name, batch_name, task_id, *, source_type="experimental"):
        if not name.strip() or not batch_name.strip():
            raise ValueError("Sample and batch names required")
        self.check_origin(namespace, source_type)
        if not self.store.get(namespace, "Task", task_id):
            raise ValueError("Task not in namespace")
        with self.store.transaction():
            batch = self.store.add(namespace, "Batch", {"name": batch_name}, source_type=source_type)
            sample = self.store.add(namespace, "Sample", {"name": name, "batch_id": batch["id"], "task_id": task_id}, source_type=source_type)
            state = self.store.add(namespace, "SampleState", {"sample_id": sample["id"], "label": "fresh",
                "parent_state_id": None, "treatment_event_id": None}, source_type=source_type)
        return sample, state

    def treat(self, namespace, state_id, label, method, *, event_key=None):
        previous = self.store.get(namespace, "SampleState", state_id)
        if not previous:
            raise ValueError("Sample state not in namespace")
        with self.store.transaction():
            event = self.store.add(namespace, "TreatmentEvent", {"sample_id": previous["payload"]["sample_id"],
                "parent_state_id": state_id, "treatment": label, "method": method,
                "event_time": event_key or now()}, source_type=previous["source_type"])
            state = self.store.add(namespace, "SampleState", {"sample_id": previous["payload"]["sample_id"],
                "label": label, "parent_state_id": state_id, "treatment_event_id": event["id"]}, source_type=previous["source_type"])
        return state

    def import_calibration(self, namespace, content, filename, metadata, *, source_type="experimental"):
        self.check_origin(namespace, source_type)
        if not isinstance(metadata, dict):
            raise ValueError("Metadata must be a JSON object")
        fitted = numeric.fit_calibration(numeric.read_csv(content), metadata)
        with self.store.transaction():
            raw = self.store.artifact(namespace, content, filename, source_type)
            return self.store.add(namespace, "Calibration", {"artifact_id": raw["id"], "filename": filename,
                "metadata": metadata, "fit": fitted}, source_type=source_type, execution_status="COMPLETED")

    def import_measurement(self, namespace, content, filename, kind, state_id, protocol_id, metadata,
                           *, replicate_type="independent_sample", calibration_id=None, source_type="experimental"):
        if kind not in {"CA", "HPLC", "GC", "XPS"}:
            raise ValueError("Unknown measurement kind")
        if not isinstance(metadata, dict):
            raise ValueError("Metadata must be a JSON object")
        state = self.store.get(namespace, "SampleState", state_id)
        protocol = self.store.get(namespace, "Protocol", protocol_id)
        if not state or not protocol:
            raise ValueError("State and protocol must exist in this namespace")
        sample = self.store.get(namespace, "Sample", state["payload"]["sample_id"])
        if sample["payload"]["task_id"] != protocol["payload"]["task_id"]:
            raise ValueError("Sample belongs to a different task")
        if source_type != sample["source_type"]:
            raise ValueError("Measurement and sample provenance differ; create a separate sample")
        if namespace.startswith("demo:") and source_type != "synthetic":
            raise ValueError("Real uploads must use a real namespace")
        if not namespace.startswith("demo:") and source_type == "synthetic":
            raise ValueError("Synthetic uploads must use a demo namespace")
        if replicate_type not in {"technical", "independent_sample"}:
            raise ValueError("Unknown replicate type")
        if calibration_id and not self.store.get(namespace, "Calibration", calibration_id):
            raise ValueError("Calibration is not in namespace")
        # Parse only the file structure here. Numerical problems remain traceable QC outcomes.
        if kind in {"CA", "HPLC"}:
            numeric.read_csv(content)
        with self.store.transaction():
            raw = self.store.artifact(namespace, content, filename, source_type)
            payload = {"kind": kind, "state_id": state_id, "sample_id": sample["id"],
                "batch_id": sample["payload"]["batch_id"], "protocol_id": protocol_id,
                "task_id": protocol["payload"]["task_id"], "metadata": metadata,
                "replicate_type": replicate_type, "calibration_id": calibration_id,
                "artifact_id": raw["id"], "original_filename": filename}
            # File name is display-only and does not change measurement identity.
            identity = {k: v for k, v in payload.items() if k != "original_filename"}
            key = stable_id("Measurement", identity)
            existing = self.store.get(namespace, "Measurement", key)
            if existing:
                return existing
            return self.store.add(namespace, "Measurement", payload, key=key, source_type=source_type,
                execution_status="COMPLETED", data_status="UNREVIEWED")

    def import_adapter_result(self, namespace, result, kind):
        if kind == "task":
            return [self.create_task(namespace, r.get("title", r["external_id"]), source_type="upstream_snapshot", upstream=r)
                    for r in result.records]
        with self.store.transaction():
            return [self.store.add(namespace, "CandidateLink", {**r, "mapping_status": "IDEAL_STRUCTURE_ONLY",
                    "integration_status": result.integration_status}, source_type="upstream_snapshot") for r in result.records]

    @staticmethod
    def check_origin(namespace, source_type):
        if namespace.startswith("demo:") and source_type != "synthetic":
            raise ValueError("Real uploads must use a real namespace")
        if not namespace.startswith("demo:") and source_type == "synthetic":
            raise ValueError("Synthetic uploads must use a demo namespace")

    def record_trend_review(self, namespace, task_id, link_id, status, note, reviewer, source_type):
        if not self.store.get(namespace, "Task", task_id) or not self.store.get(namespace, "CandidateLink", link_id):
            raise ValueError("Task and candidate must belong to namespace")
        if status not in {"MATCHED", "MISMATCH", "NOT_ASSESSED"} or not note.strip() or not reviewer.strip():
            raise ValueError("Explicit trend review status, comparison basis, and reviewer required")
        with self.store.transaction():
            return self.store.add(namespace, "CandidateLink", {"task_id": task_id, "candidate_link_id": link_id,
                "trend_review": {"status": status, "note": note, "reviewer": reviewer,
                                 "basis": "user-recorded comparison; descriptor is not experimental current"}}, source_type=source_type)

    def analyze(self, namespace, task_id=None, parameters=None):
        task = self.store.get(namespace, "Task", task_id) if task_id else self.store.latest(namespace, "Task")
        if not task:
            raise ValueError("Create or import a task first")
        protocol = self.protocol(namespace, task["id"])
        parameters = parameters or protocol["payload"]["analysis_parameters"]
        events = []

        def trace(name, refs, function, reason):
            tick = perf_counter()
            try:
                value = function()
                events.append({"tool": name, "input_refs": refs, "status": "COMPLETED", "elapsed_s": perf_counter() - tick,
                               "output": value, "reason": reason, "execution_kind": "deterministic_python_tool"})
                return value
            except (ValueError, KeyError, OSError, TypeError) as exc:
                events.append({"tool": name, "input_refs": refs, "status": "FAILED", "elapsed_s": perf_counter() - tick,
                               "output": str(exc), "reason": reason, "execution_kind": "deterministic_python_tool"})
                raise

        trace("read_task", [task["id"]], lambda: {"task_id": task["id"], "gate_status": "unresolved"}, "读取任务及协议，不推断门控放行")
        measurements = [m for m in self.store.list(namespace, "Measurement") if m["payload"]["task_id"] == task["id"]]
        trace("inspect_data", [m["id"] for m in measurements], lambda: {"measurements": len(measurements),
              "samples": len({m["payload"]["sample_id"] for m in measurements})}, "分别统计样品身份与测量次数")
        results, input_snapshot = [], []
        for measurement in measurements:
            p = measurement["payload"]
            result = {"measurement_id": measurement["id"], "kind": p["kind"], "sample_id": p["sample_id"],
                "state_id": p["state_id"], "batch_id": p["batch_id"], "artifact_id": p["artifact_id"],
                "replicate_type": p["replicate_type"], "metadata": p["metadata"], "metrics": {}, "issues": []}
            raw = self.store.get(namespace, "RawArtifact", p["artifact_id"])
            snapshot = {"measurement_id": measurement["id"], "artifact_sha256": raw["payload"]["sha256"],
                        "measurement_sha256": digest(measurement), "protocol_id": p["protocol_id"]}
            try:
                content = self.store.read_artifact(namespace, p["artifact_id"])
                if p["kind"] == "CA":
                    metrics = trace("analyze_ca", [measurement["id"]], lambda: numeric.analyze_ca(numeric.read_csv(content), p["metadata"], parameters), "按显式窗口与单位积分")
                elif p["kind"] == "HPLC":
                    calibrations = self.store.list(namespace, "Calibration")
                    calibration = self.store.get(namespace, "Calibration", p["calibration_id"]) if p["calibration_id"] else next(
                        (c for c in reversed(calibrations) if all(c["payload"]["metadata"].get(k) == p["metadata"].get(k)
                         for k in ["species", "method_version", "area_unit"])), None)
                    if calibration:
                        self.store.read_artifact(namespace, calibration["payload"]["artifact_id"])
                        snapshot["resolved_calibration_id"] = calibration["id"]
                        snapshot["calibration_sha256"] = digest(calibration)
                    fit = calibration["payload"]["fit"] if calibration else None
                    metrics = trace("analyze_hplc", [measurement["id"], calibration["id"] if calibration else "missing"],
                        lambda: numeric.analyze_hplc(numeric.read_csv(content), p["metadata"], fit, protocol["payload"]["hplc_r2_min"]), "按已确认物种标定与连续取样质量核算")
                else:
                    required = ["method_version", "operator", "acquired_at"] + (["gas_collection_method", "calibration_ref", "charge_window_s"] if p["kind"] == "GC" else ["treatment_state", "interpretation_note"])
                    metrics = trace("check_record", [measurement["id"]], lambda: {"quantitative_analysis": "NOT_IMPLEMENTED",
                        "issues": [f"MISSING_{k}" for k in required if not p["metadata"].get(k)]}, "仅检查文件与协议字段，不拟合谱图或生成 FE")
                result["metrics"] = metrics
                result["issues"].extend(metrics.get("issues", []))
                if p["kind"] in {"CA", "HPLC"}:
                    mismatches = [k for k, v in protocol["payload"]["conditions"].items() if p["metadata"].get(k) != v]
                    if mismatches:
                        result["issues"].append("PROTOCOL_CONDITIONS_MISMATCH:" + ",".join(mismatches))
                result["status"] = "PARTIAL" if result["issues"] else "VALID"
            except (ValueError, KeyError, OSError, TypeError) as exc:
                result["status"] = "NEEDS_INFO" if "MISSING_CALIBRATION" in str(exc) else "INVALID"
                result["issues"] = [str(exc)]
            input_snapshot.append(snapshot)
            results.append(result)
        evidence = trace("assess_evidence", [r["measurement_id"] for r in results],
                         lambda: self.assess(namespace, task, protocol, results, parameters), "质控、独立重复和操作性假设分别判定")
        cards = trace("select_strategy", [task["id"]], lambda: RuleBasedPlanner().plan(task, evidence, [r["measurement_id"] for r in results]), "透明规则选择实验模板，不输出成功概率")
        old = [r for r in self.store.list(namespace, "AnalysisRun") if r["payload"]["task_id"] == task["id"]]
        parent = old[-1] if old else None
        interpretation = self.interpret(evidence)
        code_hash = digest({p.name: digest(p.read_bytes()) for p in sorted(Path(__file__).parent.glob("*.py"))})
        analysis_id = "analysis_" + uuid4().hex
        previous_cards = parent["payload"]["recommendations"] if parent else []
        active_rules = {c["rule_id"] for c in cards}
        changes = [{"rule_id": c["rule_id"], "state": "UPDATED" if c["rule_id"] in active_rules else "RESOLVED"} for c in previous_cards]
        trace("emit_cards", [analysis_id], lambda: {"active_rules": sorted(active_rules), "previous_changes": changes}, "保留建议版本与已解决缺项")
        provenance = {m["source_type"] for m in measurements}
        if "synthetic" in provenance and len(provenance) > 1:
            raise ValueError("Mixed synthetic/experimental analysis forbidden")
        origin = next(iter(provenance), task["source_type"])
        with self.store.transaction():
            run = self.store.add(namespace, "AnalysisRun", {"task_id": task["id"], "protocol_id": protocol["id"],
                "version": len(old) + 1, "parent_analysis_id": parent["id"] if parent else None,
                "parameters": parameters, "input_snapshot": input_snapshot, "code_sha256": code_hash,
                "results": results, "evidence": evidence, "recommendations": cards, "recommendation_changes": changes,
                "interpretation": interpretation, "tool_events": events, "planner_mode": RuleBasedPlanner.status},
                key=analysis_id, source_type=origin, execution_status="COMPLETED",
                data_status="PARTIAL" if evidence["qc_issues"] or evidence["insufficient_groups"] or evidence["missing_product_groups"] else "VALID",
                hypothesis_outcome=evidence["hypothesis_outcome"], validation_status="SOFTWARE_ANALYZED_NOT_FORMALLY_REVIEWED")
            for card in cards:
                self.store.add(namespace, "Recommendation", {**card, "analysis_id": run["id"]}, source_type=origin)
        return run

    def assess(self, namespace, task, protocol, results, parameters=None):
        pp = protocol["payload"]
        parameters = parameters or pp["analysis_parameters"]
        valid_ca = [r for r in results if r["kind"] == "CA" and r["status"] == "VALID"]
        groups = {role: [r for r in valid_ca if r["metadata"].get("group") == role] for role in pp["groups"]}
        counts = {role: len({r["sample_id"] for r in rows}) for role, rows in groups.items()}
        # Collapse repeated traces within a physical sample before comparing independent samples.
        # Never silently select the first/favourable trace when technical repeats disagree.
        paired_groups, repeat_issues = {}, []
        for role, rows in groups.items():
            per_sample = {}
            for row in rows:
                per_sample.setdefault(row["sample_id"], []).append(row)
            paired_groups[role] = []
            for sample_id, repeated in per_sample.items():
                values = [r["metrics"]["mean_j_mA_cm2"] for r in repeated]
                mean = sum(values) / len(values)
                scale = max(abs(mean), 1e-12)
                if (max(values) - min(values)) / scale > pp["repeat_spread_limit"]:
                    repeat_issues.append(f"WITHIN_SAMPLE_DISAGREEMENT:{role}:{sample_id}")
                paired_groups[role].append({**repeated[0], "metrics": {**repeated[0]["metrics"], "mean_j_mA_cm2": mean}})
        valid_hplc = [r for r in results if r["kind"] == "HPLC" and r["status"] == "VALID"
                      and r["metrics"]["rows"][-1]["sampling_time_s"] == parameters["window_s"][1]
                      and parameters["window_s"] == pp["analysis_parameters"]["window_s"]
                      and r["metrics"]["rows"][-1]["net_formed_umol"] is not None]
        product_groups = {}
        for role, ca_rows in groups.items():
            matched = {h["sample_id"] for h in valid_hplc if any(h["state_id"] == c["state_id"]
                       and h["metadata"].get("reaction_window_id") == c["metadata"].get("reaction_window_id") for c in ca_rows)}
            product_groups[role] = len(matched)
        insufficient = [role for role in pp["groups"] if counts[role] < pp["min_independent"]]
        missing_product = [role for role in pp["groups"] if product_groups[role] < pp["min_independent"]]
        ratios, ratio_issues = [], []
        for washed in paired_groups.get("washed", []):
            fresh = next((f for f in paired_groups.get("fresh", []) if f["sample_id"] == washed["sample_id"]), None)
            if fresh:
                ratio, issue = numeric.recovery_ratio(fresh["metrics"], washed["metrics"], fresh["metadata"], washed["metadata"])
                if issue:
                    ratio_issues.append(issue)
                elif washed["sample_id"] not in {r["sample_id"] for r in ratios}:
                    ratios.append({"sample_id": washed["sample_id"], "ratio": ratio})
        # A restoration hypothesis also needs evidence of an actually lower deactivated state.
        deactivation_ratios = []
        for deactivated in paired_groups.get("deactivated", []):
            fresh = next((f for f in paired_groups.get("fresh", []) if f["sample_id"] == deactivated["sample_id"]), None)
            if fresh:
                value, issue = numeric.recovery_ratio(fresh["metrics"], deactivated["metrics"], fresh["metadata"], deactivated["metadata"])
                if value is not None:
                    deactivation_ratios.append({"sample_id": deactivated["sample_id"], "ratio": value})
        demonstrated_loss = len({r["sample_id"] for r in deactivation_ratios if r["ratio"] < pp["recovery_threshold"]}) >= pp["min_independent"]
        enough = len(ratios) >= pp["min_independent"] and not insufficient and not missing_product and demonstrated_loss
        outcomes = [r["ratio"] >= pp["recovery_threshold"] for r in ratios]
        outcome = "INCONCLUSIVE"
        if enough:
            spread = max(r["ratio"] for r in ratios) - min(r["ratio"] for r in ratios)
            outcome = "MIXED" if repeat_issues or spread > pp["repeat_spread_limit"] or any(outcomes) != all(outcomes) else ("SUPPORTED" if all(outcomes) else "REFUTED")
        issues = sorted({i for r in results for i in r["issues"]} | set(ratio_issues) | set(repeat_issues))
        # Latest explicit review per link, scoped to this task; historical mismatches can be resolved.
        trend_reviews = {}
        for candidate in self.store.list(namespace, "CandidateLink"):
            p = candidate["payload"]
            if p.get("task_id") == task["id"] and p.get("trend_review"):
                trend_reviews[p["candidate_link_id"]] = p["trend_review"]
        return {"group_counts": counts, "product_group_counts": product_groups, "min_independent": pp["min_independent"],
            "independent_batches": len({r["batch_id"] for r in valid_ca}),
            "technical_measurements": len([r for r in results if r["replicate_type"] == "technical"]),
            "insufficient_groups": insufficient, "missing_product_groups": missing_product,
            "invalid_measurements": [r["measurement_id"] for r in results if r["status"] in {"INVALID", "NEEDS_INFO"}],
            "missing_calibration": any("MISSING_CALIBRATION" in i for i in issues), "qc_issues": issues,
            "post_ca_record_present": any(r["kind"] == "XPS" and r["status"] == "VALID"
                and r["metadata"].get("treatment_state") in {"deactivated", "washed"} for r in results),
            "recovery_ratios": ratios, "hypothesis_outcome": outcome,
            "deactivation_ratios": deactivation_ratios, "deactivation_demonstrated": demonstrated_loss,
            "mechanism_outcome": "INCONCLUSIVE", "criterion": f"protocol recovery >= {pp['recovery_threshold']}; per-group n >= {pp['min_independent']}",
            "p2_mismatch": any(r["status"] == "MISMATCH" for r in trend_reviews.values())}

    @staticmethod
    def interpret(evidence):
        conclusions = {"INCONCLUSIVE": "现有证据不足以判断协议下的可恢复性。", "SUPPORTED": "有效独立重复满足演示协议的操作性恢复门槛。",
                       "REFUTED": "有效独立重复未达到恢复门槛，当前数据反驳该操作性预期。", "MIXED": "独立样品之间存在不一致，暂不归并为支持结论。"}
        return conclusions[evidence["hypothesis_outcome"]] + " 该判断仅针对匹配条件下的电流恢复；furoate 因果机制仍为 INCONCLUSIVE。合成演示不构成真实材料结论，不产生正式门控放行。"

    def review(self, namespace, analysis_id, reviewer, note):
        run = self.store.get(namespace, "AnalysisRun", analysis_id)
        if not run or not reviewer.strip():
            raise ValueError("Analysis and reviewer required")
        with self.store.transaction():
            return self.store.add(namespace, "ReviewEvent", {"analysis_id": analysis_id, "reviewer": reviewer,
                "note": note, "review_type": "simulated review", "official_acceptance": False},
                source_type=run["source_type"], feedback_status="STAGED")

    def export(self, namespace, analysis_id, review_id):
        return FeedbackAdapter().export(self.store, namespace, analysis_id, review_id)
