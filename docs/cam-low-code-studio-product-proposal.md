# CAM Low-Code Studio 产品方案

> 方案代号：S-PRODUCT
>
> 状态：MVP 决策基线
>
> 日期：2026-08-24
>
> 冻结合同：
> [CAM Flow Graph v1](contracts/cam-flow-graph-contracts.md)

## 1. 结论与口径

CAM Low-Code Studio 的定位不是再做一个自动编程外挂，而是为客户已经有权使用的
PowerMill 宏、命令日志、NX Journal/NXOpen 源码和商业外挂能力提供一个本地、
可解释、可改编、可版本化、可测试的治理与编排层。

本文使用三个标签：

| 标签 | 定义 |
| --- | --- |
| **研究事实** | 指定仓库文档、官方资料或四份研究已验证的事实 |
| **产品决策** | 本方案为 MVP 做出的可开发、可验收选择 |
| **现场待验证** | 必须通过目标用户、真实脱敏资产、客户工作站、目标 CAM 版本或法务确认的假设 |

本文不重复四份研究正文，只把证据转化为产品决策。详细依据见：

- [市场与生态预研](research/cam-low-code-market-ecosystem.md)
- [PowerMill 集成预研](research/powermill-low-code-integration.md)
- [NX 集成预研](research/nx-low-code-integration.md)
- [Flow Engine 预研](research/cam-low-code-flow-engine.md)

## 2. 目标用户与 Jobs-to-be-Done

### 2.1 目标用户

**产品决策：** 首要用户是已经积累自动化资产的模具 CAM 团队，而不是从零学习
编程的新手。

| 角色 | 现有能力 | 主要责任 |
| --- | --- | --- |
| 高级 PowerMill/NX 程序员 | 能读宏/Journal，理解工艺和现场例外 | 解释、改编、审阅和维护关键流程 |
| 初中级程序员 | 能使用 CAM 和既有外挂，但不一定能安全改源码 | 按证据改参数、完成测试、识别何时升级求助 |
| CAM 主管/应用工程师 | 管版本、模板、外挂、试点和放行流程 | 决定适用范围、审核兼容性和组织复用 |
| IT/数字化/法务协作人 | 管安装、数据、权限、合同和网络边界 | 审核本地数据、外发、供应商适配器和权利证据 |

### 2.2 Jobs-to-be-Done

1. 当我接手一批历史宏、Journal 或外挂配套资产时，我要在不运行它们的情况下
   快速知道“做什么、依赖什么、适用于哪个版本、哪里未知”。
2. 当新模具与旧项目相似时，我要从已有样本中找出真正需要修改的参数，而不是
   复制文件后全局搜索替换。
3. 当流程包含商业外挂时，我要保留已有采购和工艺能力，只把合法可见的输入、
   输出、版本、许可证和人工接力点纳入配方。
4. 当 CAM 升级、项目切换或多人复用时，我要看见源码、图、版本、能力和目标环境
   的差异，且系统不能静默修正未知内容。
5. 当我准备验证改编结果时，我要在一个窗口中选择明确的 fixture/测试项目、
   目标版本和实例，得到离线 dry-run preview 与未完成门禁，而不是误触生产执行。
6. 当我不再信任某类采集、连接或外发能力时，我要立即撤销该类别，仍能只读查看
   已保存资产，并能区分撤销、暂停、删除和卸载。

## 3. 现状痛点

| 痛点 | 证据性质 | MVP 决策 |
| --- | --- | --- |
| 资产散落在个人目录、共享盘和外挂菜单，版本/负责人/依赖不清 | 现场待验证，市场研究一致指向 | 资产注册、hash、版本、依赖、权利和状态是第一入口 |
| PowerMill 录制宏漏掉未改变的既有值 | 研究事实 | 缺失上下文必须为 unknown/requires_confirmation，禁止补默认值 |
| NX Journal 的录制对象 ID 和 API 可能随对象/版本变化 | 研究事实 | selector 默认 fragile/unresolved，目标 stubs/版本兼容单独报告 |
| 宏/Journal 中注释、空白、未知语法和隐含状态容易在改写时丢失 | 研究事实与高风险推导 | 原始 bytes 不可变，token/CST + property source map，不能证明则只读 |
| 商业外挂内部能力不可见且权利边界复杂 | 研究事实与合同风险 | 只接公开接口、用户有权导出物、签名能力清单或厂商授权 adapter |
| 同机多 CAM 窗口容易串目标 | 仓库事实 | 不猜前台目标；product/version/instance/project/hash 显式绑定 |
| “AI 解释”和“dry-run”容易被误解为正确或可生产 | 产品风险 | AI 只提建议；deterministic validator 是合同真相；门禁常驻 |
| 通用低代码画布暴露技术概念，难以核对工艺来源 | 现场待验证 | 单窗口以工艺步骤和来源核对为主，端口/依赖逐步披露 |

