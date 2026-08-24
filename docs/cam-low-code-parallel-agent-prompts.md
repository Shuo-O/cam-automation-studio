# CAM Low-Code Studio 并行 Agent 提示词

> 对应计划：
> [CAM Low-Code Studio AI 并行开发计划](cam-low-code-ai-parallel-development-plan.md)
>
> 冻结合同：
> [CAM Flow Graph v1](contracts/cam-flow-graph-contracts.md)
>
> 产品范围：
> [CAM Low-Code Studio 产品方案](cam-low-code-studio-product-proposal.md)
>
> 提示词数量：**12**
>
> 统一模型：`gpt-5.6-sol-shuo`
>
> 统一推理强度：`xhigh`

## 1. READY 两阶段启动

每个提示词可直接作为对应 Codex 新对话的第一条消息。创建新对话前，在界面中
选择 `gpt-5.6-sol-shuo` 和 `xhigh`。

第一阶段是 `PREPARE`：Agent 不读取仓库、不运行命令、不研究、不编辑、不提交，
只回复 `READY`。协调者必须等同一波全部对话回复 `READY`，再逐个发送：

```text
EXECUTE <TASK_ID> BASE_SHA=<共同基线 SHA> DEPENDENCY_SHAS=<已合并依赖 SHA，多个用逗号；无则写 none>
```

第二阶段是 `EXECUTE`：Agent 才按第一条完整提示工作。Wave 1 四个任务使用共同
基线；Wave 2 使用 Wave 1 合并 SHA；Wave 3 先执行 T09，再从 T09 合并 SHA 并行
执行 T10-T12。

每个 Agent 只拥有提示词列出的文件。发现跨所有权问题必须回复
`COORD-BLOCKED`，不能修改公共入口、复制合同 DTO 或越权修复。

## 2. T01 提示词：FlowGraph 合同与校验

```text
阶段：PREPARE
任务：T01 FlowGraph IR、JSON Schema 与确定性校验
运行要求：模型 gpt-5.6-sol-shuo，reasoning effort xhigh。

当前只做工作树准备。不要读取仓库文件，不要运行命令，不要研究，不要编辑，
不要提交；只回复 READY。只有收到协调者第二条
`EXECUTE T01 BASE_SHA=... DEPENDENCY_SHAS=...` 后才开始以下工作。

执行时先完整阅读 AGENTS.md、README.md、docs/architecture.md、
docs/contracts/parallel-development-contracts.md、
docs/contracts/cam-flow-graph-contracts.md、
docs/cam-low-code-studio-product-proposal.md 和
docs/cam-low-code-ai-parallel-development-plan.md。冻结合同是实现真相；
AI 解释、UI 文案和研究假设不是合同真相。

目标：实现 AutomationAsset、CapabilityManifest、FlowGraph、FlowNode、
FlowEdge、PortContract、ParameterBinding、SourceMapping、RoundTripReport、
CompatibilityReport、PreviewPlan、FlowVersion 的 v1 Schema/模型入口、
RFC 8785 canonical hash 和产品中立确定性 validator。共享层不得解析
NX/PowerMill 语法。

你只可修改：
- cam_automation/flow_contracts.py
- cam_automation/flow_validation.py
- cam_automation/schemas/cam-flow/**
- tests/flow_contracts/**
- tests/fixtures/cam-flow/contracts/**

其他文件只读，尤其不得修改 web_server.py、integrations.py、fixture_runtime.py、
任何 product parser、app-plugin.json 或 docs。

必须实现：
- 字段只增不删、unknown optional field 保存、semantic/source/artifact hash；
- single-product、namespace、entry flow、三类 edge、type/unit/cardinality、
  dependency cycle、subflow recursion、binding one-of、risk floor；
- source-line/source-span、opaque/unsupported、资源限制；
- 冻结错误码和稳定 diagnostics 排序；
- 安全 payload 结构化拒绝，不回显敏感内容。

永久安全边界：不得 import/execute/eval/replay Journal，不得运行宏，不得连接
真实 CAM，不得生成/返回/发送 NC、G-code、CLSF、postprocess 或 machine control；
所有输出 review-first、offline/fixture dry-run。

测试至少覆盖：11 类对象最小/完整/错误 fixture；20 次 hash/diagnostic
determinism；unknown field round-trip；mixed product、跨 flow、cycle、
type/unit/cardinality、risk downgrade、live 和 machine output 拒绝。
运行现有相关回归。不得把未运行测试写成通过。

验收：所有冻结对象可独立校验；validator 不 import 产品 parser；错误码与合同
逐项一致；git diff 只含你的独占文件。

完成后 git add 独占文件并提交：
`feat(flow): add frozen graph contracts and validation`

最终报告：实现摘要、文件列表、测试命令与结果、fixture/现场限制、
git status --short、完整 commit hash，并明确确认未修改公共入口或其他任务文件。
```

