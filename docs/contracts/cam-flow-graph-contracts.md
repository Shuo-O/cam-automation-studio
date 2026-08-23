# CAM Flow Graph v1 冻结合同

> 状态：Frozen
>
> 合同族版本：`cam.flow.contracts.v1`
>
> 冻结日期：2026-08-24
>
> 适用范围：CAM Automation Studio 的离线资产、能力注册、FlowGraph、
> 版本、兼容性、round-trip 和 dry-run preview

本文冻结 CAM 低代码 MVP 的规范性 JSON 合同。本文中的“必须”“不得”
“应”“可以”具有规范性含义。实现事实仍以 Schema、golden fixture 和
确定性校验器共同验证；自然语言说明、UI 文案和 AI 解释均不是合同真相。

本文是现有
[并行开发合同 v1](parallel-development-contracts.md) 的兄弟合同，不替换
`ActivityEvent v1`、`Recipe v1`、`CommandTask v1`、`DiffReport v1` 或
`cam.codex.bridge.v1`。FlowGraph 只有经过确定性投影和人工审阅后，才可以进入
现有 Recipe/dry-run 链。

产品闭环见
[CAM Low-Code Studio 产品方案](../cam-low-code-studio-product-proposal.md)，
实现拆分见
[AI 并行开发计划](../cam-low-code-ai-parallel-development-plan.md)，逐任务启动
文本见
[并行 Agent 提示词](../cam-low-code-parallel-agent-prompts.md)。

## 1. 证据与决策口径

| 标签 | 含义 | 本文用法 |
| --- | --- | --- |
| **研究事实** | 来自仓库冻结边界、官方资料或四份研究中标为已验证的事实 | 解释为什么存在约束，不单独构成实现 |
| **产品决策** | 本文冻结的规范性选择 | v1 实现必须遵守 |
| **现场待验证** | 依赖真实客户样本、目标 CAM 版本、工作站或法务意见 | 不得伪装成已支持能力 |

关键依据：

- **研究事实：** NX Journal 与 PowerMill macro 的语法、版本和运行时不同；
  现有仓库已经要求 parser 物理隔离。
- **研究事实：** PowerMill 录制宏只记录实际改变的值；NX Journal identifier
  可能跨版本变化，录制资产不能被视为完整状态或稳定对象选择证明。
- **研究事实：** 通用画布库不提供 CAM 类型、source map、round-trip 或安全
  语义。
- **产品决策：** `cam.flowgraph.v1` 是产品中立 canonical IR，React Flow 等
  UI 对象不得作为持久化合同。
- **现场待验证：** 目标版本支持范围、opaque 比例、500 节点上限、worker
  隔离能力和商业外挂授权范围。

## 2. 全局规范

### 2.1 JSON、ID、时间与哈希

1. 所有顶层对象必须是 JSON object，并包含整数
   `"schema_version": 1` 和本文规定的 `contract`。
2. 字段名必须使用 `snake_case`。ID 是非空、不透明、区分大小写的字符串；
   消费者不得解析 ID 内部格式。
3. 时间必须使用带时区的 RFC 3339 字符串；duration 使用非负整数毫秒。
4. 哈希格式必须为 `sha256:<64 lowercase hex>`。
5. 语义哈希必须使用 RFC 8785 JSON Canonicalization Scheme 的 UTF-8
   结果计算 SHA-256。每个合同的哈希包含范围见第 11 节。
6. `null` 只在字段表明确允许时使用。可选字段可省略，但不得用虚构默认值
   填充未知事实。
7. 数组在合同声明为有序时必须保序；无序集合在哈希前必须按稳定键排序。
8. 生产者不得写入 `NaN`、`Infinity`、重复 object key、控制字符或未脱敏
   绝对路径。

### 2.2 向后兼容与未知字段

1. v1 字段只增不删：不得删除、重命名、改变类型、改变默认语义或降低安全
   等级。
2. v1 后续新增字段必须是可选字段。破坏性变更必须使用新的合同主版本。
3. 只读消费者必须忽略不理解的新增可选字段。
4. 会保存或改写对象的消费者必须原样保留未知字段。若无法判断未知字段是否
   影响语义，必须进入只读状态并返回 `FLOW_UNKNOWN_SEMANTICS`，不得保存
   一个丢字段的新版本。
5. 新 namespaced node/action 可以在 v1 中增加；无法识别时必须 materialize
   为 visible opaque/unsupported node，并阻断相应编辑、投影和 preview。
6. enum 扩展对只读消费者是可忽略的新值；执行语义消费者遇到未知值必须
   fail closed。

### 2.3 产品与 parser 隔离

1. `product` 只允许 `nx` 或 `powermill`。MVP 中一张图只允许一个产品。
2. 共享动作和节点类型使用 `cam.*`；NX 专属使用 `nx.*`；PowerMill 专属
   使用 `powermill.*`。`flow.*` 仅用于控制与 subflow；`opaque.*` 仅用于
   可见保真占位。
3. NX Journal/NXOpen 语法只允许在
   `plugins/ug-cam-copilot/src/ugcam_ai/adapters/nx_journal.py`
   及其 NX 专属辅助模块中解析。
4. PowerMill `.mac`、命令日志和产品语法只允许在 PowerMill 应用或
   `powermill_macro.py` 专属 adapter 中解析。
5. 共享 asset、graph、version、API、UI 和 preview 层不得解析、拼接或
   猜测任一产品语法。

### 2.4 永久安全边界

以下是合同不变量，不是可配置功能：

- 所有生成物 review-first；MVP 只允许离线、只读或 fixture dry-run。
- 不 import、eval、exec、play、replay 或 live 运行 NX Journal。
- 不运行 PowerMill macro，不发送 PowerMill 命令，不附着或控制真实实例。
- 不生成、后处理、保留、返回或发送 machine-ready NC、G-code、CLSF、
  postprocess 或 machine-control payload。
- 不把 dry-run、API symbol 存在、fixture 成功或 AI 审阅解释为 CAM 仿真、
  碰撞/过切检查、机床仿真或车间批准。
- 不从前台窗口、标题、列表顺序或 PID 单值自动选择目标实例。
- 不反编译、反射探测、注入、hook 或绕过商业外挂许可证。
- preview/codegen worker 不得拥有 CAM transport handle。

命中上述边界时必须返回结构化错误并丢弃敏感 payload；不得把被拒内容写入
日志、报告、diff、AI context 或 UI。

## 3. `AutomationAsset`

`AutomationAsset` 是不可变原始资产的注册记录，合同固定为
`cam.automation_asset.v1`。

