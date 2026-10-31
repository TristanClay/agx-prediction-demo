"""Workflow-first Streamlit console; every scientific value comes from saved artifacts."""

import html
from pathlib import Path

import pandas as pd
import streamlit as st

from agx_integration.contracts import RunRequest
from agx_integration.workflow import AGENTS

from . import charts
from .runtime import SCENARIOS, open_bundle
from public_runtime import session_context, run_for_session, paths_for_session, save_for_session
from .view import (
    STEPS,
    analysis_results,
    experiment_cards,
    historical_tasks,
    inspector,
    lifecycle,
    next_task_drafts,
    node_rows,
)

PAGES = [
    "Workflow Overview",
    "Pipeline 1",
    "Pipeline 2",
    "Pipeline 3",
    "Agent Trace",
    "Results & Feedback",
]
CSS = """<style>
.stApp{background:#0b1220;color:#e6edf8}
.block-container{max-width:1780px;padding-top:4rem;padding-bottom:3rem}
[data-testid="stSidebar"]{background:#0e1829;border-right:1px solid #24334c}
[data-testid="stMetric"]{background:#111e31;border:1px solid #273a56;border-radius:10px;padding:12px}
[data-testid="stMetricValue"]{font-size:1.35rem}
.agx-eyebrow{color:#73dccc;font-size:.75rem;letter-spacing:.18em;margin-bottom:.4rem}
.agx-title{font-size:2rem;font-weight:650;line-height:1.2;margin-bottom:.4rem}
.agx-subtitle{color:#9eafc9;margin-bottom:1.2rem}
.agx-banner{border:1px solid #2b405d;background:#101e32;border-radius:9px;padding:10px 14px;overflow-wrap:anywhere}
.stTabs [data-baseweb="tab-list"]{gap:14px}
code{overflow-wrap:anywhere}
@media(max-width:900px){.block-container{padding-left:1rem;padding-right:1rem}.agx-title{font-size:1.6rem}}
</style>"""



def table(rows):
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.caption("当前记录没有此项数据。")


def json_box(label, payload, expanded=False):
    with st.expander(label, expanded=expanded):
        st.json(payload, expanded=False)


def clear_active():
    for key in ["active_bundle", "launch_error", "context_id", "canvas_seen"]:
        st.session_state.pop(key, None)
    st.session_state["story_active"] = False
    st.session_state["story_step"] = 0
    st.session_state["inspect_node"] = "knowledge"


def story_move(index):
    st.session_state["story_active"] = True
    st.session_state["story_step"] = max(0, min(5, index))
    st.session_state["inspect_node"] = STEPS[st.session_state["story_step"]][1]
    st.session_state.pop("canvas_seen", None)


def open_details():
    st.session_state["nav"] = "Agent Trace"


def launch_run():
    try:
        request = RunRequest(
            run_id=st.session_state["new_run_id"],
            mode=st.session_state["run_mode"],
            scenario=st.session_state["scenario"],
            model=st.session_state["real_model"],
            dft_budget_jobs=st.session_state["proposal_budget"],
        )
        bundle = run_for_session(session_context(st.session_state), request)
        st.session_state["active_bundle"] = bundle
        if (bundle.root / "manifest.json").exists():
            st.session_state["replay_choice"] = str(bundle.root)
        st.session_state.pop("launch_error", None)
    except (ValueError, RuntimeError, OSError) as exc:
        st.session_state.pop("active_bundle", None)
        st.session_state["launch_error"] = str(exc)


def launch_hypothesis():
    try:
        fresh = run_for_session(session_context(st.session_state), RunRequest(run_id=st.session_state["p1_new_id"], scenario=st.session_state["scenario"]))
        st.session_state["active_bundle"] = fresh
        st.session_state["replay_choice"] = str(fresh.root)
        st.session_state["p1_launched"] = fresh.result["run_id"]
        st.session_state.pop("launch_error", None)
    except (ValueError, RuntimeError, OSError) as exc:
        st.session_state["launch_error"] = str(exc)