## 3. T02 提示词：资产与能力注册

```text
阶段：PREPARE
任务：T02 AutomationAsset 与 Capability 注册
运行要求：模型 gpt-5.6-sol-shuo，reasoning effort xhigh。

现在不要读取文件、运行命令、研究、编辑或提交，只回复 READY。收到
`EXECUTE T02 BASE_SHA=... DEPENDENCY_SHAS=...` 后再执行。

执行时完整阅读 AGENTS.md、README、architecture、并行开发合同、
docs/contracts/cam-flow-graph-contracts.md、产品方案、开发计划，以及已合并
T01 的公共合同/validator。使用 T01，不复制 DTO 或 Schema。

目标：实现本地 AutomationAsset/CapabilityManifest registry、不可变 revision、
权利状态、manifest lock、一次授权与分类撤销。注册只表示可识别，不表示可执行。

你只可修改：
- cam_automation/asset_registry.py
- cam_automation/capability_registry.py
- tests/asset_capability/**
- tests/fixtures/cam-flow/registry/**

其他文件只读。不得修改 plugin_manager.py、recorder.py、product parser、公共入口、
app-plugin.json 或 docs。

必须实现：
- content-addressed revision、原始 bytes/hash/encoding/newline 不可变；
- locator 脱敏、dependencies、target versions、runtime modes；
- rights status、evidence、sharing、redistribution unknown、network false、
  binary_inspection=false；
- capability evidence/target range/risk floor/revocation/lock；
- grant 绑定 user + plugin/version + manifest hash + category + scope + purpose；
- manifest/权限扩大时重新授权；撤销关闭新 handle，不删数据，不自动恢复任务。

保持 NX/PowerMill parser 隔离；共享 registry 不解析产品语法。严禁商业二进制
分析、live CAM、Journal/宏执行、NC/G-code/CLSF/postprocess/machine control。

测试至少覆盖：同 content 幂等、不同 content 新 revision、旧 revision 不变；
权利 unknown 时本地只读可用但分享/外发/preview 拒绝；manifest hash/权限变化；
fake clock 下分类撤销 <=2 秒；重新授权不自动恢复；binary inspection 永久阻断；
无网络/CAM transport。

验收：撤销后资产/图仍可只读；注册与授权分离；git diff 只含独占文件。

提交信息：
`feat(flow): add asset and capability registries`

最终报告实现、文件、测试、限制、git status 和完整 commit hash，确认未越权。
```

## 4. T03 提示词：PowerMill 离线映射

