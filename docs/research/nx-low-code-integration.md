# Siemens NX 低代码集成预研

> 状态：研究草案，非实现规范
> 研究日期：2026-08-24
> 外部资料访问日期：2026-08-24
> 范围：NX Journal/NXOpen Python 的离线静态解释、低代码建模、版本兼容与授权适配
> 安全结论：本文不授权导入或执行 Journal，不授权修改真实零件，不生成、后处理或发送 NC/G-code，不构成生产批准

## 1. 结论摘要

1. **[已验证事实]** NX 2406 随安装提供
   `UGII_BASE_DIR\ugopen\pythonStubs`，Siemens 将其用于 VS Code/PyCharm
   IntelliSense；UF wrapped methods 不在 IntelliSense stubs 中。[S2]
2. **[研究假设]** 版本合同不能只有“NX 2406”字符串，应至少绑定
   `NX release/full version + Python minor/ABI + local stub digest + adapter version`。
   本地 stubs 是目标安装的可检查 API 证据，但不是完整运行时能力证明。
3. **[已验证事实]** `NXOpen.Session.GetSession()` 返回当前 NX session
   singleton；Siemens 的 NX remoting 仅支持 .NET/Java，不支持 Python。
   因而“外部 Python 自动附着任意已打开 NX 窗口”不能作为官方能力假设。[S1][S5]
4. **[研究假设]** 多实例必须显式绑定。Python 路径应采用“每个 NX 进程内的
   厂商授权适配器/导出器 + 外部只读协调器”，或只做离线 Journal/动作日志导入；
   不允许依靠前台窗口、窗口标题或猜测的端口选择目标实例。
5. **[已验证事实]** Journal 记录支持 journaling 的 Common API 调用，
   可能只有部分命令支持；原样回放会使用录制时的命名对象和参数。
   Siemens 将这种行为称为 Selection Stickiness，并专门建议改造
   `FindObject()` 调用和选择逻辑。[S3][S4]
6. **[已验证事实]** `NXObject.JournalIdentifier` 是“将被记录到 Journal 的
   标识符”，且 Siemens 明确说明它可能跨软件版本变化。[S1]
7. **[已验证事实]** NXOpen Builder 使用事务模型：配置在 `Commit` 前不应用到
   session；提交后或不再需要时应调用 `Destroy`。因此 Builder 生命周期是
   FlowGraph 的一等资源状态，不能当成可任意删除的录制噪声。[S1]
8. **[研究假设]** 第一阶段只应产出不可执行的 FlowGraph、参数候选、
   CompatibilityReport、DiffReport 和 dry-run preview。所有真实生产动作继续
   保持关闭，直到人工审阅、目标/项目/单位/MCS/stock/fixture/tool/holder 核验、
   CAM 仿真、过切/碰撞检查和车间批准均有独立证据。
9. **[研究假设]** 商业外挂只通过厂商授权的适配器、公开 NXOpen API、
   厂商提供的 Journal/动作日志导出或书面数据接口接入。严禁反编译、注入、
   hook 或解析受保护二进制内部协议。

## 2. 证据分级与研究边界

本文使用以下标签，避免把推断写成产品事实：

| 标签 | 含义 | 采用规则 |
| --- | --- | --- |
| **已验证事实** | Siemens 官方文档、Python 官方文档或当前仓库可直接观察的行为 | 可作为设计输入，但仍需按目标 NX 安装复验 |
| **仓库事实** | 当前仓库合同、代码或安全政策 | 约束本仓库实现，不外推为 Siemens 产品承诺 |
| **厂商主张** | 商业外挂厂商对版本、API、导出或安全能力的声明 | 必须保留原始链接/版本/日期；未通过联合验收前不能升级为已验证事实 |
| **研究假设** | 本文提出的接口、数据合同和迁移方法 | 需要 PoC、厂商确认或现场验收 |

本轮没有把任何具体商业外挂的宣传性兼容列表认定为事实。后续采购或集成时，
每一项兼容性都必须记录为 `vendor_claim`，并由目标版本上的可重复测试升级为
`verified`。

### 2.1 仓库不可突破的边界

- **[仓库事实]** NX 专属解析只允许位于
  `plugins/ug-cam-copilot/src/ugcam_ai/adapters/nx_journal.py`；不得为
  PowerMill 语法修改该解析器。[R1]
- **[仓库事实]** Journal 只能经上述 adapter 做 AST 静态解析，绝不 import、
  execute、eval 或 replay。[R2][R3]
- **[仓库事实]** 共享动作使用 `cam.*`；NX 独占动作使用 `nx.*`。
- **[仓库事实]** `ActivityEvent v1` 不删除、不重命名、不改变现有字段含义；
  新能力应采用独立 sidecar 合同，或经过合同审阅后只增加可选字段。[R1][R5]
- **[仓库事实]** 未实际运行的门禁只能是 `required`、`not_run` 或
  `unavailable`，不能写成 `passed`。[R4][R5]
- **[仓库事实]** 学习日志不得产生 machine-ready NC、G-code、postprocess
  或 machine-control 输出。[R1][R4]

## 3. Siemens 官方接入与版本合同

### 3.1 Journal 与 NXOpen 的官方接入面

**[已验证事实]** Siemens NX Open Programmer's Guide 2406 给出的 Journal
路径包括：

- 在 NX Developer 页签中录制 Journal；支持 journaling 的 NX 命令会记录实现
  该命令所需的 Common API 调用。
- 命令可能是完整或部分 journaling 支持，不能由“录制成功”推断动作完整。
- Journal 可以编辑；原生回放支持 Python、VB.NET 和 C# .NET（Windows）。
- Journal 不允许调用其他 Journal；复杂应用应考虑正式应用形态。[S3]

