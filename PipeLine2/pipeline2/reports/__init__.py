"""Source-labelled reports. This module makes no catalyst or model-performance claims."""

import csv
import io
import json
import platform
from importlib.metadata import version
from pathlib import Path

from pipeline2 import __version__
from pipeline2.registry import canonical_json, sha256_file, utc_now
from pipeline2.safety import write_new


def software_hashes() -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    return {str(p): sha256_file(p) for p in sorted(root.rglob("*.py"))}


def write_json(path: str | Path, value) -> Path:
    return write_new(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def write_csv(path: str | Path, rows: list[dict]) -> Path:
    fields = list(dict.fromkeys(k for row in rows for k in row)) or [
        "structure_id",
        "source_type",
        "prediction_status",
    ]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {k: canonical_json(v) if isinstance(v, (dict, list)) else v for k, v in row.items()}
        )
    return write_new(path, buffer.getvalue())


def finish_report(
    directory: Path,
    *,
    command: str,
    status: str,
    gate: dict,
    predictions: list[dict],
    screening: dict,
    source_hashes: dict,
    config_payload: dict | None = None,
    error: str | None = None,
    details: dict | None = None,
) -> dict:
    write_csv(directory / "predictions.csv", predictions)
    rows = screening.get("top_k", [])
    lines = [
        "# Ag-X Pipeline 2 离线 MVP",
        "",
        "**数据声明：本运行使用 mock / fixture 演示数据；不是实测 DFT，也不是已验证的材料预测。**",
        "",
        f"- 命令：`{command}`",
        f"- 运行状态：`{status}`",
        f"- 科学门控：`{gate.get('status', 'PENDING_GATE')}`",
        "- 模型质量：`UNCALIBRATED`；结果用途：`EXPLORATORY`",
        "- 实际 DFT 作业：0；实际 DFT 核时：0；未执行 VASP/PBS。",
        "",
        "## 门控与限制",
        "",
    ]
    lines += [f"- `{b}`" for b in gate.get("blockers", [])] or [
        "- 正式来源仅允许所列操作；真实提交后端仍未实现。"
    ]
    if error:
        lines += ["", f"错误：`{error}`"]
    lines += [
        "",
        "## 演示筛选",
        "",
        "排序由配置中的目标方向决定，吸附能不能直接解释为电催化活性或稳定性。",
        "",
        "| 结构 | 性质（eV） | Pareto 层 | 来源 |",
        "|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| `{row['candidate_id']}` | {canonical_json(row.get('properties', {}))} | {row.get('pareto_rank')} | {row.get('source_type')} |"
        )
    if not rows:
        lines.append("| 无可排序候选 | 未产生合格性质或仅执行软件检查 | — | fixture/mock |")
    lines += [
        "",
        "## 验证建议与追溯",
        "",
        "先完成 Pipeline 1 Ag 基线正式验收，再按原有门控评估 Ag–Cu 对照。",
        "DFT 提议、预算估计和采样记录见 JSON 附件。预算估计未经真实作业标定。",
        "未报告模型准确率、校准置信概率或实际计算节约比例。",
        "fixture benchmark 只检查软件运算；真实 MLIP、校准、训练和正式盲测尚未实现。",
        "原始输入、模型与结构哈希见 run_manifest.json 和 registry.sqlite。",
        "",
    ]
    write_new(directory / "report.md", "\n".join(lines))
    unchanged = all(Path(p).is_file() and sha256_file(p) == h for p, h in source_hashes.items())
    if not unchanged:
        raise RuntimeError("Input changed during run; outputs must not be admitted")
    manifest = {
        "schema_version": "pipeline2-run-1",
        "created_at": utc_now(),
        "command": command,
        "software": {
            "pipeline2_version": __version__,
            "python": platform.python_version(),
            "dependencies": {
                name: version(name) for name in ["ase", "numpy", "pydantic", "PyYAML"]
            },
        },
        "status": status,
        "gate": gate,
        "source_types": sorted({p.get("source_type", "fixture") for p in predictions})
        or ["fixture"],
        "calibration_status": "UNCALIBRATED",
        "input_hashes": {("repo/" + str(Path(k).relative_to(Path(__file__).resolve().parents[3])) if Path(k).is_relative_to(Path(__file__).resolve().parents[3]) else "runtime/" + Path(k).name): v for k, v in source_hashes.items()},
        "inputs_unchanged": unchanged,
        "config": config_payload,
        "actual_dft_jobs": 0,
        "actual_dft_core_hours": 0.0,
        "details": details or {},
        "error": error,
        "artifacts": {
            str(p.relative_to(directory)): sha256_file(p)
            for p in sorted(directory.rglob("*"))
            if p.is_file()
        },
    }
    # A manifest does not include its own hash (which would be self-referential).
    write_json(directory / "run_manifest.json", manifest)
    return manifest