| 字段 | 类型 | 必需 | 规范 |
| --- | --- | --- | --- |
| `schema_version` | integer | 是 | 固定 `1` |
| `contract` | string | 是 | 固定 `cam.automation_asset.v1` |
| `asset_id` | string | 是 | 不透明逻辑 ID |
| `asset_revision_id` | string | 是 | 此次不可变内容版本 |
| `product` | string | 是 | `nx` 或 `powermill`；不允许自动猜测 |
| `asset_type` | string | 是 | `nx_journal`、`nx_action_log`、`powermill_macro`、`powermill_command_log`、`vendor_export`、`capability_manifest` |
| `display_name` | string | 是 | 脱敏显示名 |
| `source_locator` | string | 是 | 本地内容寻址引用或脱敏别名；不得为现场绝对路径 |
| `content_hash` | string | 是 | 原始 bytes 的 SHA-256 |
| `byte_length` | integer | 是 | 非负 |
| `encoding` | string | 是 | 探测值，如 `utf-8`；未知使用 `unknown` |
| `bom` | string | 是 | `none`、`utf8`、`utf16le`、`utf16be`、`unknown` |
| `newline_profile` | string | 是 | `lf`、`crlf`、`cr`、`mixed`、`none`、`unknown` |
| `immutable` | boolean | 是 | 必须为 `true` |
| `source_origin` | string | 是 | `user_authored`、`employer_owned`、`vendor_export`、`open_source`、`unknown` |
| `rights` | object | 是 | 见下 |
| `target_versions` | array[string] | 是 | 可为空，表示未知 |
| `runtime_modes` | array[string] | 是 | `offline`、`native`、`teamcenter_managed` 等声明 |
| `dependencies` | array[object] | 是 | 依赖 ID、种类、hash、required 和状态 |
| `imported_at` | string | 是 | RFC 3339 |
| `extensions` | object | 是 | namespaced 扩展；默认 `{}` |

`rights` 必须包含
`status`、`evidence_ref`、`sharing_scope`、`redistribution_allowed`、
`network_egress_allowed` 和 `binary_inspection`。其中：

- `status` 为 `unreviewed`、`user_asserted`、`vendor_authorized`、
  `legal_reviewed` 或 `restricted`。
- `redistribution_allowed` 为 boolean 或 null；null 表示未知，等价于禁止导出。
- `network_egress_allowed` 默认且推荐为 `false`。
- `binary_inspection` 必须为 `false`。

原始资产不得被编辑或覆盖。任何 patch 只产生内存候选或新的
`AutomationAsset.asset_revision_id`；旧 revision 永久可追溯。

## 4. `CapabilityManifest`

`CapabilityManifest` 合同固定为 `cam.capability_manifest.v1`。注册表示
“可识别”，不表示“可执行”或“已授权”。

| 字段 | 类型 | 必需 | 规范 |
| --- | --- | --- | --- |
| `schema_version` | integer | 是 | 固定 `1` |
| `contract` | string | 是 | 固定 `cam.capability_manifest.v1` |
| `manifest_id` | string | 是 | 不可变身份 |
| `manifest_version` | string | 是 | SemVer |
| `manifest_hash` | string | 是 | 除自身和签名验证结果外的 canonical hash |
| `provider` | object | 是 | `provider_id`、`display_name`、`claim_level` |
| `product` | string | 是 | 单一产品 |
| `adapter_kind` | string | 是 | `builtin`、`licensed_adapter`、`public_api`、`vendor_export`、`manifest_only` |
| `authorization` | object | 是 | `status`、证据引用、核验人、有效期 |
| `target_version_ranges` | array[string] | 是 | 不得只写 `latest` |
| `execution_modes` | array[string] | 是 | 仅可含 `offline`、`read_only`、`fixture_dry_run` |
| `permissions` | array[string] | 是 | 最小权限类别 |
| `node_types` | array[object] | 是 | 节点定义，至少一个 |
| `prohibited_operations` | array[string] | 是 | 必须覆盖 live Journal/macro、NC/G-code/postprocess/machine control |
| `evidence` | array[object] | 是 | 类型、引用、版本、访问/验证时间 |
| `revocation` | object/null | 是 | 未撤销为 null |
| `extensions` | object | 是 | namespaced 扩展 |

每个 `node_types` 项必须包含：

- `node_type`：`cam.*`、`nx.*` 或 `powermill.*`。
- `node_type_version`：SemVer。
- `category` 和 `display_name`。
- `ports`：`PortContract[]`。
- `configuration_schema`：受限 JSON Schema object。
- `static_risk_floor`：`safe`、`review` 或 `blocked`。
- `required_gates` 和 `forbidden_modes`。
- `parse_support`、`preview_support`、`recipe_projection_support`。
- `fidelity`：`byte_exact`、`token_exact_outside_edits`、
  `semantic_round_trip`、`opaque_preserved` 或 `blocked`。

manifest 声明的风险是下限，图、UI、插件运行时或 AI 不得降低。授权、签名、
版本范围或 manifest hash 变化必须重新核验；撤销后已有图仍可只读显示，
相关节点状态变为 `capability_unavailable`。

## 5. `FlowGraph`、`FlowNode`、`FlowEdge`

### 5.1 `FlowGraph`

合同固定为 `cam.flowgraph.v1`。

| 字段 | 类型 | 必需 | 规范 |
| --- | --- | --- | --- |
| `schema_version` | integer | 是 | 固定 `1` |
| `contract` | string | 是 | 固定 `cam.flowgraph.v1` |
| `graph_id` | string | 是 | 逻辑身份 |
| `revision_id` | string | 是 | 不可变 revision |
| `parent_revision_id` | string/null | 是 | 根 revision 为 null |
| `product` | string | 是 | 单一产品 |
| `target_versions` | array[string] | 是 | 已审阅集合，可为空 |
| `entry_flow_id` | string | 是 | 必须引用一个 `main` flow |
| `graph_parameters` | array[object] | 是 | 稳定 ID、名称、类型、单位、默认值、约束、证据和审阅状态 |
| `flows` | array[object] | 是 | `FlowDefinition[]`，至少一个 |
| `asset_refs` | array[object] | 是 | asset/revision/content hash |
| `source_mappings` | array[SourceMapping] | 是 | 双向映射表 |
| `capability_lock` | array[object] | 是 | manifest/node type/version/hash |
| `required_gates` | array[string] | 是 | 不得弱化既有审阅、目标版本、仿真、碰撞和车间门禁 |
| `semantic_hash` | string | 是 | 第 11 节 |
| `source_snapshot_hash` | string | 是 | 第 11 节 |
| `layout` | object | 是 | 非语义 view state |
| `extensions` | object | 是 | namespaced 扩展 |

