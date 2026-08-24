# NX / PowerMill 多版本兼容策略

## 1. 目标

现场同时存在最新、连续发布和经典旧版 NX / PowerMill。兼容设计不能把“解析器能读”
误写成“目标版本 API、宏语义或生产结果已经验证”。本策略把目标版本分成四个可解释
层级，并让离线导入、CompatibilityReport、FlowService 和 Codex 插件使用同一结论。

顶层 `target_version_ranges` 继续只保存经过 fixture 审阅的精确版本。可选扩展
`extensions.cam_automation.version_compatibility` 只声明保守的识别与降级策略，
不会扩大已验证能力范围，因此不认识该扩展的旧消费者仍会 fail-closed。

## 2. 兼容层级

| 层级 | 允许能力 | 禁止能力 | CompatibilityReport |
| --- | --- | --- | --- |
| `verified` | 离线解析、结构化审阅；满足其他门禁时可形成 fixture preview | live Journal/宏、NC、机床控制 | `compatible` 或由其他问题降级 |
| `review_required` | 离线读取、语义图展示、人工补充版本证据 | preview、自动投影为可执行动作、生产声明 | `needs_review` |
| `opaque_only` | 原文、token、source mapping 和未知区保留 | 语义编辑、recipe projection、preview | `incompatible` |
| `unsupported` / `unknown` | 保存输入版本和诊断，不猜测映射 | 语义编辑、projection、preview | `incompatible` / `unknown` |

任何层级都不代表 CAM 仿真、碰撞检查、机床仿真、人工审阅或车间批准已经通过。

## 3. 当前清单

以下范围是首版的“识别和降级策略”，不是厂商 API 等价性声明。

### Siemens NX / UG

| 输入版本 | 层级 | 原因 |
| --- | --- | --- |
| `NX 2406` | `verified` | 仓库存在 NX 2406 stub/fixture 证据 |
| `NX >=1847,<2406`、`NX >2406` | `review_required` | 可静态识别，但必须重新核验 Python minor/ABI、stub digest、Builder/Undo、selector 和符号 |
| `NX >=8,<1847` | `opaque_only` | 经典版本只承诺保留证据，不承诺当前语义映射 |
| 更早、无数字或无法归一的版本 | `unsupported` / `unknown` | 无足够证据，禁止猜测 |

接受 `NX`、`Siemens NX`、`UG NX` 别名。输入中明确给出的 full version/build 会保留
在报告中；分类器只提取同一输入里的 release 部分用于清单匹配，不推断缺失版本号。

### Autodesk PowerMill

| 输入版本 | 层级 | 原因 |
| --- | --- | --- |
| `PowerMill 2025`、`PowerMill 2026` | `verified` | 仓库 fixture 审阅范围 |
| `PowerMill 2017-2024`、`PowerMill >2026`、内部 `PowerMill 21.*` | `review_required` | 可识别但宏语法、安装路径、程序集/COM 和命令语义需要目标机验证 |
| `PowerMill 2010-2016` | `opaque_only` | 只保留宏/日志原文和来源证据 |
| 更早、无数字或无法归一的版本 | `unsupported` / `unknown` | 无足够证据，禁止猜测 |

接受 `PowerMill`、`PowerMill Ultimate`、`Autodesk PowerMill` 和历史大小写
`PowerMILL` 别名。输入中明确给出的 build 仍保留，不会被改写成另一个发行版。

## 4. 实现约束

- NX 语法只由 `plugins/ug-cam-copilot/src/ugcam_ai/adapters/nx_journal.py`
  及 NX Flow mapper 解释。
- PowerMill 语法只由 `cam_automation/adapters/powermill_macro.py` 及 PowerMill
  Flow importer 解释。
- 图的 `target_versions` 保持精确版本集合；范围策略不能替代图 revision 的明确目标。
- `review_required` 产生 warning、节点 `needs_review`、`preview_eligible=false`。
- `opaque_only` 保持产物可读，但节点不可语义编辑或投影，且产生稳定 blocker。
- 未知版本不自动映射到“最近版本”，新版本也不继承旧版本的已验证结论。
- 已保存的未知扩展字段继续 round-trip；共享合同只增加可选字段。

## 5. 后续版本验证包

把某一版本提升为 `verified` 前，至少需要：

1. 精确 release/full version、build、architecture 和本机实例证据。
2. NX 的 Python minor/ABI、官方本地 stubs 与 digest；或 PowerMill 的宏指南、
   目标程序集/COM/插件接口证据。
3. 同版本正常、边界、未知语法和失败样本 fixture。
4. parser/source mapping/opaque preservation/round-trip 的确定性测试。
5. CompatibilityReport 与 capability lock 精确哈希。
6. fixture dry-run 全部零执行字段；生产仍需 CAM 仿真、碰撞检查、机床仿真和车间批准。
