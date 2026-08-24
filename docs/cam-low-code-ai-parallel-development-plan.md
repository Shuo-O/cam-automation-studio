# CAM Low-Code Studio AI 并行开发计划

> 计划代号：S-DELIVERY
>
> 状态：Ready for execution
>
> 任务总数：**12**
>
> 波次：**3**
>
> 冻结合同：
> [CAM Flow Graph v1](contracts/cam-flow-graph-contracts.md)
>
> 产品范围：
> [CAM Low-Code Studio 产品方案](cam-low-code-studio-product-proposal.md)

## 1. 口径与不可变边界

| 标签 | 含义 |
| --- | --- |
| **研究事实** | 当前仓库、官方资料和四份研究已确认的约束 |
| **产品决策** | 本计划冻结的任务、依赖、文件所有权、合并与验收方式 |
| **现场待验证** | 真实 CAM、客户样本、商业授权和工作站性能，不能由离线任务宣称完成 |

全部任务必须遵守：

1. 现有合同只新增可选字段，不删除、重命名或改变 v1 语义。
2. NX 语法只由
   `plugins/ug-cam-copilot/src/ugcam_ai/adapters/nx_journal.py`
   及 NX 专属模块处理；PowerMill 语法只由 PowerMill adapter 处理。
3. 共享动作使用 `cam.*`；专属动作使用 `nx.*` / `powermill.*`。
4. 原始 AutomationAsset 不可变；source-line/source-span 双向映射；
   opaque/unsupported 始终可见；不能静默重写。
5. AI 解释和 GraphPatchProposal 不是合同真相，不得绕过确定性校验。
6. 所有产物 review-first、offline/fixture dry-run；不附着或控制真实 CAM。
7. 严禁 Journal import/execute/eval/replay、PowerMill macro 执行、live command、
   machine-ready NC、G-code、CLSF、postprocess 和 machine control。
8. 商业外挂只接用户有权处理的导出物、公开接口、签名 manifest 或厂商授权
   adapter；不得分析受保护二进制。
9. 任务只能修改独占文件。公共入口只由 T09 修改；其他任务不得“顺手接线”。
10. 现场未验证能力必须标为 fixture/offline/unknown，不得升级为支持声明。

## 2. 启动、分支与交付协议

### 2.1 工作树

每个任务使用独立 worktree 和 `codex/` 分支：

| 任务 | 分支 |
| --- | --- |
| T01 | `codex/cam-flow-t01-contracts` |
| T02 | `codex/cam-flow-t02-registry` |
| T03 | `codex/cam-flow-t03-powermill` |
| T04 | `codex/cam-flow-t04-nx` |
| T05 | `codex/cam-flow-t05-versioning` |
| T06 | `codex/cam-flow-t06-api` |
| T07 | `codex/cam-flow-t07-ui` |
| T08 | `codex/cam-flow-t08-plugins` |
| T09 | `codex/cam-flow-t09-integration` |
| T10 | `codex/cam-flow-t10-barrier` |
| T11 | `codex/cam-flow-t11-performance` |
| T12 | `codex/cam-flow-t12-browser` |

### 2.2 READY 两阶段

1. 协调者为一波任务创建全部 worktree/新对话，选择
   `gpt-5.6-sol-shuo`、推理强度 `xhigh`，发送对应完整提示词。提示词处于
   `PREPARE`，Agent 不读仓库、不运行命令、不编辑，只回复 `READY`。
2. 协调者确认该波全部 `READY` 后，发送
   `EXECUTE <TASK_ID> BASE_SHA=<sha> DEPENDENCY_SHAS=<sha,...>`。只有收到这条
   消息，Agent 才开始读取、实现、测试和提交。

这样把 worktree 初始化与代码执行分离，避免 Agent 在基线或依赖尚未固定时开工。

### 2.3 完成回报

每个任务最终必须回报：

- 修改文件清单；
- 测试命令及结果；
- 已知 fixture/现场限制；
- `git status --short`；
- 唯一 commit hash；
- 未修改公共入口和其他任务文件的确认。

发现合同冲突时返回 `COORD-BLOCKED`，说明冲突、候选解决方案和受影响任务，
不得通过复制 DTO、改公共入口或跨所有权修补。

## 3. 依赖图