`FlowDefinition` 必须包含 `flow_id`、`kind=main|subflow`、`name`、
`interface_ports`、`parameter_ids`、`nodes`、`edges` 和
`source_mapping_ids`。MVP 允许非递归 subflow；edge 不得跨 flow，跨 flow
数据只能经 `flow.subflow_call` 的 interface port。

### 5.2 `FlowNode`

| 字段 | 类型 | 必需 | 规范 |
| --- | --- | --- | --- |
| `node_id` | string | 是 | 图内唯一，不能使用画布序号 |
| `node_type` | string | 是 | 合法 namespace |
| `node_type_version` | string | 是 | 精确 SemVer |
| `enabled` | boolean | 是 | false 仍需保留来源和 diff |
| `risk` | string | 是 | `safe`、`review`、`blocked`，不得低于 manifest |
| `review_status` | string | 是 | `unreviewed`、`needs_review`、`accepted`、`rejected` |
| `port_contract_refs` | array[string] | 是 | 锁定 manifest port |
| `bindings` | array[ParameterBinding] | 是 | 输入绑定 |
| `configuration` | object | 是 | 必须通过 capability schema |
| `source_mapping_ids` | array[string] | 是 | 可以为空但必须可见为 synthetic/unmapped |
| `fidelity` | string | 是 | F0/F1/F2/F3/FB 对应值 |
| `capability_ref` | object | 是 | manifest/node/version/hash |
| `compatibility_status` | string | 是 | `unknown`、`supported`、`needs_review`、`unsupported`、`capability_unavailable` |
| `opaque` | object/null | 是 | opaque/unsupported 时必须非 null |
| `extensions` | object | 是 | namespaced 扩展 |

`opaque` 必须包含 `reason`、`asset_revision_id`、`source_span_ids`、
`content_hash`、`round_trip_policy`、`semantic_editable=false` 和
`diagnostic_codes`。opaque/unsupported 不得隐藏、删除、自动修复或被 AI
猜测成已知节点。

### 5.3 `FlowEdge`

| 字段 | 类型 | 必需 | 规范 |
| --- | --- | --- | --- |
| `edge_id` | string | 是 | 图内唯一 |
| `kind` | string | 是 | `control`、`data`、`dependency` |
| `source` | object | 是 | `node_id`、`port_id` |
| `target` | object | 是 | `node_id`、`port_id` |
| `condition` | object/null | 是 | 仅允许受限确定性表达式 AST |
| `priority` | integer/null | 是 | 分支稳定顺序；非分支为 null |
| `source_mapping_ids` | array[string] | 是 | 可为空 |
| `extensions` | object | 是 | namespaced 扩展 |

校验必须满足：

- data edge 仅允许 output 到 input，并满足类型、单位和 cardinality。
- control、data、dependency 互不推断；屏幕位置和数组顺序没有执行语义。
- dependency graph 必须无环。
- control loop 只允许显式、capability 支持的 loop node；MVP 默认禁用一般
  循环。
- edge 不得跨 flow；subflow 直接或间接递归必须拒绝。
- 多 source 是否允许由目标 `PortContract.cardinality` 决定。

## 6. `PortContract` 与 `ParameterBinding`

### 6.1 `PortContract`

`PortContract` 由 capability manifest 定义，不由 React handle 决定。

| 字段 | 类型 | 必需 | 规范 |
| --- | --- | --- | --- |
| `port_id` | string | 是 | node type 内稳定 |
| `direction` | string | 是 | `input` 或 `output` |
| `edge_kinds` | array[string] | 是 | `control`、`data`、`dependency` 的非空子集 |
| `type_ref` | string | 是 | 版本化封闭类型 |
| `cardinality` | string | 是 | `one`、`optional`、`many` |
| `required` | boolean | 是 | preview 前是否必须满足 |
| `constraints` | object | 是 | range、enum、unit dimension、selector 条件 |
| `sensitivity` | string | 是 | `public`、`project_local`、`secret_ref` |
| `extensions` | object | 是 | namespaced 扩展 |

基础类型为 `string`、`integer`、`number`、`boolean`、`enum`、`path_ref`；
CAM 类型为 `cam.length`、`cam.angle`、`cam.ratio`、
`cam.coordinate_frame`、`cam.object_selector`；集合仅允许 manifest
声明的 `list<T>`。单位转换必须使用显式 conversion node，不能静默换算。
opaque data 不得连接已知 data port，除非 manifest 存在明确 adapter。

### 6.2 `ParameterBinding`

| 字段 | 类型 | 必需 | 规范 |
| --- | --- | --- | --- |
| `binding_id` | string | 是 | 图内唯一 |
| `target` | object | 是 | `node_id`、`port_id` |
| `kind` | string | 是 | `literal`、`graph_parameter`、`node_output`、`secret_ref` |
| `literal` | object/null | 是 | kind=literal 时含 value/type_ref/unit |
| `graph_parameter_id` | string/null | 是 | kind=graph_parameter 时唯一非 null ref |
| `source_output` | object/null | 是 | kind=node_output 时含 node_id/port_id |
| `secret_ref` | string/null | 是 | kind=secret_ref 时仅保存引用 |
| `source_mapping_ids` | array[string] | 是 | 参数来源 |
| `extensions` | object | 是 | namespaced 扩展 |

四种 value source 必须且只能有一种生效。secret value 不得进入 graph JSON、
diff、source map、fixture 或 AI context。对象 selector 必须使用类型化对象，
不得通过普通 string 绕过 product/version/cardinality 验证。

## 7. `SourceMapping`

`SourceMapping` 同时冻结兼容 `source_line` 和精确 `source_span`，支持
graph -> source 与 source -> graph 双向定位。

| 字段 | 类型 | 必需 | 规范 |
| --- | --- | --- | --- |
| `mapping_id` | string | 是 | 图内唯一 |
| `asset_id` | string | 是 | 原始资产 |
| `asset_revision_id` | string | 是 | 精确不可变 revision |
| `source_digest` | string | 是 | 必须匹配资产 content hash |
| `source_line` | integer | 是 | 1-based 兼容定位；必须等于 span start_line |
| `source_span` | object | 是 | 见下 |
| `target` | object | 是 | `flow_id`、`node_id`，可选 `property_path`/`port_id` |
| `role` | string | 是 | `primary`、`parameter`、`control`、`trivia`、`opaque` |
| `mapping_quality` | string | 是 | `exact`、`derived`、`ambiguous`、`synthetic` |
| `excerpt_hash` | string | 是 | span 原始 bytes hash；synthetic 可为整个空字节 hash |
| `extensions` | object | 是 | namespaced 扩展 |

`source_span` 必须包含半开区间 `start_byte`、`end_byte`，1-based
`start_line`、`end_line`，0-based `start_column`、`end_column`，以及
`column_encoding=utf16|unicode_scalar`。`end_byte >= start_byte`。

