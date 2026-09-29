# CAM Automation Studio 0.7.0 客户交付报告

日期：2026-09-29。交付范围：Windows 本地离线工艺复用与审阅工作台。客户真实 CAM 生产验收另行记录。

## 实现结果

本次按先方案、五个开发对话并行、总指挥集成的方式开发。实现与归属记录见 `implementation-plan.md`、`coordination.json`、`handoff-A.md` 至 `handoff-E.md`。现有未提交 Cimatron 开发作为基线保留并增量修复；未执行 reset、stash 或远程发布。

| 客户任务 | 实现 |
|---|---|
| 导入当前加工上下文 | 产品、版本、实例、项目、单位、材料、三轴机床、对象、可选刀具/毛坯/夹具；不可变快照与内容哈希 |
| 找到加工对象 | object_id、名称、类型、嵌套属性组合匹配；多结果报告歧义，不默认取第一项 |
| 复用已有工艺 | 本地 SQLite 案例库，按产品隔离、单位/材料/轴数/文本条件解释排序；分数不代表加工成功率 |
| 调整工艺建议 | 参数编辑后生成新的内容哈希；跨产品、单位、材料、未知动作、未绑定对象有明确拒绝或阻塞 |
| 留存验证证据 | 人工审阅、仿真、碰撞、现场批准四类记录，绑定建议与快照；上下文/参数改变不能沿用旧证据 |
| 交给工程师审阅 | 导出包含案例、快照、建议、哈希、来源、门禁和证据的 JSON 包；所有建议保持 dry-run |
| 安装、维护与迁移 | Windows 启动入口、自检、可复现 ZIP 与 SHA-256、模板、中文手册、SQLite 一致备份与恢复保护 |
| 保留旧功能 | 原插件中心、NX/PowerMill 日志解析学习、Flow Studio、审阅交换与 fixture 预览 |

## 宿主支持与验证边界

| 产品 | 本轮可交付能力 | 验证边界 |
|---|---|---|
| NX | JSON 快照导入、NXOpen 只读 body 清单导出入口、既有 Journal/Flow 功能 | SDK 实机未验证；Tag 为会话级对象标识，body 清单摘要不代表几何摘要；材料/机床按操作人输入 |
| PowerMill | JSON 快照导入、官方 PMAutomation 只读属性接口适配、既有宏/Flow 功能 | 目标版本/COM 实机未验证；实体清单需经过审阅的外部来源，不能把进程/版本/单位信息称为完整几何与 CAM 上下文 |
| Cimatron | 可安装的静态 Journal 证据解析与审阅 | 实验能力；不支持 Flow 导入、制造快照或宿主生产动作 |

本地证据登记是可追溯记录，不是独立认证。任何状态都不授权真实加工动作；系统不提供机床 NC 输出。本轮没有运行真实 CAM 仿真、碰撞测试或机床加工，测试替身不计为现场验收。

## 开源复用

复用既有 MIT 工作台及产品解析内核，使用 Python 标准库与 SQLite，前端沿用本地原生 JavaScript；本地核心运行无需云服务或模型密钥。宿主集成核对了 Autodesk `PowerShapeAndPowerMillAPI` 官方 MIT 仓库的接口，厂商 SDK 与 CAM 主程序不随包分发。详见 `delivery/THIRD_PARTY_NOTICES.md` 与 `host_bridges/README.md`。

先进 CAD/CAM 调研及后续演进路线保存在 `docs/research/project-assessment-and-evolution-2026-09-29.md`。本版交付重点是可验证的工艺复用闭环；检索与建议为确定性实现，尚无在线大模型推理、完整几何特征识别或 CAD 内核替代能力。

## 集成验收

| 验收 | 实测结果 |
|---|---|
| 独立 Python venv 根回归 | 301 项通过，65.394 秒，0 failures / errors |
| 最后集成修订专项 | 上下文、客户 API、分发、插件管理、产品集成合计 40 项通过 |
| NX / PowerMill 插件 | 分别 44 / 15 项通过 |
| Node 组件 / Flow / 来源映射 | 22 项通过 |
| 最终 ZIP 浏览器验收 | 8 项通过，0 失败；含旧 Flow 及客户完整流程、错误状态、手机布局 |
| 最终 ZIP 独立验收 | 全部文件哈希、解压自检与启动、案例/建议/证据/导出、变更失效、备份恢复全部通过 |
| Flow 性能 | 原 100ms 增量校验 p95 门槛通过；A 的 20 样本专项 p95 为 49.762ms；未提高阈值 |
| PowerMill 导出入口 | Framework v4 C# 编译与测试替身 smoke 通过；无真实实例时拒绝；不等于实机 SDK 验证 |

根回归日志：`build/customer-release/root-tests.log`；最后专项日志：`build/customer-release/final-integration-tests.log`。

分发验收可重跑：

```powershell
python scripts/build_customer_delivery.py --output dist/CAM-Automation-Studio-0.7.0-windows-local.zip
python scripts/verify_customer_delivery.py dist/CAM-Automation-Studio-0.7.0-windows-local.zip --output build/customer-release/package-acceptance.json
```

该脚本检查 ZIP 旁车及全部文件哈希，从解压目录启动实际客户入口，完成模板导入、案例检索、四类演示证据登记与导出、参数变更失效，以及停止服务后的案例库备份/恢复。其演示证据不是实际 CAM 仿真结果。

最终 ZIP：`dist/CAM-Automation-Studio-0.7.0-windows-local.zip`，556657 字节，207 个清单文件；与冻结源码逐项比较无差异。SHA-256：`8118f155dc692d2716958aa8c2c8b516c693752ef5ba7c54b1b2625ccf952051`。详细验收见同目录 `release-verification.json`，浏览器日志与截图见 `build/customer-release/browser-tests-final.log`、`build/customer-release/browser-final/`。

## 客户现场完成条件

用客户指定的软件版本、真实零件和刀具库复核导出内容与对象绑定；由工艺人员核对参数，完成 CAM 仿真和碰撞检查，记录现场批准人及证据。安装与操作步骤见 `delivery/README.zh-CN.md` 和 `docs/customer/操作手册.zh-CN.md`。