```text
阶段：PREPARE
任务：T03 PowerMill 离线 macro/log -> FlowGraph
运行要求：模型 gpt-5.6-sol-shuo，reasoning effort xhigh。

当前不得读取仓库、运行命令、研究、编辑或提交，只回复 READY。收到
`EXECUTE T03 BASE_SHA=... DEPENDENCY_SHAS=...` 后开始。

执行时完整阅读 AGENTS.md、README、architecture、并行合同、FlowGraph 冻结合同、
产品方案、开发计划和 docs/research/powermill-low-code-integration.md。读取已合并
T01 合同形状，但不得复制共享 validator。

目标：在 PowerMill 专属 adapter 内离线解析 `.mac`/命令日志，输出 FlowGraph
fragment、property source map、opaque/unsupported 和 diagnostics；保留原始 bytes、
encoding、BOM、newline、token/trivia、引号和 envelope。

你只可修改：
- cam_automation/adapters/powermill_macro.py
- cam_automation/adapters/powermill_flow.py
- plugins/powermill-cam-copilot/tests/test_powermill_flow_import.py
- plugins/powermill-cam-copilot/fixtures/flow/**

其他文件只读。绝对不得修改 NX parser、共享合同/服务、web_server、插件 manifest
或 docs。

必须：
- 仅输出 cam.* / powermill.*；
- 保守映射命令、变量、函数、控制流、include/macro、交互、NOGUI、
  selection/state 和 DOCOMMAND；
- 未录制默认/活动状态保持 unknown，不自动补值；
- 动态/未知/vendor-only 语法形成 visible opaque，原文和 span 保留；
- source_line 与 byte/line/column source_span 双向可追溯；
- 无编辑 F0，受支持局部编辑为最小候选，不覆盖原文件。

永久禁止运行宏、COM/.NET 附着、实例发现/创建/关闭、PowerMill command transport、
商业二进制探测、NC/G-code/CLSF/postprocess/machine control。

测试：CRLF/LF/mixed、BOM、注释、大小写、引号、尾空格；控制流；source mapping；
常量/tainted DOCOMMAND；include 缺失；unknown/vendor command；输入 NX Journal
明确拒绝；monkeypatch 证明没有执行/附着/机床输出；现有 PowerMill 回归。

验收：未编辑 bytes SHA-256 完全一致，unsupported 零静默丢弃，git diff 只含
独占文件。

提交：
`feat(powermill): map offline macros to flow graph`

最终报告文件、测试、已知语法/版本限制、git status、完整 commit hash，并确认
未修改 NX parser 和公共入口。
```

## 5. T04 提示词：NX 离线映射

```text
阶段：PREPARE
任务：T04 NX Journal -> FlowGraph 离线静态映射
运行要求：模型 gpt-5.6-sol-shuo，reasoning effort xhigh。

现在不读取仓库、不运行命令、不研究、不编辑、不提交，只回复 READY。收到
`EXECUTE T04 BASE_SHA=... DEPENDENCY_SHAS=...` 后执行。

执行时完整阅读 AGENTS.md、README、architecture、并行合同、FlowGraph 冻结合同、
产品方案、开发计划和 docs/research/nx-low-code-integration.md。保持当前
nx_journal.py 的 AST 静态安全边界。

目标：把 NXOpen Python Journal 静态映射为 FlowGraph fragment、source map、
Builder/Undo/selector/precondition/unsupported 语义。只分析，不 import、exec、
eval、compile 或 replay Journal。

你只可修改：
- plugins/ug-cam-copilot/src/ugcam_ai/adapters/nx_journal.py
- plugins/ug-cam-copilot/src/ugcam_ai/adapters/nx_flow.py
- plugins/ug-cam-copilot/tests/test_nx_flow_import.py
- plugins/ug-cam-copilot/examples/nx_flow/**

其他文件只读。不得修改 PowerMill adapter、共享服务、公共入口、plugin manifest
或 docs。

必须：
- 继续保持 ActivityEvent 既有输入/输出兼容；
- 保守映射顺序、函数、if/loop、try/except/finally、调用和赋值；
- Builder create/configure/commit|abandon/destroy、Undo scope 一等建模；
- FindObject/JournalIdentifier 默认 fragile/unresolved，0/多匹配不取第一个；
- stubs/API symbol 只是静态证据，UF/dynamic/vendor-only 保持 unknown/blocked；
- unsupported region 原文、digest、source_line/source_span 和 blocker 可见；
- 候选只存在内存，不写回原 Journal。

永久禁止 live Journal、NXOpen Commit/save/generate/post、真实 part 修改、实例自动
附着、NC/G-code/CLSF/postprocess/machine control、商业二进制探测。

测试：现有 NX tests 全回归；Builder 完整/异常生命周期；分支/异常边；
source mapping 与非参数 hash；selector 0/多匹配；恶意/深 AST 资源限制；
PowerMill 输入拒绝；monkeypatch 证明 Journal 从未运行和无 machine output。

验收：NX parser 文件边界不变；旧 JSONL/ActivityEvent 兼容；git diff 只含独占
文件。

提交：
`feat(nx): map offline journals to flow graph`

最终报告实现、测试、stubs/现场限制、git status 和完整 commit hash，确认未修改
PowerMill 或公共入口。
```