```text
Wave 1：并行基础
  T01 FlowGraph IR/Schema/validator
  T02 AutomationAsset + Capability registry
  T03 PowerMill offline import mapping
  T04 NX offline import mapping
          |
          v
Wave 2：并行产品化（基于已合并 Wave 1）
  T05 FlowVersion + round-trip + compatibility
  T06 API service（不改 public web_server）
  T07 drag/drop low-code UI（隔离组件）
  T08 product plugins + Codex Skills
          |
          v
Wave 3：总装与最终屏障
  T09 public integration / fixture wiring
          |
          +----------+-----------+
          v          v           v
  T10 integration  T11 perf    T12 browser
  + safety         benchmark   acceptance
          \          |           /
           \---------+----------/
                     v
               Final Barrier
```

Wave 1 内部实现只依赖冻结 Markdown 合同；T03/T04 可以输出合同 JSON，不复制
T01 的实现。Wave 2 从 Wave 1 合并 SHA 创建。T06/T07/T08 使用服务/fixture
边界，不修改入口。Wave 3 先完成 T09，再从 T09 合并 SHA 并行启动 T10-T12。

## 4. 文件所有权总表

目录模式 `/**` 表示任务独占该新目录。没有列出的文件不得修改。

| 任务 | 独占写文件 |
| --- | --- |
| T01 | `cam_automation/flow_contracts.py`; `cam_automation/flow_validation.py`; `cam_automation/schemas/cam-flow/**`; `tests/flow_contracts/**`; `tests/fixtures/cam-flow/contracts/**` |
| T02 | `cam_automation/asset_registry.py`; `cam_automation/capability_registry.py`; `tests/asset_capability/**`; `tests/fixtures/cam-flow/registry/**` |
| T03 | `cam_automation/adapters/powermill_macro.py`; `cam_automation/adapters/powermill_flow.py`; `plugins/powermill-cam-copilot/tests/test_powermill_flow_import.py`; `plugins/powermill-cam-copilot/fixtures/flow/**` |
| T04 | `plugins/ug-cam-copilot/src/ugcam_ai/adapters/nx_journal.py`; `plugins/ug-cam-copilot/src/ugcam_ai/adapters/nx_flow.py`; `plugins/ug-cam-copilot/tests/test_nx_flow_import.py`; `plugins/ug-cam-copilot/examples/nx_flow/**` |
| T05 | `cam_automation/flow_versions.py`; `cam_automation/flow_roundtrip.py`; `cam_automation/flow_compatibility.py`; `tests/flow_versions/**`; `tests/fixtures/cam-flow/versions/**` |
| T06 | `cam_automation/flow_service.py`; `cam_automation/flow_api.py`; `tests/flow_api/**`; `tests/fixtures/cam-flow/api/**` |
| T07 | `cam_automation/web/flow/**` |
| T08 | `plugins/ug-cam-copilot/app-plugin.json`; `plugins/powermill-cam-copilot/app-plugin.json`; `plugins/ug-cam-copilot/capabilities/**`; `plugins/powermill-cam-copilot/capabilities/**`; `plugins/ug-cam-copilot/skills/nx-flow-studio/**`; `plugins/powermill-cam-copilot/skills/powermill-flow-studio/**`; `tests/flow_plugins/**` |
| T09 | `cam_automation/web_server.py`; `cam_automation/integrations.py`; `cam_automation/fixture_runtime.py`; `cam_automation/__init__.py`; `cam_automation/__main__.py`; `cam_automation/cli.py`; `cam_automation/web/index.html`; `cam_automation/web/app.js`; `cam_automation/web/styles.css`; `tests/flow_integration/**` |
| T10 | `tests/flow_barrier/**`; `tests/fixtures/cam-flow/barrier/**` |
| T11 | `scripts/benchmark_cam_flow.py`; `tests/flow_performance/**`; `tests/fixtures/cam-flow/performance/**` |
| T12 | `tests/browser/cam-flow-studio.spec.cjs`; `tests/browser/fixtures/cam-flow/**`; `scripts/run_cam_flow_browser_acceptance.ps1` |

所有任务都可只读查看 `AGENTS.md`、README、architecture、冻结合同、产品方案、
四份研究和其依赖任务的已合并文件。

## 5. Wave 1 任务

