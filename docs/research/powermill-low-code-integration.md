# PowerMill 低代码集成预研

> 状态：研究草案，不是实现规范，不构成任何 PowerMill 版本兼容承诺。
> 研究范围：离线 `.mac`、命令回显/日志、公开 API、厂商授权适配器、FlowGraph 与生产安全门禁。
> 访问日期：2026-08-24（本文列出的外部资料均在该日访问）。
> 安全声明：本研究未启动、连接、枚举或控制真实 PowerMill，未执行宏，未生成 NC/G-code，未调用后处理。

## 1. 结论摘要

1. **离线文本应是 MVP 的唯一输入边界。** `.mac`、PowerMill 命令回显/日志、厂商明确导出的宏和能力清单可以离线导入；实时 COM/.NET、进程内插件、插件注册和命令发送应留到独立、显式授权的后续项目。
2. **必须同时维护 lossless CST 与语义 FlowGraph。** CST 保存原始字节、编码、换行、注释、空白、未知语法和源区间；FlowGraph 表达控制流、数据流、对话框/选择/活动实体等状态。仅靠语义 AST 无法实现非破坏 round-trip。
3. **录制宏不是完整状态快照。** Autodesk 明确说明录制器只记录用户实际改变的对话框值；已经处于目标值的字段必须重新输入才会被录制。因此，任何“录制命令序列”都必须附带前置状态、版本、项目和选择假设，不能直接视为可复现配方。[S15]
4. **PowerMill API 有公开的 .NET/COM 路径，但版本差异真实存在。** 当前 Autodesk 支持文章要求 Visual Studio 2015 Update 2 及 .NET Framework 4.8，并建议通过 NuGet 引用；公开源码和 NuGet 稳定包仍保留更早的 .NET 4/4.5 信息。此差异说明集成必须以目标安装的本地文档、依赖和验证矩阵为准，不能仅凭仓库 README 推断新版本兼容性。[S1][S3][S19]
5. **多实例绑定必须显式选择并持续校验。** Autodesk 源码提供运行中 COM 对象枚举、从选定 COM 对象构造 `PMAutomation`、读取 `ProcessId` 与版本的能力；同时，`UseExistingInstance` 在未找到或版本不匹配时会创建新实例，不能用于无副作用的“发现”。设计上应先枚举候选，再让操作者确认 PID、版本和项目，绑定后每步 fail closed。[S4][S5]
6. **商业外挂只能按授权表面注册。** 允许厂商授权适配器、公开 API、供应商导出的 `.mac`、命令日志和签名能力清单；不得反编译、反射探测或绕过许可分析受保护二进制。没有公开/授权语义的能力只能作为不透明节点保真保存，不能自动翻译或执行。
7. **所有执行能力默认不存在。** MVP 只做静态解释、参数候选、语义 diff、兼容性报告和 dry-run 预览。后续即使增加适配器，也必须保持人工复核、测试项目快照、CAM 仿真、碰撞检查和车间批准；禁止从学习日志直接产生或发送机床就绪输出。

## 2. 证据等级与边界

本文用以下标签区分证据性质：

| 标签 | 含义 | 使用规则 |
| --- | --- | --- |
| **已验证事实** | Autodesk Support/Help、Autodesk 官方 GitHub 源码、Autodesk 名下 NuGet 包，或可直接检查的一手源码行为 | 可以作为设计输入，但仍需绑定具体资料版本 |
| **厂商主张** | 商业供应商在其官网对产品、功能、许可或接入方式的陈述 | 只登记，不视为经过本项目功能验证 |
| **研究假设** | 为 MVP 提出的数据模型、算法或流程 | 必须通过验收矩阵或后续实验验证 |
| **决策** | 基于边界与风险形成的推荐约束 | 实现时默认遵守；放宽需单独评审 |

仓库边界适用于本文后续设计：

- 共享事件合同保持向后兼容，只增加可选字段，不重命名或删除既有字段。
- PowerMill 解析应位于现有 PowerMill 应用或未来 `powermill_macro.py` 适配器，不修改 NX parser。
- 跨产品语义可使用 `cam.*`；PowerMill 专属动作使用 `powermill.*`。
- 生产动作保持 dry-run，直到人工复核、CAM 仿真、碰撞检查和车间批准均通过。
- 不从学习日志生成或发送机床就绪 NC 代码。

## 3. 公开接入形态

### 3.1 `.mac` 与命令回显/日志

**已验证事实：**

- PowerMill 交互操作会向程序发送命令；官方帮助建议打开 Command Window 和 Echo Commands 来观察命令，并说明回显中的方括号与 `\r` 表示需要正确解释的日志包装，而不是宏语义本身。[S14]
- PowerMill 可以录制 `.mac`，但只记录录制期间实际改变的值；未被重新选择/输入的既有状态不会进入宏。[S15]
- 官方宏语言包含强类型变量、表达式、函数、条件、循环、数组/列表、用户输入、对话框、实体选择、文件选择、包含文件和动态 `DOCOMMAND`。[S9]-[S18]

**决策：**

- 原始 `.mac` 或日志只读导入，保存内容哈希、原始字节、来源、PowerMill 版本和项目上下文。
- 命令日志先剥离可证明的 transport envelope（时间戳、提示符、方括号、已知回显前缀），但始终保留原始行和 envelope span。
- 单次录制可以生成候选流程，不能证明哪些字面量应参数化。优先比较至少两次结构相同的会话。
- 对话框状态、活动实体、选择集合、单位、当前项目和开放模式必须作为显式前置条件，而不是从缺失命令中猜测。

### 3.2 外部 .NET/COM

**已验证事实：**

- Autodesk 支持文章给出 Visual Studio、.NET Framework 4.8 和 NuGet 引用路径；Autodesk Community 的官方入门帖指向开源 API 与 NuGet。[S1][S2]
- 官方 `Autodesk.ProductInterface.PowerMILL.PMAutomation` 源码通过 COM 类 `PowerMILL.Application` 连接，PowerMill 9 之前使用 `PMILL.Document`；它可发送命令、查询参数、录制/加载宏、读取版本、PID、窗口和活动项目。[S4]
- `PMAutomation` 支持 `UseExistingInstance`、`CreateNewInstance`、`CreateSingleInstance`，可按最小/最大版本启动；`UseExistingInstance` 捕获连接或版本错误后会创建新实例。[S4]
- 测试源码覆盖两个新实例、COM 对象列表、从给定 COM 对象附着、PID、版本范围和 2017/2018 安装路径差异。[S5]