由此得到的接入优先级为：

| 优先级 | 接入面 | 允许范围 | 证据等级 |
| --- | --- | --- | --- |
| P0 | 用户明确选择的 `.py` Journal 导出 | 本地、只读、AST 静态解析 | 已验证可行 |
| P0 | 厂商导出的结构化动作日志 | 按声明 schema 校验后导入 | 研究假设，需厂商样本 |
| P1 | 厂商签名的 NX 进程内适配器 | 只读 session/part/CAM 元数据与结构化事件导出 | 研究假设，需授权和目标版本 PoC |
| P1 | 正式 .NET/Java remoting 适配器 | 明确 session 的只读调用；未来才讨论受控 dry-run | 官方存在该技术面，但本方案未验证 |
| 禁止 | 外部 Python remoting 任意 GUI session | 不设计、不声称支持 | 官方明确不支持 Python remoting |
| 禁止 | 二进制反编译、注入、hook、私有协议猜测 | 永久禁止 | 用户与仓库安全边界 |

### 3.2 Python stubs 与目标版本

**[已验证事实]**

- NX 2406 文档要求 IDE 的 Stub Path 指向
  `UGII_BASE_DIR\ugopen\pythonStubs`；该目录在构建 `apiwrap` 后生成。[S2]
- UF wrapped methods 不可用于该 IntelliSense 能力，因此 stubs 覆盖不等于
  NXOpen + UF 的完整能力覆盖。[S2]
- NX 安装中的 Python extension modules 位于
  `<NX install directory>\NXBIN\python`。[S9]
- `Session.ReleaseNumber` 提供 release/MU 信息；NX Open Python Reference Guide
  2406 还为 API 成员提供创建版本、弃用和 license requirement 信息。[S1]
- Siemens KB000181221 展示了外部 Python batch 环境通过
  `PATH/PYTHONPATH` 引用目标 NX 安装，并从 session 读取完整版本的官方示例；
  该文章并不证明可附着任意现有 GUI 实例。[S10]

**[研究假设]** 建议将每套目标 API 定义为不可变 `NxApiContract`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `contract_id` | string | `nx-api:<release>:<stub_sha256_prefix>` |
| `release_number` | string | 例如 functional release 与 MU；不得只存营销名称 |
| `full_version` | string/null | 适配器可验证时填写 |
| `python_minor` | string/null | NX 内嵌/外部解释器的目标 minor |
| `python_abi` | string/null | 可验证时填写；未知必须为 null |
| `stub_root_alias` | string | 脱敏路径别名，不持久化现场绝对路径 |
| `stub_digest` | string | 对选定 stubs 清单和内容做规范化 SHA-256 |
| `stub_file_count` | integer | 审计和变更检测 |
| `stub_origin` | string | `nx_installation`、`vendor_bundle`；默认只信前者 |
| `apiwrap_generated` | boolean/null | 有证据才填写 |
| `coverage_exclusions` | array[string] | 至少记录 `uf_wrapped_methods_not_in_intellisense` |
| `captured_at` | timestamp | 合同快照时间 |
| `verified_by` | string/null | 责任人或自动核验器版本 |

版本核验必须 fail closed：

1. Journal/动作日志声明或推断的 source release 只作为证据，不作为目标版本。
2. 目标 NX instance 报告的 release/full version 必须与选定合同匹配。
3. 本地 stub digest 与已审阅合同不一致时重新生成 CompatibilityReport。
4. API symbol、签名或 enum 缺失时不得猜测兼容。
5. UF 包装、动态调用和 vendor-only API 统一标为 `unknown` 或 `unsupported`。

### 3.3 session、零件与多实例绑定

**[已验证事实]**

- `Session.GetSession()` 获得 NX session singleton。[S1]
- NX remoting 可连接本机或远程 NX session，但使用 .NET remoting/Java RMI，
  不支持 C++ 或 Python。[S5]
- 在 UI session 中混合 UI 事件和远程自动化事件可能导致未定义行为；Siemens
  建议不要在一个 NX 内同时运行多个 remoting server。[S5]
- NX 的 Python 脚本默认可运行在隔离 subinterpreter 中；使用某些带 C 线程的
  第三方扩展可能导入失败、挂起或崩溃。`# nx: threaded` 会改为主解释器线程，
  Siemens 也明确要求不要无必要使用。[S7]

**[研究假设]** 外部控制器不能把 `GetSession()` 理解为“按 PID 选择任意
session”。目标必须先由已授权的进程内适配器声明，再由控制器显式选择。

建议 `SessionTargetBinding` 字段如下：

| 字段 | 必需 | 说明 |
| --- | --- | --- |
| `binding_id` | 是 | 一次绑定的不可变 ID |
| `target_instance_id` | 是 | 运行期稳定、不透明；复用仓库 InstanceDescriptor |
| `pid` / `process_started_at` | 是 | 防 PID 复用；窗口标题不参与身份 |
| `window_handle` | 否 | 仅交互实例；不得单独作为身份 |
| `adapter_connection_id` | 是 | 适配器端的连接/租约 ID |
| `binding_method` | 是 | `in_process_adapter`、`dotnet_remoting`、`java_rmi`、`offline` |
| `release_number` / `full_version` | 是/否 | 必须与 `NxApiContract` 对齐 |
| `is_batch` | 是 | 来自正式 API 或适配器证明 |
| `work_part_ref` / `display_part_ref` | 否 | 脱敏、稳定引用；未知时阻断需要零件的步骤 |
| `project_id` / `snapshot_id` / `snapshot_hash` | 是 | dry-run 必须绑定测试副本或不可变快照 |
| `teamcenter_context` | 否 | 只存脱敏上下文与授权状态，不存凭据 |
| `units` / `mcs_ref` / `cam_setup_ref` | 否 | 与配方前置条件逐项核验 |
| `machine_kit_ref` | 否 | 仿真阶段使用；MVP 不加载 |
| `observed_at` / `lease_expires_at` | 是 | 避免复用过期 session |
| `attestation_hash` | 是 | 对关键绑定字段做语义哈希 |

