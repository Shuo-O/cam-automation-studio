# CAM 宏 / Journal 低代码 Flow Engine 预研

> 任务：R-LOWCODE
>
> 状态：研究提案，未实现
>
> 调研与链接访问日期：2026-08-24
>
> 适用产品：Siemens NX / UG NX、Autodesk PowerMill
>
> 安全边界：本文只讨论离线解析、图编辑、确定性校验、测试夹具和
> dry-run 预览。不得由学习日志生成、返回或发送 machine-ready NC，
> 不定义 live 生产执行。

## 0. 结论摘要

### 已验证事实

- 仓库当前以 `ActivityEvent v1`、`Recipe v1`、`CommandTask v1`、
  `DiffReport v1` 和 `cam.codex.bridge.v1` 为冻结合同，消费者必须忽略
  不理解的新增可选字段，破坏性变更必须升级合同主版本。[L1][L3]
- NX Journal 语法归
  `plugins/ug-cam-copilot/src/ugcam_ai/adapters/nx_journal.py`，PowerMill
  语法归独立 PowerMill adapter；共享层不得解析任一产品语法。[L1][L3]
- React Flow 是 MIT 许可的节点 UI 库，支持自定义节点、多 handle、
  源/目标 handle 标识以及节点/边状态的保存恢复，但它不提供 CAM
  合同、类型系统、执行语义或源码往返保证。[U1][U2][U3]
- Blockly 是 Apache-2.0 许可的可视化代码编辑库。其 connection check
  可即时阻止相邻端口的不兼容连接，但官方明确说明：检查字符串本身
  没有内建语义、默认 `null` 可连接任意类型、只检查相邻连接，且不原生
  支持泛型。[U4][U5]
- Node-RED 是 Apache-2.0 许可的事件驱动低代码平台，已有 flow/tab、
  启停、锁定和 subflow 交互；其 subflow 是折叠后的可复用节点且禁止
  直接或间接自递归。[U6][U7][U8]
- n8n 仓库可读源码，但主代码采用 Sustainable Use License，部分
  `.ee.` 文件采用 Enterprise License。n8n 官方明确称其为
  source-available / fair-code，而不是 OSI 开源；直接嵌入或复制进商业
  产品需要单独法律判断或商业许可。[U9][U10][U11]

### 项目主张

1. 新建独立、产品中立的 `cam.flowgraph.v1` 合同，作为编辑器和编译器
   之间的 canonical IR；不把 React Flow、Blockly、Node-RED 或 n8n
   的内部 JSON 当作持久化合同。
2. 首选 React Flow OSS core 作为视图与手势层。FlowGraph、类型检查、
   source map、版本、校验、preview 和 codegen 均由项目自己的确定性
   服务负责。
3. `Recipe v1` 继续作为现有 dry-run 预览与审阅链的投影合同。
   FlowGraph 只有在可确定性、无歧义地降级为 Recipe 时才允许生成该
   投影；不得把一般图静默压平成线性步骤。
4. parser 必须产生 token/CST 保留层、语义层和 source map。未支持或
   不能无损往返的源码片段必须成为 `opaque` 节点并锁定语义编辑；
   codegen 只能对已证明支持的 span 做最小补丁。
5. 图合同、类型检查、迁移、语义哈希、codegen、重新解析校验、fixture
   测试和 preview 必须确定性。AI 只能解释诊断、提出结构化建议补丁，
   不得直接修改合同或绕过安全门禁。
6. MVP 只生成本地可审查的源代码差异、图差异和 fixture dry-run 结果，
   不附着真实 CAM，不发送命令，不生成后处理或机床输出。

### 研究假设

- 一线模具刀路编程人员更容易接受“工艺步骤 + 参数卡 + 明确前后关系”
  的单窗口界面，而不是通用编程语言式画布；端口和依赖细节应逐步披露。
- 典型单条工艺流在 20-150 个节点，复杂模板在 500 个节点以内。该分布
  必须通过至少 8 名目标操作者、两种产品的真实脱敏样本验证。
- 本文第 11 节的性能阈值是验收目标，不是现有实现测量结果。

## 1. 证据口径与范围

本文使用以下标识，避免把建议写成已经实现的能力：

| 标识 | 含义 | 使用规则 |
| --- | --- | --- |
| 已验证事实 | 仓库冻结合同、已读取源码仓库、官方文档或许可证明确写出的事实 | 必须能追溯到 `[L*]` 或 `[U*]` |
| 项目主张 | 本预研建议采纳的产品或架构决定 | 尚未实现，需 ADR/合同评审批准 |
| 研究假设 | 尚缺目标用户、真实数据或性能实测支持的判断 | MVP 前必须设计验证方法 |

本报告完整阅读了根
[AGENTS.md](../../AGENTS.md)、[README.md](../../README.md)、根
[architecture.md](../architecture.md)、UG 插件 README 与
[architecture.md](../../plugins/ug-cam-copilot/docs/architecture.md)、
[并行开发合同](../contracts/parallel-development-contracts.md)、
`ActivityEvent` JSON Schema、12 个 `examples/contracts` 冻结 fixture，
以及 NX/PowerMill 的 Session Comparison 和 Recipe Review 合同。

本报告不评估真实 CAM API 的可用性，不执行宏或 Journal，不评估任何
机床、控制器或后处理器。

## 2. 仓库基线与不可破坏约束

### 2.1 已验证事实

当前主链是：

```text
NX Journal / PowerMill macro or log / ActivityEvent JSONL
                    |
                    v
           产品隔离的静态 parser
                    |
                    v
 ActivityEvent -> Session -> Recipe -> 人工审阅
                    |
                    v
          明确实例的 dry-run preview
                    |
                    v
       CommandResponse + DiffReport
```

现有合同对本研究有六个直接约束：

1. `schema_version` 顶层固定为整数 `1`；新增字段必须是可选字段，消费
   者必须忽略不理解的新增可选字段。
2. 共享动作使用 `cam.*`；新产品专属动作只使用 `nx.*` 或
   `powermill.*`。历史 `part.*` / `object.*` 只为兼容旧事件保留。
3. `RecipeStep` 当前是带 `order` 的步骤，含结构化 `arguments`、
   `condition`、证据引用、风险和审阅状态；`blocked` 步骤永不进入
   transport。
4. `recipe_hash` 是依据 RFC 8785 规范化 JSON 计算的语义哈希，布局、
   显示名、时间、证据和批注不进入语义。
5. `CommandTask v1` 只允许 `read_only` 或 `dry_run`，并必须绑定产品、
   目标版本、目标实例、项目和 recipe hash。
6. `approved_for_simulation` 不等于生产批准；CAM 仿真、碰撞/过切检查
   和车间批准必须保持独立门禁。

### 2.2 项目主张：与既有合同的关系

- `cam.flowgraph.v1` 是新增的兄弟合同，不重命名或替换任何 v1 合同。
- `ActivityEvent` 可在未来合同评审后增加可选的 `flow_graph_id`、
  `flow_revision_id`、`flow_node_id`，但旧事件和旧 JSONL 不需要回填。
