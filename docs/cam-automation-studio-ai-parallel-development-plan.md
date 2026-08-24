# CAM Automation Studio AI 并行开发任务拆分

> 目标：让多个 AI Agent 在同一仓库中并行开发 PowerMill、NX、记录、学习、命令和 Codex 插件能力
>
> 推荐并发：每个波次最多 4 个开发 Agent，另设 1 个协调/集成角色
>
> 产品依据：[CAM Automation Studio 产品方案](./cam-automation-studio-huangyan-product-proposal.md)

## 1. 并行开发原则

本计划不是审核任务书。它只解决四个问题：

1. 哪些工作可以同时开发。
2. 每个 Agent 只能修改哪些文件。
3. Agent 之间通过什么稳定接口交付。
4. 何时暂停并行、进入集成。

同一 Codex 任务中的子 Agent 可能共享工作区，因此必须执行文件所有权规则：

- 同一时刻一个文件只能有一个写入者。
- 公共入口文件由固定集成 Agent 独占。
- 业务 Agent 优先新增独立模块和模块测试，不直接修改公共入口。
- 每个 Agent 开始前读取 `AGENTS.md` 和本任务文件。
- 发现其他 Agent 的改动时保留并兼容，不回退、不覆盖。
- 共享 ActivityEvent 只新增可选字段。
- NX 与 PowerMill 解析代码保持隔离。
- 学习结果保持 dry-run，不生成或发送机床可用 NC。

## 2. Agent 角色

| Agent | 固定职责 | 独占文件/目录 |
|---|---|---|
| `COORD` | 接口冻结、任务派发、集成、全量测试 | 公共合同文档、最终合并 |
| `A-REC` | ActivityEvent、记录器、日志查询 | `models.py`、`parser.py`、`recorder.py` |
| `A-PM` | 多实例连接与 PowerMill 适配 | `connection_monitor.py`、PowerMill profile/plugin |
| `A-NX` | NX Journal/NXOpen 适配 | `plugins/ug-cam-copilot/src/ugcam_ai/` |
| `A-LEARN` | 会话、重复流程、配方生成 | `learning.py`、`generator.py`、新会话模块 |
| `A-API` | HTTP API 和跨模块装配 | `web_server.py`、`integrations.py` |
| `A-UI` | 单窗口前端 | `web/index.html`、`web/app.js`、`web/styles.css` |
| `A-CMD` | 命令中心、任务队列、诊断 | `execution.py`、新 diagnostics 模块 |
| `A-CODEX` | Codex bridge、插件 manifest、Skills | `codex_bridge.py`、`.codex-plugin/`、`plugins/*/skills/` |

根目录文件均相对于仓库：

```text
C:\Users\s3272\Documents\ChatGPT\New project 3
```

## 3. 公共文件锁

以下文件不得由多个并行 Agent 同时修改：

| 文件 | 唯一所有者 | 其他 Agent 的交付方式 |
|---|---|---|
| `cam_automation/web_server.py` | `A-API` | 提供 service class、DTO 和测试 fixture |
| `cam_automation/integrations.py` | `A-API` | 提供 adapter interface，不直接注册 |
| `cam_automation/web/app.js` | `A-UI` | 提供 API 合同和 UI 状态样例 |
| `cam_automation/web/index.html` | `A-UI` | 在任务报告中说明组件需求 |
| `cam_automation/web/styles.css` | `A-UI` | 不由后端 Agent 修改 |
| `cam_automation/models.py` | `A-REC` | 其他 Agent 使用冻结 DTO |
| `plugins/ug-cam-copilot/schemas/activity-event.schema.json` | `A-REC` | `A-NX` 只提交兼容性测试 |
| `README.md` | `COORD` | Agent 提交文档片段到任务报告 |
| `docs/architecture.md` | `COORD` | Agent 提交架构变化摘要 |

若某个任务必须修改其他 Agent 的独占文件：

1. 不直接编辑。
2. 在任务目录新增接口建议或测试 fixture。
3. 把修改请求发给文件所有者。
4. 等集成屏障统一接入。

## 4. 总体波次