### T01：FlowGraph IR、Schema 与确定性校验

**目标：** 实现冻结合同中 11 类对象的模型/Schema 入口、canonical hash 和共享
validator；不得解析 NX/PowerMill 语法。

**只读依赖：** 冻结合同、现有 models/recipes/contracts fixtures。

**实现：**

- JSON Schema 覆盖 AutomationAsset、CapabilityManifest、FlowGraph、
  FlowNode、FlowEdge、PortContract、ParameterBinding、SourceMapping、
  RoundTripReport、CompatibilityReport、PreviewPlan、FlowVersion。
- validator 覆盖 namespace、single-product、entry flow、三类 edge、type/unit/
  cardinality、dependency cycle、subflow recursion、binding one-of、risk floor、
  unknown field preservation 和资源限制。
- RFC 8785 canonicalization profile 与 semantic/source/artifact hash。
- 稳定 diagnostics 排序和冻结错误码。

**测试：**

- Schema 最小/完整/错误 fixture。
- 20 次 hash/diagnostic determinism。
- unknown optional fields round-trip。
- mixed product、跨 flow、cycle、type/unit/cardinality、risk downgrade 拒绝。
- 安全 payload 结构化拒绝且不回显。

**验收：** 所有冻结对象可独立校验；无产品语法 import；错误码与合同一致。

**提交：** `feat(flow): add frozen graph contracts and validation`

### T02：AutomationAsset 与 Capability 注册

**目标：** 实现本地资产/能力注册、不可变 revision、权利状态、manifest lock、
一次授权和分类撤销的核心服务。

**只读依赖：** T01 公共模型/validator、现有 plugin manager/recorder 权限语义。

**实现：**

- content-addressed asset revision，不覆盖原始 bytes。
- locator 脱敏、encoding/newline/hash、依赖、target version、runtime mode。
- rights unknown/restricted、sharing/redistribution/network/binary inspection 门禁。
- capability manifest 注册、证据分级、target ranges、risk floor、撤销和 lock。
- grant 绑定 user/plugin/version/manifest hash/category/scope；撤销关闭新 handle，
  不删除数据，不自动恢复任务。

**测试：**

- 同 content 幂等、不同 content 新 revision、原资产不可变。
- 权利未知时本地只读可用、分享/外发/preview 被拒。
- manifest hash/权限变化重新授权。
- 分类撤销 <=2 秒的 deterministic fake clock 测试。
- commercial binary 路径永久阻断。

**验收：** 注册不等于执行授权；撤销后图/资产可只读；无网络或 CAM transport。

**提交：** `feat(flow): add asset and capability registries`

### T03：PowerMill 离线导入映射

**目标：** 在 PowerMill adapter 内把 `.mac`/命令日志映射为 FlowGraph fragment、
source map、opaque 和 diagnostics，保持 bytes/CST fidelity。

**只读依赖：** 冻结合同、T01 JSON shape、PowerMill 研究和现有 fixtures。

**实现：**

- encoding/BOM/newline、token/trivia、命令 envelope 和 byte span 保留。
- 受支持的命令、变量、函数、分支、循环、include/macro、交互、NOGUI、
  selection/state 和 `DOCOMMAND` 保守映射。
- 录制未出现值保持 unknown，不填 PowerMill 默认。
- 未知/动态/vendor-only 语法形成 visible opaque/unsupported。
- 只输出 `cam.*` / `powermill.*`，不产生 preview transport。

**测试：**

- CRLF/LF/mixed、BOM、注释、引号、尾空格 F0。
- 控制流和 source-line/source-span。
- 常量/动态 `DOCOMMAND`，动态者 blocked。
- include 缺失、unknown command、vendor command 原文保留。
- 输入 NX Journal 必须拒绝且不调用 NX parser。
- monkeypatch 防止 macro 执行、COM/.NET 附着和 machine output。

**验收：** 未编辑 bytes 100% 一致；无静默丢弃；无 live 依赖。

**提交：** `feat(powermill): map offline macros to flow graph`

### T04：NX 离线导入映射

**目标：** 扩展既定 NX parser，把 NXOpen Python Journal 静态映射为
FlowGraph fragment、source map、Builder/Undo/selector/unsupported 语义。