## 6. T05 提示词：版本、往返与兼容

```text
阶段：PREPARE
任务：T05 FlowVersion、Round-trip、Compatibility 与 Projection
运行要求：模型 gpt-5.6-sol-shuo，reasoning effort xhigh。

当前只回复 READY，不读文件、不运行命令、不研究、不编辑、不提交。收到
`EXECUTE T05 BASE_SHA=... DEPENDENCY_SHAS=...` 后开始。

执行时完整阅读全部仓库边界、FlowGraph 冻结合同、产品方案、开发计划和 Wave 1
合并实现。共享层只能调用 NX/PowerMill adapter protocol，不得解释产品语法。

目标：实现不可变 FlowVersion、GraphCommand/undo/redo、四层 diff、F0-F3/FB、
source digest guard、minimal in-memory patch、candidate reparse、CompatibilityReport
和 FlowGraph -> Recipe v1 ProjectionReport。

你只可修改：
- cam_automation/flow_versions.py
- cam_automation/flow_roundtrip.py
- cam_automation/flow_compatibility.py
- tests/flow_versions/**
- tests/fixtures/cam-flow/versions/**

其他文件只读，不改 adapter、recipes.py、公共入口、UI、plugin manifest 或 docs。

必须：
- layout/semantic command 分离，layout 不进 semantic diff/hash；
- 版本 parent/hash/status 和审阅 hash 失效；
- 未触及 token/trivia 与 opaque bytes/hash 证明；
- candidate reparse 后 node/edge/binding/unit/selector/gate 语义等价；
- capability/node major、target version、selector、context 的兼容状态；
- 每个 enabled 语义 node/edge 可投影，否则 blocker；
- 不改变既有 recipe_hash 算法，不静默线性化一般图。

永久禁止执行 Journal/宏、真实 CAM transport、源文件覆盖、NC/G-code/CLSF/
postprocess/machine control。不能证明 round-trip 时不得保留部分候选。

测试：F0/F1/F2/F3/FB；source 漂移；reparse failure；semantic mismatch；
opaque changed；capability major mismatch；layout exclusion；undo/redo hash；
20 次 report/diff/projection determinism；现有 Recipe tests 回归。

验收：migration 必须新 revision；失败无 candidate；git diff 只含独占文件。

提交：
`feat(flow): add versions roundtrip and compatibility`

最终报告文件、测试、限制、git status 和完整 commit hash，确认共享层没有产品语法。
```

## 7. T06 提示词：API 服务

```text
阶段：PREPARE
任务：T06 Flow API service/handler
运行要求：模型 gpt-5.6-sol-shuo，reasoning effort xhigh。

现在只回复 READY；不得读取、运行、研究、编辑或提交。收到
`EXECUTE T06 BASE_SHA=... DEPENDENCY_SHAS=...` 后执行。

执行时完整阅读 AGENTS、README、architecture、两个冻结合同、产品方案、开发计划
和 Wave 1 合并服务。实现 route-neutral API；公共 web_server 接线属于 T09。

目标：提供资产、能力、图、版本、校验、diff、test、compatibility 和 PreviewPlan
的纯 service/handler，使用冻结 error envelope、state machine、revision conflict、
cancellation/deadline 和 resource limits。

你只可修改：
- cam_automation/flow_service.py
- cam_automation/flow_api.py
- tests/flow_api/**
- tests/fixtures/cam-flow/api/**

其他文件只读，尤其不得修改 web_server.py、integrations.py、fixture_runtime.py、
前端、adapter、plugin manifest 或 docs。

必须：
- 幂等创建、stable revision/ETag 语义、stale revision 409；
- 400/403/404/408/409/413/500 的结构化错误；
- PreviewPlan 只允许 fixture target、execution_mode=fixture_dry_run、
  transport=none；
- product/version/instance/project/hash/permission/rights 显式核验；
- cancellation、deadline、correlation ID、稳定错误排序；
- commands_sent=0、journal_executed=false、macro_executed=false、
  machine_output_count=0 不可改写。

禁止 live CAM、Journal/宏执行、自动前台实例、machine output、敏感拒绝 payload
回显。AI 文本不得直接成为 GraphCommand 或 API 真相。

测试：状态转换、无效/过期 revision、unknown field、大小限制、权限撤销、目标歧义、
hash mismatch、并发/取消/重复请求、零执行字段和错误 HTTP mapping。运行现有相关
服务测试。

验收：T09 可用一个注册入口接线；模块无 HTTP server 全局副作用和 CAM transport；
git diff 只含独占文件。

提交：
`feat(api): add CAM flow service handlers`

最终报告实现、API 形状、测试、限制、git status、完整 commit hash，确认未改入口。
```

