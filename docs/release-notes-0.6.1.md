# CAM Automation Studio 0.6.1 Release Notes

## 多版本兼容

- 新增统一目标版本归一与分级：`verified`、`review_required`、
  `opaque_only`、`unsupported/unknown`。
- NX 支持 `NX`、`Siemens NX`、`UG NX` 名称归一；PowerMill 支持
  `PowerMill`、`Autodesk PowerMill`、`PowerMILL`。
- 保持 capability manifest 顶层已验证版本精确、旧消费者 fail-closed；宽范围只存在于
  可选扩展中，且不代表 API 或宏语义已验证。
- CompatibilityReport 和 FlowService 使用同一个分类器，修复范围匹配与精确字符串
  判断不一致的问题。
- 未验证版本仍可离线读取和审阅，但不能进入 fixture preview；经典版本只保留原文和
  证据，不做语义编辑或 recipe projection。
- 增加跨新旧版本、别名、内部 PowerMill 版本号、未知版本和 malformed policy 测试。
- NX app/Core 插件更新为 `0.2.1`、Codex 插件更新为 `0.3.1`；PowerMill app
  插件更新为 `0.1.1`、Codex 插件更新为 `0.2.1`。两份 capability manifest
  更新为 `1.1.0`。

## 安全边界

- 没有增加 live NX Journal 或 PowerMill 宏执行。
- 没有生成或发送 NC、G-code、CLSF、postprocess 或机床控制内容。
- Preview 继续固定 `transport=none`，四个执行字段保持为零。

## 验收结果

- 根测试：265/265。
- NX 插件：44/44。
- PowerMill 插件：15/15。
- Node/Flow：19/19。
- 浏览器：6/6。
- 性能屏障：PASS；500 节点增量校验 p95 `77.520 ms`，fixture preview p95
  `237.650 ms`，进程峰值 `85.465 MiB`。

详细策略见 [cam-version-compatibility-policy.md](cam-version-compatibility-policy.md)。
