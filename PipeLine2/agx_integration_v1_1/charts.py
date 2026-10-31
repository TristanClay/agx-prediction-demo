"""Plotly views over saved coordinates and measurements only."""

import math
from pathlib import Path

import plotly.graph_objects as go

from .view import PIPELINES, analysis_results, node_rows

POSITIONS = {
    "knowledge": (0.5, 1.45),
    "screening": (1.55, 1.65),
    "decision": (1.55, 0.8),
    "experiment": (2.6, 1.65),
    "feedback": (2.6, 0.8),
}
LABELS = {
    "knowledge": "Knowledge<br>Agent",
    "screening": "Atomistic<br>Screening",
    "decision": "Uncertainty<br>& Budget",
    "experiment": "Experimental<br>Evidence",
    "feedback": "Evidence<br>Feedback",
}
STATUS_COLORS = {
    "PENDING": "#48566d",
    "CURRENT": "#ffffff",
    "RUNNING": "#ffffff",
    "COMPLETED": "#43caa3",
    "FAILED": "#ff707f",
    "BLOCKED": "#ff707f",
    "WAITING_FOR_REVIEW": "#efbe67",
    "SKIPPED": "#6e7182",
}


def theme(fig, height=420):
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#101a2b",
        plot_bgcolor="#101a2b",
        height=height,
        font=dict(family="Arial, sans-serif", color="#dce7f6"),
        margin=dict(l=22, r=22, t=40, b=25),
    )
    return fig


def workflow(bundle, step=None):
    fig = go.Figure()
    rows = node_rows(bundle, step)
    active_pipeline = next((r["Pipeline"] for r in rows if r["display_status"] == "CURRENT"), None)
    for i, (pipeline, (title, color)) in enumerate(PIPELINES.items()):
        left = i * 1.05 + 0.02
        rgb = tuple(int(color[j : j + 2], 16) for j in (1, 3, 5))
        fig.add_shape(
            type="rect",
            x0=left,
            x1=left + 0.96,
            y0=0.3,
            y1=2.35,
            line=dict(
                color=color if pipeline == active_pipeline else "#2a3850",
                width=2.5 if pipeline == active_pipeline else 1,
            ),
            fillcolor=f"rgba({rgb[0]},{rgb[1]},{rgb[2]},0.09)",
            layer="below",
        )
        fig.add_annotation(
            x=left + 0.48,
            y=2.12,
            text=f"<b>{pipeline}</b> · {title.split()[0]}",
            showarrow=False,
            font=dict(color=color, size=14),
        )
    edges = [
        ("knowledge", "screening", "KnowledgeTask"),
        ("screening", "decision", "ScreeningResult"),
        ("screening", "experiment", "CandidateLink"),
        ("experiment", "feedback", "Analysis / QC"),
    ]
    for source, target, contract in edges:
        x0, y0 = POSITIONS[source]
        x1, y1 = POSITIONS[target]
        fig.add_annotation(
            x=x1,
            y=y1,
            ax=x0,
            ay=y0,
            xref="x",
            yref="y",
            axref="x",
            ayref="y",
            text="",
            showarrow=True,
            arrowhead=2,
            arrowsize=1.2,
            arrowwidth=1.5,
            arrowcolor="#647e9f",
            standoff=34,
            startstandoff=34,
        )
        fig.add_annotation(
            x=(x0 + x1) / 2,
            y=(y0 + y1) / 2 + 0.11,
            text=contract,
            showarrow=False,
            font=dict(size=10, color="#9ab0ce"),
        )
    fig.add_trace(
        go.Scatter(
            x=[2.6, 2.6, 0.5, 0.5],
            y=[0.58, 0.43, 0.43, 1.18],
            mode="lines",
            line=dict(color="#efbe67", dash="dot", width=1.5),
            hoverinfo="skip",
            showlegend=False,
        )
    )
    fig.add_annotation(
        x=0.5,
        y=1.18,
        ax=0.5,
        ay=0.95,
        xref="x",
        yref="y",
        axref="x",
        ayref="y",
        text="",
        showarrow=True,
        arrowhead=2,
        arrowcolor="#efbe67",
    )
    fig.add_annotation(
        x=1.5,
        y=0.43,
        text="STAGED feedback → 人工审核 → 下一轮草案",
        bgcolor="#101a2b",
        showarrow=False,
        font=dict(size=11, color="#efbe67"),
    )
    fig.add_trace(
        go.Scatter(
            x=[POSITIONS[r["node_id"]][0] for r in rows],
            y=[POSITIONS[r["node_id"]][1] for r in rows],
            mode="markers+text",
            text=[LABELS[r["node_id"]] for r in rows],
            textposition="middle center",
            textfont=dict(size=12, color="#eff6ff"),
            customdata=[r["node_id"] for r in rows],
            hovertext=[
                f"{r['Agent']}<br>{r['实现类型']}<br>原始状态: {r['原始执行状态']}<br>回放: {r['回放进度']}"
                for r in rows
            ],
            hovertemplate="%{hovertext}<extra></extra>",
            marker=dict(
                symbol="square",
                size=[88 if r["display_status"] == "CURRENT" else 79 for r in rows],
                color=[PIPELINES[r["Pipeline"]][1] for r in rows],
                opacity=0.9,
                line=dict(
                    width=3, color=[STATUS_COLORS.get(r["display_status"], "#48566d") for r in rows]
                ),
            ),
            selected=dict(marker=dict(opacity=1)),
            unselected=dict(marker=dict(opacity=0.9)),
            showlegend=False,
        )
    )
    theme(fig, 425)
    fig.update_layout(
        clickmode="event+select",
        dragmode=False,
        uirevision=bundle.result["run_id"],
        xaxis=dict(range=[-0.03, 3.12], visible=False, fixedrange=True),
        yaxis=dict(range=[0.22, 2.4], visible=False, fixedrange=True),
    )
    return fig