双向映射规则：

1. node/property/port 通过 `source_mapping_ids` 定位到一个或多个 span。
2. source lookup 必须在同一 asset revision 上，返回覆盖目标 byte/line 的全部
   mapping，并按“最小 span、exact 优先、mapping_id”稳定排序。
3. 多个冲突 mapping 必须显示歧义并返回 `SOURCE_MAPPING_AMBIGUOUS`，不得猜测。
4. `source_line` 只为现有 UI/ActivityEvent 兼容；patch、round-trip 和 hash guard
   必须使用 byte span + digest。
5. 外部文件变化导致 digest 不匹配时，必须重新导入或做三方 diff，不得沿用旧 span。

## 8. 报告、预览与版本合同

### 8.1 `RoundTripReport`

合同固定为 `cam.round_trip_report.v1`。

必需字段：

- 身份：`schema_version`、`contract`、`report_id`、`graph_id`、
  `revision_id`、`asset_revision_id`。
- 哈希：`original_content_hash`、`candidate_content_hash`（可 null）、
  `original_semantic_hash`、`candidate_semantic_hash`（可 null）。
- 结果：`status=not_checked|passed|blocked|failed`、
  `fidelity=F0|F1|F2|F3|FB`、`untouched_spans_exact`、
  `opaque_spans_preserved`、`candidate_reparsed`。
- 证据：`changed_mapping_ids`、`checks`、`issues`、
  `candidate_artifact_ref`（只允许内存/本地临时候选引用或 null）、
  `checked_at`、`extensions`。

保证：

| 等级 | 必须证明 |
| --- | --- |
| `F0` | 无语义编辑时原始与候选 bytes 完全一致 |
| `F1` | 编辑 span 外 token/trivia bytes 完全一致 |
| `F2` | `parse(candidate)` 与目标图语义等价 |
| `F3` | 所有未触及 opaque span bytes/hash 完全一致 |
| `FB` | 无法证明，不产生可保存候选 |

实现不得把 F2 声称为 F0。任一 required check 失败时不得留下“尽量可用”的
候选。

### 8.2 `CompatibilityReport`

合同固定为 `cam.compatibility_report.v1`。必需字段：

- `report_id`、`graph_id`、`revision_id`、`product`。
- `source_profile` 与 `target_profile`；必须包含目标版本，产品特有证据放在
  adapter namespaced extension。
- `capability_lock_hash`、`checked_manifest_hashes`。
- `status=compatible|needs_review|incompatible|unknown`。
- `node_results`、`symbol_results`、`selector_results`、
  `coverage_gaps`、`issues`、`blocker_codes`。
- `preview_eligible`、`checked_at`、`extensions`。

`compatible` 只表示静态合同、类型和目标能力可解析，不表示运行、几何、刀路、
仿真、碰撞或生产正确。缺失目标版本、capability、selector、单位、MCS、项目
上下文或 opaque 处置时，`preview_eligible` 必须为 false。

### 8.3 `PreviewPlan`

合同固定为 `cam.preview_plan.v1`，它是离线计划，不是执行请求。

必需字段：

- `plan_id`、`graph_id`、`revision_id`、`flow_version_id`。
- `status=draft|validating|blocked|ready|previewing|succeeded|failed|cancelled`。
- `execution_mode=fixture_dry_run`、`transport=none`。
- `target`：`product`、`target_version`、`target_instance_id`、
  `project_id`、`project_snapshot_hash`、`target_kind=fixture`。
- `hashes`：source snapshot、semantic、capability lock 和 reviewed recipe
  hash（尚未投影时可 null）。
- `steps`：稳定 order、node/action、参数摘要、source mapping、effects、
  risk、compatibility 和 approval requirement。
- `gate_results`：未实际运行的 CAM 仿真、碰撞、机床仿真必须为
  `not_run|required|unavailable`。
- `round_trip_report_id`、`compatibility_report_id`、`diagnostics`。
- `commands_sent=0`、`journal_executed=false`、`macro_executed=false`、
  `machine_output_count=0`。
- `created_at`、`completed_at`（可 null）、`extensions`。

MVP 不允许 `target_kind=live`、`transport!=none` 或其他 execution mode。
PreviewPlan 不得直接作为 `CommandTask` payload。

### 8.4 `FlowVersion`

合同固定为 `cam.flow_version.v1`。必需字段：

`version_id`、`graph_id`、`revision_id`、`parent_version_ids`、
`semantic_hash`、`artifact_hash`、`source_snapshot_hash`、
`capability_lock_hash`、`status`、`author_ref`、`message`、
`round_trip_report_id`、`compatibility_report_id`、`created_at`、
`immutable=true`、`extensions`。

`status` 只允许：

- `draft`
- `review_required`
- `reviewed_for_fixture`
- `retired`

不存在 `approved_for_production`。每次语义变化、capability migration、目标版本
变化或 source revision 变化都必须创建新的 FlowVersion；不得覆盖旧版本。

## 9. 状态机

### 9.1 资产与能力

```text
asset selected
  -> rights_review
  -> imported
  -> parsed
  -> validated
  -> review_required
  -> registered

任一状态 -> restricted | retired
```

- `rights_review -> imported` 需要权利声明和不可变 bytes/hash。
- `parsed` 不表示全部语义受支持；opaque 必须可见。
- `restricted` 保留本地元数据和版本，不允许导出/preview。

```text
manifest declared
  -> contract_reviewed
  -> fixture_verified
  -> target_version_verified
  -> site_accepted

任一状态 -> revoked
```

MVP 最多依赖 `fixture_verified`；后两态是现场证据，不扩大本合同安全边界。
hash、签名、授权、版本范围或权限变化使状态回退到 `contract_reviewed`。

### 9.2 FlowVersion

```text
draft -> review_required -> reviewed_for_fixture -> retired
  ^            |
  |            +-- 变更 -> 新 draft revision
  +-- validator/round-trip/compatibility blocker 保持 draft
```

审阅只绑定精确 semantic/source/capability hashes。任何哈希变化使旧审阅失效。

### 9.3 PreviewPlan

```text
draft -> validating -> blocked
                    -> ready -> previewing -> succeeded
                                         -> failed
                                         -> cancelled
```

只有 `ready` 可进入 `previewing`。`blocked`、`failed` 和 `cancelled` 不得自动
重试；权限重新授予也不得自动恢复任务。状态机没有 live/executing/production
分支。

## 10. 校验码与错误码

所有诊断必须包含 `code`、`severity=info|warning|error|blocker`、
`message`、`object_ref`、`source_mapping_ids`、`remediation`。code 是稳定
合同，message 可本地化。已发布 code 不得复用为不同含义。