**只读依赖：** 冻结合同、T01 JSON shape、NX 研究、现有 NX tests/stub fixtures。

**实现：**

- 继续只用 AST/token 静态分析，不 import/exec/eval/replay。
- 保守映射顺序、函数、if/loop、try/except/finally、调用和赋值。
- Builder `create/configure/commit|abandon/destroy`、Undo scope 一等建模。
- `FindObject`/JournalIdentifier 默认 fragile/unresolved。
- target stubs/API symbol 只记录证据，不猜 UF/dynamic/vendor-only 能力。
- unsupported region 原文、digest、span 和 blocker 可见。

**测试：**

- 现有 NX tests 全部回归。
- Builder 完整/不完整/重复提交/destroy 后使用。
- 分支/异常边、source-line/source-span、非参数区 hash。
- selector 0/多匹配不取第一个。
- malicious/dynamic AST 资源限制。
- PowerMill 输入拒绝；Journal 从未被运行；无 NC/postprocess。

**验收：** parser 文件边界保持；ActivityEvent 向后兼容；candidate 仅是不可执行
内存 patch。

**提交：** `feat(nx): map offline journals to flow graph`

## 6. Wave 2 任务

### T05：FlowVersion、Round-trip 与 Compatibility

**目标：** 实现不可变版本、四层 diff、F0-F3/FB、candidate reparse、
capability/target compatibility 和 Recipe ProjectionReport 协调逻辑。

**只读依赖：** Wave 1 全部合并产物、现有 Recipe/DiffReport。

**实现：**

- GraphCommand 与 layout/semantic command 分离；undo/redo 可逆。
- FlowVersion parent、hash、status、审阅失效规则。
- affected-span minimal patch、source digest guard、reparse semantic compare。
- NX/PowerMill adapter protocol 调用，不在共享层解析语法。
- node/capability/target/selector/context CompatibilityReport。
- FlowGraph -> Recipe v1 确定性投影；未投影语义必须 blocker。

**测试：**

- F0/F1/F2/F3/FB golden fixtures。
- untouched token/trivia 与 opaque hash。
- source 漂移、semantic mismatch、capability major mismatch 拒绝。
- layout 不进 semantic diff/hash。
- undo/redo 往返 hash 一致。
- 20 次 reports/diff/ProjectionReport 字节一致。

**验收：** 不能证明 round-trip 时无候选；migration 新 revision；不改变既有
recipe hash 规则。

**提交：** `feat(flow): add versions roundtrip and compatibility`

### T06：Flow API 服务

**目标：** 提供不依赖 HTTP server 入口的纯服务/API handler，覆盖资产、能力、
图、版本、校验、diff、test、compatibility 和 preview plan。

**只读依赖：** Wave 1 服务、冻结合同、现有 web API 错误习惯。

**实现：**

- route-neutral request/response handlers 和错误 envelope。
- 幂等创建、稳定 cursor/ETag 或 revision conflict、request size limit。
- PreviewPlan 状态机，只允许 fixture target 和 `transport=none`。
- cancellation/deadline/correlation ID；权限撤销和 hash mismatch fail closed。
- 不修改 `web_server.py`、`integrations.py` 或前端入口。

**测试：**

- 400/403/404/408/409/413/500 映射。
- stale revision、unknown field、invalid state transition。
- multi-instance fixture 明确绑定；歧义拒绝。
- preview 零执行字段固定。
- 并发/取消/重复请求确定性。

**验收：** T09 可通过单一注册函数接入；服务无 CAM transport。

**提交：** `feat(api): add CAM flow service handlers`

### T07：拖拽低代码 UI

**目标：** 在隔离 `web/flow` 目录实现 feature-complete 单窗口编辑器组件和
fixture harness，不修改现有入口。

**只读依赖：** 产品方案、冻结合同、T06 API fixtures。

**实现：**

- React Flow 或仓库可接受的 OSS core 视图；canonical state 仍为 FlowGraph。
- 资产/步骤库、画布、schema inspector、底栏问题/源码/diff/test/preview。
- typed connect、GraphCommand undo/redo、版本列表、source 双向定位。
- opaque/unsupported/权限/版本/门禁可见；关系逐步披露。
- fixture instance/project/version selector；无“运行/部署/加工”文案。
- 键盘、screen reader、缩放、长中文和桌面/移动响应式。

