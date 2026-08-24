# CAM Automation Studio 0.6.0 Release Notes

发布日期：2026-08-24

## 发布内容

- 增加 NX/PowerMill 双产品低代码 FlowGraph 合同、冻结模型、验证和稳定语义哈希。
- 离线解析 NX Journal 与 PowerMill 宏/日志，并映射为带来源定位的可视化流程图。
- 增加 capability/asset manifest、typed ports、目标版本锁定和兼容性诊断。
- 增加单窗口 Flow Studio：拖拽、连线、参数编辑、Undo/Redo、版本、diff、测试和预览。
- 增加 round-trip、compatibility、产品 recipe projection 三重审查门禁。
- 增加 NX 与 PowerMill Flow Studio Codex Skills 和 review-first 参考合同。
- 增加两个 NX、两个 PowerMill 的多实例 fixture，以及 1,000-plan 隔离安全屏障。
- 增加 1440×900、1280×720、390×844 浏览器验收与 500 节点稳定性验收。

## 安全边界

- NX 与 PowerMill parser 保持隔离；共享动作使用 `cam.*`，产品动作使用 `nx.*` 或
  `powermill.*`。
- 所有学习、图编辑、recipe projection 和 preview 都是离线、只读或 fixture dry-run。
- preview 固定 `transport=none`，命令执行、传输发送、NC 生成和机床控制计数全部为零。
- 生产动作在人工审阅、CAM 仿真、碰撞检查和车间批准完成前保持阻断。
- 不导入或执行 live NX Journal/PowerMill 宏，不生成或发送 machine-ready NC、
  G-code、CLSF、postprocess 或 machine control。

## 验证

- 浏览器验收 6/6：桌面、移动端、trusted PointerEvent 拖拽、Undo、双向来源定位、
  typed connect、版本/diff/test/preview 和 500 节点稳定性。
- 浏览器运行期间 page error、console error、request failed 和非 loopback 网络请求均为零。
- Python：根 259/259、UG/NX 44/44、PowerMill 15/15。
- Node：22/22；T10 专项 Python 14/14、Node 3/3。
- Packaging：5 个 Codex 插件和 10 个 Skill 通过官方 validator。
- Codex：仓库 `personal` marketplace 已添加；5 个插件使用
  `codex.20260824043001` cachebuster 重装并显示 installed/enabled。新建任务可发现
  NX/PowerMill 各 5 个 Skill，两个 Flow Studio Skill 的离线审查验证均通过。
- T11 性能屏障 3/3；每个指标采样 20 次，确定性检查均为 20 次运行只产生 1 个唯一结果。
- 500 节点参数增量验证 p50 72.010 ms、p95 84.148 ms、max 89.328 ms。
- Undo p95 1.562 ms，Redo p95 1.655 ms，semantic diff p95 123.171 ms。
- NX 10,000 行静态解析 p95 103.158 ms，PowerMill 10,000 行静态解析
  p95 324.813 ms，fixture preview p95 239.476 ms。
- T10 验证 1,000 个冻结 preview plan 的产品、版本、项目和实例绑定；所有计划继续保持
  `transport=none` 和零执行字段。
- 性能 fixture 使用仓库级 LF 属性和原始字节 SHA-256 清单，Windows 与 Unix 检出使用
  同一校验结果。

## 现场验证项

真实 NXOpen/PowerMill transport、目标版本 API、项目映射、稳定对象选择器、CAM 仿真、
碰撞/过切检查和车间批准不在本次 fixture 发布范围内。上述验证完成前，不能将本版本
描述为 production live execution。