| Code | 默认级别 | 触发条件 |
| --- | --- | --- |
| `FLOW_SCHEMA_INVALID` | blocker | JSON 不符合合同 |
| `FLOW_UNKNOWN_SEMANTICS` | blocker | 编辑器无法保留/理解未知语义字段 |
| `FLOW_PRODUCT_MIXED` | blocker | 一图多产品 |
| `FLOW_NAMESPACE_INVALID` | blocker | node/action prefix 不合法 |
| `FLOW_ENTRY_INVALID` | blocker | entry flow 缺失或不是 main |
| `FLOW_CROSS_FLOW_EDGE` | blocker | edge 跨 flow |
| `FLOW_DEPENDENCY_CYCLE` | blocker | dependency 有环 |
| `FLOW_CONTROL_CYCLE_UNSUPPORTED` | blocker | 非显式受支持 loop |
| `FLOW_SUBFLOW_RECURSION` | blocker | subflow 直接/间接递归 |
| `PORT_DIRECTION_INVALID` | blocker | 非 output -> input data edge |
| `PORT_TYPE_MISMATCH` | blocker | 类型不可赋值 |
| `PORT_UNIT_MISMATCH` | blocker | 单位/维度不兼容或隐式转换 |
| `PORT_CARDINALITY_EXCEEDED` | blocker | 输入 source 超限 |
| `BINDING_ONE_OF_INVALID` | blocker | binding value source 不是恰好一个 |
| `SOURCE_DIGEST_MISMATCH` | blocker | source 与 asset revision 不一致 |
| `SOURCE_SPAN_INVALID` | blocker | byte/line/column 区间非法 |
| `SOURCE_MAPPING_AMBIGUOUS` | blocker | 双向定位存在冲突且未审阅 |
| `SOURCE_OPAQUE_EDIT` | blocker | 编辑穿越/拆分/重排 opaque |
| `SOURCE_SILENT_REWRITE` | blocker | 未触及 token/trivia 发生变化 |
| `ASSET_RIGHTS_UNKNOWN` | blocker | 权利未知却请求分享/外发/preview |
| `ASSET_BINARY_INSPECTION_FORBIDDEN` | blocker | 请求检查受保护二进制 |
| `CAPABILITY_MISSING` | blocker | manifest/node capability 缺失 |
| `CAPABILITY_VERSION_MISMATCH` | blocker | target/node major 不兼容 |
| `CAPABILITY_REVOKED` | blocker | 授权或 manifest 已撤销 |
| `CAPABILITY_RISK_DOWNGRADE` | blocker | 图风险低于 manifest floor |
| `COMPAT_TARGET_VERSION_UNKNOWN` | blocker | 未绑定目标版本 |
| `COMPAT_SELECTOR_UNRESOLVED` | blocker | 选择器未验证或 0/多匹配 |
| `COMPAT_CONTEXT_INCOMPLETE` | blocker | 单位/MCS/stock/tool/project 等缺失 |
| `ROUNDTRIP_REPARSE_FAILED` | blocker | 候选无法重新解析 |
| `ROUNDTRIP_SEMANTIC_MISMATCH` | blocker | 重解析语义不等价 |
| `ROUNDTRIP_OPAQUE_CHANGED` | blocker | opaque bytes/hash 变化 |
| `PREVIEW_HASH_MISMATCH` | blocker | 审阅与当前 hashes 不一致 |
| `PREVIEW_TARGET_AMBIGUOUS` | blocker | 实例/项目/版本绑定不明确 |
| `PREVIEW_PERMISSION_REVOKED` | blocker | 必需权限已撤销 |
| `SAFETY_LIVE_EXECUTION_FORBIDDEN` | blocker | live Journal/macro/CAM command 请求 |
| `SAFETY_MACHINE_OUTPUT_FORBIDDEN` | blocker | NC/G-code/CLSF/postprocess/machine control |
| `SAFETY_AI_AUTHORITY_EXCEEDED` | blocker | AI 直接改合同、生成源码或宣称门禁通过 |
| `RESOURCE_LIMIT_EXCEEDED` | blocker | 大小、深度、节点数、时间或内存超限 |

API 错误 envelope 必须为：

```json
{
  "schema_version": 1,
  "error": {
    "code": "PORT_TYPE_MISMATCH",
    "message": "目标端口需要 cam.length。",
    "correlation_id": "corr:example",
    "details": {
      "object_ref": "edge:e1",
      "source_mapping_ids": ["map:2"]
    }
  }
}
```

建议 HTTP 映射：Schema/参数错误 `400`，权限 `403`，缺失资源 `404`，状态或
版本冲突 `409`，大小限制 `413`，超时 `408`，内部失败 `500`。安全阻断不得用
`200` 包装为成功。

## 11. 哈希、版本兼容与迁移

### 11.1 三类哈希

| 哈希 | 包含 | 排除 |
| --- | --- | --- |
| `semantic_hash` | product、target versions、graph parameters、node type/version/enabled/risk/bindings/configuration、edges、subflow refs、capability lock、required gates、声明为 semantic 的 extensions | graph/revision ID、显示名、layout、selection、viewport、时间、批注、source span |
| `source_snapshot_hash` | 按 asset_revision_id 排序的 asset/content hash、encoding、BOM、newline profile 清单 | layout、graph 配置 |
| `artifact_hash` | 完整保存 JSON bytes | 无 |

未知 extension 若未明确 `semantic=false`，编辑器必须按潜在 semantic 处理并只读。
本合同不得改变既有 `recipe_hash` 算法。

### 11.2 合同与 node 版本

1. `contract` 主版本决定字段语义；v1 内只加可选字段。
2. node definition 使用 SemVer：
   - patch：修复，不改变 schema/语义；
   - minor：向后兼容新增可选配置/port；
   - major：需要显式 migration。
3. graph 必须锁定 manifest ID/version/hash 和 node type/version；不得以当前
   安装的“最新版”替代。
4. capability 缺失或 target version 不匹配时，图可读但节点
   `unsupported/capability_unavailable`，不可 preview。
5. migration 必须产生新 revision 和 `MigrationReport`，逐 node 标记
   `unchanged|migrated|opaque|blocked`。不得打开文件时自动覆盖。
6. migration 后必须重新执行 Schema、类型、graph、source、round-trip、
   compatibility、安全和投影校验；语义变化需人工确认。
7. `FlowGraph -> Recipe v1` 必须产生 ProjectionReport。任一 enabled 语义
   node/edge 未投影或含 blocker 时，不得生成 preview-eligible Recipe。

### 11.3 禁止静默重写

以下任一情况必须 `blocked` 且不产生候选：