**边界：**

- `PMAutomation` 是可变更项目的命令通道，不是安全的只读发现 API。
- `UseExistingInstance` 不能用于 MVP 探测，因为“找不到”可能变成“创建新实例”。
- `CreateSingleInstance` 会关闭全部运行实例，任何产品设计都不得将其用于发现、绑定或恢复。
- 本研究只描述未来适配器合同，不实现、不调用上述接口。

### 3.3 进程内插件

**已验证事实：**

- Autodesk 的 `powermill-api-examples` 要求从目标 PowerMill 安装获取/生成 `PowerMill.dll`，并从安装目录获取 `PluginFramework.dll`；插件通过 `regasm.exe` 注册，使用唯一 GUID，随后在 PowerMill Plugin Manager 中启用。[S6]
- 官方示例类实现 PowerMill 插件接口和通信接口，公开插件名称、版本、目标 PowerMill 版本、GUID 等元数据。[S7]
- 官方示例通过通信接口取得 `PluginServices` 与 token，再调用 `QueueCommand`、`DoCommandEx` 和参数查询。[S8]
- Autodesk 同时声明该示例仓库不是积极维护的高优先级项目。[S6]

**决策：**

- 进程内插件不是 MVP 依赖。
- 后续实现只能引用目标安装随附/支持的 DLL，不能下载任意预编译 `PowerMill.dll`，不能复用他人 GUID，不能猜测框架版本。
- 构建、COM 注册、启用插件均属于独立部署变更，必须在测试机和快照项目上显式授权。

### 3.4 接入形态对比

| 形态 | MVP 是否接收 | 可观察内容 | 主要风险 | 默认权限 |
| --- | --- | --- | --- | --- |
| `.mac` 文件 | 是 | 完整文本、控制流、变量、命令、注释 | 隐含项目/对话框状态、版本漂移 | 只读 |
| 命令回显/日志 | 是 | 交互命令顺序、时间和来源（若日志提供） | envelope 噪声、只记录局部行为 | 只读 |
| 厂商导出宏 | 是 | 厂商选择公开的宏文本 | 许可、依赖和版本未必完整 | 只读 |
| 能力清单 | 是 | 声明的 action、参数、版本、风险和证据 | 声明可能不真实或过期 | 注册但不可执行 |
| 外部 .NET/COM | 否 | 项目、实体、参数、命令响应、实例信息 | 错绑实例、直接项目变更 | 无通道 |
| 进程内插件 | 否 | 插件服务、项目对象、命令队列 | 注册、进程内权限、版本 ABI | 无通道 |
| 受保护商业二进制 | 否 | 仅供应商公开元数据 | 知识产权、未知副作用 | 不检查、不加载 |

## 4. 版本差异与目标版本策略

### 4.1 已核验差异

| 主题 | 已验证事实 | 工程含义 |
| --- | --- | --- |
| COM 类 | PowerMill 9 之前为 `PMILL.Document`，之后为 `PowerMILL.Application`。[S4] | 版本与 ProgID 必须进入适配器能力矩阵 |
| 版本字符串 | 官方源码分别处理 2018+ 的四位年份、2017 及以前的两位主版本、9 及以前的一位主版本。[S4] | 不把显示年份、内部主版本和文件版本混为一个字段 |
| 安装路径 | 官方测试分别断言 2017 的 `PowerMill 21.0.30` 和 2018 的 `PowerMill 2018` 目录形式。[S5] | 不能拼接猜测路径；应由安装发现或人工配置提供 |
| GUI 命令 | 官方源码在 2018+ 增加 Ribbon 最大化/最小化命令，旧版不包含。[S4] | UI/GUI 状态命令属于版本化能力 |
| API 构建基线 | Autodesk 2024 支持文章要求 .NET Framework 4.8；开源 README 仍写 .NET 4/4.5；NuGet 稳定版 1.2.5 目标为 .NET Framework 4.5。[S1][S3][S19] | 资料存在年代差异；新适配器优先遵循目标版本支持文档并做实机编译验证 |
| 插件依赖 | 2019 示例要求从对应安装生成 `PowerMill.dll` 并使用正确 `asmversion`，`PluginFramework.dll` 来自安装目录。[S6] | 插件依赖不能跨版本盲目复用 |
| 宏语法资料 | 本研究逐页核验的是 PowerMill 2025 英文 Macro Programming Guide。[S9]-[S18] | 对其他版本仅能报告“未验证”，不能声称等价 |

### 4.2 建议版本标识

版本合同不使用单个 `version` 字符串承载所有语义，建议至少包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `product_release` | string? | 用户可见发行版，例如 `2018`、`2025` |
| `product_version_raw` | string? | PowerMill 原始 COM/帮助返回值 |
| `product_version_normalized` | string? | 解析后的语义版本；无法解析则为空 |
| `product_build` | string? | 精确 build/update |
| `architecture` | enum? | `x64`/`x86`/`unknown` |
| `api_package_id` | string? | 例如 Autodesk 名下 NuGet package id |
| `api_package_version` | string? | 使用的 API 包版本 |
| `plugin_framework_version` | string? | 目标安装提供的 framework 版本 |
| `macro_guide_release` | string? | 用于解析/兼容判断的指南版本 |
| `capability_profile_hash` | string? | 对目标版本能力清单的内容哈希 |

### 4.3 兼容策略

**决策：**

- 解析器可以保真导入未知版本，但语义支持必须声明 `verified_from`、`verified_to` 或明确的 release set。
- 缺少目标版本时，允许查看原始文本和通用 FlowGraph，但 compatibility status 至少为 `version-unknown`，禁止导出可执行草稿。
- 同一命令在不同版本的参数、对话框或副作用未被目标版本资料/测试证明时，标为 `version-dependent`。
- 兼容能力以 `(product_release, build, architecture, api/package version, capability profile hash)` 为键，不以“最新版”代替。