def sidebar():
    with st.sidebar:
        st.markdown("### Ag-X Prediction")
        st.caption("V1.1 / AGENT WORKFLOW")
        st.radio("工作区", PAGES, key="nav")
        st.divider()
        st.selectbox("Scenario Selector", SCENARIOS, key="scenario", on_change=clear_active)
        paths = [str(p) for p in paths_for_session(session_context(st.session_state), st.session_state["scenario"])]
        if paths:
            if st.session_state.get("replay_choice") not in paths:
                st.session_state["replay_choice"] = paths[0]
            st.selectbox(
                "Replay Run",
                paths,
                key="replay_choice",
                format_func=lambda p: Path(p).name,
                on_change=clear_active,
            )
        else:
            st.warning("没有此场景的回放包，可新建 SYNTHETIC Run。")
        with st.expander("创建 V1.1 Run"):
            st.text_input("新 Run ID", value="v11-ag-baseline", key="new_run_id")
            st.selectbox("执行模式", ["SYNTHETIC", "LIVE"], key="run_mode")
            st.selectbox("LIVE 模型（均未配置）", ["UMA", "MACE-MH-1", "DPA4"], key="real_model")
            st.number_input(
                "DFT 提议预算 / jobs",
                min_value=0,
                max_value=100,
                value=0,
                step=1,
                key="proposal_budget",
            )
            st.button("运行 V1.1 Workflow", on_click=launch_run, width="stretch")
            st.caption("SYNTHETIC 执行本地 mock / QC；LIVE 未配置时 BLOCKED。真实 DFT 始终 0。")
        st.divider()
        st.caption("公开演示 · 每会话最多 5 个新运行。临时数据可能随重启清除，请下载保存。")
    if st.session_state.get("launch_error"):
        st.error(st.session_state["launch_error"])
        st.stop()
    active = st.session_state.get("active_bundle")
    if active:
        # Even an active sealed bundle is reverified on every interaction.
        return open_bundle(active.root) if (active.root / "manifest.json").exists() else active
    if not paths:
        st.info("需要一个经过校验的场景包。")
        st.stop()
    return open_bundle(st.session_state["replay_choice"])


def story_controls(bundle):
    active = st.session_state.get("story_active", False)
    index = st.session_state.get("story_step", 0)
    with st.container(border=True):
        st.markdown("**Story Mode** · 五分钟主线 / 手动步进")
        cols = st.columns([1, 1, 1, 1, 1.4])
        cols[0].button("Start Demo", on_click=story_move, args=(0,), width="stretch")
        cols[1].button(
            "Previous Step",
            on_click=story_move,
            args=(index - 1,),
            disabled=not active or index == 0,
            width="stretch",
        )
        cols[2].button(
            "Next Step",
            on_click=story_move,
            args=(index + 1,),
            disabled=active and index == 5,
            width="stretch",
        )
        cols[3].button("Restart", on_click=story_move, args=(0,), width="stretch")
        cols[4].button("Open Agent Details", on_click=open_details, width="stretch")
        if active:
            st.progress(
                (index + 1) / 6, text=f"Step {index + 1}/6 · {STEPS[index][0]} · {STEPS[index][2]}"
            )
            st.caption(
                "回放光标控制展示进度；原始执行状态和时间保留在 Inspector，不表示现场重新计算。"
            )
        else:
            st.caption("当前展示完整记录。Start Demo 从知识发现开始；不同 Scenario 对应独立 Run。")
    return index if active else None