```text
Wave 0: COORD 冻结接口
    |
    +---------------------------------------------------+
    |                 |                 |               |
Wave 1-A           Wave 1-B          Wave 1-C        Wave 1-D
A-REC              A-PM              A-NX            A-LEARN
记录/事件           连接/PowerMill     NX 适配          会话/学习/配方
    |                 |                 |               |
    +-----------------+-----------------+---------------+
                         Barrier 1
                 合同测试 + 模块测试通过
                             |
    +------------------------+---------------------------+
    |                 |                  |               |
Wave 2-A           Wave 2-B           Wave 2-C        Wave 2-D
A-API              A-UI               A-CMD           A-CODEX
API 装配            单窗口 UI           命令/诊断        插件/Skills
    |                 |                  |               |
    +-----------------+------------------+---------------+
                         Barrier 2
                  API/UI/任务流联调通过
                             |
                         Wave 3
              COORD 集成、性能、演示、发布
```

任何一个屏障未通过，都不启动下一波次。

## 5. Wave 0：接口冻结

### W0-01 公共合同清单

**执行 Agent**：`COORD`

**目标**

在并行写代码前固定 ActivityEvent、实例描述、日志查询、会话、配方、命令任务和 Codex 审阅结果的最小合同。

**只允许修改**

- 新增 `docs/contracts/parallel-development-contracts.md`
- 必要时新增 JSON 示例到 `examples/contracts/`

**必须冻结的合同**

```text
ActivityEvent
InstanceDescriptor
EventQuery / EventPage
Session / SessionDiff
Recipe / RecipeStep / RecipeParameter
CommandTask / CommandResponse / DiffReport
CodexReviewRequest / CodexReviewResult
```

**关键字段**

```json
{
  "source_mode": "manual|automation|system|execution_audit",
  "view_level": "L0|L1|L2|L3|L4",
  "instance_id": "string",
  "target_version": "string",
  "recipe_hash": "string"
}
```

**完成条件**

- 现有 ActivityEvent 字段未删除或重命名。
- 每个合同都有 JSON 示例。
- 所有 Wave 1 Agent 确认读取同一合同文件。

### W0-02 基线

**执行 Agent**：`COORD`

**命令**

```powershell
python -m unittest discover -s tests -v
python -m unittest discover -s plugins/ug-cam-copilot/tests -v
git status --short --branch
```

**输出**

- 当前测试数和结果。
- 当前 API 路由清单。
- 当前插件 manifest 清单。
- 当前已知未实现能力。

## 6. Wave 1：四路并行核心开发

### W1-A 事件、记录与日志查询

**执行 Agent**：`A-REC`

**独占文件**

- `cam_automation/models.py`
- `cam_automation/parser.py`
- `cam_automation/recorder.py`
- `plugins/ug-cam-copilot/schemas/activity-event.schema.json`
- `tests/test_parser.py`
- `tests/test_recorder.py`

**不要修改**

- `web_server.py`
- `integrations.py`
- Web 前端
- `nx_journal.py`

**任务**

1. 为 ActivityEvent 增加合同中定义的可选字段。
2. 保持旧 JSONL 可导入、可往返。
3. 记录插件启用后自动增量读取支持的日志源。
4. 支持 `recording / paused / source_interrupted / catching_up / error` 状态。
5. 实现模式、层级、产品、实例、项目、动作、时间和文本筛选 service。
6. 实现稳定游标分页和必要索引。
7. 暂停/恢复后从游标续接，不重复事件。

**交付给 `A-API`**

```python
RecorderService.status()
RecorderService.query_events(EventQuery) -> EventPage
RecorderService.pause(category=None)
RecorderService.resume(category=None)
RecorderService.scan_now()
```

**完成命令**

```powershell
python -m unittest tests.test_parser tests.test_recorder -v
```

### W1-B 多实例连接与 PowerMill

**执行 Agent**：`A-PM`

**独占文件**

- `cam_automation/connection_monitor.py`
- `cam_automation/profiles/powermill.py`
- `plugins/powermill-cam-copilot/`
- 新增 `cam_automation/adapters/powermill_macro.py`
- `tests/test_connection_monitor.py`
- 新增 PowerMill adapter 测试