## 4. 产品原则

1. **已有资产优先。** 首次价值从导入和体检开始，不要求用户从空白图重建流程。
2. **证据优先于解释。** 每个节点、参数、风险和兼容结论必须回到 asset revision
   和 source span；AI 文案不能替代证据。
3. **原文不可变。** 编辑产生 GraphCommand、新 FlowVersion 和内存候选 patch；
   不覆盖原始宏/Journal。
4. **未知必须可见。** opaque、unsupported、ambiguous、capability missing 和
   未运行门禁不能隐藏、自动删除或静默降级。
5. **产品 parser 隔离。** UI 和共享 graph service 统一，NX/PowerMill 语法永远
   各自处理。
6. **单窗口、CAM 术语、逐步披露。** 默认突出工艺步骤和主顺序；关系、源码和
   adapter 细节按需展开。
7. **版本和目标是上下文，不是偏好。** 任何 preview 都绑定精确产品、目标版本、
   fixture 实例、项目快照和 hashes。
8. **权限可撤销。** 一次授权只覆盖当前 manifest hash 和明确类别，不是永久万能
   授权。
9. **本地与离线默认。** 未授权时网络外发为零；MVP 没有 CAM transport。
10. **review-first 永久存在。** 不生成或发送 machine-ready NC/G-code/postprocess；
    不以 preview 替代仿真、碰撞和车间批准。

## 5. 单窗口信息架构

### 5.1 顶级导航

应用首屏就是工作台，不是营销页。顶级区域只保留：

- `资产`：宏、Journal、日志、能力清单、来源、权利、依赖和体检。
- `流程`：画布、步骤库、参数、来源、版本、diff、test 和 preview。
- `能力`：产品插件、商业外挂 manifest、目标版本支持、许可和撤销状态。
- `诊断`：权限、worker、导入、兼容、性能、被拒操作和本地数据状态。

### 5.2 流程工作台

```text
┌ 产品/目标版本 | 资产版本 | 撤销/重做 | 版本 | Diff | Test | Preview ┐
├──────────────┬───────────────────────────────┬────────────────┤
│ 步骤库/资产   │ 工艺流程画布                   │ 当前步骤        │
│ 搜索/阶段     │ 默认突出 control 主路径         │ 参数/单位/对象   │
│ 依赖/opaque   │ 关系模式展开 data/dependency   │ 证据/风险/能力   │
├──────────────┴───────────────────────────────┴────────────────┤
│ 问题 | 源码 | 图 Diff | 源码 Diff | Test | Preview | 门禁       │
└───────────────────────────────────────────────────────────────┘
```

交互规则：

- 左栏不是通用 node palette，而是“当前产品 + 目标版本 + 已授权 capability”
  可用的 CAM 工艺步骤和当前资产结构。
- 画布默认隐藏大部分端口，仅突出 control path；选中节点或进入关系模式才显示
  data/dependency。
- 右栏使用单位输入、枚举、对象 selector、开关和证据链接，不允许直接改 JSON。
- 底栏问题或 blocker 出现时显示计数，不抢输入焦点。
- opaque/blocked/权限缺失/版本不匹配必须同时有图标、短文本和 source 定位，
  不能只用颜色。
- 节点到源码、源码到节点均在两次操作内完成；映射歧义时列出候选，不猜。

### 5.3 多实例抽屉