def render_inspector(bundle):
    node = st.selectbox(
        "Agent Inspector", list(AGENTS), format_func=lambda n: AGENTS[n], key="inspect_node"
    )
    details = inspector(bundle, node)
    st.markdown(f"#### {details['Agent']}")
    st.caption(f"{details['Pipeline']} · {details['实现类型']}")
    execution = details["execution"]
    st.write("原始执行状态：", execution["status"] or "PENDING")
    st.write("实际工具：", execution["tool"] or "未执行")
    st.write("规则 / 证据依据：", execution["reason"] or "未记录")
    for title, key in [
        ("Input · 上游合同与关键 ID", "input"),
        ("Execution · 真实工具与时间", "execution"),
        ("Output · 保存的输出", "output"),
    ]:
        json_box(title, details[key])
    json_box(
        "Trace · Run / Node",
        {
            "run_id": details["run_id"],
            "node_id": node,
            "原事件": next((e for e in bundle.result["events"] if e["node_id"] == node), {}),
        },
    )
    artifacts = details["artifacts"]
    st.caption(f"Artifacts · {len(artifacts)} 个已声明文件")
    if artifacts:
        names = [r["path"] for r in artifacts]
        selected = st.selectbox(
            "查看 Artifact", names, key=f"artifact_{bundle.result['run_id']}_{node}"
        )
        ref = next(r for r in artifacts if r["path"] == selected)
        path = bundle.artifact(selected)
        st.caption(f"SHA-256: {ref['sha256']}")
        if path.suffix in {".json", ".csv", ".md", ".yaml", ".extxyz"}:
            with st.expander("文件预览"):
                st.code(
                    path.read_text()[:8000], language="json" if path.suffix == ".json" else None
                )
        st.download_button(
            "下载此 Artifact", path.read_bytes(), file_name=path.name, key=f"download_{node}"
        )


def overview(bundle, step):
    main, detail = st.columns([2.1, 1], gap="large")
    with main:
        st.subheader("Three pipelines. One traceable research loop.")
        selection = st.plotly_chart(
            charts.workflow(bundle, step),
            key=f"dag_{bundle.result['run_id']}_{step}",
            on_select="rerun",
            selection_mode="points",
            width="stretch",
            config={"displayModeBar": False},
        )
        points = selection.selection.points
        if points:
            picked = points[-1].get("customdata")
            signature = (bundle.result["run_id"], step, picked)
            if picked in AGENTS and st.session_state.get("canvas_seen") != signature:
                st.session_state["inspect_node"] = picked
                st.session_state["canvas_seen"] = signature
        st.caption(
            "点击 Agent 节点查看详情。边框：绿色完成 · 琥珀待审 · 红色阻塞/失败 · 白色当前回放。"
        )
        rows = node_rows(bundle, step)
        table(
            [{k: v for k, v in r.items() if k not in {"display_status", "node_id"}} for r in rows]
        )
        if step is not None:
            st.markdown(f"### {STEPS[step][0]}")
            if step == 0 and "knowledge" in bundle.result["outputs"]:
                st.write(bundle.result["outputs"]["knowledge"]["hypothesis"])
            elif step == 1:
                show_structure(bundle, "story")
            elif step == 2:
                model_cards(bundle)
                decision_view(bundle)
            elif step == 3:
                planning(bundle, compact=True)
            elif step == 4:
                analysis_view(bundle, compact=True)
            elif step == 5:
                feedback_view(bundle)
        else:
            st.markdown(
                "**数据传递** · KnowledgeTask → ScreeningResult → CandidateLink / Analysis → ExperimentalFeedback"
            )
            st.write(
                bundle.result["outputs"].get("knowledge", {}).get("hypothesis", "知识节点尚未完成")
            )
    with detail:
        with st.container(border=True):
            render_inspector(bundle)


def role(node):
    from .view import NODE_KIND, NODE_PIPELINE

    st.caption(f"Agent Role · {AGENTS[node]} / {NODE_PIPELINE[node]} / {NODE_KIND[node]}")


