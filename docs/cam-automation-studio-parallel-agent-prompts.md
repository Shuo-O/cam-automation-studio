# CAM Automation Studio 并行 Agent 对话提示词

> 用途：直接粘贴到 Codex 的独立任务对话中
>
> 峰值并发：5 个对话窗口
>
> 全周期建议：1 个协调对话 + 4 个 Wave 1 对话 + 4 个 Wave 2 对话

## 1. 应该开几个窗口

不要一次打开 9 个窗口同时写代码。推荐顺序：

| 阶段 | 同时打开 | 说明 |
|---|---:|---|
| 接口冻结 | 1 | 只运行协调窗口 |
| Wave 1 | 5 | 协调窗口保持待命，4 个核心 Agent 并行 |
| Barrier 1 | 1 | 只由协调窗口集成 |
| Wave 2 | 5 | 协调窗口保持待命，4 个产品 Agent 并行 |
| Barrier 2 / 发布 | 1 | 只由协调窗口联调和发布 |

全周期如果不复用开发对话，共 9 个对话：

```text
窗口 1：COORD，贯穿全程
窗口 2-5：Wave 1 四个开发任务
窗口 6-9：Wave 2 四个开发任务
```

每个开发对话应使用同一项目的独立 worktree。Wave 1 的 4 个 worktree 必须从协调窗口完成接口冻结后的同一提交创建；Wave 2 的 4 个 worktree 必须从 Barrier 1 合并后的同一提交创建。

不要让 4 个开发对话直接共用同一个工作目录写文件。若只能使用同一目录，则必须严格遵守文件独占清单，并且同一时间不得运行集成 Agent。

## 2. 操作顺序

1. 在当前项目打开窗口 1，粘贴“COORD 启动提示词”。
2. 等窗口 1 完成 Wave 0、提交并给出基线提交 SHA。
3. 从该 SHA 创建 4 个独立 worktree 对话，分别粘贴 Wave 1 提示词。
4. 4 个 Agent 完成后，把提交 SHA 发回窗口 1。
5. 窗口 1 执行 Barrier 1，合并并给出新 SHA。
6. 从 Barrier 1 SHA 新建 4 个 worktree 对话，分别粘贴 Wave 2 提示词。
7. 把 Wave 2 的提交 SHA 发回窗口 1。
8. 窗口 1 执行 Barrier 2、全量测试、演示和 GitHub 推送。

## 3. 窗口 1：COORD 启动提示词

```text
你是 CAM Automation Studio 的协调与集成 Agent，贯穿整个项目。

仓库：
C:\Users\s3272\Documents\ChatGPT\New project 3

当前第一目标不是实现全部功能，而是完成 Wave 0：
1. 读取 AGENTS.md。
2. 完整读取：
   - docs/cam-automation-studio-huangyan-product-proposal.md
   - docs/cam-automation-studio-ai-parallel-development-plan.md
3. 检查 git status，保留所有已有修改。
4. 运行根仓库与 UG/NX 插件测试，记录基线。
5. 新建 docs/contracts/parallel-development-contracts.md。
6. 冻结并写出以下合同及 JSON 示例：
   - ActivityEvent
   - InstanceDescriptor
   - EventQuery / EventPage
   - Session / SessionDiff
   - Recipe / RecipeStep / RecipeParameter
   - CommandTask / CommandResponse / DiffReport
   - CodexReviewRequest / CodexReviewResult
7. ActivityEvent 只能增加可选字段，不删除或重命名现有字段。
8. 共享动作使用 cam.*，NX 专属动作使用 nx.*，
   PowerMill 专属动作使用 powermill.*。
9. 不修改 NX parser 来支持 PowerMill。
10. 不生成或发送机床可用 NC。

Wave 0 只允许修改合同文档、合同示例和必要的基线文档。
不要开始 Wave 1 功能实现。

完成后：
- 运行 git diff --check。
- 提交 Wave 0。
- 给出提交 SHA。
- 输出四个 Wave 1 Agent 应基于的准确 SHA。
- 输出基线测试结果和仍未实现的能力。

不要注销、关机或重启电脑。
```

## 4. Wave 1：四个并行开发窗口