## 8. T07 提示词：低代码 UI

```text
阶段：PREPARE
任务：T07 单窗口拖拽低代码 UI
运行要求：模型 gpt-5.6-sol-shuo，reasoning effort xhigh。

当前不读仓库、不运行命令、不研究、不编辑、不提交，只回复 READY。收到
`EXECUTE T07 BASE_SHA=... DEPENDENCY_SHAS=...` 后开始。

执行时完整阅读产品方案、FlowGraph 冻结合同、开发计划、现有 UI 风格和 T06 API
fixtures。实现隔离组件与 fixture harness；公共 index/app/styles 接线留给 T09。

你只可修改：
- cam_automation/web/flow/**

不得修改 cam_automation/web/index.html、app.js、styles.css、web_server.py、
integrations.py、任何 Python service/adapter、plugin manifest 或 docs。

目标：构建可用的单窗口 Flow Studio：资产/步骤库、画布、schema inspector、
问题/源码/图 diff/源码 diff/test/preview 底栏，支持 typed drag/connect、
GraphCommand undo/redo、版本、source 双向定位、目标选择和 fixture preview。

设计要求：
- 使用现有风格；React Flow OSS core 可用时作为视图，不持久化其 JSON；
- 首屏是实际工作台，不做 landing page；
- CAM 术语、主 control path、data/dependency 渐进披露；
- opaque/unsupported/blocked/权限/版本/门禁同时有图标、文本、source 定位；
- 参数使用单位输入、selector、枚举、开关，不让用户改 JSON；
- 多实例不自动选前台；禁止“运行/部署/一键加工”文案；
- 键盘、screen reader、tooltip、长中文、1440/1280/390 响应式；
- 不使用颜色作为唯一状态，不出现文本/控件重叠；
- AI 开关只影响解释，不能改变 canonical graph。

永久安全边界：UI 不提供 live Journal/宏/CAM 命令、NC/G-code/CLSF/postprocess/
machine control；preview 固定显示零执行字段和未完成门禁。

测试放在 web/flow 内，覆盖 typed connect、undo/redo、source mapping、opaque、
version/diff、目标选择、AI 开关、500 node viewport hook、无 live 文案。

验收：fixture harness 可完成导入后的改参、diff、test、preview；组件可由 T09
接入；git diff 只含 web/flow。

提交：
`feat(ui): add CAM flow studio editor`

最终报告组件、交互、测试、未完成浏览器事项、git status 和完整 commit hash。
```

## 9. T08 提示词：插件与 Codex Skills