def pipeline1(bundle):
    if "knowledge" not in bundle.result["outputs"]:
        st.warning("知识节点尚未完成。")
        return
    task = bundle.result["outputs"]["knowledge"]
    role("knowledge")
    tabs = st.tabs(["Knowledge Agent", "Mechanism & Hypothesis", "Research Task Planning"])
    with tabs[0]:
        st.subheader("文献证据与知识图谱")
        st.caption(
            "REPLAY · 历史 P1 快照；不是现场文献检索。原 confidence 未记录，不能用 priority 代替概率。"
        )
        corpus = bundle.json("knowledge", "p1_snapshot/01_corpus.json")
        table(
            [
                {k: p.get(k) for k in ["paper_id", "title", "doi", "year", "review_status"]}
                for p in corpus
                if p.get("paper_id") in task["evidence_refs"]
            ]
        )
        graph = bundle.json("knowledge", "p1_snapshot/07_knowledge_graph.json")
        st.plotly_chart(charts.knowledge_graph(graph, task["evidence_refs"]), width="stretch")
        st.caption("仅展示证据文献的一跳子图，最多 60 个节点；原始完整图可在下方展开。")
        json_box("知识图谱 · 原始节点与关系", graph)
        json_box("Input / Tools / Execution / Provenance", inspector(bundle, "knowledge"))
    with tabs[1]:
        st.subheader(task["hypothesis_id"])
        st.write(task["hypothesis"])
        for limitation in task["scientific_constraints"]:
            st.markdown(f"- {limitation}")
        st.caption("操作性恢复观察不等于证明 furoate 的表面因果机制。")
        table(
            [
                {k: r.get(k) for k in ["element", "mechanism_bucket", "role", "next_action_status"]}
                for r in bundle.json("knowledge", "p1_snapshot/04b_element_recommendations.json")
            ]
        )
    with tabs[2]:
        st.subheader("Select Hypothesis → Generate Task → Send to Pipeline 2")
        st.selectbox("可运行历史假设", [task["hypothesis_id"]])
        st.write("当前执行器支持已有 Ag 基线主任务；下表其他任务作为实验规划来源。")
        table(
            [
                {
                    k: t.get(k)
                    for k in [
                        "task_id",
                        "experiment",
                        "priority",
                        "linked_hypothesis",
                        "formal_recommendation",
                    ]
                }
                for t in historical_tasks(bundle)
            ]
        )
        json_box("KnowledgeTask 预览", task, expanded=True)
        st.text_input("新任务 Run ID", value="v11-p1-to-p2", key="p1_new_id")
        st.button("Generate Task → Send to Pipeline 2", type="primary", on_click=launch_hypothesis)
        if st.session_state.get("p1_launched") == bundle.result["run_id"]:
            st.success(
                f"已通过原合同执行新的 SYNTHETIC Run：{bundle.result['run_id']}。选择 Overview 查看。"
            )


def show_structure(bundle, key):
    candidates = bundle.result["outputs"].get("screening", {}).get("results", [])
    if not candidates:
        st.warning("没有已执行结构与模型输出。")
        return
    selected = st.selectbox(
        "候选结构",
        [r["candidate_id"] for r in candidates],
        key=f"candidate_{key}_{bundle.result['run_id']}",
    )
    candidate = next(r for r in candidates if r["candidate_id"] == selected)
    fig, geometry = charts.structure(bundle, candidate)
    st.plotly_chart(fig, width="stretch", key=f"structure_{key}")
    st.caption(f"Structure ID: {candidate['structure_id']} · 固定原子 {geometry['fixed_indices']}")
    json_box("Input · 结构任务与原子坐标", {"candidate": candidate, "geometry": geometry})


def model_cards(bundle):
    st.caption("Tools / Models · 保留原 ModelAdapter 边界；本轮不安装模型、不下载权重。")
    table(
        [
            {
                "模型": "MockModelAdapter",
                "能力状态": "MOCK",
                "接入状态": "ACTIVE" if "screening" in bundle.result["outputs"] else "未执行",
                "用途": "软件流程验证；解析测试势",
                "输入": "ASE 原子坐标",
                "输出": "能量 / 力 / 演示描述符",
                "结果来源": "SYNTHETIC" if "screening" in bundle.result["outputs"] else "无结果",
            },
            *[
                {
                    "模型": name,
                    "能力状态": "PLANNED",
                    "接入状态": "NOT_CONFIGURED",
                    "用途": "未来原子级评估 / 预弛豫",
                    "输入": "原子坐标与已确认参考态",
                    "输出": "预期能量 / 力",
                    "结果来源": "无真实推理结果",
                }
                for name in ["UMA", "MACE-MH-1", "DPA4"]
            ],
        ]
    )