**不要修改**

- `nx_journal.py`
- `web_server.py`
- `integrations.py`
- Web 前端

**任务**

1. 建立稳定运行期 `InstanceDescriptor`。
2. 同时识别多个 PowerMill/NX 进程和窗口。
3. 无窗口 CAM 进程仍作为进程实例显示。
4. 实现 `.mac / .log / JSONL` PowerMill 适配器。
5. 保留引号内空格、参数顺序、原始行和宏默认值缺口提示。
6. 定义 PowerMill 查询 transport protocol 和 fixture transport。
7. 验证每实例独立连接和响应归属。

**交付给 `A-API` 和 `A-CMD`**

```python
ConnectionMonitor.list_instances() -> list[InstanceDescriptor]
PowerMillAdapter.parse(source) -> list[ActivityEvent]
PowerMillTransport.query(instance_id, command) -> CommandResponse
PowerMillTransport.snapshot(instance_id) -> SnapshotMetadata
```

**完成命令**

```powershell
python -m unittest tests.test_connection_monitor -v
python -m unittest discover -s plugins/powermill-cam-copilot -v
```

### W1-C NX Journal 与 NXOpen

**执行 Agent**：`A-NX`

**独占目录**

- `plugins/ug-cam-copilot/src/ugcam_ai/`
- `plugins/ug-cam-copilot/tests/`
- `plugins/ug-cam-copilot/examples/`

**不要修改**

- 根 `cam_automation/parser.py`
- PowerMill profile/adapter
- 根 `web_server.py`
- Web 前端

**任务**

1. 保持 Journal AST 静态解析，不执行 Journal。
2. 识别 Builder、参数赋值、Commit、Destroy 和 Undo Mark。
3. 输出统一 ActivityEvent 可选字段。
4. 标记脆弱 `FindObject` 录制 ID，提供稳定选择器建议模型。
5. 增加目标版本 Python stubs 对照接口。
6. 建立 NX 查询 transport protocol 和 fixture。
7. 保持 `nx.*` 动作与共享 `cam.*` 动作边界。

**交付给 `A-API` 和 `A-CMD`**

```python
NxJournalAdapter.parse(path) -> list[ActivityEvent]
NxVersionContract.load_stubs(path)
NxTransport.query(instance_id, operation) -> CommandResponse
NxTransport.snapshot(instance_id) -> SnapshotMetadata
```

**完成命令**

```powershell
python -m unittest discover -s plugins/ug-cam-copilot/tests -v
```

### W1-D 会话、学习与配方

**执行 Agent**：`A-LEARN`

**独占文件**

- `cam_automation/learning.py`
- `cam_automation/generator.py`
- 新增 `cam_automation/sessions.py`
- 新增 `cam_automation/recipes.py`
- `tests/test_learning.py`
- `tests/test_generator.py`
- 新增 session/recipe 测试

**不要修改**

- `models.py`
- `parser.py`
- `recorder.py`
- `web_server.py`
- Web 前端

**任务**

1. 根据实例、项目、显式标记和时间生成确定性会话。
2. 支持合并、拆分和 2-5 个会话对比。
3. 使用结构签名发现重复流程。
4. 输出支持度、来源会话、公共步骤、分支和类型化参数。
5. 生成稳定 recipe hash。
6. 配方支持步骤排序、启停、参数、条件、版本范围和来源。
7. PowerMill/NX 预览通过 adapter protocol 调用，不在共享生成器中解析产品语法。

**交付给 `A-API`、`A-UI` 和 `A-CODEX`**

```python
SessionService.build(events) -> list[Session]
SessionService.compare(session_ids) -> SessionDiff
WorkflowLearner.mine(session_ids) -> list[RecipeCandidate]
RecipeService.save(recipe) -> RecipeVersion
RecipeService.preview(recipe_hash, parameters) -> DiffReport
```

**完成命令**

```powershell
python -m unittest tests.test_learning tests.test_generator -v
python -m unittest discover -s tests -p "test_session*.py" -v
```