```text
阶段：PREPARE
任务：T08 产品插件、CapabilityManifest 与 Codex Skills
运行要求：模型 gpt-5.6-sol-shuo，reasoning effort xhigh。

当前只回复 READY，不读取仓库、不运行命令、不研究、不编辑、不提交。收到
`EXECUTE T08 BASE_SHA=... DEPENDENCY_SHAS=...` 后执行。

执行时完整阅读 AGENTS、README、architecture、插件 manifest、现有 NX/PowerMill
Skills、FlowGraph 冻结合同、产品方案和开发计划。保持产品 Skill/parser 隔离。

你只可修改：
- plugins/ug-cam-copilot/app-plugin.json
- plugins/powermill-cam-copilot/app-plugin.json
- plugins/ug-cam-copilot/capabilities/**
- plugins/powermill-cam-copilot/capabilities/**
- plugins/ug-cam-copilot/skills/nx-flow-studio/**
- plugins/powermill-cam-copilot/skills/powermill-flow-studio/**
- tests/flow_plugins/**

其他文件只读。不得修改 parser、共享服务、公共入口、旧 Skill 文件或 docs。

目标：
- app-plugin.json 只新增向后兼容 capability_manifest/flow_sdk_range 等可选字段；
- 每产品独立 manifest，锁定 versions、ports、types、risk floor、required gates、
  forbidden modes、permissions、evidence、fixtures；
- 建立 nx-flow-studio 与 powermill-flow-studio 两个结构化 Skill；
- Skill 只做解释、compatibility/round-trip 审阅和 GraphPatchProposal；
- 安装、撤销、缺 capability 的状态明确。

每个 Skill 必须拒绝：自由文本执行、live Journal/宏/CAM command、源码/codegen、
NC/G-code/CLSF/postprocess/machine control、prompt injection、AI 宣称门禁通过。
NX Skill 只引用 NX adapter；PowerMill Skill 只引用 PowerMill adapter。

测试：manifest Schema/hash/SemVer/risk floor；plugin install/uninstall/permission
回归；dependency/import isolation；Skill prompt injection/machine output 拒绝；
discovery metadata 和 cache 重复确定性；现有 plugin/Skill tests 回归。

验收：旧插件与 Skills 保持可用；新增字段向后兼容；没有公共入口或 parser 变更；
git diff 只含独占文件。

提交：
`feat(plugins): expose CAM flow capabilities and skills`

最终报告 manifest/Skill 列表、测试、许可/现场限制、git status 和完整 commit hash。
```

## 10. T09 提示词：公共总装

```text
阶段：PREPARE
任务：T09 公共入口、HTTP、CLI、fixture 与 UI 总装
运行要求：模型 gpt-5.6-sol-shuo，reasoning effort xhigh。

当前不读文件、不运行命令、不研究、不编辑、不提交，只回复 READY。收到
`EXECUTE T09 BASE_SHA=... DEPENDENCY_SHAS=...` 后开始。

执行时完整阅读 AGENTS、README、architecture、两个冻结合同、产品方案、开发计划，
并审查 T01-T08 所有已合并接口。你是唯一公共入口 owner，不重写其他任务内部实现。

你只可修改：
- cam_automation/web_server.py
- cam_automation/integrations.py
- cam_automation/fixture_runtime.py
- cam_automation/__init__.py
- cam_automation/__main__.py
- cam_automation/cli.py
- cam_automation/web/index.html
- cam_automation/web/app.js
- cam_automation/web/styles.css
- tests/flow_integration/**

其他文件只读。发现 T01-T08 接口问题要 COORD-BLOCKED 或请求原 owner follow-up，
不得越权改其文件。

目标：
- 注册 Flow API，保持旧 API/错误/插件安装语义兼容；
- 未安装产品插件返回 409 plugin_not_installed；
- fixture runtime 提供 2 NX + 2 PowerMill 实例和测试项目；
- 接入 web/flow 工作台，首屏是实际应用，插件仍显式安装；
- CLI 只提供 offline import/validate/test/preview；
- 正确 shutdown/cancel worker，无泄漏；
- 公共层只处理 canonical contract，不解析产品语法。

永久安全要求：无真实 CAM attach/command；不自动前台实例；不 import/execute
Journal、不运行宏；不生成/返回/发送 NC/G-code/CLSF/postprocess/machine control；
PreviewPlan 固定 transport=none 和四个零执行字段。

测试：旧 root/plugin/NX/PowerMill 回归；API->service->adapter->version->preview；
未安装、撤销、歧义 target、stale hash、opaque blocker；旧 /api/learn 和现有路由；
CLI 无 live 选项；静态页面加载 flow 组件。

验收：只有此 commit 修改公共入口；新旧回归通过；git diff 只含独占文件。

提交：
`feat(studio): integrate CAM flow workspace`

最终报告路由/CLI/UI 接线、测试、fixture 限制、git status 和完整 commit hash。
```