连接摘要显示 `NX n / PowerMill n / 记录状态`。展开后按实例显示 product、
PID、临时窗口标题、前台状态、目标版本、项目、adapter、licensed、capable、
authorized 和 busy。窗口标题只作临时本机提示，不持久化到资产、事件、流程或
导出。

MVP 只展示 fixture 实例。真实实例发现、项目映射和 adapter 状态标记为现场待
验证，不得出现“已连接可运行”的误导状态。

## 6. 端到端用户流程

### 6.1 导入已有宏、Journal 与外挂能力

1. 用户安装 NX 或 PowerMill 离线资产插件。
2. Studio 展示当前 manifest hash、请求权限、数据范围、是否外发和撤销效果。
3. 用户选择自己有权处理的文件/目录或导入签名 CapabilityManifest。
4. 系统保存权利声明、脱敏 locator、原始 bytes hash、encoding、newline、
   product、版本线索、依赖和 immutable asset revision。
5. product 无法确定、权利未知、二进制输入或 manifest 签名/版本无效时阻断；
   不自动尝试另一个 parser。
6. 商业外挂没有授权 adapter/能力清单时，只能创建“人工/不透明步骤”，不分析
   二进制、不猜内部动作。

### 6.2 解释

1. NX asset 只进入 NX parser；PowerMill asset 只进入 PowerMill parser。
2. parser 输出 FlowGraph、property-level SourceMapping、opaque regions、
   参数候选和 CompatibilityReport。
3. 画布用 CAM 术语展示“做什么”，证据面板展示“为什么这样理解”。
4. PowerMill 缺失的默认/对话框/活动实体状态显示 unknown；NX Builder、
   Undo、selector 和 target stubs 状态作为一等语义显示。
5. AI 可以解释节点和诊断，但解释旁固定显示 evidence refs、confidence 和
   “非合同真相”；关闭 AI 不改变任何确定性结果。

### 6.3 拖拽改编

1. 用户可修改已支持节点的参数、显式控制关系、启停状态和合法 subflow。
2. 拖拽只是 GraphCommand；屏幕位置不推断顺序，layout 不进入 semantic diff。
3. port 类型、单位、cardinality、capability 和风险下限即时校验。
4. opaque/ambiguous span 只读；跨越它的重排、拆分、删除或 codegen 被阻断。
5. 参数必须保留稳定 ID、单位、范围、selector 和来源；单次出现的数字不自动
   参数化。
6. AI 只能形成 GraphPatchProposal；用户查看 graph/source diff 并接受后，
   deterministic preflight 才能产生 GraphCommand。

### 6.4 版本与 diff

1. 所有语义编辑保存为不可变 FlowVersion，记录 parent、semantic/source/
   artifact/capability hashes、作者和说明。
2. 自动保存仅产生本地 draft checkpoint；命名版本必须由用户确认。
3. Diff 分为 source、graph、compatibility 和 preview 四层；layout 默认排除。
4. opaque 即使未变也显示“保留的未知结构”，避免用户误认为系统理解。
5. 目标版本、capability manifest 或 source asset 变化均创建新 revision，
   旧审阅失效。

### 6.5 Test

Test 只运行：

- Schema、类型、edge、subflow 和安全校验；
- adapter fixture parse；
- source mapping 和 unknown field preservation；
- F0-F3 round-trip；
- candidate reparse 与 semantic comparison；
- Recipe ProjectionReport；
- determinism 与资源限制。

Test 不连接 CAM，不 import/执行 Journal，不运行宏，不生成刀路或机床输出。

### 6.6 Dry-run preview

1. 用户必须选择 product、target version、fixture instance、test project snapshot
   和精确 FlowVersion。
2. 系统再次核对 semantic/source/capability/reviewed recipe hashes、权限和
   CompatibilityReport。
3. Preview 只生成内存 source candidate、source/graph diff、计划步骤、
   effects、opaque 和未完成门禁。
4. 固定显示 `transport=none`、`commands_sent=0`、
   `journal_executed=false`、`macro_executed=false`、
   `machine_output_count=0`。
5. 任一 blocker 只返回结构化拒绝，不留下部分候选，不自动重试。
6. CAM simulation、collision/gouge、machine simulation 和 shop approval 始终
   为 `required/not_run/unavailable`。