绑定后仍须在 transport 前和响应后重新核验
`product + release + instance + project/snapshot + recipe_hash`。任何一项变化都使
任务失效。

## 4. 商业外挂与厂商授权适配器

### 4.1 可接受的接入路径

**[研究假设]** 商业外挂按以下顺序接入：

1. 厂商正式提供的公开 API/SDK 或签名 NXOpen 应用。
2. 厂商明确授权导出的 Journal。
3. 厂商定义并版本化的动作日志/审计日志。
4. 厂商提供的离线项目报告或 CapabilityManifest。

下列路径不接受：

- 反编译 DLL/EXE、绕过签名或授权检查；
- 注入 NX/外挂进程、读取私有内存、hook 私有函数；
- 从加密或受保护协议猜测动作；
- 通过 UI 像素/前台窗口推断目标对象后直接执行；
- 将 vendor claim 自动映射成已验证 NXOpen capability。

### 4.2 CapabilityManifest 建议合同

`CapabilityManifest` 是本产品的注册合同，不是 Siemens 官方格式。
建议作为独立 sidecar，不修改 `ActivityEvent v1` 既有字段。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_version` | integer | 首版固定 `1` |
| `manifest_id` | string | 全局唯一、不可变 |
| `adapter_id` / `adapter_version` | string | 适配器身份与语义版本 |
| `vendor` / `product_name` | string | 厂商与商业插件名 |
| `vendor_authorization` | object | 授权依据、合同/工单引用、有效期、核验人 |
| `distribution_signature` | object/null | 签名主体、指纹、核验状态 |
| `integration_kind` | enum | `journal_export`、`action_log_export`、`nxopen_in_process`、`dotnet_remoting`、`java_rmi` |
| `public_api_references` | array[object] | URL、文档版本、访问日期 |
| `supported_nx_versions` | array[string] | 先记录 `vendor_claim`，逐版本验收后升级 |
| `supported_python` | object/null | minor/ABI/subinterpreter/threading 声明 |
| `nx_api_contracts` | array[string] | 已验证 stub contract ID |
| `input_contracts` / `output_contracts` | array[object] | mime/schema/version/最大尺寸 |
| `capabilities` | array[object] | 名称、mode、证据等级、前置条件、license |
| `execution_modes` | array[string] | MVP 只允许 `offline`、`read_only`、`fixture_dry_run` |
| `target_binding` | object | 支持的 binding method 与防串实例策略 |
| `threading_model` | object | main/subinterpreter/UI thread 约束 |
| `permissions` | array[string] | 文件、进程、网络、项目数据等最小权限 |
| `data_handling` | object | 脱敏、保留期、出站策略 |
| `prohibited_operations` | array[string] | Journal execute、save、NC、postprocess、machine control 等 |
| `evidence` | array[object] | `vendor_claim`、`verified_test`、证据链接/哈希 |
| `self_test` | object | 只读检查项、最后结果、环境摘要 |

### 4.3 商业能力的状态机

`declared -> vendor_claim -> contract_reviewed -> fixture_verified ->
target_version_verified -> site_accepted`

- `declared/vendor_claim` 不能进入 transport。
- `fixture_verified` 只证明 schema 和离线行为。
- `target_version_verified` 只证明指定 NX 版本和测试副本上的能力。
- `site_accepted` 仍不代表配方或加工结果被批准。
- 厂商授权撤销、签名变化、版本变化或 stubs digest 变化时，状态退回
  `contract_reviewed` 或更低。

## 5. Journal AST 到可视化 FlowGraph

### 5.1 当前基线

**[仓库事实]** 当前 `nx_journal.py`：

- 使用 Python `ast.parse`，不 import 或执行 Journal；
- 识别 NXOpen import/alias、调用、属性赋值、Builder
  create/set/commit/destroy、Undo Mark、`FindObject`、try/handler/finally、
  raise 和动态 `if/for/while`；
- 对非信任调用、动态表达式、危险输出和不完整 Builder 生命周期生成诊断；
- 产出 `ActivityEvent`，保留 `source_file/source_line`，但不是完整控制流图。

这是一条安全的静态基线，不等于完整 Python 解释器。后续 FlowGraph 分析仍必须
留在同一 NX parser 边界内，或由该 parser 输出的中立 IR 构建；不得在共享服务中
重新解释 NX 语法。

### 5.2 FlowGraph 顶层合同

**[研究假设]**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schema_version` | integer | 首版固定 `1` |
| `graph_id` / `graph_hash` | string | 图身份与语义哈希 |
| `product` | string | 固定 `nx` |
| `source_artifact` | object | 原文 digest、编码、脱敏路径、source release |
| `parser_contract` | object | parser 版本、Python AST grammar、限制 |
| `target_api_contract_id` | string/null | 未选择目标版本时为 null |
| `entry_nodes` / `exit_nodes` | array[string] | 支持多函数/多入口 |
| `nodes` | array[FlowNode] | 控制、动作、资源、诊断节点 |
| `edges` | array[FlowEdge] | 控制流和数据流 |
| `parameters` | array[ParameterSlot] | 参数候选，不是已批准参数 |
| `preconditions` | array[Precondition] | 单位/MCS/stock/tool/holder 等 |
| `selectors` | array[ObjectSelector] | 选择器证据与风险 |
| `unsupported_regions` | array[object] | 原样保留的不可解释区域 |
| `source_map` | array[SourceMapEntry] | 双向定位 |
| `compatibility_report_id` | string/null | 目标版本检查结果 |
| `diagnostics` | array[object] | severity/code/evidence/remediation |

