"""Transparent experimental templates selected from computed evidence."""

from typing import Protocol


class LLMAdapter(Protocol):
    """Optional future advisory provider. Never receives numerical write authority."""

    def explain(self, evidence: dict) -> dict: ...


class BayesianOptimizer:
    status = "NOT_IMPLEMENTED"

    def suggest(self, observations, constraints):
        raise NotImplementedError("Bayesian optimization is an extension interface, not active in V1")


TEMPLATES = {
    "repair_qc": ("补齐证据", "修正分析阻塞项", "先修正单位、窗口或原始记录，再重新分析。", "当前质控失败的数据不能支撑比较。"),
    "calibration": ("补齐证据", "补充 HPLC 标定", "缺少匹配物种与方法版本的有效标曲。", "浓度、稀释与液相量需要可追溯标定。"),
    "independent": ("补齐证据", "增加独立样品重复", "技术重复不能替代独立电极；按当前协议补足每组样品。", "样品差异尚未被独立重复覆盖。"),
    "product": ("补齐证据", "补充匹配窗口的产物定量", "电流变化不能独立证明目标产物生成变化。", "缺少同一样品、条件和时间窗口下的有效产物分析。"),
    "solution_control": ("区分机制", "补充匹配溶液对照", "匹配 pH、离子强度和体积，区分 spike 的特异作用。", "抑制也可能来自溶液条件变化。"),
    "surface": ("区分机制", "补充 Post-CA 表面证据", "关联失活后与清洗后的 XPS 文件及处理记录。", "电流恢复不等于证实 furoate 表面因果机制。"),
    "p2_mismatch": ("区分机制", "复核 P2 与实验的可比性", "先核对结构、实际条件和模型适用域，再暂存 P2 计算请求。", "预测描述符与实验电流不是同一种标签。"),
    "counter": ("区分机制", "保留反证并检查替代解释", "当前有效数据不支持协议中的可恢复性预期，检查不可逆变化。", "不得将反证通过措辞改写为支持。"),
    "inconsistent": ("补齐证据", "排查独立样品不一致", "固定前处理并增加独立制备批次，保留现有差异。", "重复间差异超过协议阈值，不能用平均值掩盖。"),
    "optimize": ("优化性能", "规划单变量操作窗口实验", "在现有证据约束下比较操作电位或清洗时长，并同步定量产物。", "这是规则生成的后续实验草案，不保证收益。"),
}


class RuleBasedPlanner:
    status = "RULE_BASED_NO_LLM"

    def plan(self, task, evidence, references):
        rules = []
        if evidence["invalid_measurements"] or evidence["qc_issues"]:
            rules.append("repair_qc")
        if evidence["missing_calibration"]:
            rules.append("calibration")
        if evidence["insufficient_groups"]:
            rules.append("independent")
        if evidence["missing_product_groups"]:
            rules.append("product")
        if evidence["group_counts"].get("spike", 0) and evidence["group_counts"].get("solution_control", 0) < evidence["min_independent"]:
            rules.append("solution_control")
        if not evidence["post_ca_record_present"]:
            rules.append("surface")
        if evidence["p2_mismatch"]:
            rules.append("p2_mismatch")
        if evidence["hypothesis_outcome"] == "REFUTED":
            rules.append("counter")
        if evidence["hypothesis_outcome"] == "MIXED":
            rules.append("inconsistent")
        if evidence["hypothesis_outcome"] == "SUPPORTED" and not evidence["invalid_measurements"] and not evidence["insufficient_groups"] and not evidence["missing_product_groups"]:
            rules.append("optimize")
        cards = []
        for rule in rules:
            category, title, purpose, reason = TEMPLATES[rule]
            cards.append({
                "rule_id": rule, "category": category, "title": title,
                "purpose": purpose, "task_id": task["id"],
                "hypothesis_id": task["payload"].get("hypothesis_id"),
                "evidence_refs": references, "reason": reason,
                "variables": ["操作电位（保持标尺）", "处理状态", "采样时点"] if rule == "optimize" else ["由缺项限定的单一变量"],
                "controls": ["相同电解液、温度、几何面积与电位标尺", "匹配空白／溶液对照"],
                "replicate_plan": {"independent_samples_per_group": evidence["min_independent"],
                                   "technical_repeats_count_as_independent": False,
                                   "batch_plan": "至少记录制备批次；跨批次外推需要新增独立批次"},
                "endpoints": ["协议窗口电流密度", "经标定的产物液相量", "状态及原始文件完整性"],
                "interpretation": {"expected": "仅支持当前协议下的操作性假设，机制仍需区分性证据",
                                   "opposite": "保留反证，复查不可逆结构变化与溶液效应",
                                   "inconsistent": "增加独立样品／批次，结论保持 MIXED 或 INCONCLUSIVE"},
                "alternative_explanations": ["表面重构", "传质与气泡", "pH／离子强度变化", "取样或标定偏差"],
                "resources": "规则估计：新增独立电极至少2个；CA/HPLC 1个实验时段，表征另排期（非实测工时）",
                "method": "RuleBasedPlanner/v1", "review_status": "DRAFT", "state": "ACTIVE",
            })
        return cards