## 7. 商业外挂授权与 IP 边界

### 7.1 默认政策

允许：

- 用户明确选择且有权处理的 `.mac`、Journal、自有源码和合法日志；
- 官方公开 API/文档及其允许的使用范围；
- 履行许可证义务后的 MIT/Apache 示例；
- 客户确认有权处理的厂商导出物；
- 厂商签名或客户确认的 CapabilityManifest；
- 厂商授权 adapter。

禁止：

- 反编译、反汇编、解密、反射探测、注入、hook、私有协议猜测；
- 绕过席位、登录、订阅、Plugin Manager、签名或技术保护；
- 复制商业外挂隐藏算法、UI、模板或受保护工作流；
- 因“客户买过软件”自动推导 API 编排、再分发或跨组织分享权；
- 导入或生成 machine-ready NC/G-code/postprocess。

### 7.2 产品记录而不替客户判断

每个 asset/adapter 必须记录：

- source origin、owner/use-right assertion、rights evidence ref；
- private/team/site/organization/restricted sharing scope；
- redistribution 为 true/false/unknown；
- vendor/product/adapter/version/target versions；
- 席位/许可状态来源和有效期；
- 允许的输入、输出、外发目标和数据类别；
- derived-from-restricted-asset 标记；
- 二进制检查固定 false。

权利未知时允许本地只读盘点，但不允许团队分享、网络外发或 preview。真实合同
解释是 **现场待验证**，由客户法务、采购和供应商文件决定。

## 8. 多实例、目标版本与项目绑定

**产品决策：** 运行上下文是精确绑定，不是 UI 当前状态。

Preview 前必须同时匹配：

```text
product
+ target_version
+ target_instance_id
+ project_id / project_snapshot_hash
+ flow semantic_hash
+ source_snapshot_hash
+ capability_lock_hash
+ reviewed recipe_hash（投影后）
+ active permission categories
+ asset rights status
```

任一项 unknown、过期、撤销或变化即 fail closed。PID 和窗口句柄只可作为复合
运行身份的一部分；窗口标题、前台状态和列表顺序不得作为自动目标。实例退出、
项目切换、另存、版本变化、capability hash 变化或审阅过期都要求重新选择。

**现场待验证：** 真实 PowerMill/NX 的实例发现、项目身份、目标 build、Teamcenter
上下文和许可证状态。MVP 只使用两个 NX、两个 PowerMill fixture，不能外推为 live
连接已实现。

## 9. 一次授权与高级分类撤销

“一次授权”表示对当前 user + plugin version + manifest hash + 权限类别 + scope
的一次明确本地授权；相同集合不重复打扰。它不是对未来权限或 live 执行的永久
同意。

| 类别 | 默认 | 撤销效果 |
| --- | --- | --- |
| `read:selected-files` | 选择资产时开启 | 停止新扫描，保留已索引元数据 |
| `read:cam-logs` | 关闭/按目录授权 | 停止读取新日志 |
| `read:cam-process-list` | 推荐但可关闭 | 停止实例枚举 |
| `read:cam-window-titles` | 高级、可独立关闭 | 清空内存标题，不补删历史数据 |
| `write:local-artifacts` | 保存版本时授权 | 禁止新版本/候选落盘，仍可只读 |
| `write:local-database` | 安装相应本地模块时授权 | 停止新持久化 |
| `network:declared-hosts` | 默认关闭 | 立即阻断新外发并审计拒绝 |

新增权限、扩大路径/产品/版本/外发范围或 manifest hash 改变必须重新授权。
撤销立即关闭新 handle 并取消相关 worker；不删除已存数据。暂停、撤销、删除和
卸载必须是四个不同动作。曾撤销的配置重新安装不得自动恢复；重新授权也不得
自动恢复旧任务。

## 10. MVP 范围

### 10.1 包含