## 5. 多实例绑定

### 5.1 已验证接口事实

- `PMAutomation.GetListOfPmComObjects()` 返回运行中的 PowerMill COM 对象列表。[S4][S5]
- `PMAutomation(comObject)` 可附着到指定 COM 对象。[S4][S5]
- 可读取 `ProcessId`、`Version`、`MainWindowId` 和活动项目。[S4]
- `UseExistingInstance` 不是纯发现：连接失败或版本不符会创建新实例。[S4]

### 5.2 未来绑定协议

以下是**研究假设/建议合同**，没有在真实 PowerMill 上验证：

1. 仅在用户显式授权“发现实例”后，通过公开枚举接口取得候选；不发送命令，不调用会创建/关闭实例的构造模式。
2. 对每个候选收集最小只读身份：PID、进程启动时间、产品版本、主窗口句柄、项目路径/身份、项目读写模式；读不到时显示 `unknown`。
3. 多于一个候选时必须人工选择。不得按枚举顺序、窗口标题模糊匹配或“第一个 PowerMill”自动绑定。
4. 形成不可变 `binding_lease`：候选身份字段、操作者、确认时间、目标 recipe hash、过期时间和随机 nonce。
5. 每个未来命令前重新核验 PID + 启动时间 + 版本 + 项目身份 + recipe hash。任一变化即撤销 lease 并 fail closed。
6. 禁止 PID 单独作为长期身份，因为 PID 可复用；禁止项目显示名单独作为身份，因为名称可重复。
7. 实例退出、项目切换、项目另存、版本/build 变化、能力清单变化或审批过期都要求重新绑定。

### 5.3 建议 `InstanceBinding` 字段

| 字段 | 必需 | 说明 |
| --- | --- | --- |
| `binding_id` | 是 | 随机、不可推导的绑定标识 |
| `discovery_method` | 是 | 公开 API 名称与版本 |
| `process_id` | 是 | 仅作复合身份的一部分 |
| `process_start_time` | 是 | 防止 PID 复用 |
| `main_window_id` | 否 | 辅助人工核对 |
| `product_release/build/architecture` | 是 | 精确目标 |
| `project_identity` | 是 | 规范化路径或产品提供的稳定身份 |
| `project_mode` | 是 | read-only/editable/unknown |
| `instance_nonce` | 是 | 绑定会话随机值 |
| `recipe_hash` | 是 | 审阅过的精确配方 |
| `capability_profile_hash` | 是 | 审阅时能力表 |
| `operator_approval_id` | 是 | 人工确认记录 |
| `issued_at`/`expires_at` | 是 | 短期 lease |
| `last_verified_at` | 是 | 最近一次前置校验 |
| `status` | 是 | candidate/bound/revoked/expired |
| `revocation_reason` | 否 | 失败关闭原因 |

## 6. 商业外挂与宏能力注册

### 6.1 可接受的五条注册路径

| 路径 | 所需证据 | 可注册范围 | 禁止行为 |
| --- | --- | --- | --- |
| 厂商授权适配器 | 合同/许可、SDK/API 文档、厂商身份、版本支持、签名包哈希 | 厂商明确授权的 capability | 探测未公开入口、绕过授权 |
| 公开 API | 官方公开文档/源码、许可、版本矩阵 | 文档和测试可证明的 action/query | 将示例行为外推到所有版本 |
| 厂商导出宏 | 供应商明确导出的 `.mac`、来源与许可 | 可静态解析的宏语义 | 加载或分析供应商受保护 DLL |
| 命令日志 | 合法记录的操作日志、会话边界、产品版本和项目上下文 | 观察到的命令及候选参数 | 把单次日志当作完整能力 |
| 能力清单 | 供应商签名 manifest、schema 版本、动作/参数/风险/证据 | 声明能力与 UI 展示 | 无证据自动生成执行模板 |

### 6.2 商业实例

- **已验证事实：** Autodesk 的公开插件示例包含面向 Vericut 等第三方系统的数据导出/转换源码，并说明这些插件可能只以编译形式随 PowerMill 提供或不直接随产品提供；示例可作为公开 API 适配的模板，但 Autodesk 声明其维护优先级较低。[S6]
- **厂商主张：** CAD Studio/ARKANCE 称其 Power Tools 是 PowerMill 垂直插件，增加 setup sheet、夹具/卡盘、孔特征、刀路段选择、自动碰撞检查、备份等函数和宏，并通过激活码许可、Plugin Manager 启用。[S20]
- **边界判断：** 上述厂商功能列表只证明“供应商这样声明”，不证明命令语义、版本兼容、碰撞检查完备性或安全性。除非厂商提供授权适配器、导出宏或能力清单，本系统不得推断其内部实现。

### 6.3 建议 `CapabilityManifest`

| 字段 | 必需 | 说明 |
| --- | --- | --- |
| `manifest_schema` | 是 | 清单 schema 版本 |
| `capability_id` | 是 | 稳定 ID；PowerMill 专属以 `powermill.*` 开头 |
| `canonical_action` | 是 | 跨产品时可为 `cam.*`，否则为 `powermill.*` |
| `provider_id/name` | 是 | 供应商稳定身份与显示名 |
| `provider_claim` | 是 | `verified-fact`/`vendor-claim`/`research-assumption` |
| `adapter_kind` | 是 | licensed/public-api/exported-macro/log/manifest-only |
| `license_reference` | 是 | 许可或合同引用，不存许可密钥 |
| `documentation_urls` | 是 | 一手文档链接 |
| `artifact_hash/signature` | 条件 | 适配器或导出物的完整性证明 |
| `product_releases/builds` | 是 | 明确支持集合；不得只写 `latest` |
| `architecture/framework` | 否 | x64/x86、.NET、PluginFramework 条件 |
| `parameters` | 是 | 名称、类型、单位、枚举、约束、敏感性 |
| `preconditions` | 是 | 项目、实体、活动对象、选择、对话框状态 |
| `effects` | 是 | read/create/update/delete/export/unknown |
| `risk_class` | 是 | safe/review/blocked |
| `dry_run_renderer` | 否 | 仅生成解释性预览，不是命令发送器 |
| `transport_required` | 是 | none/com/plugin/manual |
| `evidence` | 是 | 来源、版本、样本/测试 ID、访问日期 |
| `known_limitations` | 是 | 未支持语法、版本、状态与副作用 |
| `review_owner`/`reviewed_at` | 是 | 人工责任与时效 |
| `revoked_at/reason` | 否 | 撤销能力 |