def decision_view(bundle):
    decision = bundle.result["outputs"].get("decision")
    if not decision:
        st.warning("预算决策尚未执行。")
        return
    cols = st.columns(3)
    cols[0].metric("不确定性", decision["uncertainty"])
    cols[1].metric("提议预算 / jobs", decision["configured_proposal_budget_jobs"])
    cols[2].metric("真实 DFT", decision["actual_dft_jobs"])
    st.write(f"分歧阈值 {decision['threshold_eV']} eV；已记录 spread：{decision['spread_eV']}")
    if decision["comparison_fixture"]:
        st.warning("SYNTHETIC policy fixture · 此分歧不是 UMA/MACE/DPA4 的模型输出。")
        table(decision["comparison_fixture"])
    table(decision["dft_proposals"])
    st.caption(
        "High Uncertainty → DFT Proposed → Pending Gate / Blocked。额度只允许提出建议，真实提交始终禁用。"
    )
    json_box("Decision & Evidence · 原始规则结果", decision)


def ranking(bundle):
    st.warning("Demo Ranking — Not Validated Catalytic Activity")
    rows = sorted(
        bundle.result["outputs"].get("screening", {}).get("results", []),
        key=lambda r: r["predicted_properties"][0]["value"],
    )
    table(
        [
            {
                "展示顺序": i + 1,
                "候选": r["candidate_id"],
                "组成": str(r["composition"]),
                "mock 吸附能 / eV": r["predicted_properties"][0]["value"],
                "OOD": r["ood_status"],
                "DFT": r["dft_verification_status"],
                "门控": r["gate_status"],
            }
            for i, r in enumerate(rows)
        ]
    )
    st.caption(
        "中性 FF 吸附能不能直接解释为催化活性或 furoate 生成性能；Ag–Cu 是显式探索分支，未获正式放行。"
    )


def pipeline2(bundle):
    role("screening")
    tabs = st.tabs(
        [
            "Structure / Screening",
            "Model Evaluation",
            "Uncertainty & Budget",
            "DFT Planning",
            "Candidate Ranking",
        ]
    )
    with tabs[0]:
        st.caption("Input · P1 Ag 基线任务 / Cu 为显式探索案例。Tools · ASE / 原生 P2 runner。")
        show_structure(bundle, "workspace")
        json_box("Execution / Output / Artifacts", inspector(bundle, "screening"))
    with tabs[1]:
        model_cards(bundle)
        st.markdown("**Output · 已执行的 Mock 描述符**")
        table(
            [
                {
                    "候选": r["candidate_id"],
                    "模型": r["model_name"],
                    "吸附能 / eV": r["predicted_properties"][0]["value"],
                    "来源": r["provenance"]["origin"],
                    "不确定性": r["uncertainty"],
                }
                for r in bundle.result["outputs"].get("screening", {}).get("results", [])
            ]
        )
        json_box(
            "原生 Adapter 初始化记录",
            bundle.result["outputs"].get("screening", {}).get("models", {}),
        )
    with tabs[2]:
        role("decision")
        decision_view(bundle)
        json_box("Execution / Artifacts", inspector(bundle, "decision"))
    with tabs[3]:
        st.subheader("DFT proposals · scientific gate first")
        decision_view(bundle)
        screening = bundle.result["outputs"].get("screening", {})
        json_box("结构 / 输入配置 / 原生门控", screening.get("native_runs", []))
        for native in screening.get("native_runs", []):
            json_box(
                f"{native['element']} · 已保存的 P2 输入配置",
                bundle.json("screening", f"config_{native['element']}.json"),
            )
        st.write("资源需求：未记录正式资源估算；实际作业 0，core-hours 0。")
        st.error("SUBMISSION DISABLED · PENDING_GATE · HUMAN REVIEW REQUIRED")
    with tabs[4]:
        ranking(bundle)