**测试：**

- 组件/fixture tests：连接类型、撤销/重做、source mapping、opaque、版本/diff。
- 目标选择不默认前台。
- 500 node viewport 性能 hook。
- AI 开/关对 canonical JSON 无影响。
- 文案与按钮确认不存在 live 执行暗示。

**验收：** 可由 fixture harness 完成产品闭环；入口接线留给 T09。

**提交：** `feat(ui): add CAM flow studio editor`

### T08：产品插件、CapabilityManifest 与 Codex Skills

**目标：** 为 NX/PowerMill 插件注册 Flow 能力并各提供一个结构化、
review-first Codex Skill。

**只读依赖：** Wave 1 adapter 输出、冻结合同、现有插件/Skill 结构。

**实现：**

- app-plugin manifest 只新增可选 capability/flow SDK 字段。
- 每产品独立 capability manifest，锁定 product、versions、ports、risk、
  forbidden modes、evidence 和 fixture。
- `nx-flow-studio` 只引用 NX adapter；`powermill-flow-studio` 只引用
  PowerMill adapter。
- Skill 只做解释、compatibility/round-trip 审阅和 GraphPatchProposal；
  不执行、不生成源码、不声称门禁通过。
- 安装/撤销/缺 capability 的可发现性元数据。

**测试：**

- manifest Schema/hash/version/risk floor。
- plugin install/uninstall/permission regression。
- dependency/import test 证明两产品 Skill 不交叉。
- prompt injection、自由文本执行、machine output 拒绝。
- cache/discovery metadata 可重复。

**验收：** 原有 Skills 可用；新增字段向后兼容；无 `.codex-plugin` 公共入口争用。

**提交：** `feat(plugins): expose CAM flow capabilities and skills`

## 7. Wave 3 任务

### T09：公共入口与 fixture 总装

**目标：** 作为唯一公共入口 owner，把 T01-T08 接入现有 app、HTTP server、
fixture runtime、CLI 和静态页面。

**只读依赖：** T01-T08 全部合并 commit。

**实现：**

- public route 注册和兼容错误处理；旧 API 保持兼容。
- 插件未安装时 `409 plugin_not_installed`；权限/版本/revision 状态明确。
- fixture runtime 注册两个 NX、两个 PowerMill 离线实例和测试项目。
- 加载 `web/flow` 组件，工作台成为实际首屏；插件中心仍维持显式安装。
- CLI 只提供离线 import/validate/test/preview，不提供 live 命令。
- 集成 shutdown/cancellation，不泄漏 worker。

**测试：**

- 旧 root/plugin/NX/PowerMill tests 回归。
- API -> service -> adapter -> version -> preview 完整 fixture 路径。
- 未安装插件、撤销权限、目标歧义、stale hash、opaque blocker。
- 旧 `/api/learn` 和现有路由不破坏。

**验收：** 公共入口无产品语法；PreviewPlan 零执行；仅此任务改入口文件。

**提交：** `feat(studio): integrate CAM flow workspace`

### T10：端到端与安全屏障

**目标：** 只写集成/对抗测试，证明产品闭环和永久安全边界。产品缺陷回报原 owner，
不得越权修代码。

**只读依赖：** T09 合并后的完整产品。

**测试：**

- 3 PowerMill + 3 NX 导入、解释、改参、version/diff/test/preview。
- source-line/source-span 双向定位，opaque 不丢。
- unknown field、F0-F3、candidate reparse、projection blocker。
- 2 NX + 2 PowerMill 多实例，1,000 fixture plan 串线为 0。
- 分类撤销、重新授权不自动恢复、网络默认 0。
- parser import isolation。
- live Journal/macro、CAM command、NC/G-code/CLSF/postprocess/machine control、
  prompt injection、binary inspection 全部拒绝且 payload 不落盘。

**验收：** 新旧全量测试通过；安全失败是 release blocker。

**提交：** `test(flow): add end-to-end safety barrier`

### T11：性能与确定性屏障

**目标：** 建立可复现 benchmark 和性能测试，不修改产品代码。

**只读依赖：** T09 完整产品、冻结性能目标。

**测试：**