**决策：**

- manifest 注册与执行授权分离。注册成功只表示 UI 可以识别和展示能力。
- `manifest-only`、`vendor-claim` 或缺少版本证据的能力不可导出执行草稿。
- 不接受通过受保护二进制的字符串、资源、IL、导出表或运行时探测“补全”能力清单。

## 7. 宏语法到 FlowGraph

### 7.1 双层表示

**研究假设：**

```text
Source bytes
  -> encoding-preserving lexer
  -> lossless CST (tokens + trivia + error/opaque nodes)
  -> semantic lowering
  -> FlowGraph (control + data + state + effect)
```

两个模型职责不同：

- **Lossless CST**：决定源行映射、诊断、局部编辑和 byte-identical round-trip。
- **FlowGraph**：决定可视化、静态解释、参数化、diff、dry-run 和兼容性分析。
- 两者通过稳定 `source_ref` 双向关联；FlowGraph 不负责重建未编辑的源文本。

### 7.2 已核验的语法族

| 语法族 | 官方事实 | FlowGraph 表达 |
| --- | --- | --- |
| 命令 | 命令不区分大小写；交互 UI 会产生命令。[S14] | `CommandNode`，保存 raw tokens 与可选 action mapping |
| 注释 | 整行注释以 `//` 开头，也可用于暂时停用命令。[S13] | CST trivia；可关联到下一节点，但不丢失 |
| 变量 | `INT/REAL/STRING/BOOL/ENTITY/OBJECT`，变量名大小写不敏感；赋值左侧必须使用 `$`。[S10] | `DeclareNode`、`AssignNode`、typed symbol |
| 数组/列表 | 支持定长 array、可变 list、索引、内建增删函数。[S10] | `CollectionNode`、data edges、shape abstract value |
| 函数 | 支持 `FUNCTION`、唯一 `Main`、`CALL`、输入/`OUTPUT` 参数、局部作用域、`INCLUDE`。[S11] | `FunctionGraph`、call/return edges、scope |
| 条件 | `IF/ELSEIF/ELSE`。[S9] | condition node + true/false branches + merge |
| 多路分支 | `SWITCH/CASE/DEFAULT`，无 `BREAK` 时 fall-through。[S18] | ordered case edges，显式 fall-through |
| 循环 | `FOREACH`、`WHILE`、`DO-WHILE`，支持 `BREAK/CONTINUE`。[S12] | loop header/back-edge/break/continue edges |
| 返回 | 支持 `RETURN`。[S9] | return edge 与函数出口 |
| 动态命令 | `DOCOMMAND` 执行运行时构造的字符串，官方提示谨慎使用。[S16] | `DynamicCommandNode`，常量可二次解析，否则 opaque |
| 输入/对话框 | `INPUT`、`QUERY`、`MESSAGE`、`FILESELECT`、实体/多实体/choice 选择。[S10] | `InteractionNode` + state/taint edges |
| GUI 模式 | `NOGUI` 可隐藏工具栏、模式栏、对话框或图形；其 graphics lock 可能影响 pattern segment 选择。[S17] | `GuiModeNode`、`GraphicsState`、selection hazard |
| 宏调用 | `MACRO` 调用文件并按顺序传参。[S11] | `MacroCallNode`，外部文档依赖边 |

### 7.3 建议节点

最小节点集合：

- `DocumentStart` / `DocumentEnd`
- `Command`
- `Declare` / `Assign`
- `Expression` / `Literal` / `ParameterRef` / `EntityRef`
- `FunctionDecl` / `Call` / `Return`
- `MacroCall` / `Include`
- `If` / `ElseIf` / `Else` / `Merge`
- `Switch` / `Case` / `Default`
- `ForEach` / `While` / `DoWhile`
- `Break` / `Continue`
- `Input` / `Query` / `Message` / `FileSelect`
- `DialogState` / `GuiMode` / `GraphicsState`
- `SelectionRead` / `SelectionWrite` / `ActiveEntity`
- `DynamicCommand`
- `OpaqueCommand` / `UnsupportedConstruct` / `ParseError`
- `CompatibilityGate` / `ApprovalGate`

### 7.4 边类型

| 边 | 语义 |
| --- | --- |
| `control.next` | 顺序执行 |
| `control.true/false/case/default` | 条件选择 |
| `control.back` | 循环回边 |
| `control.break/continue/return` | 非局部控制转移 |
| `data.read/write` | 变量/参数读取与写入 |
| `state.requires/produces` | 活动实体、选择、对话框、GUI、单位等状态 |
| `effect.may`/`effect.must` | 可能/必然副作用 |
| `source.maps_to` | 节点到源 span |
| `evidence.derived_from` | 动作映射的文档/日志证据 |
| `compatibility.blocked_by` | 不支持或版本冲突 |

### 7.5 对话框、选择与活动状态

FlowGraph 不应把以下内容降级成普通命令文本：

| 状态域 | 示例值 | 合并策略 |
| --- | --- | --- |
| `dialog.messages/errors` | on/off/unknown | 分支不一致时为 unknown |
| `gui.mode` | gui/nogui/unknown | 状态边显式传递 |
| `graphics.lock` | locked/unlocked/unknown | NOGUI 可产生已知/可能锁定 |
| `open_form` | form id/none/unknown | 未知命令可污染状态 |
| `active_entity[type]` | stable entity ref/none/unknown | 每实体类型分别跟踪 |
| `selection[type]` | finite set/symbolic/unknown | 用户选择为 tainted symbolic |
| `project.units` | metric/imperial/unknown | 数值字面量与单位绑定 |
| `current_mode` | workplane/curve editor/... | 进入/接受/取消形成状态机 |
| `project.identity` | stable id/unknown | 不允许由显示名推断 |