`FlowNode` 至少包含：

`node_id`、`kind`、`action`、`risk`、`review_status`、`source_span`、
`api_symbol`、`arguments`、`defines`、`uses`、`builder_id`、
`selector_id`、`parameter_refs`、`precondition_refs`、`unsupported`。

`FlowEdge.kind` 使用有限枚举：

- `next`、`true`、`false`；
- `loop_body`、`loop_back`、`loop_exit`；
- `exception`、`finally`；
- `defines`、`uses`、`configures`、`commits`、`destroys`；
- `selects`、`guards`、`depends_on`。

### 5.3 控制流映射

| Python AST | FlowGraph 表达 | 安全语义 |
| --- | --- | --- |
| `Module`/函数 | `entry -> scope -> exit` | 不自动调用未证明的函数 |
| 顺序语句 | `next` edge | 保留原始顺序与 source span |
| `if` | branch + true/false 子图 + merge | 条件不求值；两分支都是静态可能性 |
| `for/while` | loop header/body/back/exit | 不展开迭代次数；动态 iterable 为 review |
| `try/except/finally` | protected region + exception/finally edge | 不推断具体异常是否发生 |
| `raise` | terminal/exception edge | 保留表达式但不构造异常 |
| 函数调用 | call node | 只解析受信 NXOpen alias 或白名单纯内建 |
| 动态调用 | unsupported call node | `blocked`，不做字符串/反射解析 |

当前 MVP 不做跨函数精确调用图、SSA、别名完备分析或异常类型证明。简单局部
binding 可以连 `defines/uses`；遇到闭包、动态别名或反射时降级为
`unknown_definition`，而不是猜测对象。

### 5.4 Builder 生命周期

**[已验证事实]** Siemens 将 Builder 定义为创建/编辑对象的事务对象：
`Commit` 前变更不应用到 session；提交后应 `Destroy`，也可不提交直接
`Destroy` 以放弃编辑。[S1]

**[研究假设]** FlowGraph 使用以下资源状态机：

`created -> configured* -> previewed? -> committed | abandoned -> destroyed`

规则：

- create 节点必须定义唯一 `builder_id` 和 factory API；
- property/`Set*` 节点通过 `configures` edge 关联 Builder；
- `Commit` 是语义边界，不因“看似录制噪声”删除；
- `Destroy` 是资源释放边界，不推断为撤销已提交对象；
- 未提交、未销毁、重复提交、destroy 后配置等都产生 blocker；
- `GetCommittedObjects` 可能返回多个对象，不能只假设单一结果；
- `ShowResults/PreviewBuilder` 只能记录为 preview 能力证据，不能自动等同
  产品级 dry-run。

### 5.5 Undo Mark

**[已验证事实]** NXOpen Session API 暴露 Undo Mark 数据、可见性和最新可见
Undo Mark 等概念。[S1]

**[研究假设]** Undo Mark 在图中是事务/审计 envelope，不是加工正确性证明：

- `SetUndoMark` 打开一个 `undo_scope_id`；
- rename/delete 关联同一 scope；
- scope 内的 mutating 候选以 `guarded_by` edge 关联；
- mark 缺失、嵌套不配对或动态 ID 进入 diagnostics；
- 不把 Undo Mark 自动解释成数据库式原子事务；
- 不把“可 undo”解释成 production rollback；
- dry-run preview 只显示预期 scope，不调用 Undo API。

### 5.6 `FindObject` 与稳定对象选择

**[已验证事实]**

- 原样 Journal 回放使用录制时的命名对象和参数。[S4]
- Siemens 明确描述 Selection Stickiness，并演示替换具体
  `FindObject("SKETCH(4)")` 和曲线名称的方法。[S4]
- `NXObject.JournalIdentifier` 可能跨 release 改变。[S1]

因此任何录制的 `FindObject` identifier 默认都是 `fragile=true`、
`resolved=false`，不得声明唯一。

建议 `ObjectSelector`：

| 字段 | 说明 |
| --- | --- |
| `selector_id` | 图内稳定 ID |
| `recorded_identifier` | 原始值；动态表达式则为 null |
| `recorded_source_span` | 精确行列/字节范围 |
| `object_kind` / `owning_part_ref` / `component_path` | 选择上下文 |
| `strategy` | `governed_name`、`user_attribute`、`pmi`、`geometry_query`、`interactive_review` |
| `criteria` | 类型化条件，禁止自由文本执行 |
| `units` / `tolerance` | 几何查询必须显式 |
| `expected_cardinality` | 默认 `exactly_one` |
| `ambiguity_policy` | `reject_zero_or_many` |
| `verification_query` | 只读、allowlisted 查询描述 |
| `unique_match_claimed` | 未验证前固定 false |
| `resolution_status` | `unresolved`、`candidate`、`verified`、`ambiguous`、`missing` |
| `evidence_refs` | Journal、快照、人工审阅证据 |

稳定策略的优先顺序不是绝对的。受治理的唯一属性通常优于自由命名；PMI 只有在
现场工艺把它作为权威语义时可用；几何签名必须绑定单位、公差、零件版本和装配
上下文，并在 0 或多匹配时拒绝。不得静默回退到第一个对象。