## 11. T10 提示词：端到端安全屏障

```text
阶段：PREPARE
任务：T10 CAM Flow 端到端与安全屏障
运行要求：模型 gpt-5.6-sol-shuo，reasoning effort xhigh。

现在只回复 READY，不读取、不运行、不研究、不编辑、不提交。收到
`EXECUTE T10 BASE_SHA=... DEPENDENCY_SHAS=...` 后执行。

执行时完整阅读 AGENTS、两个冻结合同、产品方案、开发计划和 T09 合并后的完整
产品。你只新增测试/fixtures，不修改产品；发现缺陷明确回报对应 owner。

你只可修改：
- tests/flow_barrier/**
- tests/fixtures/cam-flow/barrier/**

目标：建立 release-blocking E2E/对抗套件，证明从资产到 preview 的闭环和永久
安全边界。

必须测试：
- 3 PowerMill + 3 NX：注册、离线 parse、解释、改参、version/diff/test/preview；
- source-line/source-span 双向定位，opaque/unknown field 不丢；
- F0-F3、candidate reparse、ProjectionReport blocker；
- 2 NX + 2 PowerMill，1,000 fixture plans 串实例为 0，歧义保持未映射；
- 分类撤销 <=2 秒、重授权不自动恢复、网络默认 0；
- NX/PowerMill parser import/dependency 隔离；
- live Journal/宏、CAM command、NC/G-code/CLSF/postprocess/machine control、
  prompt injection、binary inspection 全部结构化拒绝；
- 被拒 payload 不落入日志、diff、报告、AI context。

运行全部现有 root、NX、PowerMill、Node tests。不得跳过失败或把未运行写成通过。
产品缺陷时不要越权修复：最终以 BARRIER-BLOCKED 回报测试、最小复现、owner 和
建议；如果全通过再提交。

验收：新旧全量测试通过；git diff 仅测试和 barrier fixtures。

提交：
`test(flow): add end-to-end safety barrier`

最终报告测试数量/命令/结果、发现、git status 和完整 commit hash。
```

## 12. T11 提示词：性能与确定性

```text
阶段：PREPARE
任务：T11 性能与确定性屏障
运行要求：模型 gpt-5.6-sol-shuo，reasoning effort xhigh。

当前只回复 READY，不读取、运行、研究、编辑或提交。收到
`EXECUTE T11 BASE_SHA=... DEPENDENCY_SHAS=...` 后执行。

执行时完整阅读 FlowGraph 冻结合同第 13/15 节、产品方案指标、开发计划和 T09
合并产品。只写 benchmark/tests/fixtures，不修改产品代码。

你只可修改：
- scripts/benchmark_cam_flow.py
- tests/flow_performance/**
- tests/fixtures/cam-flow/performance/**

目标：建立可复现 benchmark 和 release performance gates。

覆盖：
- 500 nodes/800 edges 冷打开 p95<=2.0s、温打开<=750ms；
- 参数增量校验<=100ms、undo/redo<=50ms、semantic diff<=300ms；
- 10,000 行 NX/PowerMill 单产品静态 parse p95<=1.5s；
- 500 node fixture preview p95<=3.0s、cancel<=250ms；
- 主窗口/进程内存目标<=350MiB（环境允许时）；
- 每 fixture 20 次 semantic hash、diagnostic order、source diff、
  RoundTripReport、CompatibilityReport 完全确定。

报告必须记录 CPU、RAM、Windows、Python/Node、cold/warm、采样数和统计口径。
不能通过跳过 Schema/type/source/round-trip/safety/reparse、降低 fixture 大小或
缓存验证结果伪造达标。不得启动/连接 CAM，不执行 Journal/宏，不产生 machine
output。

若任何硬目标失败，不修改产品；输出 PERF-BLOCKED 和可复现数据，交原 owner。
全通过才提交。

验收：命令可在干净 worktree 重复；生成数据不含时间随机性对 semantic 结果的
影响；git diff 只含独占文件。

提交：
`perf(flow): add deterministic performance barriers`

最终报告环境、每项 p50/p95/max、determinism、测试、git status、完整 commit hash。
```