交互节点未绑定到已审阅参数时，dry-run 必须显示“需要人工输入/选择”，不能用默认值替代。

## 8. 源行映射与非破坏 round-trip

### 8.1 `SourceArtifact` 建议字段

| 字段 | 说明 |
| --- | --- |
| `artifact_id` | 内容寻址或不可变 ID |
| `source_kind` | mac/command-log/jsonl/vendor-export |
| `origin_uri` | 本地相对路径或脱敏来源；不自动上传 |
| `content_sha256` | 原始字节哈希 |
| `byte_length` | 原始长度 |
| `encoding`/`bom` | 探测结果与置信度 |
| `newline_profile` | CRLF/LF/CR/mixed |
| `product_release/build` | 若来源提供 |
| `project_context` | 可选、最小化、可脱敏 |
| `session_id` | 日志会话边界 |
| `captured_at` | 来源时间，不等于导入时间 |
| `imported_at` | 导入时间 |
| `immutable` | 始终为 true |

### 8.2 `SourceSpan` 建议字段

| 字段 | 说明 |
| --- | --- |
| `artifact_id` | 源文档 |
| `byte_start/byte_end` | 以原始字节为准的半开区间 |
| `line_start/line_end` | 1-based |
| `column_start/column_end` | 明确按 Unicode scalar 或 UTF-16 计数 |
| `raw_sha256` | span 内容哈希，用于补丁前检查 |
| `token_start/token_end` | CST token 索引 |
| `leading_trivia/trailing_trivia` | 注释、空白、换行关联 |

### 8.3 Round-trip 规则

**决策：**

1. 无编辑时导出直接返回原始 bytes，验收要求 SHA-256 完全相同。
2. 局部编辑只替换节点拥有且源哈希仍匹配的 span；其他 bytes 原样拼接。
3. 不统一大小写、引号、缩进、换行、尾随空格、注释位置或数值格式。
4. `INCLUDE` 文件是独立 artifact；不内联、不覆盖，除非用户显式选择编辑该文件。
5. 解析错误和未知语法形成 opaque/error CST 节点并原样保留，绝不静默删除。
6. 编辑跨越 unsupported span、动态字符串拼接或不确定语法边界时，拒绝写回并给出兼容性问题。
7. “语义等价”不能替代 byte-identical 的未编辑 round-trip 验收。

## 9. Unsupported construct 与兼容性报告

### 9.1 状态分类

| 状态 | 含义 | 是否允许语义编辑 | 是否允许未来发送 |
| --- | --- | --- | --- |
| `supported` | 目标版本语法与语义均有证据 | 是 | 仍需全部门禁 |
| `preserved` | 可保真保存但未语义理解 | 否 | 否 |
| `interactive` | 依赖人工输入、对话框或选择 | 仅参数绑定 | 默认否 |
| `version-dependent` | 语义随版本变化或未验证目标版本 | 否 | 否 |
| `unknown-effect` | 可解析但副作用不明 | 否 | 否 |
| `blocked` | 触犯安全/许可/生产边界 | 否 | 否 |

### 9.2 必须报告的构造

- 非常量或受用户输入污染的 `DOCOMMAND`。
- 缺失/循环/版本不明的 `INCLUDE` 与宏调用。
- 未知命令关键字、插件专属命令或无 capability manifest 的厂商能力。
- 依赖活动实体、选择集合、开放对话框、当前 mode，而日志未给出前置状态的命令。
- `NOGUI` 下可能失效的选择操作。[S17]
- 文件读写、外部进程、网络、任意路径输出或不可解析路径。
- 递归函数、无法证明终止的循环或超出静态解释预算的流程。
- 生成 NC/G-code、调用后处理、写入机床输出或由学习日志产生的 machine-ready 行为。
- 加载、检查或推断受保护商业二进制内部实现的请求。

### 9.3 `CompatibilityIssue` 建议字段

| 字段 | 说明 |
| --- | --- |
| `issue_id` | 稳定诊断 ID |
| `status/severity` | 上述状态与 info/warn/error/blocking |
| `code` | 可机器处理的原因码 |
| `message` | 面向操作者的中文解释 |
| `source_span` | 精确源位置 |
| `node_id` | FlowGraph 节点 |
| `target_profile` | 目标版本/能力 profile |
| `evidence` | 证据链接、版本、测试 ID |
| `assumption` | 当前缺失的项目/选择/对话框假设 |
| `remediation` | 需要的资料、参数或人工动作 |
| `roundtrip_impact` | none/local-edit-blocked/export-blocked |
| `execution_impact` | none/review/blocked |

兼容性报告顶层还应包含解析覆盖率、opaque span 数量、目标版本、来源指南版本、能力清单哈希、未解析 include、状态假设、round-trip 结果和 export eligibility。

## 10. 静态解释、参数化与 diff

### 10.1 静态解释

**研究假设：** 使用有界抽象解释，不模拟真实 PowerMill。

抽象值至少包含：

- `Const(value, unit)`
- `FiniteSet(values)`
- `Range(min, max, unit)`
- `EntityRef(type, stable-or-symbolic-id)`
- `CollectionShape(element_type, size-range)`
- `TaintedInput(source)`
- `Unknown(reason)`

约束：

- 不执行 `DOCOMMAND`、文件 I/O、插件调用或 PowerMill 命令。
- 函数调用设深度和总步数预算；循环使用固定点/扩大策略。
- 未知命令将可能影响的状态域污染为 `unknown`，不假设无副作用。
- `INPUT/QUERY/FILESELECT/实体选择` 产生 tainted symbolic value。
- `INCLUDE` 只有在导入包中明确提供时才参与分析；不从任意磁盘路径隐式读取。

### 10.2 参数候选

参数候选按证据排序：