### 5.7 参数、单位与 CAM 前置条件

参数分为：

- `literal`：录制常量；
- `observed_variant`：至少两个独立 session 中同位置变化的值；
- `reference`：局部变量或对象引用；
- `expression`：未求值表达式；
- `secret/path`：需要脱敏或本地解析的值；
- `unsupported`：无法静态类型化。

只有 `observed_variant` 是参数候选，不代表已批准。每个 `ParameterSlot`
建议包含：

`name`、`value_type`、`dimension`、`units`、`default`、`samples`、
`constraints`、`source_spans`、`confidence`、`approval_status`、
`target_api_binding`、`normalization`、`round_trip_policy`。

FlowGraph 顶层必须显式表达以下前置条件，缺失即 `required`：

| 前置条件 | 最小证据 |
| --- | --- |
| NX release/full version | session 证明 + stub contract |
| instance/project/part | 显式 binding + 测试副本/快照 hash |
| work part/display part | 稳定引用与一致性检查 |
| part units/parameter units | 单位系统、维度、转换策略和范围 |
| MCS | 稳定选择器、原点/轴向、单位、所属 CAM setup |
| stock/blank | 类型、选择器、快照引用 |
| fixture/check geometry | 选择器、用途、装配上下文 |
| tool | 工具库身份、revision、几何摘要 |
| holder | holder 身份、revision、装配关系 |
| CAM setup/method/program/geometry group | 稳定选择器和父子关系 |
| machine kit | 仿真阶段的已批准 kit/revision |

MVP 只记录和核验这些前置条件，不自动创建、替换或修复它们。

### 5.8 Unsupported construct

下列结构默认产生 `UnsupportedRegion`，保留原文和 source map，不执行、不删除：

- `eval`、`exec`、动态 import、`getattr`/`setattr` 驱动的 NXOpen 调用；
- monkey patch、元类、运行时生成代码、未知 decorator；
- C 扩展副作用、线程/async 并发、UI callback；
- 无法解析的 closure/nonlocal/global 别名；
- 动态 `FindObject`、动态 API path、无法确定目标 Builder 的调用；
- `match`、comprehension、generator、context manager 等尚未进入 MVP 语义集；
- 受保护的 vendor module 调用和非公开 API。

字段至少包含
`region_id`、`ast_kind`、`reason_code`、`source_span`、`source_excerpt_hash`、
`blocking_scope`、`preservation_mode=opaque`、`review_required=true`。

## 6. Source mapping、round-trip 与兼容报告

### 6.1 Source mapping

**[已验证事实]** Python AST 节点提供行列及可选结束位置，
`ast.get_source_segment` 可按这些位置取得原始 source segment。[P1]

建议 `SourceMapEntry`：

| 字段 | 说明 |
| --- | --- |
| `source_artifact_id` / `source_digest` | 不可变原文身份 |
| `source_file_alias` / `encoding` | 脱敏别名与编码 |
| `start_line/start_col/end_line/end_col` | AST 位置 |
| `start_byte/end_byte` | 处理 UTF-8 列偏移和精确 patch |
| `ast_path` / `ast_kind` | 如 `Module.body[3].value` |
| `source_excerpt_hash` | 防止原文漂移 |
| `flow_node_id` / `event_ref` | 指向图节点和 ActivityEvent |
| `generated_artifact_id/generated_span` | 仅对不可执行 preview/recipe |
| `transformation_id` | 参数化或迁移建议身份 |

原始 Journal 保持本地、只读、内容哈希固定。显示层可用脱敏路径，但审计包必须能
在授权环境中重新解析同一字节内容。

### 6.2 Round-trip

**[已验证事实]** Python 官方文档说明 `ast.unparse()` 产生的是可重新解析为
等价 AST 的代码，但不保证与原始代码文本相同。[P1]

因此 MVP 不承诺“Journal 文本精确 AST 往返”，而采用两条路径：

1. **证据往返**：
   `original bytes -> AST -> FlowGraph -> source map -> original spans`。
   原文不修改，要求每个支持节点能回到同一 source digest 和 span。
2. **参数 patch 往返**：
   仅对已白名单的 literal span 生成 patch proposal；在内存副本上应用，
   重新 `ast.parse`，比较未修改子图和目标 API bindings，再输出 diff。
   不把 patch 作为可执行 Journal，也不写回原文件。

验收条件：

- 不受支持区域字节级保留；
- 非参数区域 excerpt hash 不变；
- 重新解析无语法错误；
- FlowGraph 的控制边、Builder 生命周期和 selector 引用不意外变化；
- 语义 hash 变化只来自已声明参数或迁移；
- 任一条件失败即 `round_trip_status=failed`。

### 6.3 CompatibilityReport

建议字段：

| 字段 | 说明 |
| --- | --- |
| `report_id` / `schema_version` | 报告身份 |
| `source_artifact_id` / `graph_hash` | 输入证据 |
| `source_release` | 已知或 unknown |
| `target_api_contract_id` | 明确目标 stubs 合同 |
| `target_instance_attestation` | 有 live/fixture 目标时填写 |
| `required_symbols` | symbol、observed signature、source span |
| `symbol_results` | `supported`、`missing`、`signature_changed`、`deprecated`、`internal`、`unknown` |
| `license_requirements` | API reference 可确认的 license；未知不推断 |
| `enum_results` | enum/member 差异 |
| `vendor_capability_results` | manifest claim 与 verified 状态 |
| `coverage_gaps` | UF wrapped、动态调用、stub 缺失等 |
| `migration_actions` | 只读建议与 source refs，不含可执行代码 |
| `overall_status` | `compatible`、`needs_review`、`incompatible` |
| `blockers` / `warnings` | 结构化诊断 |