以下 4 个对话必须基于同一个 Wave 0 提交创建独立 worktree。

### 窗口 2：A-REC

```text
你是 Wave 1 的 A-REC Agent，负责事件合同落地、记录器和日志查询。

开始前完整读取：
- AGENTS.md
- docs/contracts/parallel-development-contracts.md
- docs/cam-automation-studio-ai-parallel-development-plan.md

你独占并且只能修改：
- cam_automation/models.py
- cam_automation/parser.py
- cam_automation/recorder.py
- plugins/ug-cam-copilot/schemas/activity-event.schema.json
- tests/test_parser.py
- tests/test_recorder.py
- 可新增与 recorder/query 直接对应的独立测试文件

禁止修改：
- cam_automation/web_server.py
- cam_automation/integrations.py
- cam_automation/web/*
- plugins/ug-cam-copilot/src/ugcam_ai/adapters/nx_journal.py
- PowerMill adapter/profile
- README.md 和公共架构文档

实现：
1. 将冻结合同中的 ActivityEvent 可选字段落地。
2. 保持旧 JSONL 兼容和新字段往返。
3. 记录插件启用后自动增量读取支持的日志源。
4. 实现 recording、paused、source_interrupted、catching_up、error 状态。
5. 实现模式、层级、产品、实例、项目、动作、时间和文本查询 service。
6. 实现稳定游标分页、索引、暂停/恢复和增量续接。
7. 相同输入重复扫描不得重复写入。

必须向 A-API 交付稳定接口：
- RecorderService.status()
- RecorderService.query_events(EventQuery) -> EventPage
- RecorderService.pause(category=None)
- RecorderService.resume(category=None)
- RecorderService.scan_now()

测试至少包括：
- 旧事件兼容
- 新字段 round-trip
- manual + L2 + instance_id 组合查询
- 相同时间戳游标分页
- 暂停后追加日志再恢复
- 重启/重复扫描不重复写入

不要删除、跳过或弱化现有测试。
不要编辑其他 Agent 已修改的文件。

完成后运行本模块测试和 git diff --check，提交独立 commit，
输出提交 SHA、接口说明、测试结果和需要 COORD/A-API 接入的事项。
不要自行合并其他分支，不要推送主集成分支。
```

### 窗口 3：A-PM

```text
你是 Wave 1 的 A-PM Agent，负责多实例连接和 PowerMill 适配器。

开始前完整读取：
- AGENTS.md
- docs/contracts/parallel-development-contracts.md
- docs/cam-automation-studio-ai-parallel-development-plan.md
- Autodesk PowerMill 相关现有 references 和 fixtures

你独占并且只能修改：
- cam_automation/connection_monitor.py
- cam_automation/profiles/powermill.py
- plugins/powermill-cam-copilot/
- 可新增 cam_automation/adapters/powermill_macro.py
- tests/test_connection_monitor.py
- 可新增 PowerMill adapter/transport 测试和 fixtures

禁止修改：
- plugins/ug-cam-copilot/src/ugcam_ai/adapters/nx_journal.py
- cam_automation/models.py
- cam_automation/parser.py
- cam_automation/web_server.py
- cam_automation/integrations.py
- cam_automation/web/*
- README.md 和公共架构文档

实现：
1. 使用冻结的 InstanceDescriptor 建立稳定运行期实例 ID。
2. 同时识别多个 PowerMill/NX 进程和窗口。
3. 无窗口 CAM 进程仍显示为进程实例。
4. 新建独立 PowerMill .mac/.log/JSONL 解析适配器。
5. 保留原始行、引号内空格、参数顺序和宏默认值缺口提示。
6. 定义 PowerMill 查询 transport protocol 和 fixture transport。
7. 每个实例连接、命令队列和响应归属必须隔离。
8. 未配置真实 API 时不得伪造 live 已连接。

必须向 A-API/A-CMD 交付：
- ConnectionMonitor.list_instances() -> list[InstanceDescriptor]
- PowerMillAdapter.parse(source) -> list[ActivityEvent]
- PowerMillTransport.query(instance_id, command) -> CommandResponse
- PowerMillTransport.snapshot(instance_id) -> SnapshotMetadata

测试至少包括：
- 多进程多窗口实例数量
- 重扫后实例 ID 稳定
- 无窗口进程
- 两个 PowerMill 实例响应不串线
- 超时、断连和错误响应
- PowerMill 解析完全不经过 NX parser

完成后运行本模块测试和 git diff --check，提交独立 commit，
输出提交 SHA、接口说明、fixtures 和仍需真实 PowerMill 验证的部分。
不要自行合并其他分支，不要推送主集成分支。
```