1. 两次或以上结构相同会话中变化的字面量。
2. `Main` 输入、`INPUT/QUERY/FILESELECT` 和实体选择。
3. 项目/实体名称、路径、容差、直径、步距、单位化数值等上下文敏感字面量。
4. manifest 明确声明的参数。

不得自动参数化：

- 命令关键字、实体类型关键字、分支结构或 `SWITCH CASE` 语义常量。
- 只出现一次且无语义/manifest 证据的任意数字。
- NC/postprocess 输出路径或机床就绪内容。

每个候选需记录 `evidence_spans`、推断类型、单位、候选名、出现次数、取值集合、置信度和人工决定。

### 10.3 三层 diff

| 层 | 内容 | 用途 |
| --- | --- | --- |
| Source diff | bytes/tokens/trivia/行 | 审查保真写回 |
| Semantic diff | 节点、控制流、变量、参数 | 解释宏结构变化 |
| Effect/state diff | 读写实体、前后状态、风险、兼容性 | 评估工艺与安全影响 |

重排命令即使文本相同也可能改变状态，必须在 control/effect diff 中显示。仅格式变化不能伪装成动作变化。

## 11. Dry-run 预览与生产门禁

### 11.1 MVP dry-run

MVP 的 dry-run 是**离线计划渲染**，不是调用 PowerMill 的“试运行”：

| 字段 | 说明 |
| --- | --- |
| `step_id/order` | 稳定顺序 |
| `source_span/raw_text` | 审计证据 |
| `action` | `cam.*` 或 `powermill.*` |
| `target/parameters` | 符号目标与参数 |
| `pre_state/post_state` | 已知/符号/未知状态 |
| `effects` | read/create/update/delete/export/unknown |
| `support/confidence` | 语义覆盖与置信度 |
| `risk_class` | safe/review/blocked |
| `compatibility_issues` | 阻塞原因 |
| `capability_ref` | manifest 与哈希 |
| `approval_required` | 需要的人工/仿真/车间门禁 |

MVP 中不存在 command transport，因此任何预览都无法发送。

### 11.2 后续执行门禁

后续独立项目若获授权，建议按以下顺序，任一步失败都停止：

```text
源文件冻结与哈希
  -> lossless parse / compatibility report
  -> 参数与完整宏人工复核
  -> 精确版本 + 项目快照 + 实例绑定
  -> 离线 dry-run 与 effect diff
  -> 测试项目中的显式试验授权
  -> CAM 仿真
  -> 碰撞检查
  -> 结果、周期与预期对比
  -> 车间批准
  -> 仍由受控适配器执行，保留完整审计
```

全局阻断条件：

- recipe、source、capability profile 或项目 identity 的哈希与批准记录不一致。
- 目标版本/build/架构不在明确验证集合中。
- 存在 `blocked`、`unknown-effect`、未解析动态命令或未绑定交互。
- 项目不是一次性副本/快照，或输出路径未声明。
- 未完成 CAM 仿真、碰撞检查和车间批准。
- 动作涉及从学习日志直接生成/发送 NC、G-code 或 postprocess 结果。

## 12. 共享事件合同的可选扩展

为保持向后兼容，不修改既有字段；建议只增加以下可选对象：

| 可选字段 | 说明 |
| --- | --- |
| `product` | `name/release/build/architecture` |
| `source` | artifact id、hash、kind、session、source span |
| `parser` | parser/dialect/profile 版本 |
| `flow` | node id、control/data/state/effect 摘要 |
| `binding` | binding id 与校验摘要；不放 COM 对象 |
| `capability_ref` | capability id、manifest version/hash |
| `compatibility` | status、issue ids、target profile |
| `risk` | class、reasons、required gates |
| `evidence` | source spans、日志 session、文档/测试引用 |
| `approval` | review id、scope、hashes、expiry |
| `execution` | 始终显式标注 `dry_run`; MVP 不出现 transport handle |

命名规则：

- 跨产品且语义确实相同的动作使用 `cam.*`。
- PowerMill 独有命令、对话框、活动实体或插件能力使用 `powermill.*`。
- 厂商专属能力建议 `powermill.vendor.<provider>.<capability>`，并保留 `canonical_action`（若存在）。

## 13. MVP

### 13.1 范围

- 只读导入 `.mac`、命令回显/日志、JSONL trace、厂商导出宏和 capability manifest。
- 保留原始 bytes、编码、换行、注释、空白和 source span。
- 解析第 7.2 节已列语法族；未知构造形成 opaque/error 节点。
- 输出 FlowGraph 数据、兼容性报告、参数候选、三层 diff 和离线 dry-run。
- 支持至少两个 session 的结构比较，单 session 显式降低参数推断置信度。
- 维护版本化 capability profiles，但不声称跨版本自动兼容。
- capability manifest 离线校验、来源分级和撤销状态展示。
- 所有生产相关动作保持 `blocked` 或 `review`，无实时 transport。

### 13.2 非目标

- 不发现、启动、连接、关闭或控制 PowerMill 实例。
- 不实现 COM/.NET adapter，不注册/启用进程内插件。
- 不执行、单步或调试宏，不点击真实对话框。
- 不生成 NC/G-code，不调用 postprocessor，不输出机床就绪文件。
- 不反编译、反射、注入、hook 或动态探测商业二进制。
- 不自动修复未知语法，不重写整份宏，不承诺任意版本 round-trip 后可执行。
- 不以 UI 自动化代替公开 API。
- 不修改 NX parser，也不把 PowerMill 语法混入 NX adapter。
- 不以静态解释结果替代 CAM 仿真、碰撞检查或车间批准。

## 14. 风险