`compatible` 仅表示所需静态 API 在目标合同中可解析，不表示运行成功、几何正确、
刀路正确、仿真通过或生产批准。

## 7. 静态解释、参数化、diff、preview 与版本迁移

### 7.1 分阶段管线

```text
选定 Journal / 厂商导出动作日志
  -> 不可变 SourceArtifact
  -> NX parser AST 静态解释
  -> ActivityEvent v1 + FlowGraph sidecar
  -> 多 session 参数候选
  -> 人工审阅 Recipe
  -> target stubs CompatibilityReport
  -> fixture/offline dry-run preview
  -> DiffReport（所有生产门禁仍 required/not_run）
```

任何阶段都没有 Journal import/play/run，也没有 NX part save、toolpath generation、
postprocess、NC/G-code 或 machine control。

### 7.2 参数化

- 至少两个独立 session 才能把变化值标为 `observed_variant`；
- 相同值可能仍需参数化，但必须由人工标注，不能从单次录制推断；
- feeds、speeds、tolerance、stepover、tool number 等制造参数默认高风险；
- 所有数值必须有 dimension、units、范围和来源；
- selector、路径、项目名不能作为普通字符串参数绕过专门合同；
- 参数变更必须改变 recipe semantic hash。

### 7.3 Diff

Diff 至少分四层：

1. source diff：原始 span 与参数 patch proposal；
2. graph diff：节点、控制边、Builder/Undo/selector/precondition 变化；
3. API diff：目标 stubs 的 symbol/signature/enum/license 变化；
4. preview diff：fixture 快照上的计划变化，不是 NX 真实对象变化。

`succeeded` 只表示离线计算完成；未运行的仿真、过切、碰撞、机床仿真和车间批准
必须继续显示 `not_run` 或 `required`。

### 7.4 Dry-run preview

MVP 的 preview 是“计划预览”，内容包括：

- 目标版本合同与目标绑定摘要；
- 将选择的对象及 selector 风险；
- 参数值、单位和边界；
- Builder/Undo scope 的预期顺序；
- 可能创建/修改的对象类型；
- blocked/unsupported 节点；
- 所有未满足门禁。

preview 不调用 `Commit`、`ShowResults`、`GenerateToolPath` 或任何 live NX API。
仓库现有 fixture transport 可作为响应合同测试，但不能表述成真实 NX dry-run。

### 7.5 版本迁移

迁移只生成 `MigrationPlan`：

- 对比 source 与 target `NxApiContract`；
- 列出 symbol/signature/enum/created/deprecated/license 差异；
- 保留每项变化的 source spans 和官方/本地 stub 证据；
- 对明确的 rename/signature mapping 给出候选，不自动 patch；
- 动态/UF/vendor-only 调用保持 blocked；
- selector 在新 release 必须重新验证，因为 JournalIdentifier 可能变化；
- 由人工批准后才可形成新的 recipe version/hash；
- 迁移后的目标版本仍须重新走 fixture、仿真、碰撞和现场批准。

## 8. 现场安全门禁

以下门禁不可由学习器、FlowGraph 或 CompatibilityReport 清除：

| 门禁 | MVP 默认 | 最低证据 |
| --- | --- | --- |
| identified human recipe review | `required` | reviewer、时间、recipe hash |
| exact release/instance/project snapshot | `required` | binding attestation |
| units/MCS/stock/fixture/tool/holder | `required` | 每项稳定引用与核验记录 |
| stable selector review | `required` | 每个 selector 唯一匹配证据 |
| unsupported/blocked resolution | `required` | 0 个未处置 blocker |
| offline/fixture preview | `not_run` | DiffReport |
| CAM simulation | `not_run` | 目标 NX 与项目副本证据 |
| gouge/collision checks | `not_run` | 保留的检查报告 |
| machine simulation | `not_run` | 已批准 machine kit |
| shop approval | `required` | 现有放行流程中的批准记录 |
| postprocessor/NC release | `out_of_scope` | 永远留在车间既有控制之外 |

任何缺失证据都是 `required/not_run/unavailable`，绝不默认通过。测试项目也必须是
版本化副本，不静默覆盖 active part。

## 9. MVP

### 9.1 MVP 包含

1. 用户明确选择的 NXOpen Python Journal 和厂商导出的结构化动作日志。
2. 仅经既定 `nx_journal.py` 边界做 AST 静态解析。
3. SourceArtifact、source map、ActivityEvent v1 和 FlowGraph sidecar。
4. 顺序、函数、简单分支/循环、try/except/finally、调用、赋值的保守控制流。
5. Builder 生命周期、Undo Mark、`FindObject` 风险和 unsupported regions。
6. 两个以上 session 的参数候选与来源引用。
7. 单位/MCS/stock/fixture/tool/holder 等前置条件清单。
8. 选定本地 NX installation stubs 的版本合同和 CompatibilityReport。
9. CapabilityManifest 元数据注册、vendor claim 分级和签名/授权记录。
10. 不可执行的参数 patch proposal、graph/API diff 和 offline/fixture preview。
11. 全部生产门禁以未满足状态显示。

### 9.2 MVP 非目标