def planning(bundle, compact=False):
    st.subheader("Experiment Planning · 功能子模块")
    st.caption(
        "Input · P1 机制假设 + P2 候选 / 未校准描述符 + P3 已有证据。四类任务来自历史 P1，不代表四次新实验调用。"
    )
    cards = experiment_cards(bundle)
    labels = [c["推荐实验"] for c in cards]
    selected = st.selectbox("实验任务卡", labels, key="plan_story" if compact else "plan_workspace")
    card = next(c for c in cards if c["推荐实验"] == selected)
    with st.container(border=True):
        st.markdown(f"#### {card['推荐实验']}")
        st.caption(card["Experiment ID"])
        for label in [
            "研究问题",
            "实验目的",
            "控制变量",
            "预期观察",
            "替代解释",
            "优先级",
            "所需数据",
            "当前状态",
        ]:
            st.write(f"**{label}** · {card[label]}")
        json_box(
            "Plan / Protocol · 实际保存的上下文",
            {k: card[k] for k in ["当前 P3 Plan ID", "P3 Plan 关系", "Protocol"]},
        )
    if not compact:
        st.markdown("**RuleBasedPlanner · 已保存的下一轮实验草案**")
        for recommendation in (
            bundle.result["outputs"]
            .get("experiment", {})
            .get("analysis", {})
            .get("payload", {})
            .get("recommendations", [])
        ):
            json_box(recommendation["title"], recommendation)


def samples_view(bundle):
    st.subheader("Planned → Prepared → Measured → Analyzed → Reviewed")
    st.caption(
        "展示里程碑由记录存在性推导。Prepared 表示样品已建档；来源为 SYNTHETIC 时不表示完成真实制备。没有样品级 ReviewEvent 不勾选 Reviewed。"
    )
    table(lifecycle(bundle))
    records = bundle.result["outputs"]["experiment"]["records"]
    selected = st.selectbox(
        "样品 / 原始记录类型",
        [
            "Sample",
            "SampleState",
            "TreatmentEvent",
            "Measurement",
            "RawArtifact",
            "CandidateLink",
            "Protocol",
        ],
    )
    matching = [r for r in records if r["kind"] == selected]
    table(
        [
            {
                k: r[k]
                for k in [
                    "id",
                    "kind",
                    "source_type",
                    "execution_status",
                    "data_status",
                    "created_at",
                ]
            }
            for r in matching
        ]
    )
    json_box("制备 / 测量条件、原始文件哈希与来源", matching)
    st.caption(
        "CandidateLink 保留 IDEAL_STRUCTURE_ONLY。没有样品级映射证据时，不建立结构与真实样品的一一对应。"
    )


def analysis_view(bundle, compact=False):
    exp = bundle.result["outputs"].get("experiment")
    if not exp:
        st.warning("实验分析没有执行，无替代数据。")
        return
    payload = exp["analysis"]["payload"]
    samples = lifecycle(bundle)
    if not samples:
        st.warning("没有样品记录。")
        return
    selected = st.selectbox(
        "分析样品",
        [s["Sample ID"] for s in samples],
        format_func=lambda sid: next(s["样品"] for s in samples if s["Sample ID"] == sid),
        key="analysis_story" if compact else "analysis_workspace",
    )
    left, right = st.columns([1.7, 1])
    with left:
        st.plotly_chart(
            charts.ca(bundle, selected),
            width="stretch",
            key="ca_story" if compact else "ca_workspace",
        )
        st.plotly_chart(
            charts.hplc(bundle, selected),
            width="stretch",
            key="hplc_story" if compact else "hplc_workspace",
        )
        st.caption(
            "曲线与 μmol 数值直接取自 P3 保存的分析结果。当前没有原始色谱；XPS 只记录文件与元数据，没有定量谱图分析。"
        )
    with right:
        st.markdown("#### Analysis & QC")
        st.metric("数据状态", exp["analysis"]["data_status"])
        st.write("独立样品要求：", payload["evidence"]["min_independent"])
        st.write("组内样品数：", payload["evidence"]["group_counts"])
        st.write("协议：", payload["evidence"]["criterion"])
        for name in [
            "insufficient_groups",
            "missing_product_groups",
            "invalid_measurements",
            "qc_issues",
        ]:
            st.write(name, payload["evidence"][name])
        table(
            [
                {
                    "方法": r["kind"],
                    "状态": r["status"],
                    "问题": str(r["issues"]),
                    "测量": r["measurement_id"],
                }
                for r in analysis_results(bundle)
                if r["sample_id"] == selected
            ]
        )
    if not compact:
        json_box("Execution · 原生分析工具事件", payload.get("tool_events", []))
        json_box(
            "Output / Decision / 输入快照",
            {k: payload[k] for k in ["input_snapshot", "interpretation", "evidence", "parameters"]},
        )