| 风险 | 影响 | 缓解/门禁 |
| --- | --- | --- |
| 录制宏遗漏既有对话框值 | 配方无法复现 | 前置状态合同、对比多 session、缺失状态报告 |
| 版本/更新漂移 | 命令或参数语义变化 | 精确 profile、目标版本指南、版本依赖 fail closed |
| API 文档年代冲突 | 编译/运行失败 | 目标安装依赖、本地文档、实机编译矩阵 |
| 多实例错绑 | 修改错误项目 | 显式人工选择、复合身份、短期 lease、逐步复核 |
| `UseExistingInstance` 隐式创建 | 无意启动实例 | 发现阶段禁止使用该路径 |
| `CreateSingleInstance` 关闭其他实例 | 数据损失/中断 | 能力永久禁止用于发现与恢复 |
| 动态 `DOCOMMAND` | 静态分析绕过 | 常量二次解析；tainted/unknown 一律 blocked |
| `NOGUI` 与选择副作用 | 选择结果错误 | 状态建模、版本报告、真实执行前人工试验 |
| include/外部文件 | 隐藏依赖或路径副作用 | 显式导入闭包、内容哈希、缺失即阻塞 |
| Opaque 插件能力 | 未知副作用 | 只接受签名 manifest/授权 API；否则不可执行 |
| 厂商主张过期 | 能力/兼容性误判 | 单独标注 vendor claim、要求版本证据与到期复核 |
| 非破坏写回失败 | 宏损坏 | byte-span patch、hash guard、未编辑 bytes 全等测试 |
| 解析器错误吞掉语法 | 行为变化 | error/opaque node、零静默丢弃、覆盖率报告 |
| 静态解释不终止 | 资源耗尽 | 步数/深度/循环预算与 widening |
| 参数误推断 | 工艺值被错误泛化 | 多 session 证据、类型/单位、人工确认 |
| 共享事件合同破坏 | NX/PowerMill 消费方回归 | 仅可选字段、schema contract tests |
| 资料链接漂移 | 证据不可复核 | 记录访问日期、标题、仓库 commit/ref、定期链接检查 |
| 从日志生成机床输出 | 生产事故 | 架构无 NC/G-code/postprocess generator；全局阻断规则 |

## 15. 验收矩阵

| ID | 需求 | 验收方法 | 通过标准 |
| --- | --- | --- | --- |
| A01 | 仓库与 skill 边界 | 文档审查 | 明确 PowerMill/NX 分离、可选事件字段、dry-run 与生产门禁 |
| A02 | `.mac` 保真导入 | CRLF/LF/mixed、BOM、注释、大小写、尾空格 fixtures | 未编辑 round-trip SHA-256 100% 相同 |
| A03 | 命令日志 envelope | 时间戳、提示符、方括号、`\r` fixtures | 语义行可定位，原始日志 span 不丢失 |
| A04 | 控制流 | IF/ELSEIF/ELSE、SWITCH fall-through、三类循环、break/continue/return | CFG 边与官方语义 fixtures 一致 |
| A05 | 函数与变量 | Main、CALL、OUTPUT、INCLUDE、scope、数组/list | symbol/data-flow 与 source span 可追溯 |
| A06 | 对话框/选择状态 | INPUT/QUERY/MESSAGE/FILESELECT、实体多选、NOGUI | 生成交互/state 节点，未绑定时 dry-run 阻塞 |
| A07 | 动态命令 | 常量和 tainted `DOCOMMAND` fixtures | 常量可嵌套解析；tainted/unknown 标为 blocked |
| A08 | Unsupported 保真 | 未知命令、损坏块、插件命令 | 原始 bytes 保留，诊断精确，不静默删除 |
| A09 | 源行映射 | 多字节文本与不同换行 | byte/line/column 映射稳定，局部 patch 不漂移 |
| A10 | 参数化 | 单 session 与多 session 对比 | 单 session 不宣称已证明参数；多 session 给证据和置信度 |
| A11 | 三层 diff | 格式改动、值改动、重排命令 fixtures | source/semantic/effect diff 正确区分 |
| A12 | 兼容报告 | 未知版本、2017/2018 差异、2025 宏 profile | 明确 verified/unknown/version-dependent，不外推 |
| A13 | Capability manifest | 公共 API、vendor claim、manifest-only 样本 | schema 校验、证据分级、撤销与风险可见 |
| A14 | 商业二进制边界 | 安全审查 | 无反编译、反射、加载或运行受保护二进制路径 |
| A15 | 多实例合同 | 模拟候选/项目切换/PID 复用数据 | 需要人工选择；身份变化撤销 lease；不按列表索引绑定 |
| A16 | Dry-run | 代表性宏与 blocked 构造 | 只输出离线计划，进程无 command transport |
| A17 | 生产安全 | 架构/代码审查 | 不存在 NC/G-code/postprocess 生成或发送能力 |
| A18 | 共享合同兼容 | schema contract test | 既有字段未删除/重命名；新增均可选 |
| A19 | 链接与证据 | 自动状态检查 + 人工页面复核 | 所有引用有标题、URL、访问日期；失败链接有说明 |
| A20 | 变更范围 | `git diff --name-only` | 研究任务只新增本文档 |

## 16. 建议分阶段研究

1. **M0：语料与语法基线。** 取得合法、脱敏、不同版本的 `.mac`/日志 fixtures；以 2025 官方宏指南建立语法测试，不运行。
2. **M1：Lossless front end。** 完成 bytes/CST/source map/opaque node 和 byte-identical round-trip 验收。
3. **M2：FlowGraph。** 实现控制、数据、状态与 effect 边；加入对话框、选择、NOGUI 和动态命令。
4. **M3：离线分析。** 参数候选、三层 diff、抽象解释、compatibility report、dry-run renderer。
5. **M4：能力注册。** 引入签名 manifest、证据分级、版本 profile 与撤销；仍不连接 PowerMill。
6. **M5：独立安全评审。** 只有在用户另行授权后，才研究只读实例发现和测试项目 adapter；不得沿用本研究授权。

## 17. 资料来源

所有来源访问日期均为 **2026-08-24**。

### Autodesk 官方与一手源码

- **[S1] 已验证事实** — Autodesk Support, “How to automate PowerMill and PowerShape using API libraries” （页面标注更新日期 2024-04-29）
  https://www.autodesk.com/support/technical/article/caas/sfdcarticles/sfdcarticles/How-to-automate-Powermill-using-Api-libraries.html
- **[S2] 已验证事实** — Autodesk Community Manager, “Getting started with PowerShape and PowerMill API” （含官方开源仓库、NuGet 与 2017 R3 历史说明）
  https://forums.autodesk.com/t5/powershape-and-powermill-api/getting-started-with-powershape-and-powermill-api/td-p/6868839
