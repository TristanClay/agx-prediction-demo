# Ag-X Pipeline 2 离线 MVP

**数据声明：本运行使用 mock / fixture 演示数据；不是实测 DFT，也不是已验证的材料预测。**

- 命令：`demo`
- 运行状态：`COMPLETED_OFFLINE`
- 科学门控：`PENDING_GATE`
- 模型质量：`UNCALIBRATED`；结果用途：`EXPLORATORY`
- 实际 DFT 作业：0；实际 DFT 核时：0；未执行 VASP/PBS。

## 门控与限制

- `formal_pipeline1_source_missing`

## 演示筛选

排序由配置中的目标方向决定，吸附能不能直接解释为电催化活性或稳定性。

| 结构 | 性质（eV） | Pareto 层 | 来源 |
|---|---|---|---|
| `S_e23271b170f5e7f795491f7c3eb8d79be6db1a08d52953a6beed687ade7ccaba` | {"adsorption_energy_eV":-0.2329955718401734} | 0 | mock |
| `S_95137c4ee3ee9ebff44b601eb1e1be53300648cdde168b36fb68a48c815726c7` | {"adsorption_energy_eV":-0.16107335390187238} | 1 | mock |

## 验证建议与追溯

先完成 Pipeline 1 Ag 基线正式验收，再按原有门控评估 Ag–Cu 对照。
DFT 提议、预算估计和采样记录见 JSON 附件。预算估计未经真实作业标定。
未报告模型准确率、校准置信概率或实际计算节约比例。
fixture benchmark 只检查软件运算；真实 MLIP、校准、训练和正式盲测尚未实现。
原始输入、模型与结构哈希见 run_manifest.json 和 registry.sqlite。