### 窗口 4：A-NX

```text
你是 Wave 1 的 A-NX Agent，负责 NX Journal 和 NXOpen 适配。

开始前完整读取：
- AGENTS.md
- docs/contracts/parallel-development-contracts.md
- docs/cam-automation-studio-ai-parallel-development-plan.md
- plugins/ug-cam-copilot/README.md
- NX Skill references

你独占并且只能修改：
- plugins/ug-cam-copilot/src/ugcam_ai/
- plugins/ug-cam-copilot/tests/
- plugins/ug-cam-copilot/examples/
- 与 NX adapter 直接相关的 NX 文档

禁止修改：
- cam_automation/parser.py
- cam_automation/models.py
- PowerMill profile/adapter/plugin
- cam_automation/web_server.py
- cam_automation/integrations.py
- cam_automation/web/*
- README.md 和根公共架构文档

实现：
1. Journal 只做 AST 静态解析，绝不 import、exec 或运行 Journal。
2. 识别 Builder、参数赋值、Commit、Destroy 和 Undo Mark。
3. 输出冻结合同定义的统一 ActivityEvent 可选字段。
4. 标记脆弱 FindObject 录制 ID，输出稳定选择器建议模型。
5. 增加目标版本 Python stubs 对照接口。
6. 建立 NX 查询 transport protocol 和 fixture。
7. NX 专属动作使用 nx.*，共享动作使用 cam.*。
8. PowerMill 输入不得进入 NX adapter。

必须向 A-API/A-CMD 交付：
- NxJournalAdapter.parse(path) -> list[ActivityEvent]
- NxVersionContract.load_stubs(path)
- NxTransport.query(instance_id, operation) -> CommandResponse
- NxTransport.snapshot(instance_id) -> SnapshotMetadata

测试至少包括：
- 现有 NX pipeline
- 异常和恶意 Python Journal 不会执行
- Builder 生命周期与 Undo Mark
- FindObject 脆弱 ID 提示
- 目标版本 stubs fixture
- 10,000 事件存储性能不退化

完成后运行 UG/NX 插件全量测试和 git diff --check，提交独立 commit，
输出提交 SHA、接口说明、测试结果和仍需真实 NX 环境验证的部分。
不要自行合并其他分支，不要推送主集成分支。
```

### 窗口 5：A-LEARN

```text
你是 Wave 1 的 A-LEARN Agent，负责会话、重复流程学习和配方模型。

开始前完整读取：
- AGENTS.md
- docs/contracts/parallel-development-contracts.md
- docs/cam-automation-studio-ai-parallel-development-plan.md
- cam_automation/learning.py
- cam_automation/generator.py
- 关联测试

你独占并且只能修改：
- cam_automation/learning.py
- cam_automation/generator.py
- 可新增 cam_automation/sessions.py
- 可新增 cam_automation/recipes.py
- tests/test_learning.py
- tests/test_generator.py
- 可新增 session/recipe 测试

禁止修改：
- cam_automation/models.py
- cam_automation/parser.py
- cam_automation/recorder.py
- cam_automation/web_server.py
- cam_automation/integrations.py
- cam_automation/web/*
- NX/PowerMill adapter
- README.md 和公共架构文档

实现：
1. 根据实例、项目、显式标记和时间生成确定性会话。
2. 支持会话合并、拆分和 2-5 个会话对比。
3. 对比输出公共步骤、缺失步骤、参数差异和耗时。
4. 使用结构签名发现重复流程。
5. 输出支持度、来源会话、公共步骤、分支和类型化参数。
6. 返工/探索步骤不要错误提升为稳定主流程。
7. 配方支持步骤、参数、条件、版本范围、来源和稳定 recipe hash。
8. 产品预览只通过 adapter protocol，不在共享代码中解析产品语法。

必须向 A-API/A-UI/A-CODEX 交付：
- SessionService.build(events) -> list[Session]
- SessionService.compare(session_ids) -> SessionDiff
- WorkflowLearner.mine(session_ids) -> list[RecipeCandidate]
- RecipeService.save(recipe) -> RecipeVersion
- RecipeService.preview(recipe_hash, parameters) -> DiffReport

测试至少包括：
- 多实例交错事件不会错误合并
- 相同输入会话和候选排序确定
- 三个相似会话发现公共主流程
- 参数类型与来源
- recipe hash 变化
- 空日志、单会话和完全不同会话

完成后运行本模块测试和 git diff --check，提交独立 commit，
输出提交 SHA、接口说明、测试结果和 A-UI 所需状态样例。
不要自行合并其他分支，不要推送主集成分支。
```