def structure(bundle, candidate):
    from ase.data import chemical_symbols

    geometry = bundle.json(
        "screening", str(Path(candidate["structure_artifact"]["path"]).with_suffix(".json"))
    )["geometry"]
    symbols = [chemical_symbols[z] for z in geometry["numbers"]]
    fig = go.Figure()
    colors = {"Ag": "#b9c8db", "Cu": "#efaa6b", "C": "#71a7cf", "O": "#f1787e", "H": "#f5f6fb"}
    for symbol in dict.fromkeys(symbols):
        points = [xyz for xyz, s in zip(geometry["positions_A"], symbols) if s == symbol]
        fig.add_trace(
            go.Scatter3d(
                x=[p[0] for p in points],
                y=[p[1] for p in points],
                z=[p[2] for p in points],
                mode="markers",
                name=symbol,
                marker=dict(size=5, color=colors.get(symbol, "#888888")),
            )
        )
    theme(fig, 400)
    fig.update_layout(
        title="ASE 实际初始坐标 · SYNTHETIC",
        scene=dict(
            aspectmode="data", xaxis_title="x / Å", yaxis_title="y / Å", zaxis_title="z / Å"
        ),
    )
    return fig, geometry


def ca(bundle, sample_id):
    fig = go.Figure()
    for result in analysis_results(bundle, "CA"):
        if result["sample_id"] != sample_id or "time_s" not in result.get("metrics", {}):
            continue
        data = result["metrics"]
        fig.add_trace(
            go.Scatter(
                x=data["time_s"],
                y=data["j_mA_cm2"],
                name=result["metadata"].get("group", result["measurement_id"]),
                mode="lines",
                customdata=[result["measurement_id"]] * len(data["time_s"]),
                hovertemplate="%{x} s · %{y:.3f} mA/cm²<br>%{customdata}<extra>%{fullData.name}</extra>",
            )
        )
    theme(fig, 320)
    fig.update_layout(
        title="CA · 已保存分析 / SYNTHETIC",
        xaxis_title="Time / s",
        yaxis_title="j / mA cm⁻²",
        legend=dict(orientation="h", y=-0.25),
    )
    return fig


def hplc(bundle, sample_id):
    fig = go.Figure()
    for result in analysis_results(bundle, "HPLC"):
        if result["sample_id"] != sample_id:
            continue
        rows = result.get("metrics", {}).get("rows", [])
        fig.add_trace(
            go.Scatter(
                x=[r["sampling_time_s"] for r in rows],
                y=[r["net_formed_umol"] for r in rows],
                name=result["metadata"].get("group", "HPLC"),
                mode="lines+markers",
            )
        )
    theme(fig, 300)
    fig.update_layout(
        title="HPLC · 经标定液相净生成量 / SYNTHETIC",
        xaxis_title="Sampling time / s",
        yaxis_title="Net formed / μmol",
        legend=dict(orientation="h", y=-0.25),
    )
    return fig


def knowledge_graph(graph, evidence_refs):
    """Show a bounded one-hop subgraph with only original nodes and edges."""
    nodes = {n["id"]: n for n in graph["nodes"]}
    seeds = set(evidence_refs) & nodes.keys()
    adjacent = {
        endpoint
        for e in graph["edges"]
        if e["source"] in seeds or e["target"] in seeds
        for endpoint in [e["source"], e["target"]]
    }
    selected = sorted(seeds) + sorted(adjacent - seeds)[: max(0, 60 - len(seeds))]
    positions = {
        name: (
            math.cos(i * 2 * math.pi / max(len(selected), 1)),
            math.sin(i * 2 * math.pi / max(len(selected), 1)),
        )
        for i, name in enumerate(selected)
    }
    fig = go.Figure()
    for edge in graph["edges"]:
        if edge["source"] not in positions or edge["target"] not in positions:
            continue
        a, b = positions[edge["source"]], positions[edge["target"]]
        fig.add_trace(
            go.Scatter(
                x=[a[0], b[0]],
                y=[a[1], b[1]],
                mode="lines",
                line=dict(color="#304c64", width=1),
                hoverinfo="skip",
                showlegend=False,
            )
        )
    colors = ["#35d5c5", "#70a7ff", "#efbe67", "#be9bff", "#ef8099"]
    for i, kind in enumerate(sorted({nodes[n].get("node_type", "Node") for n in selected})):
        group = [n for n in selected if nodes[n].get("node_type", "Node") == kind]
        fig.add_trace(
            go.Scatter(
                x=[positions[n][0] for n in group],
                y=[positions[n][1] for n in group],
                mode="markers",
                name=kind,
                marker=dict(size=13, color=colors[i % len(colors)]),
                hovertext=[
                    f"{n}<br>{nodes[n].get('title', nodes[n].get('name', kind))}" for n in group
                ],
                hovertemplate="%{hovertext}<extra></extra>",
            )
        )
    theme(fig, 340)
    fig.update_layout(
        title=f"P1 知识图谱 · 文献一跳子图 / {len(selected)} 节点",
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        showlegend=True,
    )
    return fig