def evidence_view(bundle):
    exp = bundle.result["outputs"].get("experiment")
    if not exp:
        st.warning("没有可评估的实验证据。")
        return
    evidence = exp["analysis"]["payload"]["evidence"]
    feedback = bundle.result["outputs"].get("feedback", {}).get("feedback", {})
    cols = st.columns(3)
    cols[0].metric("操作性假设", evidence["hypothesis_outcome"])
    cols[1].metric("机制结论", evidence["mechanism_outcome"])
    cols[2].metric("正式审核", "WAITING_FOR_REVIEW")
    table(
        [
            {"证据层": "P1 科学假设", "记录": bundle.result["outputs"]["knowledge"]["hypothesis"]},
            {"证据层": "P3 操作性问题", "记录": exp["task"]["payload"]["hypothesis"]},
            {"证据层": "P2 描述符", "记录": "中性 FF 吸附能 / eV / 解析 mock / 未校准"},
            {"证据层": "P3 观察", "记录": exp["analysis"]["payload"]["interpretation"]},
            {"证据层": "可比性", "记录": str(feedback.get("prediction_deviation", {}))},
        ]
    )
    st.warning(
        "吸附能与电流不是同一物理量。numeric_delta 保持 null；操作性 SUPPORTED 不能升级为因果机制已证实。"
    )
    json_box("反证、恢复比例与缺失证据", evidence)
    if evidence["hypothesis_outcome"] == "REFUTED":
        st.error("当前协议中的可恢复性预期被反证；保留 REFUTED 和替代解释。")
    if evidence["p2_mismatch"]:
        st.warning("MISMATCH 为显式合成趋势审核案例，要求核对条件及模型适用域。")


def feedback_view(bundle):
    output = bundle.result["outputs"].get("feedback")
    if not output:
        st.warning("反馈节点未执行；没有可发布或暂存的结果。")
        return
    st.subheader("Experimental Finding → Evidence Review → Feedback Proposal → Next Research Task")
    feedback = output["feedback"]
    st.write(feedback["conclusion"])
    table(
        [
            {
                "目标 Pipeline": target,
                **{k: r[k] for k in ["status", "official_publish", "blocked_from_formal_feedback"]},
            }
            for target, r in output["receipts"].items()
        ]
    )
    st.caption(
        f"Hypothesis {feedback['hypothesis_id']} · Candidate {feedback['candidate_id']} · {feedback['review_status']} · {feedback['gate_status']}"
    )
    drafts = next_task_drafts(bundle)
    selected = st.selectbox(
        "下一轮研究任务草案",
        range(len(drafts)),
        format_func=lambda i: drafts[i]["source_recommendation"].get("title", "待审任务"),
        key=f"draft_{bundle.result['run_id']}",
    )
    draft = drafts[selected]
    json_box("任务目的、目标 Pipeline、证据与审核状态", draft, expanded=True)
    st.write("DFT：仅已有 decision 节点中的提议有效；此页面不新增作业。")
    if st.button("保存待审任务草案", key=f"save_{bundle.result['run_id']}"):
        path = save_for_session(session_context(st.session_state), bundle, draft)
        st.success("草案已保存到当前访客临时空间。")
    from public_runtime import saved_draft
    saved = saved_draft(session_context(st.session_state), bundle, draft)
    if saved.exists():
        st.download_button("下载待审任务草案", saved.read_bytes(), file_name=saved.name, key=f"download_{draft['draft_id']}")
    st.warning("STAGED / DRAFT 不触发正式反馈、人工签署或科学放行。")
    json_box("Artifacts · 原始反馈合同", feedback)