## 7. Barrier 1：核心合同集成

**执行 Agent**：`COORD`

Wave 1 全部停止写公共代码，协调者执行：

1. 对照 `docs/contracts/parallel-development-contracts.md` 检查 DTO。
2. 解决 import 和类型名称差异。
3. 运行根仓库和 UG 插件全量测试。
4. 生成统一 fixture：
   - 两个 NX 实例。
   - 两个 PowerMill 实例。
   - 三个相似 manual 会话。
   - 一组 automation/system/command 事件。
5. 固定 Wave 2 API 响应示例。

**屏障通过条件**

```powershell
python -m unittest discover -s tests -v
python -m unittest discover -s plugins/ug-cam-copilot/tests -v
git diff --check
```

- 无 DTO 重复定义。
- 无循环依赖。
- 所有产品事件可以进入同一 session/learning 流水线。
- 任何 PowerMill 测试都不依赖 NX parser。

## 8. Wave 2：四路并行产品装配

### W2-A HTTP API 装配

**执行 Agent**：`A-API`

**独占文件**

- `cam_automation/web_server.py`
- `cam_automation/integrations.py`
- `tests/test_web_server.py`
- `tests/test_integrations.py`

**任务**

1. 只装配 Wave 1 已冻结 service，不复制业务逻辑。
2. 提供插件、连接、记录、日志、会话、学习、配方和命令 API。
3. 日志 API 支持模式/层级组合筛选和游标分页。
4. 错误码区分插件未安装、实例未连接、参数错误、超时和任务取消。
5. 输出给 `A-UI` 的 OpenAPI 风格 Markdown/JSON 示例。

**API 最小集合**

```text
GET  /api/plugins
GET  /api/connections
GET  /api/recorder
GET  /api/recorder/events
GET  /api/sessions
POST /api/sessions/compare
POST /api/workflows/mine
GET  /api/recipes/:id
POST /api/recipes
POST /api/execution/run
GET  /api/execution/tasks/:id
```

**完成命令**

```powershell
python -m unittest tests.test_web_server tests.test_integrations -v
```

### W2-B 单窗口前端

**执行 Agent**：`A-UI`

**独占文件**

- `cam_automation/web/index.html`
- `cam_automation/web/app.js`
- `cam_automation/web/styles.css`
- 必要时新增 `cam_automation/web/components/`

**任务**

1. 顶部连接条显示 Codex、NX、PowerMill 数量和记录状态。
2. 连接详情使用右侧抽屉，不创建多个顶级窗口。
3. 日志页提供“仅手动”快捷筛选和独立 L0-L4 控件。
4. 会话页支持时间线、合并/拆分和对比。
5. 配方页提供步骤、参数/条件、来源/差异三栏。
6. 命令页显示目标实例、版本、项目、响应和 DiffReport。
7. 插件中心支持安装、卸载、更新和推荐组合。
8. 诊断页显示实例、游标、延迟、队列和任务耗时。

**输入**

- 只使用 `A-API` 输出的固定 API fixture。
- API 尚未完成时使用 fixture server，不修改后端入口。

**视口**

```text
1280x720
1920x1080
390x844
```

**完成条件**

- 无文本/控件重叠。
- 日志超过 500 条时使用分页或虚拟滚动。
- 加载、空、错误、断连、超时和无匹配状态完整。

### W2-C 命令中心与诊断

**执行 Agent**：`A-CMD`

**独占文件**

- `cam_automation/execution.py`
- 新增 `cam_automation/command_tasks.py`
- 新增 `cam_automation/diagnostics.py`
- `tests/test_execution.py`
- 新增 command/diagnostics 测试

**不要修改**

- `web_server.py`
- `integrations.py`
- Web 前端

**任务**

1. 命令任务绑定产品、目标版本、`target_instance_id`、项目和 recipe hash。
2. 每实例独立串行队列。
3. 支持进度、取消、超时、断连和结果缓存。
4. 查询和 dry-run 返回原始响应、结构化响应、耗时和 DiffReport。
5. 输出实例心跳、队列长度、日志延迟和任务指标。
6. 保持仓库现有 execution 回归边界。

