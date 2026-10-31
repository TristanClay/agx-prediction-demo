"""Pure, source-grounded UI projections. No new scientific claims or events."""

from agx_integration.io import digest
from agx_integration.workflow import AGENTS

PIPELINES = {
    "P1": ("Knowledge Discovery", "#35d5c5"),
    "P2": ("Atomistic Screening", "#70a7ff"),
    "P3": ("Experimental Validation", "#efbe67"),
}
NODE_PIPELINE = {
    "knowledge": "P1",
    "screening": "P2",
    "decision": "P2",
    "experiment": "P3",
    "feedback": "P3",
}
NODE_KIND = {
    "knowledge": "工具编排节点 · 历史快照适配",
    "screening": "工具编排节点 · ASE / MockModelAdapter",
    "decision": "规则节点 · 不确定性与预算",
    "experiment": "工具编排节点 · P3 Service / RuleBasedPlanner",
    "feedback": "规则节点 · 合同校验 / STAGED reader",
}
STEPS = (
    ("Knowledge Discovery", "knowledge", "历史文献 → 假设 → Ag 基线任务"),
    ("Structure Generation", "screening", "原生 ASE 坐标与 Ag / Ag–Cu 探索结构"),
    ("AI Screening Strategy", "decision", "解析 mock → 不确定性 → 稀疏 DFT 提议"),
    ("Experiment Planning", "experiment", "已有实验任务、匹配条件与独立样品"),
    ("Evidence Validation", "experiment", "已保存的 CA / HPLC 分析与 QC"),
    ("Evidence Feedback", "feedback", "证据 → STAGED 收件箱 → 下一轮草案"),
)
DEPENDENCIES = {
    "knowledge": [],
    "screening": ["knowledge"],
    "decision": ["screening"],
    "experiment": ["knowledge", "screening"],
    "feedback": ["knowledge", "screening", "experiment"],
}


def node_rows(bundle, step=None):
    events = {e["node_id"]: e for e in bundle.result["events"]}
    current = STEPS[step][1] if step is not None else None
    seen = {s[1] for s in STEPS[:step]} if step is not None else set()
    rows = []
    for node, name in AGENTS.items():
        event = events.get(node, {})
        original = event.get("status", "PENDING")
        playback = "CURRENT" if node == current else "COMPLETED" if node in seen else "PENDING"
        rows.append(
            {
                "node_id": node,
                "Agent": name,
                "Pipeline": NODE_PIPELINE[node],
                "实现类型": NODE_KIND[node],
                "原始执行状态": original,
                "回放进度": playback if step is not None else "完整记录",
                "display_status": playback if step is not None else original,
            }
        )
    return rows


def inspector(bundle, node):
    event = next((e for e in bundle.result["events"] if e["node_id"] == node), {})
    outputs = bundle.result["outputs"]
    inputs = {n: outputs.get(n) for n in DEPENDENCIES[node] if n in outputs}
    return {
        "Agent": AGENTS[node],
        "Pipeline": NODE_PIPELINE[node],
        "实现类型": NODE_KIND[node],
        "run_id": bundle.result["run_id"],
        "node_id": node,
        "input": {"recorded_input": event.get("input"), "upstream_contracts": inputs},
        "execution": {
            k: event.get(k)
            for k in [
                "tool",
                "model",
                "reason",
                "status",
                "origin",
                "started_at",
                "finished_at",
                "elapsed_s",
                "attempt",
                "error",
            ]
        },
        "output": outputs.get(node, event.get("output")),
        "artifacts": [
            r
            for r in bundle.manifest["artifacts"]
            if r["path"].startswith(
                bundle.result.get("node_directories", {}).get(node, "__absent__") + "/"
            )
        ],
    }


def historical_tasks(bundle):
    return bundle.json("knowledge", "p1_snapshot/06_experiment_tasks.json")


def experiment_cards(bundle):
    """P1 task identities stay distinct from the one P3 operational plan."""
    output = bundle.result["outputs"].get("experiment", {})
    records = output.get("records", [])
    plan = next((r for r in records if r["kind"] == "ExperimentPlan"), None)
    protocol = next((r for r in records if r["kind"] == "Protocol"), None)
    return [
        {
            "Experiment ID": t["task_id"],
            "研究问题": t.get("experiment_question", "未记录"),
            "实验目的": t.get("reason", "未记录"),
            "推荐实验": t.get("experiment", "未记录"),
            "控制变量": t.get("required_control", "未记录"),
            "预期观察": t.get("expected_observation", "未记录"),
            "替代解释": "未记录（可查看下方 P3 规则草案）",
            "优先级": t.get("priority", "未记录"),
            "所需数据": plan["payload"].get("required_measurements", "未记录")
            if plan
            else "未记录",
            "结构限制": t.get("activation_condition", "未记录"),
            "当前状态": "历史 P1 任务 · REPLAY · 非正式放行",
            "当前 P3 Plan ID": plan["id"] if plan else None,
            "P3 原始计划状态": plan["execution_status"] if plan else None,
            "P3 Plan 关系": "同一 Ag 基线任务上下文；不是四条独立已执行 Plan",
            "Protocol": protocol["payload"] if protocol else None,
        }
        for t in historical_tasks(bundle)
    ]


def lifecycle(bundle):
    records = bundle.result["outputs"].get("experiment", {}).get("records", [])
    rows = []
    for sample in (r for r in records if r["kind"] == "Sample"):
        sid = sample["id"]
        measurements = [
            r for r in records if r["kind"] == "Measurement" and r["payload"]["sample_id"] == sid
        ]
        analyzed = any(
            sid == r.get("sample_id")
            for r in bundle.result["outputs"]["experiment"]["analysis"]["payload"]["results"]
        )
        reviews = [
            r
            for r in records
            if r["kind"] == "ReviewEvent" and r["payload"].get("sample_id") == sid
        ]
        rows.append(
            {
                "Sample ID": sid,
                "样品": sample["payload"]["name"],
                "Planned": any(r["kind"] == "ExperimentPlan" for r in records),
                "Prepared": True,
                "Measured": bool(measurements),
                "Analyzed": analyzed,
                "Reviewed": bool(reviews),
                "execution_status": sample["execution_status"],
                "data_status": sample["data_status"],
                "来源": sample["source_type"],
                "候选关联": "任务级 CandidateLink / IDEAL_STRUCTURE_ONLY；无样品级一一映射",
            }
        )
    return rows


def analysis_results(bundle, kind=None):
    rows = (
        bundle.result["outputs"]
        .get("experiment", {})
        .get("analysis", {})
        .get("payload", {})
        .get("results", [])
    )
    return [r for r in rows if kind is None or r["kind"] == kind]


def next_task_drafts(bundle):
    output = bundle.result["outputs"].get("feedback", {}).get("feedback")
    if not output:
        return []
    return [
        {
            "draft_id": "DRAFT_"
            + digest([bundle.result["run_id"], output["feedback_id"], action])[:20],
            "parent_run_id": bundle.result["run_id"],
            "hypothesis_id": output["hypothesis_id"],
            "candidate_id": output["candidate_id"],
            "feedback_id": output["feedback_id"],
            "target_pipeline": action.get(
                "target", "P3" if action.get("rule_id") != "p2_mismatch" else "P2"
            ),
            "source_recommendation": action,
            "evidence_refs": action.get("evidence_refs", output["measurement_ids"]),
            "status": "DRAFT",
            "review_status": "WAITING_FOR_REVIEW",
            "gate_status": "PENDING_GATE",
            "official_publish": False,
            "origin": output["provenance"]["origin"],
        }
        for action in output["recommended_actions"]
    ]