def pipeline3(bundle):
    if "experiment" not in bundle.result["outputs"]:
        st.warning("实验节点尚未执行，未生成合成回退结果。")
        return
    role("experiment")
    tabs = st.tabs(
        [
            "Experiment Planning",
            "Sample & Data",
            "Analysis & QC",
            "Evidence Evaluation",
            "Evidence Feedback",
        ]
    )
    with tabs[0]:
        planning(bundle)
    with tabs[1]:
        samples_view(bundle)
    with tabs[2]:
        analysis_view(bundle)
    with tabs[3]:
        evidence_view(bundle)
    with tabs[4]:
        role("feedback")
        feedback_view(bundle)


def trace_view(bundle):
    table(
        [
            {
                k: e.get(k)
                for k in ["node_id", "agent", "tool", "status", "elapsed_s", "reason", "error"]
            }
            for e in bundle.result["events"]
        ]
    )
    render_inspector(bundle)


def results_view(bundle):
    ranking(bundle)
    st.metric(
        "原始 Python 工具总耗时 / s",
        f"{sum(e.get('elapsed_s', 0) for e in bundle.result['events']):.4f}",
    )
    st.caption("本机软件与 I/O 耗时；缺少真实 DFT 对照，节省率未计算。")
    feedback_view(bundle)
    try:
        report = bundle.artifact("report.md")
    except ValueError:
        return
    st.download_button(
        "下载原运行报告", report.read_bytes(), file_name=f"{bundle.result['run_id']}-report.md"
    )


def main():
    st.set_page_config(page_title="Ag-X Prediction V1.1", page_icon="⚛", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)
    try:
        bundle = sidebar()
        identity = (str(bundle.root), bundle.result["run_id"])
        if st.session_state.get("context_id") != identity:
            st.session_state["context_id"] = identity
            st.session_state["story_active"] = False
            st.session_state["story_step"] = 0
            st.session_state["inspect_node"] = "knowledge"
            st.session_state.pop("canvas_seen", None)
        st.markdown(
            '<div class="agx-eyebrow">AGENT-DRIVEN MATERIALS DISCOVERY PLATFORM · V1.1</div><div class="agx-title">Ag-X Prediction</div><div class="agx-subtitle">Knowledge Discovery → Atomistic Screening → Experimental Validation</div>',
            unsafe_allow_html=True,
        )
        result = bundle.result
        source = "REPLAY" if (bundle.root / "manifest.json").exists() else "EXECUTION ATTEMPT"
        st.markdown(
            f'<div class="agx-banner">Run <b>{html.escape(result["run_id"])}</b>　·　{source} / 原来源 {html.escape(result["mode"])}　·　Scenario {html.escape(bundle.request["scenario"])}　·　<b>PENDING_GATE</b></div>',
            unsafe_allow_html=True,
        )
        st.caption(
            "P1 历史知识快照 · P2 解析 mock 测试势 / 未配置真实模型 · P3 SYNTHETIC 输入（仅已执行时）· V1.0 文件保持原样"
        )
        cols = st.columns(4)
        cols[0].metric("软件流程", result.get("software_workflow_status", result["status"]))
        cols[1].metric("候选结构", len(result["outputs"].get("screening", {}).get("results", [])))
        cols[2].metric("真实 DFT 作业", result["actual_dft_jobs"])
        cols[3].metric(
            "科学反馈", "WAITING_FOR_REVIEW" if "feedback" in result["outputs"] else "未生成"
        )
        if result["status"] in {"FAILED", "BLOCKED"}:
            error = next(e for e in result["events"] if e["status"] in {"FAILED", "BLOCKED"})
            st.error(error.get("error") or error["reason"])
        step = story_controls(bundle)
        page = st.session_state["nav"]
        if page == "Workflow Overview":
            overview(bundle, step)
        elif page == "Pipeline 1":
            pipeline1(bundle)
        elif page == "Pipeline 2":
            pipeline2(bundle)
        elif page == "Pipeline 3":
            pipeline3(bundle)
        elif page == "Agent Trace":
            trace_view(bundle)
        else:
            results_view(bundle)
        st.caption(f"Verified source · {bundle.result['run_id']} · PUBLIC DERIVATIVE")
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        st.session_state.pop("active_bundle", None)
        st.error(f"无法展示可验证数据：{exc}")
        st.stop()