- AutomationAsset 与 CapabilityManifest 注册、权利和撤销状态。
- 只读导入 PowerMill `.mac`/命令日志和 NXOpen Python Journal/结构化动作日志。
- 单产品 FlowGraph、typed ports、control/data/dependency edge。
- graph parameters、显式单位、对象 selector、非递归 subflow。
- property-level source map 和 source-line/source-span 双向定位。
- opaque/unsupported 可见、原始 bytes 不可变、F0-F3 round-trip。
- GraphCommand undo/redo、不可变 FlowVersion、四层 diff。
- target-version CompatibilityReport 和 capability lock。
- deterministic fixture test、Recipe ProjectionReport 和 PreviewPlan。
- 单窗口 React Flow 视图、schema-driven inspector、渐进披露和无障碍。
- 产品插件、CapabilityManifest、Codex Skills 和结构化 AI 建议。
- 一次授权、分类撤销、多 fixture 实例与显式目标绑定。
- API 服务、集成、安全、性能和浏览器验收。

### 10.2 非目标

- 真实 NX/PowerMill 附着、实例控制、live command 或自动前台选择。
- Journal import/execute/eval/replay；PowerMill macro 执行。
- 通用 Python/PowerMill IDE、任意代码生成、任意循环或递归 subflow。
- 自动修复、删除或重写 opaque/unsupported。
- 自动创建/修改真实零件、MCS、stock、fixture、tool、holder 或 CAM operation。
- 生成刀路、CLSF、NC、G-code、postprocess 或 machine control。
- 以 fixture/dry-run 替代 CAM 仿真、碰撞/过切、机床仿真和车间批准。
- 商业二进制分析、许可证绕过、跨客户模板市场。
- 云端协同、默认外发、跨客户训练或任意第三方 JSX/脚本 UI。
- AI 自动接受建议、改变合同、生成产品源码或宣告门禁通过。

## 11. 指标

合同/安全不变量必须 100% 达成，不能用业务指标抵消。以下数值除仓库已测离线
基线外均为 **现场待验证**：

| 维度 | MVP 目标 |
| --- | ---: |
| 从选择目录到首份可解释报告的中位时间 | <= 10 分钟 |
| 到首个被用户接受的流程候选 | <= 30 分钟 |
| enabled 节点具有 asset/source span 证据 | 100% |
| unsupported/opaque 显式可见 | 100% |
| 专家确认的参数建议精确率 | >= 80% |
| 多实例 fixture 串目标 | 0 |
| 不可靠映射保持未映射 | 100% |
| 撤销后停止相关新事件/handle | <= 2 秒 |
| 未授权网络外发 | 0 |
| 商业 adapter/导出物有权利证据引用 | 100% |
| live Journal/macro、NC/G-code/postprocess 输出 | 0 |
| 8 名用户无开发帮助完成导入、改参、diff、test | >= 6 |
| 节点到源码或源码到节点 | <= 2 次操作 |

性能指标使用冻结合同第 13 节：500 nodes/800 edges 冷打开 p95 <= 2 秒，
参数增量校验 p95 <= 100 ms，fixture preview p95 <= 3 秒，主窗口 <= 350 MiB。

## 12. 路线图

### Phase 0：合同与真实样本门槛

- 冻结 JSON Schema、错误码、hash profile、fixture pack 和文件所有权。
- 获取两个产品各至少 10 份合法、脱敏、不同版本样本。
- 访谈/任务测试至少 8 名目标操作者。
- 完成商业资产权利声明、网络数据流和 SBOM/许可证评审。

退出条件：合同 golden fixtures 可重复；样本权利明确；现场问题有 owner。

### Phase 1：离线资产与解释

- 资产/能力注册、PowerMill/NX 隔离导入、FlowGraph、source map、opaque。
- CompatibilityReport、静态参数候选、资产体检和只读单窗口。

退出条件：文件发现无静默遗漏；未知内容全可见；不运行任何 Journal/macro。

### Phase 2：改编、版本与 preview

- typed drag/drop、GraphCommand、FlowVersion、diff、F0-F3、Recipe 投影。
- fixture test、PreviewPlan、权限撤销、Codex Skills 和 AI proposal 隔离。

退出条件：candidate reparse 等价；commands sent=0；全部生产门禁常驻。

### Phase 3：试点与兼容矩阵

- 两家客户、两档 Windows 工作站、目标版本 fixture/只读现场验证。
- 调整节点上限、opaque 策略、术语、权限和商业 adapter 合作模型。