**交付给 `A-API`**

```python
CommandTaskService.submit(task) -> TaskDescriptor
CommandTaskService.get(task_id) -> TaskStatus
CommandTaskService.cancel(task_id)
DiagnosticsService.snapshot() -> DiagnosticsSnapshot
```

**完成命令**

```powershell
python -m unittest tests.test_execution -v
python -m unittest discover -s tests -p "test_command*.py" -v
```

### W2-D Codex 插件与 Skills

**执行 Agent**：`A-CODEX`

**独占文件**

- `cam_automation/codex_bridge.py`
- `.codex-plugin/`
- `plugins/*/.codex-plugin/`
- `plugins/*/skills/`
- Codex bridge/Skill 测试

**不要修改**

- NX/PowerMill parser
- `web_server.py`
- Web 前端

**任务**

1. 定义 `CodexReviewRequest / CodexReviewResult`。
2. 上下文包包含配方、来源事件、会话差异、产品、版本和问题。
3. 返回 `findings / suggested_parameters / questions / summary`。
4. NX Skill 只引用 NX adapter；PowerMill Skill 只引用 PowerMill adapter。
5. 更新插件 manifest、版本号、Skills references 和 marketplace 元数据。
6. 执行 Codex 插件 cachebuster、重新安装和技能可发现性检查。

**完成条件**

- NX/PowerMill fixture 产生相同结构的审阅结果。
- manifest 与 Skill 目录校验通过。
- 不通过自由文本正则解析 Codex 结果。

## 9. Barrier 2：产品联调

**执行 Agent**：`COORD`

联调顺序：

1. 合并 `A-CMD` service。
2. 由 `A-API` 装配 command/diagnostics API。
3. 用固定 API fixture 验证 `A-UI`。
4. 接入真实本地 API，再跑浏览器交互。
5. 接入 `A-CODEX` bridge 和 Skills。

**必须跑通的链路**

```text
多实例发现
  -> 自动记录
  -> manual + L2 筛选
  -> 三会话对比
  -> 重复配方
  -> Codex 结构化建议
  -> 目标实例 dry-run
  -> response + DiffReport
```

**屏障通过条件**

- API fixture 与真实 API 字段一致。
- 前端不包含后端业务判断的复制实现。
- 多实例任务目标从 UI 到 transport 全程一致。
- 根仓库和 UG 插件全量测试通过。

## 10. Wave 3：集成、性能和发布

### W3-01 端到端演示

**执行 Agent**：`COORD`

1. 从空白插件注册表启动。
2. 安装 PowerMill、NX、本地记录、命令和 Codex 插件。
3. 加载多实例 fixture。
4. 自动记录三个相似 manual 会话。
5. 筛选、对比、学习并生成配方。
6. Codex 返回结构化建议。
7. 指定实例执行 dry-run。
8. 展示响应、差异、诊断和统计。

### W3-02 性能验证

**可并行 Agent**：`A-REC`、`A-LEARN`、`A-CMD` 分别运行，不再修改公共接口。

| 路径 | 目标 |
|---|---|
| 新日志可见 | 2 秒内 |
| 后台记录 CPU | 平均 < 2% |
| 100,000 事件导入 | < 10 秒 |
| UI 查询 p95 | < 200 ms |
| 单宿主查询超时 | 默认 5 秒 |
| 10,000 会话学习 | 结果确定、无内存失控 |

### W3-03 发布收口

**执行 Agent**：`COORD`

统一修改：

- `README.md`
- `docs/architecture.md`
- `docs/mvp.md`
- 插件版本和 manifest
- Skills 版本
- 演示说明
- 发布说明

最终命令：

```powershell
python -m unittest discover -s tests -v
python -m unittest discover -s plugins/ug-cam-copilot/tests -v
git diff --check
git status --short --branch
```

## 11. 可直接派发的 Agent 提示词

完整的逐窗口提示词见：

- [并行 Agent 对话提示词](./cam-automation-studio-parallel-agent-prompts.md)