- 不连接或执行 live Journal；
- 不修改、保存或关闭真实零件；
- 不自动附着前台/任意 NX 实例；
- 不实现 Python/.NET/Java remoting server；
- 不实现完整 Python 解释器、精确别名分析或任意代码生成；
- 不自动消除 Selection Stickiness；
- 不自动创建 MCS、stock、fixture、tool、holder 或 CAM operation；
- 不生成刀路、CLSF、postprocess、NC、G-code 或 machine command；
- 不绕过 NX/外挂授权、签名、license 或 Teamcenter 控制；
- 不反编译受保护二进制；
- 不把 vendor claim、fixture 成功或 API symbol 存在当成生产正确性。

## 10. 风险

| 风险 | 可能性/影响 | 控制 |
| --- | --- | --- |
| stubs 与目标运行时/MU 不一致 | 中/高 | full version + digest + target attestation；不一致即重报 |
| stubs 不覆盖 UF wrapped methods | 高/中 | 明确 coverage gap；UF 调用默认 unknown/blocked |
| Journal 只部分支持命令 | 高/高 | 记录 journaling coverage；缺口保持 unsupported |
| 动态 Python 隐藏副作用 | 中/高 | 不求值、不 import；opaque region + resource limit |
| AST 恶意/超大输入导致资源耗尽 | 中/高 | 文件大小、节点数、深度、耗时限制；隔离解析进程 |
| Selection Stickiness/JournalIdentifier 漂移 | 高/高 | 每个 selector 独立核验；0/多匹配拒绝 |
| Builder/Undo 被错误“去噪” | 中/高 | 生命周期与事务 envelope 一等建模 |
| 单位/MCS/stock/tool/holder 缺失 | 高/高 | 显式 precondition；缺一项即 required |
| 多实例串线 | 中/极高 | PID start time + instance ID + lease + attestation；禁止前台猜测 |
| UI 与 remote 自动化交错 | 中/高 | MVP 不做 live；未来 per-instance 串行和操作员占用门 |
| Python subinterpreter/C 扩展崩溃 | 低/高 | 适配器最小依赖；threading model 入 manifest |
| 商业外挂接口变更/授权撤销 | 中/高 | vendor contract/version/signature/expiry；状态回退 |
| Teamcenter/许可/签名不满足 | 中/高 | manifest license 字段；现场授权与签名验收 |
| source path/项目数据泄露 | 中/高 | 路径脱敏、本地保留、禁止未授权出站 |
| preview 被误认为生产批准 | 中/极高 | UI/合同分离状态；所有生产门禁持续可见 |

Python 官方文档还提醒，复杂 AST/`literal_eval` 输入可能导致内存、CPU 或栈资源
耗尽；因此“不执行代码”仍不等于“不需要输入资源限制”。[P1]

## 11. 验收矩阵

| ID | 验收项 | 通过证据 | 失败条件 |
| --- | --- | --- | --- |
| A01 | Journal 从未 import/execute/eval | 审计字段均为 false；静态测试拦截执行路径 | 任意运行期加载 Journal |
| A02 | 解析边界正确 | 所有 NX 语法只由既定 `nx_journal.py` 产生 | 共享/PowerMill 模块解释 NX 语法 |
| A03 | ActivityEvent 向后兼容 | 旧 fixture 完整往返；无字段删除/重命名 | 旧 JSONL 不能导入或含义变化 |
| A04 | source mapping 精确 | 节点回到相同 digest/span/excerpt hash | 行列、字节或原文不一致 |
| A05 | 控制流保守 | 分支/循环/异常边齐全，条件未求值 | 静默选择单一分支 |
| A06 | Builder 生命周期 | create/configure/commit/destroy 及异常诊断 | 未完成生命周期仍被标安全 |
| A07 | Undo Mark 保留 | scope 和关联动作可追踪 | 当作噪声删除或当作生产回滚 |
| A08 | 稳定 selector | 所有录制 ID 默认 unresolved；0/多匹配拒绝 | 首个匹配或名称猜测自动通过 |
| A09 | 参数有证据 | 至少两 session 或人工标注；单位/范围/source refs 齐全 | 单次变化被自动批准 |
| A10 | 版本合同 | release/full version/Python/stub digest 对齐 | 仅凭“NX 2406”放行 |
| A11 | API 兼容报告 | 每个 API symbol/signature 有状态和 source ref | 缺失/动态 API 被猜成 supported |
| A12 | UF coverage gap | UF wrapped 调用明确 unknown/unsupported | 因 stubs 无报错而认定支持 |
| A13 | 多实例绑定 | 明确 instance + PID start + lease + snapshot + hash | 使用前台窗口或标题选目标 |
| A14 | CapabilityManifest | 授权、签名、版本、claim/evidence、禁用能力齐全 | vendor claim 直接进入 transport |
| A15 | round-trip | 非参数区不变、重解析成功、图差异可解释 | `ast.unparse` 被当作文本精确往返 |
| A16 | unsupported 保留 | opaque source span、原因和 blocker 可见 | 删除、执行或静默跳过 |
| A17 | preview 无副作用 | commands sent=0、Journal executed=false、无 NX 写入 | Commit/save/generate/post 调用 |
| A18 | 安全门禁 | 未运行项为 required/not_run/unavailable | 缺证据项显示 passed |
| A19 | 无 machine-ready 输出 | 输出扫描与结构化拒绝记录 | NC/G-code/CLSF/postprocess 出现在产物 |
| A20 | 来源分级 | 每项主张带事实/vendor claim/假设标签和链接 | 宣传或推断被写成事实 |

## 12. 待验证问题

1. 目标工厂实际使用哪些 NX functional release/MU、Python minor、Teamcenter
   模式和 CAM 模块 license？
2. 目标安装的 `pythonStubs` 是否完整、由何流程生成、digest 是否能在部署时稳定
   复现？