## 5. 窗口 1：Barrier 1 集成提示词

四个 Wave 1 Agent 完成后，将它们的提交 SHA 填入下面提示词，再发给窗口 1。

```text
现在执行 Barrier 1 集成。

Wave 1 提交：
- A-REC: <SHA>
- A-PM: <SHA>
- A-NX: <SHA>
- A-LEARN: <SHA>

要求：
1. 读取四个 Agent 完成报告和所有 diff。
2. 按 A-REC、A-PM、A-NX、A-LEARN 顺序集成。
3. 保留已有修改，不使用 reset --hard 或 checkout -- 覆盖文件。
4. 以 docs/contracts/parallel-development-contracts.md 为唯一合同依据。
5. 解决 import、类型名称、fixture 和 schema 差异。
6. 不把 PowerMill 语法加入 nx_journal.py。
7. 生成统一集成 fixtures：
   - 两个 NX 实例
   - 两个 PowerMill 实例
   - 三个相似 manual 会话
   - automation/system/execution_audit 事件
8. 运行根仓库和 UG/NX 插件全量测试。
9. 运行 git diff --check。
10. Barrier 1 未全部通过时，不允许开始 Wave 2。

完成后提交 Barrier 1，输出：
- 集成提交 SHA
- 全量测试结果
- 冻结的 Wave 2 API/DTO
- 四个 Wave 2 Agent 必须基于的同一个 SHA

不要注销、关机或重启电脑。
```

## 6. Wave 2：四个并行产品窗口

建议新开 4 个 worktree 对话，不复用 Wave 1 的旧上下文。全部基于 Barrier 1 提交。

### 窗口 6：A-API

```text
你是 Wave 2 的 A-API Agent，负责 HTTP API 和跨模块装配。

开始前完整读取：
- AGENTS.md
- docs/contracts/parallel-development-contracts.md
- docs/cam-automation-studio-ai-parallel-development-plan.md
- Barrier 1 完成报告和 API fixtures

你独占并且只能修改：
- cam_automation/web_server.py
- cam_automation/integrations.py
- tests/test_web_server.py
- tests/test_integrations.py
- 可新增 API contract tests/fixtures

禁止修改：
- recorder、parser、models 的业务实现
- NX/PowerMill adapter
- execution.py
- cam_automation/web/*
- Codex Skills/manifest
- README.md 和公共架构文档

实现：
1. 只装配已冻结 service，不复制业务逻辑。
2. 提供插件、连接、记录、日志、会话、学习、配方、命令和诊断 API。
3. 日志 API 支持 source_mode、view_level、实例和游标分页。
4. 错误码区分插件未安装、实例未连接、参数错误、超时和任务取消。
5. 保持旧 API 兼容。
6. 输出给 A-UI 的固定 API JSON fixtures。

最小 API：
- GET /api/plugins
- GET /api/connections
- GET /api/recorder
- GET /api/recorder/events
- GET /api/sessions
- POST /api/sessions/compare
- POST /api/workflows/mine
- GET/POST /api/recipes
- POST /api/execution/run
- GET /api/execution/tasks/:id
- GET /api/diagnostics

完成后运行 API/集成测试和 git diff --check，提交独立 commit，
输出提交 SHA、路由合同、错误码和给 A-UI 的 fixtures。
不要自行合并其他分支，不要推送主集成分支。
```