## 13. T12 提示词：浏览器验收

```text
阶段：PREPARE
任务：T12 浏览器、响应式、键盘与视觉验收
运行要求：模型 gpt-5.6-sol-shuo，reasoning effort xhigh。

现在只回复 READY；不要读取仓库、运行命令、研究、编辑或提交。收到
`EXECUTE T12 BASE_SHA=... DEPENDENCY_SHAS=...` 后执行。

执行时完整阅读产品方案第 5/14 节、FlowGraph 冻结合同、开发计划和 T09 合并后的
应用。使用 Playwright 或仓库现有浏览器栈启动本地服务、截屏和检查；只新增测试、
fixtures 和运行脚本，不改产品。

你只可修改：
- tests/browser/cam-flow-studio.spec.cjs
- tests/browser/fixtures/cam-flow/**
- scripts/run_cam_flow_browser_acceptance.ps1

必须验收：
- 1440x900、1280x720、390x844，无文本/控件重叠、溢出或遮挡；
- 首屏是工作台，不是营销页；
- fixture 导入后完成 node/source 双向定位、改参、typed connect、undo/redo、
  version/diff/test/preview；
- opaque/unsupported/blocked/权限/版本/门禁不只用颜色，均可定位；
- 键盘可完成核心流程，focus/aria/tooltip 可用，长中文适配；
- 多实例不自动选择前台，目标 product/version/instance/project 明确；
- preview 显示 transport=none、commands_sent=0、journal_executed=false、
  macro_executed=false、machine_output_count=0；
- canvas 像素非空、缩放/拖拽稳定、500 node 不因动态内容跳布局；
- console 无未处理错误，network audit 无未授权外发。

不得启动/连接真实 CAM，不执行 Journal/宏，不生成 NC/G-code/CLSF/postprocess/
machine control。发现 UI/集成缺陷不要越权修改；回报 UI 或 T09 owner。

测试必须保存有意义的桌面/移动截图和失败 trace。失败时报告
BROWSER-BLOCKED；全通过才提交。

验收：浏览器测试可一键运行；git diff 只含独占文件。

提交：
`test(ui): add browser acceptance for CAM flow studio`

最终报告 viewport、流程、截图/trace 路径、console/network 结果、测试命令、
git status 和完整 commit hash。
```

## 14. 一致性核对

| 任务 | 提示词 | 计划任务 | 独占 owner | Commit |
| --- | --- | --- | --- | --- |
| T01 | 第 2 节 | Wave 1 | 合同/validator | `feat(flow): add frozen graph contracts and validation` |
| T02 | 第 3 节 | Wave 1 | asset/capability registry | `feat(flow): add asset and capability registries` |
| T03 | 第 4 节 | Wave 1 | PowerMill adapter | `feat(powermill): map offline macros to flow graph` |
| T04 | 第 5 节 | Wave 1 | NX adapter | `feat(nx): map offline journals to flow graph` |
| T05 | 第 6 节 | Wave 2 | version/round-trip/compatibility | `feat(flow): add versions roundtrip and compatibility` |
| T06 | 第 7 节 | Wave 2 | route-neutral API | `feat(api): add CAM flow service handlers` |
| T07 | 第 8 节 | Wave 2 | isolated UI | `feat(ui): add CAM flow studio editor` |
| T08 | 第 9 节 | Wave 2 | product manifests/Skills | `feat(plugins): expose CAM flow capabilities and skills` |
| T09 | 第 10 节 | Wave 3 | public integration | `feat(studio): integrate CAM flow workspace` |
| T10 | 第 11 节 | Wave 3 | E2E/safety tests | `test(flow): add end-to-end safety barrier` |
| T11 | 第 12 节 | Wave 3 | performance tests | `perf(flow): add deterministic performance barriers` |
| T12 | 第 13 节 | Wave 3 | browser acceptance | `test(ui): add browser acceptance for CAM flow studio` |

总数固定为 12。增删任务、改变所有权或公共入口 owner 前，必须先更新开发计划和
本表并重新做冲突审查。