- `Recipe` 可增加可选 `flow_graph_ref`，`RecipeStep` 可增加可选
  `flow_node_id`；现有 recipe hash 规则若纳入这些字段会改变语义，故
  v1 中这些引用只作为非语义追踪信息，不能暗改既有 hash 算法。
- FlowGraph 编译为 Recipe 时，必须生成 `ProjectionReport`，列出每个
  node/edge 的投影结果、被拒绝原因和证据。存在未投影的语义节点或边
  时，结果不得标为可预览。
- FlowGraph 不能直接成为 transport payload。现有
  Recipe -> reviewed `CommandTask` -> adapter preview 门禁保持不变。

## 3. 上游项目、许可证与可复用边界

### 3.1 许可证表

| 项目 | 一手仓库 | 2026-08-24 所见许可证 | 可否直接作为依赖 | 本项目处理意见 |
| --- | --- | --- | --- | --- |
| React Flow / xyflow | [xyflow/xyflow][U1] | [MIT](https://github.com/xyflow/xyflow/blob/main/LICENSE) | 可以，保留版权和许可声明 | 候选画布层；仅采用 OSS core，不把 Pro 内容视为 MIT 资产 |
| Blockly | [RaspberryPiFoundation/blockly][U4] | [Apache-2.0](https://github.com/RaspberryPiFoundation/blockly/blob/main/LICENSE) | 可以，履行 LICENSE/NOTICE 和变更声明；需纳入专利条款审查 | 可借鉴类型连接、序列化和无障碍，不作为首选主画布 |
| Node-RED | [node-red/node-red][U6] | [Apache-2.0](https://github.com/node-red/node-red/blob/main/LICENSE) | 法律上可作为依赖或 fork，但集成成本和运行语义不合适 | 借鉴 flow/tab、启停、锁定、subflow，不引入其 runtime |
| n8n | [n8n-io/n8n][U9] | [Sustainable Use License](https://github.com/n8n-io/n8n/blob/master/LICENSE.md)；部分代码为 [Enterprise License](https://github.com/n8n-io/n8n/blob/master/LICENSE_EE.md) | 商业嵌入不可默认视为允许 | 只做黑盒交互研究；不复制代码、样式或受限资产，复用前必须法律批准 |

> 许可证结论不是法律意见。实现阶段仍需锁定具体包版本、生成 SBOM、
> 扫描传递依赖并由组织法务确认。许可证可能在访问日期后改变。

### 3.2 React Flow

#### 已验证事实

- React Flow 官方将 handle 定义为节点连接点。内置节点有一个 source
  和一个 target handle，自定义节点可以有多个 handle；多个 handle
  必须使用唯一 ID，edge 使用 `sourceHandle` / `targetHandle` 指定连接
  点。[U2]
- 官方保存/恢复示例通过 React Flow instance 的 `toObject()` 或本地
  nodes/edges state 持久化图和 viewport。[U3]
- 仓库 README 将其描述为可定制的 node-based UI 库，许可证为 MIT。
  [U1]

#### 可复用模式

- 自定义节点、端口 handle、边路由、选择、缩放、框选、拖拽和 viewport。
- React 受控状态模式，便于把每次 UI 操作转换为项目自己的事务命令。
- `sourceHandle` / `targetHandle` 可映射到稳定 `port_id`。

#### 不适合直接复用

- `nodes + edges + viewport` 只能作为 UI state，不能表达 CAM 类型、
  单位、权限、capability、source span、opaque 策略或安全门禁。
- React Flow 的 loose connection mode 允许 typeless handle 双向连接，
  不适合作为生产合同默认值。
- 保存/恢复示例是界面状态示例，不是版本迁移、兼容合同或源码往返方案。

### 3.3 Blockly

#### 已验证事实

- Blockly 用图形积木表达变量、表达式、循环等代码概念，官方推荐新项目
  使用 JSON serialization；旧 XML 格式保留但不再获得新特性。[U4][U5][U12]
- JSON serialization 由多个 serializer 组成，内建 blocks/variables
  serializer，也允许插件注册自有 serializer，并显式规定反序列化顺序。
  [U12]
- connection check 是 nullable string array。双方至少共享一个字符串
  才匹配；任一为 `null` 则可连接任意类型。系统只看直接相邻连接，不
  自带上下文约束和泛型。[U5]
- 仓库 README 声明键盘和 screen reader 可用，并提供 WCAG 2.2 AA
  相关合规报告入口。[U4]

#### 可复用模式

- 拖拽时立即给出类型兼容反馈。
- 稳定 JSON serializer、插件序列化顺序和 extra state 机制。
- 键盘操作、screen reader、积木形状暗示连接兼容性的无障碍思路。

#### 不适合直接复用

- Blockly 更适合语法树/表达式嵌套；CAM 工艺流需要同时表达顺序、数据、
  非数据依赖、证据和源文件跨度。
- 字符串集合类型过弱；本项目需要单位维度、对象选择器、产品/版本能力
  和可赋值规则。
- 立即相邻检查无法独立满足跨图循环、依赖闭包、门禁和 subflow 接口
  校验。

### 3.4 Node-RED

#### 已验证事实

- Node-RED 将 flow 表示为编辑器工作区中的 tab，支持重排、属性编辑、
  启停、隐藏和锁定。[U7]
- subflow 是折叠为单节点的节点集合，可像普通节点一样放入工作区；
  subflow 不能直接或间接包含自身。[U8]
- subflow 支持实例属性、输入/输出和模块元数据，但官方当前说明其自身
  至多一个输入、可有多个输出。[U8]
- 项目为 OpenJS Foundation 项目，许可证为 Apache-2.0。[U6]

#### 可复用模式

- flow tab / subflow tab 的心智模型。
- 禁用、锁定、隐藏和从选区创建 subflow 的交互。
- 节点 palette、属性侧栏和可复用实例属性。

#### 不适合直接复用

- Node-RED runtime 以事件/message 传递和 deploy 为核心；CAM Journal/
  宏的主要语义是有证据的顺序操作、对象状态与版本化源代码。
- “部署”按钮和实时 runtime 容易让操作者误解为允许发送 CAM 命令。
- 引入整个编辑器/runtime 会扩大供应链、插件和网络攻击面。

### 3.5 n8n

#### 已验证事实

- n8n 自述为可视化工作流/AI automation 平台，支持 custom node。[U9]
- 官方许可证只允许内部业务、非商业或个人用途等限定范围；部分企业代码
  需要 Enterprise License。官方明确说明它是 source-available，而非
  OSI 开源。[U10][U11]

#### 可借鉴模式

- 节点搜索、凭据/能力分离、节点说明、分步测试和执行可观察性。
- 对技术用户保留高级代码扩展入口、对普通用户默认隐藏的逐步披露思想。

#### 不适合直接复用

- 许可不支持把仓库当作普通开源组件纳入商业 CAM 产品。
- integration/credential/cloud workflow 语义与本项目的本地 CAM 源码
  保真、安全门禁和产品版本能力不一致。
- n8n 的“执行工作流”体验不可原样移植到始终 review-first 的 CAM 场景。

## 4. ADR-001：Flow Engine 与编辑器技术路线

> 分类：项目主张
>
> 状态：Proposed
>
> 决策日期：2026-08-24

### 4.1 背景与决策驱动

必须同时满足：

- NX / PowerMill parser 物理隔离；
- canonical graph 不绑定 UI 框架；
- typed ports、三类边、subflow、opaque 与 source map；
- 不能无损往返时 fail closed；
- 单窗口、低门槛、可撤销；
- deterministic validation/codegen/preview；
- 本地优先、插件显式安装、权限可撤销；
- 不产生或发送 machine-ready NC。

### 4.2 选项比较

| 选项 | 合同独立性 | CAM 交互适配 | 往返可控性 | 许可/集成风险 | 结论 |
| --- | --- | --- | --- | --- | --- |
| A. 自有 FlowGraph + React Flow view | 高 | 高，可自定义节点和端口 | 高，parser/compiler 自有 | MIT，依赖面可控 | **选择** |
| B. Blockly 作为主模型与 view | 中低 | 表达式强，工艺图一般 | 中，需对抗其 block model | Apache-2.0 | 备选，仅借鉴 |
| C. fork Node-RED editor/runtime | 低 | 事件流强，CAM 源码弱 | 低，deploy 语义冲突 | Apache-2.0，但体量大 | 拒绝 |
| D. fork n8n | 低 | 集成型 workflow 强 | 低 | source-available / EE | 拒绝 |
| E. 从零写 Canvas/WebGL 编辑器 | 高 | 高 | 高 | 无上游许可，但成本最高 | 暂缓 |

### 4.3 决策

采用三层架构：

```text
React Flow view/controller
        |
        | GraphCommand / Selection / ViewState
        v
cam.flowgraph.v1 + deterministic graph service
        |
        | validate / project / diff / test
        v
NX adapter                 PowerMill adapter
parser + source map        parser + source map
minimal-patch codegen      minimal-patch codegen
        \                     /
         \---- dry-run preview + DiffReport
```

- React Flow node/edge 对象只是缓存视图，随时可由 FlowGraph + layout
  重建。
- renderer 不读取产品语法；产品节点可通过 schema-driven inspector
  显示，但 parse/codegen 只在对应 adapter worker 内。
- FlowGraph service 拥有事务、undo/redo、版本、diff、类型检查和校验。
- 生成结果只进入本地 preview artifact；进一步进入现有 CommandTask
  仍需人工审阅和既有五项绑定。

### 4.4 后果

正面：

- 可替换前端库而不迁移业务合同。
- 对未知源码、插件缺失和版本不匹配可统一 fail closed。
- 可针对一线用户定制 CAM 术语，不暴露通用编程框架。

成本：

- 必须实现独立布局投影、命令事务、source map、type registry 和
  compiler contract。
- 不能依赖 React Flow 的序列化解决版本和往返问题。
- 插件 UI 首版受限为 schema-driven，牺牲任意自定义 JSX 能力。

## 5. 面向一线模具刀路编程人员的交互模型

### 5.1 研究假设：目标用户

目标操作者熟悉刀具、毛坯、工序、坐标系、边界、余量、步距、刀路计算
和仿真，但不应被要求理解 AST、JSON Schema、dependency graph 或
source map。常见任务是：

1. 导入一段已录制 Journal/宏；
2. 看懂“这段流程做了什么”；
3. 修改少量参数或步骤顺序；
4. 找到不可靠选择器、版本差异或缺失前置条件；
5. 对比改前/改后并在 fixture/test copy 上预览；
6. 保存一个可审阅版本。

该画像需用现场访谈验证，不应仅以开发者经验定案。

### 5.2 项目主张：单窗口布局

```text
┌ 产品/版本 | 文件 | 版本状态 | 撤销/重做 | 对比 | 测试 | 预览 ┐
├───────────┬──────────────────────────────┬──────────────┤
│ 步骤库     │ 工艺流程画布                  │ 当前步骤参数  │
│ 搜索/阶段  │ 默认只显示步骤与主顺序         │ 证据/风险     │
│ 可折叠     │ 选择时才展开端口与依赖         │ 高级项折叠    │
├───────────┴──────────────────────────────┴──────────────┤
│ 可折叠底栏：问题 | 源码 | 图差异 | 源码差异 | 测试 | 预览 │
└─────────────────────────────────────────────────────────┘
```

- 首屏是实际编辑器，不是产品宣传页。
- 左侧步骤库按“准备、几何、刀具、策略、参数、计算、检查”等 CAM
  阶段组织，默认只显示当前产品/版本 capability 支持的节点。
- 中央画布默认突出 control edge；data/dependency edge 淡化或隐藏，
  选中节点、诊断或“高级关系”模式时才展开。
- 右侧只编辑当前选中节点，参数使用单位明确的数值输入、枚举、对象选择
  器和开关；不让用户直接编辑 JSON。
- 底栏默认收起，错误、diff、测试或 preview 产生结果时自动提示但不
  抢焦点。

### 5.3 逐步披露

| 层级 | 默认可见内容 | 进入方式 |
| --- | --- | --- |
| 基础 | 步骤名称、主顺序、核心 CAM 参数、风险状态 | 默认 |
| 关系 | 输入/输出端口、data/dependency edge、分支条件 | 选中节点或切换“关系” |
| 证据 | 来源文件、行列、原始片段、解析置信度、capability | 展开“证据” |
| 专家 | adapter payload、类型详情、迁移报告、opaque 原文 | 明确开启“专家信息” |

不使用颜色作为唯一状态信号。`opaque`、`blocked`、权限缺失、版本不匹配
必须同时有图标、短文本和可定位诊断。

### 5.4 图与源码双向定位

- 单击图节点：源码面板滚动并高亮该节点的 primary span；多 span 时列出
  “定义、参数、提交/结束”等来源。
- 单击源码行：选择覆盖该位置的最小语义节点；多个候选时显示面包屑，
  不猜测。
- hover 节点：源码 gutter 标记对应 span；hover 源码：画布只强调相关
  node/edge。
- 节点没有可靠 source map 时明确显示“合成节点”或“映射不完整”。
- 移动画布布局不修改源码；改标签、折叠状态、注释视图也不触发 codegen。
- opaque span 可查看原文和诊断，但不能直接通过图属性改写。

### 5.5 编辑、undo、diff 和版本

- 所有语义编辑进入 `GraphCommand` 事务日志；一次拖拽插入、参数确认、
  批量移动分别作为一个 undo 单元。
- layout command 与 semantic command 分开。布局变化不污染语义 diff、
  recipe hash 或 codegen。
- undo/redo 不调用 parser/codegen；只回放可逆 GraphCommand，然后运行
  增量确定性校验。
- 保存版本生成不可变 `revision_id`、父 revision、语义哈希、作者和
  说明。自动保存只能产生本地草稿 checkpoint，不能替代用户命名版本。
- diff 默认显示“步骤新增/删除/移动、参数、连接、capability、风险”
  的语义差异；源码 diff 是第二视图，布局 diff 默认忽略。
- opaque 内容即使字节未变，也应在 diff 中显示“保留的未知结构”，避免
  用户误以为系统理解了它。

### 5.6 test 与 preview

- “测试”只在本地 fixture/test project snapshot 上运行 parser、
  validator、投影和重新解析断言。
- “预览”只生成受支持 span 的候选源码差异、Graph diff 和
  `DiffReport`；默认不写回原文件。
- preview 面板固定显示产品、目标版本、源 hash、图语义 hash、capability
  lock、未完成门禁和 `commands_sent=0`（fixture）。
- 任何 unsupported/ambiguous/permission revoked/type error/round-trip
  mismatch 都使 preview 不可用，并定位到 node、port 和 source span。
- UI 不使用“运行”“部署”“一键加工”等可能被理解为 live 执行的主按钮
  文案。

## 6. `cam.flowgraph.v1` 合同建议

> 分类：项目主张

### 6.1 设计原则

1. canonical graph 与画布布局分离。
2. control、data、dependency 三类边显式存储，不从屏幕位置或节点顺序
   推断。
3. port 类型来自版本化 capability manifest；图保存所需 manifest
   lock，不能只依赖“当前安装最新版”。
4. 源码未知结构是数据，不是可丢弃噪声。
5. 任何影响执行含义的变化都进入语义哈希；布局、选择、viewport、
   人工批注不进入。
6. JSON 消费者可忽略未知可选字段；编辑器则必须保留未知字段，避免读写
   后丢失。
7. 未知 node type 不得被忽略，必须 materialize 为 opaque/unsupported
   并阻断相应投影。

### 6.2 顶层结构

以下是合同形状示意，不是已实现 Schema：

```json
{
  "schema_version": 1,
  "contract": "cam.flowgraph.v1",
  "graph_id": "flowgraph:example",
  "revision_id": "revision:example:1",
  "parent_revision_id": null,
  "product": "nx",
  "target_versions": ["NX <reviewed-version>"],
  "entry_flow_id": "flow:main",
  "parameters": [],
  "flows": [],
  "source_documents": [],
  "capability_lock": {},
  "required_gates": [
    "recipe_review",
    "target_version_validation",
    "cam_simulation",
    "collision_check",
    "shop_approval"
  ],
  "semantic_hash": "sha256:<64-lowercase-hex>",
  "source_snapshot_hash": "sha256:<64-lowercase-hex>",
  "layout": {},
  "extensions": {}
}
```

| 字段 | 必需 | 语义 |
| --- | --- | --- |
| `schema_version` | 是 | 固定 `1` |
| `contract` | 是 | 固定 `cam.flowgraph.v1` |
| `graph_id` | 是 | 逻辑身份，不随 revision 改变 |
| `revision_id` | 是 | 不可变版本身份 |
| `parent_revision_id` | 是 | 根版本为 null |
| `product` | 是 | 单一 `nx` 或 `powermill`；MVP 不允许混合产品图 |
| `target_versions` | 是 | 已声明并审阅的目标版本，可为空表示未知 |
| `entry_flow_id` | 是 | 入口 flow |
| `parameters` | 是 | 图级类型化参数 |
| `flows` | 是 | main flow 与 subflow definitions |
| `source_documents` | 是 | 原文快照引用、编码、换行与 hash |
| `capability_lock` | 是 | plugin/node/type manifest 的版本与 hash |
| `required_gates` | 是 | 不得弱化既有门禁 |
| `semantic_hash` | 是 | 图语义的 RFC 8785 + SHA-256 |
| `source_snapshot_hash` | 是 | 所有输入源快照清单的 hash |
| `layout` | 是 | 非语义 view state |
| `extensions` | 是 | namespaced 可选扩展 |

### 6.3 Flow、Node 与 Port

`FlowDefinition`：

| 字段 | 含义 |
| --- | --- |
| `flow_id` | 图内稳定唯一 |
| `kind` | `main` 或 `subflow` |
| `name` | 用户显示名，不参与语义 hash |
| `interface_ports` | subflow 对外的类型化端口 |
| `parameter_ids` | 可见图级参数 |
| `nodes` | `FlowNode[]` |
| `edges` | `FlowEdge[]` |
| `source_map_refs` | flow 级来源 |

`FlowNode`：

| 字段 | 含义 |
| --- | --- |
| `node_id` | graph revision 之间尽量稳定，不能使用画布序号 |
| `node_type` | `cam.*`、`nx.*`、`powermill.*`、`flow.*` 或 `opaque.*` |
| `node_type_version` | capability manifest 中的精确版本 |
| `enabled` | 是否进入投影 |
| `risk` | `safe`、`review`、`blocked`，不得由 UI 降级 |
| `review_status` | 保持现有审阅语义 |
| `bindings` | port/parameter 到 literal、graph parameter 或 output 的绑定 |
| `configuration` | 由 JSON Schema 定义的确定性节点配置 |
| `source_map_refs` | node/property/port 对应来源 |
| `fidelity` | `lossless`、`semantic`、`opaque`、`unmapped` |
| `capability_ref` | 锁定到 manifest node definition |
| `extensions` | namespaced 可选字段 |

`PortDefinition` 不由 React handle 决定，而由 capability manifest 声明：

| 字段 | 允许值/含义 |
| --- | --- |
| `port_id` | node type 内稳定 ID |
| `direction` | `input` 或 `output`；MVP 不用双向端口 |
| `edge_kinds` | `control`、`data`、`dependency` 的非空子集 |
| `type_ref` | 版本化类型，例如 primitive、带单位 number、对象选择器 |
| `cardinality` | `one`、`optional`、`many` |
| `required` | preview 前是否必须连接/绑定 |
| `constraints` | range、enum、unit dimension、产品/版本 selector 等 |
| `sensitivity` | `public`、`project_local`、`secret_ref`；不得内嵌凭据 |

类型系统应是 JSON Schema 兼容的封闭基础集合加 namespaced semantic
type：

- primitives：`string`、`integer`、`number`、`boolean`、`enum`、`path_ref`；
- CAM values：`cam.length`、`cam.angle`、`cam.ratio`、`cam.coordinate_frame`；
- selector：`cam.object_selector` + product/kind/version constraints；
- collections：`list<T>`，MVP 不提供用户自定义泛型；
- control token：只允许 control edge，不作为 data value；
- opaque：不可连接到已知 data port，除非 manifest 提供显式 adapter。

单位不是标签字符串。`cam.length:mm` 到 `cam.length:inch` 需要显式、
确定性的 conversion node；不得自动换算后隐藏该事实。

### 6.4 Edge

`FlowEdge` 至少包含：

| 字段 | 含义 |
| --- | --- |
| `edge_id` | 图内稳定唯一 |
| `kind` | `control`、`data` 或 `dependency` |
| `source` | `{node_id, port_id}` |
| `target` | `{node_id, port_id}` |
| `condition` | 仅允许确定性表达式 AST；无条件为 null |
| `priority` | 同一分支的稳定顺序；非分支为 null |
| `source_map_refs` | 原始跳转/绑定/顺序证据 |
| `extensions` | namespaced 可选字段 |

语义：

- `control`：表达可执行步骤先后与显式分支。
- `data`：表达值从 output 到 input；必须满足类型、单位和 cardinality。
- `dependency`：表达“必须先完成/存在”，但不传值、不替代 control。

校验：

- data edge 必须 output -> input，且类型可赋值。
- dependency graph 必须无环。
- control graph 默认无环；循环只允许显式、受 capability 支持的
  `flow.loop` 节点。MVP 可完全禁用循环。
- edge 不能跨 flow，跨 flow 只通过 subflow interface port。
- 同一 input 是否允许多个 source 由 cardinality 决定，不能由 UI 猜测。

### 6.5 Parameter 与 Binding

沿用现有 `RecipeParameter` 的 name/type/required/default/samples/
constraints/evidence 思路，并增加稳定 `parameter_id`。绑定只允许：

```text
literal
graph_parameter(parameter_id)
node_output(node_id, port_id)
secret_ref(secret_id)        # 只引用，不含秘密值
```

- literal 必须通过 type/constraint/unit 校验。
- graph parameter 改名不改变 `parameter_id`。
- 删除被引用参数必须作为一个可回滚事务，并显示影响节点。
- object selector 必须保留产品、目标版本、选择策略、解析状态和证据；
  录制期临时 ID 不能静默升级为稳定 selector。
- secret 不进入 graph JSON、diff、source map、AI context 或 fixture。

### 6.6 Subflow

- subflow 是独立 `FlowDefinition`，实例使用
  `node_type=flow.subflow_call`，通过 `subflow_revision_ref` 锁定版本。
- 接口只暴露显式 ports 与 parameters；内部 node ID 不可从调用方引用。
- 修改 subflow 接口是语义变更，必须产生新 revision 和迁移报告。
- MVP 禁止直接或间接递归，与 Node-RED 的安全可理解模式一致。[U8]
- “从选中节点创建 subflow”只有在边界 edge 均可唯一映射为接口时启用；
  否则给出诊断，不自动合并端口。
- 展开 subflow 是导航，不是复制；inline preview 只读。

### 6.7 Opaque / Unsupported Node

这是 round-trip 安全的核心，不是异常兜底：

| 字段 | 含义 |
| --- | --- |
| `node_type` | `opaque.source_fragment` 或 namespaced unsupported type |
| `opaque_reason` | `unknown_syntax`、`unsupported_api`、`ambiguous_mapping`、`missing_capability`、`parse_error` |
| `document_id` / `span_id` | 原始字节范围 |
| `content_hash` | 原始片段 hash |
| `round_trip_policy` | 固定 `preserve_exact` 或 `block_codegen` |
| `diagnostics` | 人可读且可定位 |
| `semantic_editable` | 必须为 false |

规则：

- 只移动 opaque 节点的布局不会改变源码。
- 原文和 hash 未变、邻接编辑不影响其 span 时，可按字节原样拼回。
- 编辑需要穿越、拆分、重排或重缩进 opaque span 时，preview 必须阻断。
- capability 缺失时先显示 `missing_capability`，安装正确版本后可以重新
  解析；不得把缺失插件误报为源码损坏。
- 删除 opaque 节点是显式的高风险源码删除事务，必须显示原文 diff 和
  二次确认；MVP 可直接禁止。

### 6.8 Source Mapping

`SourceDocument`：

- `document_id`、脱敏 display name、product、language；
- `content_hash`、byte length、encoding、BOM、newline style；
- 只读 source snapshot 的本地内容寻址引用；
- parser/capability 版本。

`SourceSpan`：

```text
span_id
document_id
start_byte / end_byte
start_line / start_column
end_line / end_column
role: primary | parameter | control | trivia | opaque
mapping_quality: exact | derived | ambiguous | synthetic
```

每个语义属性应允许独立 span，例如
`configuration.tolerance -> span-17`。单一 `source_line` 无法支撑精确
双向定位和最小补丁，但现有 `ActivityEvent.source_line` 仍保留兼容。

解析器必须保留：

- token 顺序、空白、换行、注释和引号风格；
- 原始 literal 与规范值；
- 语句/命令边界；
- 未识别语法的精确 byte slice；
- 由一个语义 node 跨多个源位置形成的多 span 关系。

### 6.9 Hash、版本与兼容

建议区分三个 hash：

| hash | 包含 | 排除 |
| --- | --- | --- |
| `semantic_hash` | product/version、parameters、node types/config/bindings、edges、subflow refs、capability lock、gates | layout、viewport、selection、显示名、批注、source spans |
| `source_snapshot_hash` | source document ID/hash/encoding/newline 清单 | graph layout |
| `artifact_hash` | 完整保存文件 | 无 |

`semantic_hash` 沿用既有 RFC 8785 + SHA-256 规则，但使用独立
FlowGraph canonicalization profile，不能复用或改变 `recipe_hash` 含义。

兼容规则：

1. `cam.flowgraph.v1` 内只做新增可选字段和新 namespaced node type。
2. 删除、重命名、改变字段类型/默认语义或安全等级需要
   `cam.flowgraph.v2`。
3. node definition 使用独立 semver：patch 不变更合同；minor 只能做向后
   兼容新增；major 需要显式 migration。
4. capability lock 缺失时图仍可读，但相关 node 为 unsupported，不能
   preview。
5. 只读消费者可以忽略未知可选字段；会重写文档的编辑器必须完整保留
   未知字段。
6. migration 产出 `MigrationReport`，逐 node 列出
   `unchanged/migrated/opaque/blocked`；禁止打开文件时自动覆盖原版本。
7. migration 后必须重新 parse/project/validate，语义变化需用户确认并
   保存新 revision。

## 7. Parser / Graph / Codegen 往返保真

> 分类：项目主张

### 7.1 保真等级

| 等级 | 保证 | 允许行为 |
| --- | --- | --- |
| F0 `byte_exact` | 无语义编辑时输出与输入字节一致 | 只读、布局、批注 |
| F1 `token_exact_outside_edits` | 编辑 span 外 token/trivia 字节一致 | 支持字段的最小补丁 |
| F2 `semantic_round_trip` | `parse(generate(graph))` 与编辑后图语义等价 | 新建的完全受支持节点 |
| F3 `opaque_preserved` | 未知 span hash 与字节保持 | 未触及 opaque 的邻接编辑 |
| FB `blocked` | 无法证明上述任一保证 | 不生成候选源码 |

文件、flow、node 和 property 都记录 fidelity；文件总体 fidelity 取最弱
值。不能把 F2 宣称成 F0。

### 7.2 确定性流水线

```text
source bytes
  -> encoding/newline/hash
  -> lexer/token stream + CST/AST
  -> product adapter semantic extraction
  -> FlowGraph + property-level SourceMap + opaque spans
  -> GraphCommand transaction
  -> schema/type/control/dependency/safety validation
  -> affected-span analysis
  -> minimal source patch in memory
  -> parse generated candidate again
  -> semantic graph comparison + untouched-span byte comparison
  -> source diff + graph diff + ProjectionReport + DiffReport
```

任何阶段不确定都返回诊断和 `blocked`，不得“尽量生成”。

### 7.3 产品边界

- NX：仍由 `nx_journal.py` 静态解析，绝不 import/exec Journal。Python
  AST 可用于语义识别，但往返层还需 token/CST 保留注释、空白和原始
  literal；具体库须在实现任务中另行评估。
- PowerMill：仅由 `powermill_macro.py` 处理。lexer 必须保留命令顺序、
  引号内空白、delimiter、注释和未知命令原文。
- 共享 FlowGraph service 只见 canonical node/port/edge/source map，
  不见产品命令语法。
- adapter codegen 只接受已校验 GraphPatch 和 capability lock，不接受
  自由文本 prompt。

### 7.4 禁止静默重写的情形

- parser 只识别语句的一部分；
- 一个 source span 映射到多个冲突语义；
- 编辑跨越 opaque/ambiguous span；
- capability/node major version 不匹配；
- 输出重新解析后 node/edge/binding/units/gates 不等价；
- 未触及 span 的 token 或 trivia 改变；
- 原始编码或换行无法保留；
- selector、单位、坐标系、默认值或隐藏 CAM 状态未解析；
- 图无法无损投影为现有 Recipe v1；
- codegen 输出命中 NC、G-code、postprocess、machine control 或 live
  Journal 安全扫描。

最后一项只返回结构化拒绝，不保留或展示敏感 payload。

## 8. Node / Plugin SDK、Capability 与权限

> 分类：项目主张

### 8.1 与现有 app plugin manifest 的关系

现有 `app-plugin.json` 已有 `schema_version`、`id`、`version`、
`features`、`dependencies`、`permissions`。保持它们不变，未来只增加
可选字段：

```json
{
  "capability_manifest": "capabilities/flow-nodes.v1.json",
  "flow_sdk_range": ">=1.0.0 <2.0.0"
}
```

能力文件采用 `cam.node.capabilities.v1`，至少声明：

| 类别 | 字段 |
| --- | --- |
| 身份 | plugin ID/version、manifest version/hash、签名/来源 |
| 适用性 | product、target version ranges、required host capabilities |
| 节点 | node type/version、category、ports、configuration schema、defaults |
| 编译 | parse support、codegen support、projection support、fidelity level |
| 安全 | static risk floor、required gates、forbidden modes |
| 权限 | 每个 hook 所需 permission categories |
| 迁移 | from/to node version、migration ID、是否可逆 |
| 测试 | fixture pack、golden hash、SDK conformance version |

`risk` 是下限：manifest 定义 `review` 的节点不能由图文件或插件运行时
降成 `safe`。

### 8.2 SDK 表面

MVP SDK 只提供 JSON-RPC 风格的纯数据 hook：

```text
describe_capabilities()
parse_source(read_handle, options) -> graph fragment + source map + diagnostics
validate_node(node, context) -> diagnostics
validate_graph(graph summary) -> diagnostics
project_to_recipe(graph) -> recipe candidate + ProjectionReport
generate_preview(source snapshot, GraphPatch) -> in-memory patch + diagnostics
reparse_candidate(candidate) -> graph fragment
migrate_node(node, from, to) -> node + MigrationReport
run_fixture(fixture_id) -> deterministic test result
```

- hook 输入输出必须可 JSON Schema 校验，并有大小、时间和深度限制。
- UI schema 只允许宿主提供的输入控件、图标和文案；MVP 不加载第三方
  React/HTML/脚本到主窗口。
- 节点插件不能直接获取工作区路径、网络、进程、窗口句柄、凭据或 CAM
  transport。
- parser 获得一次性的只读 file handle；generator 只返回内存候选 patch，
  由宿主显示 diff 后另行保存。

### 8.3 Sandbox

- 插件 hook 运行在独立低权限 worker process，不与 Web UI、数据库或
  transport 同进程。
- 默认无网络、无子进程、无动态库加载、无任意文件系统；临时目录有配额
  且任务结束清理。
- Windows 上“普通 Python 子进程”不能被宣称为完整 sandbox。实现需组合
  OS ACL、Job Object/低完整性或等价隔离，并做逃逸测试；达不到时只能
  安装受信内置插件。
- IPC 每次调用带 plugin/manifest hash、correlation ID、deadline 和
  cancellation；超时、崩溃或超限只隔离该插件。
- 结果重新由宿主 Schema、类型、安全和敏感内容扫描校验；不能信任插件
  自报 risk。
- preview/codegen worker 永远没有 transport handle。

### 8.4 一次授权与分类撤销

“一次授权”定义为：安装时对当前 manifest hash 中声明的低风险本地权限
进行一次明确授权，后续相同版本/权限集合不重复打扰。它不是永久万能
授权。

规则：

1. grant 绑定用户、plugin ID、plugin version、manifest hash、权限类别
   和授权时间。
2. 新增权限、扩大路径/产品/版本范围或 manifest hash 改变时重新询问。
3. 设置页可按类别撤销，例如：
   `read:selected-files`、`read:cam-logs`、`read:cam-process-list`、
   `read:cam-window-titles`、`write:local-artifacts`、
   `write:local-database`、`network:declared-hosts`。
4. 撤销立即关闭新 handle 并取消相关 worker 任务，但不删除已保存数据；
   删除数据走独立确认。
5. 失去权限的节点仍可只读显示，状态为 `capability_unavailable`，不能
   preview；重新授权不自动执行任务。
6. `review:cam-command` 不能解释为 live transport 权限。MVP 不向 node
   SDK 暴露任何 live 权限。
7. 凭据只以宿主管理的 `secret_ref` 传递给明确获准的非 CAM 外部连接
   能力；本 MVP 不需要该能力。

### 8.5 Capability 缺失与降级

- manifest 不存在：图可打开，节点变为 opaque/unsupported。
- target version 不匹配：节点只读，诊断指出可用版本范围。
- parser 可用而 codegen 不可用：允许导入/解释，禁止语义编辑与 preview。
- 只有 renderer 缺失：使用宿主通用节点外观，不影响合同。
- 任一降级不得删除 node 配置、unknown fields 或 source map。

## 9. AI 边界

> 分类：项目主张

AI 允许：

- 用一线 CAM 术语解释节点、边、参数、diagnostic 和 diff；
- 根据已脱敏证据提出参数名、说明、风险提示和测试建议；
- 生成结构化 `GraphPatchProposal`，包含 rationale、confidence、
  evidence refs、预期影响和待确认问题。

AI 不允许：

- 直接写 FlowGraph、source snapshot、Recipe 或 CommandTask；
- 定义或改变 port type、node schema、capability 或安全风险下限；
- 生成 codegen 文本、执行 preview、声称门禁通过；
- 解析未知源码后猜测并丢弃 opaque；
- 把自然语言审阅结果当作确定性校验结果；
- 接收或输出 machine-ready NC、凭据、未脱敏绝对路径。

建议流程：

```text
AI proposal (untrusted)
  -> JSON Schema parse
  -> deterministic patch preflight
  -> 人工查看 node/source diff
  -> 人工接受
  -> GraphCommand
  -> deterministic validation/test/preview
```

AI 开关关闭后，导入、编辑、验证、版本、diff、test 和 preview 必须完整
可用且结果相同。

## 10. 测试夹具与质量策略

### 10.1 Fixture 合同

每个 fixture pack 包含：

- 脱敏 source bytes 与 encoding/newline 元数据；
- product、target version、parser/capability lock；
- expected FlowGraph semantic hash；
- expected source maps 和 opaque spans；
- 一组 GraphCommand；
- expected graph diff、source diff、diagnostics、ProjectionReport；
- expected regenerated candidate hash；
- round-trip fidelity 等级；
- 安全断言：无 transport、无外部写、无网络、无机床输出。

可复用现有 `examples/contracts`、PowerMill fidelity fixtures 与 NX
Journal examples 作为输入基础，但新增预研合同需使用独立 fixture，不
修改冻结 v1 示例。

### 10.2 测试层

| 层 | 必测内容 |
| --- | --- |
| Schema | required/optional、unknown field preservation、enum、深度/大小限制 |
| Type | primitive、单位、selector、cardinality、三类 edge、跨 flow 拒绝 |
| Parser | source span、trivia、quote、unknown syntax、错误恢复 |
| Round trip | F0 字节一致、F1 未编辑 span 一致、F2 语义一致、F3 opaque hash |
| Codegen | 最小 patch、重新解析比较、失败不产生候选 |
| Version | node/graph migration、capability lock、缺失插件只读 |
| Undo | 每个 GraphCommand 正反回放后 semantic/artifact hash |
| Diff | layout 排除、语义变化完整、opaque 显式 |
| Plugin | crash、timeout、超限、越权、非法 Schema、非确定输出 |
| Security | 路径穿越、symlink、控制字符、注入、敏感内容与 machine-output 拒绝 |
| UI | 键盘、screen reader、缩放、双向定位、长中文、错误不只靠颜色 |
| Determinism | 乱序输入、重复运行、跨进程、AI 开关开/关结果一致 |

同一 fixture 重复运行至少 20 次，FlowGraph semantic hash、diagnostics
排序、source diff 和 ProjectionReport 必须字节一致；时间戳和随机 ID
必须由测试时钟/确定性 ID provider 注入。

## 11. 性能目标

> 分类：研究假设；待在目标客户 Windows 工作站上验证。

仓库现有离线基线显示 100,000 event import、查询、会话学习和 fixture
任务可在秒级/毫秒级完成，但这些结果不等同于图编辑器性能。[L2]

| 场景 | MVP 目标 | 测量口径 |
| --- | ---: | --- |
| 500 node / 800 edge 图冷打开 | p95 <= 2.0 s | 读文件到首个可操作画面 |
| 同图温打开 | p95 <= 750 ms | 缓存 manifest，但不缓存验证结果 |
| 拖拽/缩放 | p95 frame <= 16.7 ms | 典型视口可见 150 节点 |
| 参数编辑增量校验 | p95 <= 100 ms | 输入完成到诊断更新 |
| 单事务 undo/redo | p95 <= 50 ms | 不含 codegen |
| 图语义 diff | p95 <= 300 ms | 500 node 相邻 revision |
| 10,000 行源文件静态 parse | p95 <= 1.5 s | 单产品 adapter、无执行 |
| 500 node preview | p95 <= 3.0 s | patch + reparse + compare，fixture |
| 后台 test 取消响应 | <= 250 ms | 发出取消到 worker 确认 |
| 主窗口常驻内存 | <= 350 MiB | 500 node、源码和一个 diff |

若 60 FPS 目标无法满足，应先采用 viewport culling、简化 edge、折叠
subflow、延迟显示非 control edge 和 worker 增量验证；不能通过减少
合同校验或跳过 round-trip 检查换性能。

## 12. MVP 与非目标

### 12.1 MVP

- 导入受支持子集的 NX Journal 或 PowerMill macro/log 为 FlowGraph。
- 单产品、单 entry flow；control/data/dependency edge 和 typed ports。
- graph parameters、literal/output binding、显式单位与对象 selector。
- property-level source map、图/源码双向定位。
- opaque 节点只读保留，无法无损时阻断。
- 非递归 subflow、从合法选区提取 subflow。
- React Flow 单窗口编辑器、schema-driven inspector。
- GraphCommand undo/redo、不可变 revision、语义 diff 和源码 diff。
- fixture test、deterministic preview、reparse comparison。
- capability manifest lock、内置受信节点 provider、分类权限撤销。
- AI explanation 与 GraphPatchProposal，可完全关闭。
- 投影到现有 Recipe v1，并生成 ProjectionReport；只进入现有 dry-run
  审阅链。

### 12.2 明确非目标

- 真实 NX/PowerMill 附着、live command、自动选前台实例。
- machine-ready NC、G-code、后处理、机床控制。
- 以 dry-run 替代 CAM 仿真、碰撞/过切检查或车间批准。
- 通用 Python/PowerMill 语言 IDE 或任意代码编辑器。
- 任意循环、递归 subflow、跨产品图。
- 云端协同、公共插件市场、任意第三方 JSX/脚本 UI。
- 自动修复 opaque/unsupported 源码。
- 从屏幕坐标推断执行顺序，或把任意图静默线性化。
- fork Node-RED/n8n runtime，或复制 n8n 受限代码/视觉资产。
- AI 自动接受建议、修改图、生成源码或宣告门禁通过。

## 13. 风险清单

| 风险 | 分类 | 影响 | 缓解/退出条件 |
| --- | --- | --- | --- |
| AST 丢失注释/空白导致重写 | 高 | 破坏用户宏/Journal | token/CST + F0/F1 fixture；不满足则只读 |
| 未知语法被“正常化” | 高 | 语义变化不可见 | opaque exact slice；跨 span 编辑阻断 |
| FlowGraph 无法投影现有 Recipe | 高 | 预览链断裂 | ProjectionReport；限制 MVP 图语义 |
| CAM 版本/node capability 漂移 | 高 | 旧图在新环境含义改变 | capability lock、显式迁移、新 revision |
| 插件逃逸或越权 | 高 | 本地文件/进程风险 | 内置插件优先、低权限进程、host handle、越权测试 |
| “预览”被理解为可生产 | 高 | 安全误用 | 固定 dry-run 文案、commands_sent=0、门禁常驻 |
| 大图视觉混乱 | 中 | 一线用户放弃使用 | 主 control path、subflow、edge 渐进显示、用户测试 |
| 类型系统太技术化 | 中 | 低门槛目标失败 | CAM 名称/单位，端口默认隐藏，错误给修复路径 |
| undo 与源码 patch 不一致 | 高 | 保存错误版本 | GraphCommand 为唯一真相；保存前 reparse compare |
| source map 在外部编辑后失效 | 高 | 错 span patch | source hash 前置检查；要求重新导入/三方 diff |
| n8n 许可误判 | 高 | 商业许可风险 | 不复制；SBOM/法务 gate；只记录抽象模式 |
| 性能目标不成立 | 中 | 交互卡顿 | 真实工作站基准；500 节点上限与 subflow 折叠 |
| AI 建议被当成事实 | 高 | 错误工艺参数 | proposal 隔离、证据/置信度、人工接受、确定性复检 |

## 14. 验收矩阵

| 编号 | 需求 | 验收证据 | 通过标准 |
| --- | --- | --- | --- |
| A01 | canonical FlowGraph | JSON Schema + golden fixture | 不依赖 React Flow JSON，可重建 view |
| A02 | typed ports | type conformance suite | 错类型、单位、cardinality 在连线时和保存前均失败 |
| A03 | 三类 edge | graph validator tests | control/data/dependency 不互相推断 |
| A04 | parameters/bindings | schema + property tests | 改名不破坏 ID，secret 只保存引用 |
| A05 | source mapping | 双向定位 UI + span fixture | node->source、source->node 精确；歧义不猜 |
| A06 | subflow | recursion/interface tests | 非递归、接口稳定、跨 flow 只走 ports |
| A07 | opaque | unknown syntax fixtures | 未编辑字节一致；跨 span 编辑被阻断 |
| A08 | capability | manifest lock fixtures | 缺失/版本不匹配可读但不可 preview |
| A09 | 版本兼容 | v1 unknown field round trip | 编辑器保存后 unknown optional fields 不丢失 |
| A10 | Recipe 投影 | ProjectionReport fixtures | 每个语义 node/edge 有映射或明确 blocker |
| A11 | round trip | F0-F3 test suite | 声明等级全部满足；不满足时无候选输出 |
| A12 | 单窗口低门槛 | 目标用户可用性测试 | 8 名用户中至少 6 名无开发帮助完成导入、改参、diff、test |
| A13 | 源图双向定位 | UI acceptance | 2 次操作内从节点到源码或源码到节点 |
| A14 | undo/redo | command replay suite | 往返后 semantic/artifact hash 与初始一致 |
| A15 | version/diff | revision fixtures | layout 不进语义 diff；opaque 始终可见 |
| A16 | test/preview | fixture acceptance | 无网络、无 transport、候选 reparse 等价 |
| A17 | 权限撤销 | integration tests | 撤销立即失效，图可读，任务不自动恢复 |
| A18 | plugin sandbox | adversarial fixtures | 文件、网络、子进程、IPC 超限均 fail closed |
| A19 | deterministic core | repeatability suite | AI 开关不改变 hash、诊断、codegen、preview |
| A20 | AI 隔离 | proposal tests | AI 只能产生待审结构化建议，不能直接 GraphCommand |
| A21 | 性能 | 目标工作站 benchmark | 第 11 节各项 p95 达标或 ADR 明确降级 |
| A22 | 安全边界 | gateway/static scanner tests | live、postprocess、machine-output 类内容全部拒绝 |
| A23 | 许可证 | SBOM + legal checklist | MIT/Apache notice 完整；n8n 代码和资产为零 |
| A24 | 产品隔离 | dependency/import tests | NX parser 不含 PowerMill 语法，反向亦然 |

## 15. 待验证问题

> 分类：研究假设

1. 一线用户是否更偏好严格自上而下步骤，还是允许二维自由布局？
2. 端口默认隐藏后，用户能否理解参数来源和依赖错误？
3. 真实 Journal/宏中 opaque span 的比例是多少？若超过 15%，MVP 是否
   应先做只读解释器而不是图编辑器？
4. NX builder 生命周期与 PowerMill 隐式选择状态能否稳定映射为显式
   control/dependency edge？
5. 同一源片段映射多个语义节点时，最小 patch 的可接受粒度是什么？
6. subflow 是否应按“工艺模板”命名并弱化编程术语？
7. 500 节点是否覆盖 95% 现场流程？若否，产品应优先多 flow 分页还是
   更激进的自动折叠？
8. Windows 客户环境可接受的 worker 隔离能力是什么？不能达标时，第三方
   node plugin 必须保持禁用。

上述问题在进入实现前至少需要：两个产品各 10 份脱敏真实样本、8 名目标
操作者访谈/任务测试、两档客户工作站性能基准，以及安全/法务评审。

## 16. 一手资料与本地证据

### 16.1 本地证据

- [L1] [仓库协作边界](../../AGENTS.md)，读取日期 2026-08-24。
- [L2] [CAM Automation Studio 架构](../architecture.md)，读取日期
  2026-08-24。
- [L3] [并行开发合同 v1](../contracts/parallel-development-contracts.md)，
  读取日期 2026-08-24。
- [L4] [ActivityEvent JSON Schema](../../plugins/ug-cam-copilot/schemas/activity-event.schema.json)，
  读取日期 2026-08-24。
- [L5] [UG 插件架构](../../plugins/ug-cam-copilot/docs/architecture.md)，
  读取日期 2026-08-24。
- [L6] [仓库 README](../../README.md) 与
  [UG 插件 README](../../plugins/ug-cam-copilot/README.md)，读取日期
  2026-08-24。
- [L7] [`examples/contracts` 冻结 JSON fixtures](../../examples/contracts)，
  读取日期 2026-08-24。
- [L8] [NX Session Comparison 合同](../../plugins/ug-cam-copilot/skills/nx-session-comparison/references/comparison-contract.md)
  与 [NX Recipe Review 合同](../../plugins/ug-cam-copilot/skills/nx-recipe-review/references/review-contract.md)，
  读取日期 2026-08-24。
- [L9] [PowerMill Session Comparison 合同](../../plugins/powermill-cam-copilot/skills/powermill-session-comparison/references/comparison-contract.md)
  与 [PowerMill Recipe Review 合同](../../plugins/powermill-cam-copilot/skills/powermill-recipe-review/references/review-contract.md)，
  读取日期 2026-08-24。

### 16.2 上游一手资料

- [U1] xyflow, [React Flow / Svelte Flow repository and MIT license](https://github.com/xyflow/xyflow),
  访问日期 2026-08-24。
- [U2] React Flow, [Handles](https://reactflow.dev/learn/customization/handles),
  访问日期 2026-08-24。
- [U3] React Flow, [Save and Restore](https://reactflow.dev/examples/interaction/save-and-restore),
  访问日期 2026-08-24。
- [U4] Raspberry Pi Foundation, [Blockly repository and Apache-2.0 license](https://github.com/RaspberryPiFoundation/blockly),
  访问日期 2026-08-24。历史 `google/blockly` 地址在访问日已重定向到该
  仓库。
- [U5] Blockly, [Connection checks](https://docs.blockly.com/guides/create-custom-blocks/type-checks),
  访问日期 2026-08-24。
- [U6] OpenJS Foundation, [Node-RED repository and Apache-2.0 license](https://github.com/node-red/node-red),
  访问日期 2026-08-24。
- [U7] Node-RED, [Flows](https://nodered.org/docs/user-guide/editor/workspace/flows),
  访问日期 2026-08-24。
- [U8] Node-RED, [Subflows](https://nodered.org/docs/user-guide/editor/workspace/subflows),
  访问日期 2026-08-24。
- [U9] n8n, [n8n repository](https://github.com/n8n-io/n8n)，访问日期
  2026-08-24。
- [U10] n8n, [Sustainable Use License](https://github.com/n8n-io/n8n/blob/master/LICENSE.md)
  与 [Enterprise License](https://github.com/n8n-io/n8n/blob/master/LICENSE_EE.md)，
  访问日期 2026-08-24。
- [U11] n8n Docs, [Sustainable use license FAQ](https://docs.n8n.io/sustainable-use-license/),
  访问日期 2026-08-24。
- [U12] Blockly, [Save and load / JSON serialization](https://docs.blockly.com/guides/configure/web/serialization),
  访问日期 2026-08-24。