退出条件：指标达标或 ADR 明确缩小范围。真实 transport/live 执行不因试点自动
进入范围，必须另立项目和重新授权。

## 13. 风险与控制

| 风险 | 影响 | 控制/退出条件 |
| --- | --- | --- |
| AST/CST 丢失注释、空白或默认状态 | 资产损坏/含义变化 | 原始 bytes 不可变，F0/F1；失败只读 |
| opaque 比例过高 | 用户看得见但改不了 | 两产品各 10 份样本；若 >15%，先交付只读解释器 |
| FlowGraph 不能投影 Recipe v1 | preview 链断裂 | ProjectionReport；缩小节点语义，不静默线性化 |
| 版本/capability 漂移 | 旧图被错误复用 | 精确 lock、CompatibilityReport、新 revision |
| NX selector/Builder 或 PowerMill 状态建模不足 | 错对象/错顺序 | 一等语义、缺上下文 blocker、现场 fixture |
| 商业外挂权利不明 | 合同/IP 风险 | 只读本地、权利证据、供应商 adapter、法务 gate |
| 插件越权或逃逸 | 本地数据/进程风险 | 内置优先、低权限 worker、无 transport、对抗测试 |
| UI 大图混乱 | 采用失败 | 主 control path、subflow、viewport culling、用户测试 |
| AI 建议被当事实 | 错工艺参数/错误放行 | proposal 隔离、人工接受、确定性复检 |
| preview 被理解为生产 | 安全事故 | 固定 dry-run 文案、0 commands、门禁常驻 |
| 性能目标不成立 | 交互不可用 | 两档工作站基准；不以削弱校验换性能 |
| n8n/第三方许可误用 | 商业发布风险 | React Flow OSS core；SBOM；n8n 代码/资产为零 |

## 14. MVP 验收

### 14.1 产品闭环

1. 从空白基础内核安装两个离线产品插件。
2. 一次授权明确列出类别，网络外发保持关闭。
3. 导入 3 个 PowerMill 和 3 个 NX 样本，产生不可变资产、体检和权利记录。
4. 两个产品使用同一合同/UI，但 parser/import dependency 完全隔离。
5. 生成 FlowGraph、source map、opaque、参数候选和兼容报告。
6. 用户完成改参、拖拽、undo/redo、命名版本和四层 diff。
7. 插入一个商业外挂 manifest-only 人工步骤；未授权时不可调用。
8. 用户选择明确 fixture version/instance/project 进行 test 和 preview。
9. Preview 显示 source/graph diff、未完成门禁和全部零执行计数。
10. 撤销实例发现或外发后，2 秒内停止相关 handle/任务，图仍可只读。

### 14.2 合同与质量

- 以 [FlowGraph v1 冻结合同](contracts/cam-flow-graph-contracts.md) 第 15 节为
  必过屏障。
- `git diff` 证明 parser 隔离；ActivityEvent 既有字段无删除/重命名。
- 20 次重复 fixture 的 hashes、diagnostics、diff、reports 字节一致。
- unknown optional fields 保存后不丢；opaque 未触及 bytes 不变。
- 单元、集成、性能和桌面/移动浏览器验收全部通过。
- 安全扫描和人工审查证明不存在 live Journal/macro、CAM transport、
  machine-ready NC/G-code/CLSF/postprocess/machine-control 生成或发送路径。

### 14.3 现场验收前保留项

以下必须明确显示“未验证”，不能阻塞离线 MVP 发布，也不能被宣传为已支持：

- 真实 PowerMill/NX 多版本连接和项目 identity；
- NX installation stubs 完整性、Teamcenter managed 行为；
- 商业外挂公开/授权 adapter 的许可证与席位状态；
- CAM simulation、gouge/collision、machine simulation 和 shop approval；
- 目标客户工作站的性能与 500 节点覆盖率；
- 客户资产再分发、组织分享和供应商导出物的法律结论。

## 15. 实施入口

开发任务、独占文件所有权、依赖、测试、提交和合并屏障见
[并行开发计划](cam-low-code-ai-parallel-development-plan.md)。每项任务可直接
发送的新对话提示词见
[并行 Agent 提示词](cam-low-code-parallel-agent-prompts.md)。
