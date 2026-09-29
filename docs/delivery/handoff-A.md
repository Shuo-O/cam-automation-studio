# 方案 A 交接

日期：2026-09-29

## 完成内容

- `cam_automation/integrations.py`
  - 修复 `_map_offline_flow_source()` 的产品分派：只有 `nx` 和
    `powermill` 进入 Flow mapper；`cimatron` 明确返回
    `FlowIntegrationError(code="CAPABILITY_UNAVAILABLE", http_status=409)`。
  - `OfflineFlowIntegration.import_source()` 对 Cimatron 同样 fail-closed；
    不创建 PowerMill asset，不注册 Cimatron Flow 图。
  - Cimatron JSONL 旧动作 `cimatron.*` 兼容读取为 canonical
    `cam.source.call`，原始动作保存在 `params.raw_action`/`params.api`，
    `mapping_confidence=opaque`，不授予执行能力。
- `cam_automation/adapters/cimatron_journal.py`
  - Python/C# 仅静态提取调用，所有事件使用 `action="cam.source.call"`；
    动态执行、外部进程、postprocess/NC/G-code 等仍为 blocked 风险。
- `cam_automation/product_catalog.py`, `cam_automation/flow_validation.py`,
  `cam_automation/codex_bridge.py`
  - Cimatron 目录元数据保留 `action_namespace="cimatron"`，但
    `PRODUCT_ACTION_NAMESPACES` 只含 NX/PowerMill，避免扩大生产动作白名单。
  - 公共 Flow validator 仍拒绝 Cimatron 图中的 `nx.*`/`powermill.*`
    节点（跨产品诊断 `FLOW_PRODUCT_MIXED`）。
  - 产品边界错误消息恢复 `NX product boundary`/
    `PowerMill product boundary` 兼容文本。
- `cam_automation/flow_validation.py`
  - 对 immutable `FrozenContract` 且使用默认资源限制的 schema/safety
    preflight 增加有界缓存（32 项）；自定义限制仍走完整校验。
- `plugins/cimatron-cam-copilot/**`
  - `app-plugin.json` 是工作台安装清单；Codex 使用的
    `.codex-plugin/plugin.json` 是独立的技能/市场清单。两者均明确
    Cimatron 只支持静态分析/审阅。
  - 完善 Codex manifest 与 5 个静态分析/审阅 skill，移除 TODO，明确
    dry-run、人工 review、simulation、collision、shop approval、NC/G-code
    限制；工作台 manifest 未声明 Flow、执行或 connection feature。
- 相关回归断言更新：`tests/test_integrations.py`,
  `tests/test_web_server.py`, `tests/test_codex_plugins.py`,
  `tests/flow_contracts/test_flow_validation.py`。

## 冻结接口与限制

- `analyze(product="cimatron", source, source_format="cimatron_journal"|"jsonl")`
  返回与现有分析相同的可序列化 dict，`adapter.execution_mode="dry-run"`、
  `source_execution=false`、输出类型 `cimatron_evidence_report`。
- Cimatron 仅交付静态 Journal 证据；不支持 Flow import、实时宿主连接、
  command tasks、语义编辑、生产动作或 NC 输出。静态证据不等于 SDK/宿主
  版本验证。

## 测试

- `python -S -m unittest tests.test_integrations tests.test_codex_bridge tests.test_codex_plugins tests.test_web_server -q`
 ：37 tests，OK。
- `python -S -m unittest tests.flow_contracts.test_flow_validation tests.flow_contracts.test_flow_contracts -q`
  ：34 tests，OK。
- `python -S -m unittest tests.test_recorder tests.test_connection_monitor -q`
  ：17 tests，OK。
- `python -S -m unittest tests.test_plugin_manager -q`：3 tests，OK；
  Cimatron 工作台清单可见且默认不安装。
- `scripts/benchmark_cam_flow.py --samples 20 --determinism-runs 20`：
  `PASS`；500 节点 `incremental_validation` p95=49.762ms，阈值仍
  100ms，未调高阈值。
- Cimatron 专项离线探针：静态 Python、旧 `cimatron.*` JSONL 归一化、
  Cimatron Flow 拒绝均通过；无宿主连接或源代码执行。
- HTTP 专项（临时 loopback server，隔离临时 app-data）：安装
  `cimatron-cam-copilot` 返回 200；`POST /api/analyze` 返回
  `cimatron_evidence_report` 与 `cam.source.call`；`POST /api/flow/import`
  返回 409 `CAPABILITY_UNAVAILABLE`。
- `plugin-creator/validate_plugin.py` 与每个 Cimatron skill 的
  `skill-creator/quick_validate.py`：通过（使用系统 Python；交付 venv
  未安装 PyYAML，故其无法运行插件验证脚本）。

## 交接注意

共享工作区存在其他对话的未提交改动；本方案未 reset、stash、分支或提交，
未编辑 `web_server.py`。主控应在最终根回归中复核所有并行改动。