- 500 node/800 edge 冷/温打开服务数据。
- 10,000 行 NX/PowerMill 离线 parse。
- 参数增量校验、undo/redo、semantic diff、500 node preview。
- cancel response、主进程/浏览器内存采样。
- 每 fixture 20 次 hash/diagnostic/diff/report determinism。
- 记录 CPU/RAM/Windows/runtime/cold-warm 方法。

**验收：** 目标达标；若未达标，提交 benchmark 与 `PERF-BLOCKED` 报告，不通过
跳过校验或缩小 fixture 伪造成功。

**提交：** `perf(flow): add deterministic performance barriers`

### T12：浏览器与可用性验收

**目标：** 使用真实本地服务和 Playwright/仓库浏览器栈做桌面、移动、键盘和
视觉验收，不修改产品代码。

**只读依赖：** T09 完整产品、产品方案 UI 验收。

**测试：**

- 1440x900、1280x720、390x844；无文本/控件重叠或截断。
- 首屏直接是工作台；品牌/产品上下文清晰，不是营销页。
- 完成导入 fixture、节点/源码双向定位、改参、undo/redo、version/diff、
  test/preview。
- opaque/blocked/权限/版本/门禁不只靠颜色。
- 键盘完成核心操作；screen reader labels、focus、tooltip。
- 多实例不自动选前台；preview 固定零执行字段。
- canvas 像素检查非空、缩放/拖拽稳定、500 node 视口不跳布局。

**验收：** screenshots、console/network audit 和测试报告齐全；无未授权请求。
失败回报 UI/T09 owner，不越权修改。

**提交：** `test(ui): add browser acceptance for CAM flow studio`

## 8. 合并顺序

每次合并使用非交互命令，先验证 commit 只包含独占文件。

1. T01
2. T02
3. T03
4. T04
5. 在 Wave 1 合并 SHA 上运行根、NX、PowerMill 回归。
6. T05
7. T06
8. T07
9. T08
10. 在 Wave 2 合并 SHA 上运行合同、adapter、service、UI fixture 回归。
11. T09
12. T10
13. T11
14. T12

T02-T04 在 T01 后合并，出现同名 contract helper 时必须改为引用 T01，不接受复制。
T05-T08 只处理各自目录；接线集中在 T09。T10-T12 只提交测试/脚本，不修产品。
其发现的问题由原 owner 在原分支追加单一 follow-up commit，再重新执行受影响屏障。

## 9. 最终屏障

### 9.1 文件与合同

- `git diff --name-only <BASE>...HEAD` 与 12 个所有权集合完全一致。
- 没有任务修改其他任务独占文件。
- 任务数在本计划、依赖图、提示词和提交列表中均为 12。
- 11 类冻结合同均有 Schema、实现、fixtures 和 errors。
- ActivityEvent/Recipe/CommandTask 既有字段和 hash 规则无破坏。

### 9.2 测试

至少运行：

```powershell
python -m unittest discover -s tests -v
python -m unittest discover -s plugins/ug-cam-copilot/tests -v
python -m unittest discover -s plugins/powermill-cam-copilot/tests -v
node --test cam_automation/web/components/fixtures.test.cjs
```

并运行 T01-T12 新增合同、registry、adapter、version、API、UI、plugin、
integration、performance 和 browser suites。若 PATH Python 不可用，使用工作区
bundled Python 并在报告中写明绝对路径；不得把未运行写成通过。

### 9.3 安全

通过测试与静态审查证明：

- Journal/macro 从未 live 执行；
- preview/codegen 无 transport handle；
- machine-ready NC/G-code/CLSF/postprocess/machine-control 输入输出为零；
- 商业二进制不加载、不探测；
- AI 不能直接写合同对象或改变安全结论；
- payload 被拒后不出现在日志、diff、报告、AI context；
- commands sent=0，四项生产门禁保持 required/not_run/unavailable。

### 9.4 现场声明

发布说明必须继续标记为未验证：

- 真实 NX/PowerMill 连接、项目映射和目标 build；
- 商业外挂席位、许可和授权 adapter；
- CAM 仿真、碰撞/过切、机床仿真、车间批准；
- 客户资产所有权/再分发法律结论；
- 客户工作站性能和 500 节点覆盖率。

Final Barrier 通过后才可把 MVP 标记为“离线可验收”。它永远不等于生产执行或
机床输出获批。