- parser 只识别一个语句/命令的局部；
- span 对应冲突语义，或编辑跨越 opaque/ambiguous span；
- 未触及 span 的 token、空白、注释、引号、换行或大小写变化；
- 编码/BOM/newline 无法保留；
- candidate reparse 后 node、edge、binding、unit、selector、gate 不等价；
- 隐藏 CAM 状态、默认值、对象或目标版本被猜测；
- 图不能无歧义投影现有 Recipe v1；
- 内容命中 live、NC/G-code/CLSF/postprocess/machine-control 扫描。

## 12. AI、权限与多实例

### 12.1 AI 权限

AI 可以解释节点、参数、诊断和 diff，或输出未信任的
`GraphPatchProposal`。Proposal 必须带 rationale、confidence、evidence refs、
预期影响和待确认问题。

AI 不得：

- 直接写 FlowGraph、AutomationAsset、Recipe、PreviewPlan 或 CommandTask；
- 定义/改变 port type、node schema、capability 或风险下限；
- 生成产品源码、codegen 文本或执行 preview；
- 猜测 opaque/unsupported；
- 宣称 deterministic validation、仿真、碰撞或审批通过。

AI 关闭后，导入、编辑、校验、版本、diff、test 和 preview 必须完整可用且
结果相同。

### 12.2 一次授权与分类撤销

grant 必须绑定 user、plugin ID/version、manifest hash、权限类别、scope、purpose
和时间。相同版本与权限集合可以一次授权；新增权限、扩大路径/产品/版本/网络
范围或 manifest hash 变化必须重新询问。

推荐类别：

`read:selected-files`、`read:cam-logs`、`read:cam-process-list`、
`read:cam-window-titles`、`write:local-artifacts`、
`write:local-database`、`network:declared-hosts`。

撤销立即关闭新 handle 并取消相关 worker 任务，但不删除已保存数据。删除、
暂停、撤销、卸载是四个独立动作。重新授权不自动执行或恢复任务。

### 12.3 多实例与目标绑定

PreviewPlan 必须绑定 product、target version、target instance、project snapshot、
graph/recipe/capability hashes。前台窗口和标题只用于临时显示，不参与身份。
无法可靠映射时保持未映射。MVP 的 target 必须是 fixture；真实 NX/PowerMill
实例、项目映射和版本能力属于现场待验证，不能进入本合同的可执行分支。

## 13. 资源限制与性能目标

默认输入上限：单资产 10 MiB、单图 500 nodes/800 edges、最大嵌套深度 64、
单个 string 1 MiB、单次诊断 5,000 项。超限必须返回
`RESOURCE_LIMIT_EXCEEDED`，不得截断后继续。

以下是 **现场待验证的 MVP 目标**，测量环境必须记录 CPU、内存、Windows、
Python/Node 版本和冷/温缓存状态：

| 场景 | 目标 |
| --- | ---: |
| 500 node / 800 edge 图冷打开 | p95 <= 2.0 s |
| 同图温打开 | p95 <= 750 ms |
| 典型视口拖拽/缩放 | p95 frame <= 16.7 ms |
| 参数编辑增量校验 | p95 <= 100 ms |
| 单事务 undo/redo | p95 <= 50 ms |
| 500 node 相邻 revision 语义 diff | p95 <= 300 ms |
| 10,000 行单产品离线 parse | p95 <= 1.5 s |
| 500 node fixture preview | p95 <= 3.0 s |
| 后台 test 取消响应 | <= 250 ms |
| 主窗口常驻内存 | <= 350 MiB |

不能通过跳过 Schema、类型、source map、round-trip、安全或重新解析校验换取性能。

## 14. 完整最小 JSON 示例

下例在一个 JSON bundle 中覆盖本文冻结的 11 类对象。示例 hash 是格式合法的
占位证据；正式 golden fixture 必须由 canonicalizer 计算并验证实际值。

