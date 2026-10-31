# 部署验收记录 · 2026-10-31

应用：https://agx-prediction-v1-1.streamlit.app/

仓库：https://github.com/TristanClay/agx-prediction-demo

首次部署代码：af6b7a46b8e4a144c9304d67428c65ad1e37fcff；Python 3.12。

## 已完成

- GitHub Linux CI 首次运行成功：https://github.com/TristanClay/agx-prediction-demo/actions/runs/41181696066
- 本地部署包与干净克隆均为 8 项测试通过。覆盖五个场景完整性和预期结论、六页 AppTest、Story 步进、两个会话同名运行隔离、草案隔离、5 次上限、执行互斥、LIVE 阻塞。
- 五种公开场景的 mock 吸附能与原版逐值一致；实验证据去除重新生成的 Sample / Measurement ID 后一致。原版及本机材料保留；本地核对 3,174 个原文件，全部未变更。
- 公开发布包扫描未发现本机用户路径；未发布论文全文、摘要或原始 PDF。public-v11 场景是重新执行的公开派生包。
- 公网浏览器验证：Story Start / Step 2、Agent Trace、P1 知识页、P2 结构与 DFT 门控页、P3 规划页、结果页、counter 场景切换。
- 公网实际执行 judge-online-check 合成工作流，五节点完成，反馈 WAITING_FOR_REVIEW，真实 DFT 0。
- 公网保存草案成功并出现下载入口；报告已实际下载并读取，来源 SYNTHETIC、真实 DFT 0。

## 实际限制与待补检查

- 当前自动化浏览器实际可用页面宽度为 682 px。已检查导航折叠/展开及主要操作；要求 390 px 或 1440 px 的视口设置未改变实际宽度，因此精确手机/桌面断点验收未确认。
- 部署初次加载时控制台出现动态 JS 模块加载失败及 WebSocket 断开。重新加载后，Story、导航、新运行和下载通过。未将此记录写为“全程无控制台错误”。完整云端日志审阅尚待补充。
- 草案下载入口已确认；草案 JSON 文件的浏览器下载落盘尚未单独确认。
- 免费服务可能休眠，首次访问请按页面提示唤醒。临时运行和草案不承诺跨会话、重启持久化。

## 更新与回滚

应用跟随 main。功能修改先在 codex/ 分支通过 CI，再进入 main。回滚时使用 git revert 恢复上一验收提交，等待自动重新部署。代码审查与哈希验证均不等于科研验收。