3. 商业外挂是否提供正式 SDK、签名应用、Journal 导出或动作日志 schema？
4. 厂商是否书面授权事件导出、字段持久化和版本兼容测试？
5. 每种 CAM object 的受治理唯一属性/PMI/命名规范是什么？
6. 现场 MCS、stock、fixture、tool、holder 和 machine kit 的权威系统分别是什么？
7. 需要覆盖哪些动态 Python 构造；哪些可以明确留在 unsupported？
8. .NET/Java remoting 是否真的需要进入后续阶段，还是进程内只读导出已经足够？

## 13. 参考资料

### Siemens 官方资料

- **[S1]** Siemens, [NX Open Python Reference Guide, NX 2406 Series](https://docs.sw.siemens.com/en-US/doc/209349590/PL20231101866122454.custom_api.nxopen_python_ref)，
  访问日期 2026-08-24。核验 `Session`、`Builder`、`NXObject`、
  `JournalIdentifier`、API 创建/弃用/license 元数据。
- **[S2]** Siemens, [Set up Python Intellisense, NX 2406 Series](https://docs.sw.siemens.com/en-US/doc/209349590/PL20231101866122454.nxopen_prog_guide/xid2250951)，
  访问日期 2026-08-24。核验 `UGII_BASE_DIR\ugopen\pythonStubs`、
  `apiwrap` 与 UF wrapped method 限制。
- **[S3]** Siemens, [Journals, NX 2406 Series](https://docs.sw.siemens.com/en-US/doc/209349590/PL20231101866122454.nxopen_prog_guide/journals)，
  访问日期 2026-08-24。核验录制、编辑、回放、完整/部分支持与 Journal 限制。
- **[S4]** Siemens, [Turning Journals Into Applications, NX 2406 Series](https://docs.sw.siemens.com/en-US/doc/209349590/PL20231101866122454.nxopen_prog_guide/turning_journals_into_apps)，
  访问日期 2026-08-24。核验命名对象、参数、Selection Stickiness 与
  `FindObject()` 改造。
- **[S5]** Siemens, [Remote Processes, NX 2406 Series](https://docs.sw.siemens.com/en-US/doc/209349590/PL20231101866122454.nxopen_prog_guide/executing_remote_processes)，
  访问日期 2026-08-24。核验 .NET/Java remoting、Python 不支持、UI 并发风险。
- **[S6]** Siemens, [Development Cycle Considerations, NX 2406 Series](https://docs.sw.siemens.com/en-US/doc/209349590/PL20231101866122454.nxopen_prog_guide/development_cycle)，
  访问日期 2026-08-24。核验 author license、签名与 feature-based licensing。
- **[S7]** Siemens, [Run threaded extension modules with Python, NX 2406 Series](https://docs.sw.siemens.com/en-US/doc/209349590/PL20231101866122454.nxopen_prog_guide/xid1124929)，
  访问日期 2026-08-24。核验 subinterpreter、C 线程扩展和
  `# nx: threaded` 限制。
- **[S8]** Siemens, [Use external Python distribution, NX 2406 Series](https://docs.sw.siemens.com/en-US/doc/209349590/PL20231101866122454.nxopen_prog_guide/xid1124926)，
  访问日期 2026-08-24。核验 `UGII_PYTHON_LIBRARY_DIR`、
  `UGII_PYTHONPATH` 与 NXOpen extension module path。
- **[S9]** Siemens, [Finding files and syntax notes, NX 2406 Series](https://docs.sw.siemens.com/en-US/doc/209349590/PL20231101866122454.nxopen_prog_guide/finding_files_syntax)，
  访问日期 2026-08-24。核验 NX 安装目录、`UGII_BASE_DIR` 和 Python binding
  位置。
- **[S10]** Siemens Support, [Execute an external NXOpen Python batch script without run_journal.exe utility](https://support.sw.siemens.com/en-US/okba/KB000181221_EN_US/Execute-an-external-NXOpen-Python-batch-script-without-run_journalexe-utility/index.html)，
  KB000181221_EN_US，访问日期 2026-08-24。

### 一手技术资料

- **[P1]** Python Software Foundation,
  [ast — Abstract syntax trees](https://docs.python.org/3/library/ast.html)，
  访问日期 2026-08-24。核验 source location、`get_source_segment`、
  `unparse` 非文本精确往返与资源耗尽风险。

### 仓库约束与合同

- **[R1]** [AGENTS.md](../../AGENTS.md)
- **[R2]** [NX Workflow Learning skill](../../plugins/ug-cam-copilot/skills/nx-workflow-learning/SKILL.md)
- **[R3]** [NX Open Static Review Playbook](../../plugins/ug-cam-copilot/skills/nx-workflow-learning/references/nx-open-playbook.md)
- **[R4]** [NX Production Safety Gates](../../plugins/ug-cam-copilot/skills/nx-workflow-learning/references/production-safety.md)
- **[R5]** [Parallel Development Contracts](../contracts/parallel-development-contracts.md)

## 14. 最终边界声明

本文只定义研究证据和候选合同。它不包含可执行 Journal、NXOpen 写入代码、
机床控制指令、postprocess、NC 或 G-code。任何后续实现仍必须：

1. 保持 NX parser 在既定文件边界；
2. 保持 ActivityEvent 向后兼容；
3. 对版本、实例、项目和 recipe hash 显式绑定；
4. 对未知能力 fail closed；
5. 只在人工审阅后的测试副本上进入独立的仿真阶段；
6. 将碰撞/过切、machine simulation、shop approval 和 NC release 留在现场既有
   受控流程中。