- **[S3] 已验证事实** — Autodesk, `PowerShapeAndPowerMillAPI` README（API、构建基线、许可）
  https://github.com/Autodesk/PowerShapeAndPowerMillAPI/blob/master/README.md
- **[S4] 已验证事实** — Autodesk, `PMAutomation.cs`（COM 类、版本解析、实例策略、PID、命令/参数接口、对话框与碰撞状态）
  https://github.com/Autodesk/PowerShapeAndPowerMillAPI/blob/master/Delcam.ProductInterface.PowerMILL/PMAutomation.cs
- **[S5] 已验证事实** — Autodesk, `PMAutomationTest.cs`（多实例、COM 附着、版本与路径测试）
  https://github.com/Autodesk/PowerShapeAndPowerMillAPI/blob/master/Delcam.ProductInterface.PowerMILL.Test/PMAutomationTest.cs
- **[S6] 已验证事实** — Autodesk, `powermill-api-examples` README（插件依赖、类型库、COM 注册、唯一 GUID、Plugin Manager、维护状态）
  https://github.com/Autodesk/powermill-api-examples/blob/master/README.md
- **[S7] 已验证事实** — Autodesk, PowerMill plugin class example（接口、GUID、插件元数据与目标版本）
  https://github.com/Autodesk/powermill-api-examples/blob/master/addin-powermill-to-vericut/PowerMILLVERICUTInterfacePlugin/PowerMILLPlugin.cs
- **[S8] 已验证事实** — Autodesk, in-process `PowerMILLAutomation.cs` example（services/token、queue/query 接口）
  https://github.com/Autodesk/powermill-api-examples/blob/master/addin-powermill-to-vericut/PowerMILLVERICUTInterfacePlugin/PowerMILLAutomation.cs
- **[S19] 已验证事实** — NuGet Gallery, Autodesk owner, `Autodesk.ProductInterface.PowerMILL` 1.2.5（稳定包、目标 .NET Framework 4.5、2024-04-05 更新）
  https://www.nuget.org/packages/Autodesk.ProductInterface.PowerMILL

### Autodesk PowerMill 2025 Macro Programming Guide

- **[S9] 已验证事实** — “Writing your own macros”（语法章节总览）
  https://help.autodesk.com/cloudhelp/2025/ENU/PWRM-Macros/files/PWRM-MACROS-WRITING-YOUR-OWN-MACROS.html
- **[S10] 已验证事实** — “Variables in macros”（类型、赋值、输入、对话框、选择、数组/list）
  https://help.autodesk.com/cloudhelp/2025/ENU/PWRM-Macros/files/macros-writing-your-own-macros/PWRM-MACROS-VARIABLES-IN-MACROS.html
- **[S11] 已验证事实** — “Macro functions”（Main、参数、scope、OUTPUT、INCLUDE）
  https://help.autodesk.com/cloudhelp/2025/ENU/PWRM-Macros/files/macros-writing-your-own-macros/PWRM-MACROS-MACRO-FUNCTIONS.html
- **[S12] 已验证事实** — “Repeating commands in macros”（FOREACH、WHILE、DO-WHILE、BREAK、CONTINUE）
  https://help.autodesk.com/cloudhelp/2025/ENU/PWRM-Macros/files/macros-writing-your-own-macros/PWRM-MACROS-REPEATING-COMMANDS-IN-MACROS.html
- **[S13] 已验证事实** — “Adding comments to macros”
  https://help.autodesk.com/cloudhelp/2025/ENU/PWRM-Macros/files/macros-writing-your-own-macros/PWRM-MACROS-ADDING-COMMENTS-TO-MACROS.html
- **[S14] 已验证事实** — “PowerMill commands for macros”（命令回显与大小写）
  https://help.autodesk.com/cloudhelp/2025/ENU/PWRM-Macros/files/macros-writing-your-own-macros/PWRM-MACROS-POWERMILL-COMMANDS-FOR-MACROS.html
- **[S15] 已验证事实** — “Recording macros in PowerMill”（只记录实际改变的值）
  https://help.autodesk.com/cloudhelp/2025/ENU/PWRM-Macros/files/PWRM-MACROS-RECORDING-MACROS-IN-POWERMILL.html
- **[S16] 已验证事实** — “Executing a macro string variable as a command using DOCOMMAND”
  https://help.autodesk.com/cloudhelp/2025/ENU/PWRM-Macros/files/macros-writing-your-own-macros/PWRM-MACROS-EXCUTING-A-MACRO-STRING-VARIABLE.html
- **[S17] 已验证事实** — “Running macros without displaying GUI items”（NOGUI 与 selection 风险）
  https://help.autodesk.com/cloudhelp/2025/ENU/PWRM-Macros/files/macros-writing-your-own-macros/PWRM-MACROS-RUNNING-MACROS-WITHOUT-DISPLAYING-GUI.html
- **[S18] 已验证事实** — “SWITCH statement”（fall-through 与 BREAK）
  https://help.autodesk.com/cloudhelp/2025/ENU/PWRM-Macros/files/macros-writing-your-own-macros/PWRM-MACROS-SWITCH-STATEMENT.html

### 商业供应商一手陈述

- **[S20] 厂商主张** — CAD Studio/ARKANCE, “Power Tools add-on for Autodesk PowerMill”（功能、激活码许可、Plugin Manager 启用方式）
  https://www.cadstudio.cz/en/apps/powertools/

## 18. 自检清单

- [x] 仅研究与文档，没有实现产品代码。
- [x] 未执行宏，未连接或控制 PowerMill。
- [x] 未生成 NC/G-code/postprocess。
- [x] 未研究或反编译受保护二进制。
- [x] 已区分已验证事实、厂商主张和研究假设。
- [x] 已覆盖版本差异、多实例、FlowGraph、源映射、round-trip、compatibility、dry-run 和生产门禁。
- [x] 已给出建议合同字段、MVP、非目标、风险与验收矩阵。
- [x] 所有外部资料均列出访问日期和链接。