```json
{
  "schema_version": 1,
  "contract": "cam.flow.example_bundle.v1",
  "automation_asset": {
    "schema_version": 1,
    "contract": "cam.automation_asset.v1",
    "asset_id": "asset:pm:demo",
    "asset_revision_id": "asset-rev:pm:demo:1",
    "product": "powermill",
    "asset_type": "powermill_macro",
    "display_name": "demo.mac",
    "source_locator": "local-content:asset-rev:pm:demo:1",
    "content_hash": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "byte_length": 24,
    "encoding": "utf-8",
    "bom": "none",
    "newline_profile": "crlf",
    "immutable": true,
    "source_origin": "user_authored",
    "rights": {
      "status": "user_asserted",
      "evidence_ref": "rights:demo:1",
      "sharing_scope": "private",
      "redistribution_allowed": null,
      "network_egress_allowed": false,
      "binary_inspection": false
    },
    "target_versions": ["PowerMill 2025"],
    "runtime_modes": ["offline"],
    "dependencies": [],
    "imported_at": "2026-08-24T00:00:00Z",
    "extensions": {}
  },
  "capability_manifest": {
    "schema_version": 1,
    "contract": "cam.capability_manifest.v1",
    "manifest_id": "manifest:powermill:builtin:1",
    "manifest_version": "1.0.0",
    "manifest_hash": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "provider": {
      "provider_id": "cam-automation-studio",
      "display_name": "Built-in offline adapter",
      "claim_level": "fixture_verified"
    },
    "product": "powermill",
    "adapter_kind": "builtin",
    "authorization": {
      "status": "fixture_verified",
      "evidence_ref": "fixture:pm:minimal",
      "verified_by": "test-suite",
      "valid_until": null
    },
    "target_version_ranges": ["PowerMill 2025"],
    "execution_modes": ["offline", "fixture_dry_run"],
    "permissions": ["read:selected-files"],
    "node_types": [
      {
        "node_type": "cam.flow.start",
        "node_type_version": "1.0.0",
        "category": "control",
        "display_name": "Start",
        "ports": [
          {
            "port_id": "next",
            "direction": "output",
            "edge_kinds": ["control"],
            "type_ref": "cam.control",
            "cardinality": "many",
            "required": false,
            "constraints": {},
            "sensitivity": "public",
            "extensions": {}
          }
        ],
        "configuration_schema": {
          "type": "object",
          "additionalProperties": false
        },
        "static_risk_floor": "safe",
        "required_gates": ["recipe_review"],
        "forbidden_modes": ["live"],
        "parse_support": true,
        "preview_support": true,
        "recipe_projection_support": true,
        "fidelity": "semantic_round_trip"
      },
      {
        "node_type": "powermill.command.inspect",
        "node_type_version": "1.0.0",
        "category": "inspection",
        "display_name": "Inspect command",
        "ports": [
          {
            "port_id": "in",
            "direction": "input",
            "edge_kinds": ["control"],
            "type_ref": "cam.control",
            "cardinality": "one",
            "required": true,
            "constraints": {},
            "sensitivity": "public",
            "extensions": {}
          },
          {
            "port_id": "tolerance",
            "direction": "input",
            "edge_kinds": ["data"],
            "type_ref": "cam.length",
            "cardinality": "one",
            "required": true,
            "constraints": {
              "unit_dimension": "length",
              "minimum": 0
            },
            "sensitivity": "project_local",
            "extensions": {}
          }
        ],
        "configuration_schema": {
          "type": "object",
          "additionalProperties": false
        },
        "static_risk_floor": "review",
        "required_gates": [
          "recipe_review",
          "target_version_validation",
          "cam_simulation",
          "collision_check",
          "shop_approval"
        ],
        "forbidden_modes": ["live"],
        "parse_support": true,
        "preview_support": true,
        "recipe_projection_support": true,
        "fidelity": "token_exact_outside_edits"
      }
    ],
    "prohibited_operations": [
      "live_journal",
      "live_macro",
      "nc",
      "gcode",
      "clsf",
      "postprocess",
      "machine_control"
    ],
    "evidence": [
      {
        "kind": "fixture_test",
        "ref": "fixture:pm:minimal",
        "version": "1",
        "checked_at": "2026-08-24T00:00:00Z"
      }
    ],
    "revocation": null,
    "extensions": {}
  },
  "flow_graph": {
    "schema_version": 1,
    "contract": "cam.flowgraph.v1",
    "graph_id": "graph:pm:demo",
    "revision_id": "revision:pm:demo:1",
    "parent_revision_id": null,
    "product": "powermill",
    "target_versions": ["PowerMill 2025"],
    "entry_flow_id": "flow:main",
    "graph_parameters": [
      {
        "parameter_id": "parameter:tolerance",
        "name": "tolerance",
        "type_ref": "cam.length",
        "unit": "mm",
        "required": true,
        "default": 0.01,
        "constraints": {
          "minimum": 0
        },
        "evidence_mapping_ids": ["map:tolerance"],
        "review_status": "accepted"
      }
    ],
    "flows": [
      {
        "flow_id": "flow:main",
        "kind": "main",
        "name": "Main",
        "interface_ports": [],
        "parameter_ids": ["parameter:tolerance"],
        "nodes": [
          {
            "node_id": "node:start",
            "node_type": "cam.flow.start",
            "node_type_version": "1.0.0",
            "enabled": true,
            "risk": "safe",
            "review_status": "accepted",
            "port_contract_refs": ["cam.flow.start@1.0.0#next"],
            "bindings": [],
            "configuration": {},
            "source_mapping_ids": ["map:start"],
            "fidelity": "F2",
            "capability_ref": {
              "manifest_id": "manifest:powermill:builtin:1",
              "manifest_hash": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
              "node_type": "cam.flow.start",
              "node_type_version": "1.0.0"
            },
            "compatibility_status": "supported",
            "opaque": null,
            "extensions": {}
          },
          {
            "node_id": "node:inspect",
            "node_type": "powermill.command.inspect",
            "node_type_version": "1.0.0",
            "enabled": true,
            "risk": "review",
            "review_status": "accepted",
            "port_contract_refs": [
              "powermill.command.inspect@1.0.0#in",
              "powermill.command.inspect@1.0.0#tolerance"
            ],
            "bindings": [
              {
                "binding_id": "binding:tolerance",
                "target": {
                  "node_id": "node:inspect",
                  "port_id": "tolerance"
                },
                "kind": "graph_parameter",
                "literal": null,
                "graph_parameter_id": "parameter:tolerance",
                "source_output": null,
                "secret_ref": null,
                "source_mapping_ids": ["map:tolerance"],
                "extensions": {}
              }
            ],
            "configuration": {},
            "source_mapping_ids": ["map:inspect"],
            "fidelity": "F1",
            "capability_ref": {
              "manifest_id": "manifest:powermill:builtin:1",
              "manifest_hash": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
              "node_type": "powermill.command.inspect",
              "node_type_version": "1.0.0"
            },
            "compatibility_status": "supported",
            "opaque": null,
            "extensions": {}
          }
        ],
        "edges": [
          {
            "edge_id": "edge:start-inspect",
            "kind": "control",
            "source": {
              "node_id": "node:start",
              "port_id": "next"
            },
            "target": {
              "node_id": "node:inspect",
              "port_id": "in"
            },
            "condition": null,
            "priority": null,
            "source_mapping_ids": [],
            "extensions": {}
          }
        ],
        "source_mapping_ids": ["map:start", "map:inspect", "map:tolerance"]
      }
    ],
    "asset_refs": [
      {
        "asset_id": "asset:pm:demo",
        "asset_revision_id": "asset-rev:pm:demo:1",
        "content_hash": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      }
    ],
    "source_mappings": [
      {
        "mapping_id": "map:start",
        "asset_id": "asset:pm:demo",
        "asset_revision_id": "asset-rev:pm:demo:1",
        "source_digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "source_line": 1,
        "source_span": {
          "start_byte": 0,
          "end_byte": 0,
          "start_line": 1,
          "start_column": 0,
          "end_line": 1,
          "end_column": 0,
          "column_encoding": "unicode_scalar"
        },
        "target": {
          "flow_id": "flow:main",
          "node_id": "node:start"
        },
        "role": "control",
        "mapping_quality": "synthetic",
        "excerpt_hash": "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "extensions": {}
      },
      {
        "mapping_id": "map:inspect",
        "asset_id": "asset:pm:demo",
        "asset_revision_id": "asset-rev:pm:demo:1",
        "source_digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "source_line": 1,
        "source_span": {
          "start_byte": 0,
          "end_byte": 24,
          "start_line": 1,
          "start_column": 0,
          "end_line": 1,
          "end_column": 24,
          "column_encoding": "unicode_scalar"
        },
        "target": {
          "flow_id": "flow:main",
          "node_id": "node:inspect"
        },
        "role": "primary",
        "mapping_quality": "exact",
        "excerpt_hash": "sha256:cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        "extensions": {}
      },
      {
        "mapping_id": "map:tolerance",
        "asset_id": "asset:pm:demo",
        "asset_revision_id": "asset-rev:pm:demo:1",
        "source_digest": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "source_line": 1,
        "source_span": {
          "start_byte": 18,
          "end_byte": 22,
          "start_line": 1,
          "start_column": 18,
          "end_line": 1,
          "end_column": 22,
          "column_encoding": "unicode_scalar"
        },
        "target": {
          "flow_id": "flow:main",
          "node_id": "node:inspect",
          "property_path": "/bindings/binding:tolerance"
        },
        "role": "parameter",
        "mapping_quality": "exact",
        "excerpt_hash": "sha256:dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
        "extensions": {}
      }
    ],
    "capability_lock": [
      {
        "manifest_id": "manifest:powermill:builtin:1",
        "manifest_version": "1.0.0",
        "manifest_hash": "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
      }
    ],
    "required_gates": [
      "recipe_review",
      "target_version_validation",
      "cam_simulation",
      "collision_check",
      "shop_approval"
    ],
    "semantic_hash": "sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    "source_snapshot_hash": "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
    "layout": {
      "nodes": {}
    },
    "extensions": {}
  },
  "round_trip_report": {
    "schema_version": 1,
    "contract": "cam.round_trip_report.v1",
    "report_id": "roundtrip:demo:1",
    "graph_id": "graph:pm:demo",
    "revision_id": "revision:pm:demo:1",
    "asset_revision_id": "asset-rev:pm:demo:1",
    "original_content_hash": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "candidate_content_hash": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "original_semantic_hash": "sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    "candidate_semantic_hash": "sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    "status": "passed",
    "fidelity": "F0",
    "untouched_spans_exact": true,
    "opaque_spans_preserved": true,
    "candidate_reparsed": true,
    "changed_mapping_ids": [],
    "checks": ["byte_exact", "semantic_equal"],
    "issues": [],
    "candidate_artifact_ref": "memory:candidate:demo:1",
    "checked_at": "2026-08-24T00:00:01Z",
    "extensions": {}
  },
  "compatibility_report": {
    "schema_version": 1,
    "contract": "cam.compatibility_report.v1",
    "report_id": "compat:demo:1",
    "graph_id": "graph:pm:demo",
    "revision_id": "revision:pm:demo:1",
    "product": "powermill",
    "source_profile": {
      "target_version": "PowerMill 2025",
      "profile_hash": "sha256:1111111111111111111111111111111111111111111111111111111111111111"
    },
    "target_profile": {
      "target_version": "PowerMill 2025",
      "profile_hash": "sha256:1111111111111111111111111111111111111111111111111111111111111111"
    },
    "capability_lock_hash": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
    "checked_manifest_hashes": [
      "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    ],
    "status": "compatible",
    "node_results": [
      {
        "node_id": "node:inspect",
        "status": "supported"
      }
    ],
    "symbol_results": [],
    "selector_results": [],
    "coverage_gaps": [],
    "issues": [],
    "blocker_codes": [],
    "preview_eligible": true,
    "checked_at": "2026-08-24T00:00:02Z",
    "extensions": {}
  },
  "preview_plan": {
    "schema_version": 1,
    "contract": "cam.preview_plan.v1",
    "plan_id": "preview:demo:1",
    "graph_id": "graph:pm:demo",
    "revision_id": "revision:pm:demo:1",
    "flow_version_id": "flow-version:demo:1",
    "status": "succeeded",
    "execution_mode": "fixture_dry_run",
    "transport": "none",
    "target": {
      "product": "powermill",
      "target_version": "PowerMill 2025",
      "target_instance_id": "fixture:powermill:1",
      "project_id": "fixture-project:1",
      "project_snapshot_hash": "sha256:3333333333333333333333333333333333333333333333333333333333333333",
      "target_kind": "fixture"
    },
    "hashes": {
      "source_snapshot_hash": "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
      "semantic_hash": "sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
      "capability_lock_hash": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
      "reviewed_recipe_hash": null
    },
    "steps": [
      {
        "order": 1,
        "node_id": "node:inspect",
        "action": "powermill.command.inspect",
        "parameters": {
          "tolerance": {
            "value": 0.01,
            "unit": "mm"
          }
        },
        "source_mapping_ids": ["map:inspect", "map:tolerance"],
        "effects": ["unknown"],
        "risk": "review",
        "compatibility_status": "supported",
        "approval_required": true
      }
    ],
    "gate_results": [
      {
        "gate": "recipe_review",
        "status": "passed"
      },
      {
        "gate": "cam_simulation",
        "status": "not_run"
      },
      {
        "gate": "collision_check",
        "status": "required"
      },
      {
        "gate": "shop_approval",
        "status": "required"
      }
    ],
    "round_trip_report_id": "roundtrip:demo:1",
    "compatibility_report_id": "compat:demo:1",
    "diagnostics": [],
    "commands_sent": 0,
    "journal_executed": false,
    "macro_executed": false,
    "machine_output_count": 0,
    "created_at": "2026-08-24T00:00:03Z",
    "completed_at": "2026-08-24T00:00:04Z",
    "extensions": {}
  },
  "flow_version": {
    "schema_version": 1,
    "contract": "cam.flow_version.v1",
    "version_id": "flow-version:demo:1",
    "graph_id": "graph:pm:demo",
    "revision_id": "revision:pm:demo:1",
    "parent_version_ids": [],
    "semantic_hash": "sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    "artifact_hash": "sha256:4444444444444444444444444444444444444444444444444444444444444444",
    "source_snapshot_hash": "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
    "capability_lock_hash": "sha256:2222222222222222222222222222222222222222222222222222222222222222",
    "status": "reviewed_for_fixture",
    "author_ref": "user:demo",
    "message": "Initial offline review",
    "round_trip_report_id": "roundtrip:demo:1",
    "compatibility_report_id": "compat:demo:1",
    "created_at": "2026-08-24T00:00:05Z",
    "immutable": true,
    "extensions": {}
  }
}
```