### 窗口 7：A-UI

```text
你是 Wave 2 的 A-UI Agent，负责 CAM Automation Studio 单窗口前端。

开始前完整读取：
- AGENTS.md
- docs/cam-automation-studio-huangyan-product-proposal.md
- docs/cam-automation-studio-ai-parallel-development-plan.md
- Barrier 1 API/DTO fixtures

你独占并且只能修改：
- cam_automation/web/index.html
- cam_automation/web/app.js
- cam_automation/web/styles.css
- 可新增 cam_automation/web/components/
- 可新增前端 fixture/test 文件

禁止修改：
- web_server.py
- integrations.py
- recorder/parser/models
- execution.py
- NX/PowerMill adapter
- Codex Skills/manifest
- README.md 和公共架构文档

实现单窗口 UI：
1. 顶部连接条显示 Codex、NX、PowerMill 数量和记录状态。
2. 多实例详情使用右侧抽屉，不开多个顶级窗口。
3. 插件中心支持安装、卸载、更新和推荐组合。
4. 日志页提供“仅手动”快捷入口和独立 L0-L4 控件。
5. 会话页提供时间线、合并/拆分和 2-5 会话对比。
6. 配方页提供步骤、参数/条件、来源/差异三栏。
7. 命令页显示目标实例、版本、项目、响应和 DiffReport。
8. 诊断页显示实例、游标、延迟、队列和任务耗时。
9. 500 条以上日志使用分页或虚拟滚动。
10. 完成加载、空、错误、断连、超时和无匹配状态。

必须验证视口：
- 1280x720
- 1920x1080
- 390x844

使用固定 API fixtures 开发；不要为等待后端而修改后端文件。
使用现有图标库和样式模式，不创建多个窗口或嵌套卡片。

完成后启动本地服务进行浏览器验证，提交独立 commit，
输出提交 SHA、截图路径、控制台错误检查和仍需 A-API 联调的字段。
不要自行合并其他分支，不要推送主集成分支。
```

### 窗口 8：A-CMD

```text
你是 Wave 2 的 A-CMD Agent，负责命令中心、任务队列和诊断 service。

开始前完整读取：
- AGENTS.md
- docs/contracts/parallel-development-contracts.md
- docs/cam-automation-studio-ai-parallel-development-plan.md
- Barrier 1 的 PowerMill/NX transport 合同

你独占并且只能修改：
- cam_automation/execution.py
- 可新增 cam_automation/command_tasks.py
- 可新增 cam_automation/diagnostics.py
- tests/test_execution.py
- 可新增 command/diagnostics 测试

禁止修改：
- web_server.py
- integrations.py
- cam_automation/web/*
- recorder/parser/models
- NX/PowerMill parser
- Codex Skills/manifest
- README.md 和公共架构文档

实现：
1. CommandTask 必须绑定 product、target_version、target_instance_id、
   project 和 recipe_hash。
2. 每个实例使用独立串行任务队列。
3. 支持进度、取消、超时、断连和结果缓存。
4. 查询和 dry-run 返回原始响应、结构化响应、耗时和 DiffReport。
5. 诊断 service 输出实例心跳、队列长度、日志延迟和任务指标。
6. 保持仓库现有 execution 回归边界。
7. 不生成或发送机床可用 NC。

必须向 A-API 交付：
- CommandTaskService.submit(task) -> TaskDescriptor
- CommandTaskService.get(task_id) -> TaskStatus
- CommandTaskService.cancel(task_id)
- DiagnosticsService.snapshot() -> DiagnosticsSnapshot

测试至少包括：
- 两个实例并行提交但各实例内部串行
- target_instance_id 缺失
- 版本不匹配
- 取消、超时、断连
- DiffReport 归属正确
- 机床 NC 输出仍被阻止

完成后运行 execution/command 测试和 git diff --check，提交独立 commit，
输出提交 SHA、service 合同、状态机和 A-API 接入说明。
不要自行合并其他分支，不要推送主集成分支。
```

