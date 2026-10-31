# Ag-X Prediction V1.1 · 评委演示

完整交互演示：六个工作区、五种场景、Story Mode、Agent Inspector、合成工作流与待审草案。

本仓库是独立公开部署包；P2、P3 保持独立模块，通过适配器连接。P1 为去除全文、摘要、引用长文本及本机路径的历史知识派生快照。五个 public-v11 场景是重新执行的 SYNTHETIC 演示，不是原始历史运行包。

## 本地运行

Python 3.12：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## 评委操作

1. 在 Workflow Overview 点击 Start Demo，用 Next Step 查看六步主线。
2. 点击流程节点或 Open Agent Details 查看实际工具、输入、输出和证据。
3. Scenario Selector 切换 baseline、disagreement、missing、mismatch、counter。
4. 浏览 Pipeline 1 / 2 / 3、Agent Trace、Results & Feedback。
5. 展开“创建 V1.1 Run”，使用新的 Run ID 执行 SYNTHETIC 工作流。每会话最多 5 个运行；同一实例串行执行。
6. 在反馈页保存、下载待审草案，在结果页下载报告。

每位访客使用独立临时空间，刷新断开会话或服务重启后数据可能清除，请及时下载。固定场景只读。免费 Streamlit 应用闲置后可能休眠；打开页面后按唤醒提示等待启动。

## 科学边界

P1 是历史知识；P2 使用解析 mock 测试势；P3 使用合成 CA/HPLC 输入和规则规划。真实模型均 NOT_CONFIGURED，LIVE 返回 BLOCKED。真实 DFT 作业为 0；反馈 STAGED / WAITING_FOR_REVIEW / PENDING_GATE，不构成科学验收或正式发布。

## Streamlit Cloud

选择本仓库 main 分支，入口 streamlit_app.py，Python 3.12，设置应用公开。无需 API key、模型权重或数据库凭据。先确认 Linux CI 通过再首次部署。后续修改先在 codex/ 分支验证再进入部署分支。回滚使用 git revert 恢复上一验收版本。

## 在线访问

- [打开评委演示](https://agx-prediction-v1-1.streamlit.app/)
- [Linux 验证记录](https://github.com/TristanClay/agx-prediction-demo/actions)
- [部署验收及限制](docs/DEPLOYMENT_ACCEPTANCE.md)