## 15. 验收屏障

v1 实现只有同时满足以下条件才可合并：

1. 11 类对象均有 JSON Schema、DTO/validator、最小/错误/unknown-field
   golden fixtures。
2. 同一 fixture 重复至少 20 次，semantic hash、诊断排序、source diff、
   RoundTripReport 和 CompatibilityReport 字节一致。
3. NX 与 PowerMill parser import/dependency tests 证明互不解析对方语法。
4. F0-F3、opaque、source-line/source-span 双向定位和外部 source 漂移均有测试。
5. 缺 capability、撤销权限、版本不匹配、歧义 selector、跨 flow edge、
   类型/单位错误全部 fail closed。
6. AI 开/关不改变 graph、hash、validation、round-trip、compatibility 或
   preview 结果。
7. 安全测试证明 Journal/macro live、CAM transport、NC/G-code/CLSF、
   postprocess 和 machine control 全部结构化拒绝，且敏感 payload 不落盘。
8. PreviewPlan 永远满足 `transport=none`、`commands_sent=0`、
   `journal_executed=false`、`macro_executed=false`、
   `machine_output_count=0`。
9. 性能目标达标，或由 ADR 明确记录现场硬件、偏差、降级策略和不削弱校验的
   修复计划。
10. 浏览器验收证明 opaque/unsupported、权限撤销、目标版本/实例/项目、
    未完成门禁和 source mapping 始终可见。