### 窗口 9：A-CODEX

```text
你是 Wave 2 的 A-CODEX Agent，负责 Codex bridge、插件 manifest 和 Skills。

开始前完整读取：
- AGENTS.md
- docs/contracts/parallel-development-contracts.md
- docs/cam-automation-studio-ai-parallel-development-plan.md
- 现有两个 CAM Skills 及 references
- 当前所有 plugin manifest

你独占并且只能修改：
- cam_automation/codex_bridge.py
- 根或插件内 .codex-plugin/
- plugins/*/skills/
- Codex bridge/Skill 测试
- 插件 marketplace 元数据

禁止修改：
- NX/PowerMill parser
- recorder/parser/models
- web_server.py
- integrations.py
- execution.py
- cam_automation/web/*
- README.md 和公共架构文档

实现：
1. 落地 CodexReviewRequest 和 CodexReviewResult。
2. 上下文包包含配方、来源事件、会话差异、产品、版本和待确认问题。
3. 返回 findings、suggested_parameters、questions、summary。
4. NX Skill 只引用 NX adapter；PowerMill Skill 只引用 PowerMill adapter。
5. 更新插件 manifest、版本号、Skills references 和 marketplace 元数据。
6. 执行插件 cachebuster、重新安装和技能可发现性检查。
7. fixture 演示不得调用真实 CAM live 项目。

测试至少包括：
- NX/PowerMill fixture 产生相同结构的审阅结果
- JSON 结果无需自由文本正则解析
- manifest 校验
- Skill 脚本路径和 references 存在
- 重新安装后技能可发现

完成后提交独立 commit，输出提交 SHA、manifest/Skill 版本、
安装验证结果和 COORD 需要同步到 README 的内容。
不要自行合并其他分支，不要推送主集成分支。
```

## 7. 窗口 1：Barrier 2 与最终发布提示词

将 Wave 2 的四个 SHA 填入：

```text
现在执行 Barrier 2 和最终发布。

Wave 2 提交：
- A-API: <SHA>
- A-UI: <SHA>
- A-CMD: <SHA>
- A-CODEX: <SHA>

集成顺序：
1. A-CMD
2. A-API
3. A-UI
4. A-CODEX

要求：
1. 读取四个完成报告和全部 diff。
2. 保留已有修改，不使用 reset --hard 或 checkout -- 覆盖文件。
3. 解决 API fixture 与真实 API 差异。
4. 跑通完整链路：
   多实例发现
   -> 自动记录
   -> manual + L2 筛选
   -> 三会话对比
   -> 重复配方
   -> Codex 结构化建议
   -> 指定实例 dry-run
   -> response + DiffReport
5. 验证多实例目标从 UI 到 transport 全程一致。
6. 执行性能基准：
   - 新日志 2 秒内可见
   - 100,000 事件导入 < 10 秒
   - UI 查询 p95 < 200 ms
   - 宿主查询默认超时 5 秒
   - 10,000 会话学习结果确定
7. 更新 README.md、docs/architecture.md、docs/mvp.md、
   插件版本、Skills 版本和发布说明。
8. 运行：
   python -m unittest discover -s tests -v
   python -m unittest discover -s plugins/ug-cam-copilot/tests -v
   git diff --check
   git status --short --branch
9. 提交最终集成。
10. 推送当前 GitHub 分支并确认远端 SHA。

最终报告必须包含：
- 合并提交 SHA
- 测试数量和结果
- 性能结果
- 演示步骤
- 已完成模块
- 仍需真实 PowerMill/NX 环境验证的项目
- GitHub 分支和远端 SHA

不得注销、关机、重启或结束当前用户会话。
```

## 8. 给所有开发窗口追加的统一约束

如果某个 Agent 偏离任务，可追加下面这段：

```text
保持当前任务边界。不要修改公共入口或其他 Agent 独占文件；
不要做无关重构；不要删除或弱化现有测试；不要声称未实测的
PowerMill/NX live 能力已经完成。发现接口冲突时停止编辑冲突文件，
把准确文件、符号、期望合同和阻塞原因报告给 COORD。
```