协调者向每个 Agent 发送以下模板：

```markdown
你负责 CAM Automation Studio 的 <任务 ID / Agent 角色>。

必须先读取：
- AGENTS.md
- docs/cam-automation-studio-ai-parallel-development-plan.md
- docs/contracts/parallel-development-contracts.md

你只能修改：
<独占文件清单>

禁止修改：
<公共文件和其他 Agent 文件>

实现目标：
<任务列表>

必须交付：
1. 实现代码
2. 本模块测试
3. 对公共接口的交付说明
4. 实际运行的测试命令和结果
5. 未完成项或外部环境阻塞

不要回退其他 Agent 的修改。发现接口不匹配时停止修改公共文件，
把所需变更发送给 COORD 或该文件所有者。
```

## 12. Agent 完成报告

每个 Agent 使用统一格式：

```markdown
## <任务 ID> 完成报告

- Agent：
- 状态：complete / blocked
- 修改文件：
- 新增接口：
- 使用的冻结合同：
- 测试命令：
- 测试结果：
- 性能结果：
- 交付给其他 Agent：
- 未完成项：
- 建议集成顺序：
```

## 13. 冲突处理

### 13.1 同文件冲突

- 立即停止其中一个 Agent。
- 保留双方当前改动。
- 由文件所有者读取并手工合并。
- 不使用 `git checkout --`、`git reset --hard` 或覆盖式复制。

### 13.2 接口冲突

- 以 Wave 0 合同为准。
- 不能满足时新增可选字段或 adapter，不修改既有含义。
- 由 `COORD` 记录合同变更并通知全部 Agent。

### 13.3 测试冲突

- 模块 Agent 负责修复自己独占模块的失败。
- 公共 API 装配失败由 `A-API` 处理。
- 跨产品 schema 失败由 `COORD + A-REC + A-NX` 处理。
- 真实 PowerMill/NX 环境缺失记录为 fixture 与现场验证任务，不伪造通过结果。

## 14. 完成定义

并行开发完成必须同时满足：

- 两个屏障全部通过。
- 所有 Agent 完成报告齐全。
- 多实例状态、自动记录、模式/层级筛选、会话学习、配方、Codex 和 dry-run 链路跑通。
- PowerMill 与 NX 解析器边界保持不变。
- ActivityEvent 旧输入继续兼容。
- 根仓库和 UG 插件全量测试通过。
- 文档、插件 manifest、Skills 和 GitHub 分支同步更新。

## 15. 发布执行状态（2026-08-24）

| 阶段 | 状态 | 集成基线 |
|---|---|---|
| Wave 0 | 完成 | `8b10612` |
| Wave 1 / Barrier 1 | 完成 | `a225e52` |
| Wave 2 / Barrier 2 | 完成 | 最终发布提交 |
| Wave 3 | 完成 | fixture E2E、性能、浏览器、文档和发布校验 |

最终链路已在两个 NX、两个 PowerMill 离线实例上通过：本地自动记录、`manual + L2`
游标分页、3/5 会话比较、学习候选、版本化配方、Codex 结构化审阅、明确目标实例
dry-run、任务轮询/取消、`DiffReport` 和 diagnostics。没有调用真实 CAM。

发布校验：

- 根 117、UG/NX 31、PowerMill 6；focused Barrier/合同/API/CMD/Codex 53。
- 前端 4；20 个 JSON、2 个 JSONL（26 行）；`compileall` 通过。
- 5 个插件官方 validator、8 个 Skill 官方 validator 通过。
- 100,000 事件导入 5.489 秒；查询 p95 0.404 ms。
- 10,000 会话学习 1.021 秒，逆序输入结果确定。
- 1,000 命令队列 0.770 秒，全部有序成功，单实例最大并发为 1。
- 浏览器 1920x1080、1280x720、390x844 无页面级横向溢出或控件重叠，
  控制台 warning/error 为 0。

现场 PowerMill/NX transport、仿真、碰撞检查和车间批准仍是发布外验证项。当前版本
不提供 silent upload、live execution 或 machine-ready NC 输出。
