# CAM Automation Studio AI 执行任务书

> 执行对象：Codex 或其他代码 AI
>
> 对应方案：[CAM Automation Studio 产品方案](./cam-automation-studio-huangyan-product-proposal.md)
>
> 审核入口：[程序员开发者审核任务书](./cam-automation-studio-developer-review-taskbook.md)

## 1. 使用规则

本任务书把 MVP 拆成 `T00-T15`。AI 每次只领取一个任务或一组明确可并行的任务，不得用“大范围重构”替代任务交付。

每个任务都必须遵守：

1. 开始前读取 `AGENTS.md`、目标模块、关联测试和插件 manifest。
2. 保留用户已有修改，不回退无关文件。
3. 优先使用仓库已有模型、解析器、服务和前端样式。
4. ActivityEvent 通过可选字段扩展，不删除或重命名现有字段。
5. NX 与 PowerMill 解析器保持隔离。
6. 生成动作保持 dry-run，不生成或发送机床可用 NC。
7. 实现后运行任务内测试和全量回归。
8. 未满足验收标准时不得把任务标记为完成。

## 2. AI 提交格式

每个任务完成后，AI 必须输出以下内容：

```markdown
## Txx 交付报告

- 结果：
- 修改文件：
- 数据/接口变化：
- 兼容性说明：
- 测试命令与结果：
- 手工验证：
- 未完成项：
- 建议审核重点：
```

代码提交建议使用：

```text
Txx: <imperative summary>
```

禁止把多个无关任务压成一个提交。

## 3. 依赖关系

```text
T00
 ├─ T01
 ├─ T02 ─┬─ T05 ─ T06
 │       ├─ T07 ─ T08 ─ T09
 │       ├─ T10
 │       └─ T11
 ├─ T03 ─ T04
 └────────────────┐
                  v
             T12 ─ T13
                  |
                 T14
                  |
                 T15
```

可并行组：

- `T01 / T02 / T03`
- `T06 / T07 / T10 / T11`，前提是对应数据合同已经在 `T02` 固定
- `T13 / T14`，前提是命令和事件接口不再变化

## 4. 任务总表

| ID | 工作包 | 主要执行对象 | 依赖 | 完成证据 |
|---|---|---|---|---|
| T00 | 基线与合同固定 | 全仓库 | 无 | 基线报告、测试结果、接口清单 |
| T01 | 插件内核与插件中心 | PluginManager / Web UI | T00 | 空白启动、安装/卸载/依赖测试 |
| T02 | ActivityEvent 扩展 | models/schema/parser | T00 | 兼容旧事件、新字段往返测试 |
| T03 | 多实例连接中心 | connection monitor / UI | T00 | 多窗口状态、稳定实例 ID |
| T04 | 自动记录生命周期 | recorder / integrations | T03 | 安装后自动运行、暂停/恢复 |
| T05 | 日志查询与筛选 API | recorder / web server | T02,T04 | 模式/层级组合查询 |
| T06 | 日志中心 UI | web app | T05 | 仅手动、L0-L4、保存视图 |
| T07 | 会话时间线 | parser/store/UI | T02,T05 | 合并/拆分/对比会话 |
| T08 | 重复流程学习 | learning/mining | T07 | 重复序列、参数和支持度 |
| T09 | 配方编辑器 | models/generator/UI | T08 | 步骤/参数/来源/差异 |
| T10 | PowerMill 适配器 | PowerMill plugin | T02,T03 | 宏/日志/查询响应 |
| T11 | NX 适配器 | UG plugin | T02,T03 | Journal AST/版本 stubs |
| T12 | 命令中心与 dry-run | execution/UI | T09,T10,T11 | 指定实例、响应、DiffReport |
| T13 | Codex 插件与 Skills | codex bridge/plugins | T09,T12 | 审阅上下文、Skills 回归 |
| T14 | 诊断、性能与统计 | recorder/store/UI | T04-T13 | 指标、恢复、性能报告 |
| T15 | 集成演示与发布 | 全仓库 | 全部 | MVP 演示、文档、发布清单 |

## 5. 详细任务

### T00 基线与合同固定

**目标**

建立可复现基线，列出现有插件、HTTP API、ActivityEvent、连接、记录、执行和 Skills 接口。

**执行范围**

- `README.md`
- `docs/architecture.md`
- `cam_automation/models.py`
- `cam_automation/web_server.py`
- `plugins/*/app-plugin.json`
- `plugins/ug-cam-copilot/schemas/activity-event.schema.json`
- `tests/`

**实施**

1. 运行根仓库和 UG 插件测试。
2. 记录当前 API 路由、请求/响应形状和错误码。
3. 记录 ActivityEvent 必填/可选字段和动作前缀。
4. 记录当前插件依赖图和默认安装状态。
5. 在 `docs/implementation-baseline.md` 写入基线，不修改运行行为。

**验收**

```powershell
python -m unittest discover -s tests -v
python -m unittest discover -s plugins/ug-cam-copilot/tests -v
```

- 文档中的测试数量、路由和字段与代码一致。
- 工作区不包含无关生成文件。

### T01 插件内核与插件中心

**目标**

保持基础软件空白启动，业务功能通过软件内插件中心安装、卸载和更新。

**执行范围**

- `cam_automation/plugin_manager.py`
- `cam_automation/integrations.py`
- `cam_automation/web_server.py`
- `cam_automation/web/index.html`
- `cam_automation/web/app.js`
- `cam_automation/web/styles.css`
- `plugins/*/app-plugin.json`
- `tests/test_plugin_manager.py`
- `tests/test_integrations.py`

**实施**

1. 统一插件状态：`available / installing / installed / update_available / error`。
2. 返回依赖、产品、版本范围、运行服务和连接摘要。
3. 支持推荐组合，但组合仍逐个写入安装注册表。
4. 安装/卸载后更新当前主窗口，不打开额外顶级窗口。
5. 保留空插件集只暴露核心能力的行为。

**验收**

- 默认安装注册表为空。
- 安装执行插件时依赖解析正确。
- 卸载被依赖插件时返回可读错误。
- UI 在 1280×720 和 390×844 下无文本重叠。

### T02 ActivityEvent 可选扩展

**目标**

为日志模式、层级、实例、命令响应和配方版本增加向后兼容字段。

**建议字段**

```json
{
  "source_mode": "manual|automation|system|execution_audit",
  "view_level": "L0|L1|L2|L3|L4",
  "expertise_label": "routine|expert_demo|training|unknown",
  "instance_id": "string",
  "target_version": "string",
  "command_response": {},
  "review_status": "draft|reviewed|needs_changes|ready_for_test",
  "recipe_hash": "string"
}
```

**执行范围**

- `cam_automation/models.py`
- `cam_automation/parser.py`
- `plugins/ug-cam-copilot/schemas/activity-event.schema.json`
- `plugins/ug-cam-copilot/src/ugcam_ai/models.py`
- `plugins/ug-cam-copilot/src/ugcam_ai/adapters/jsonl.py`
- `tests/test_parser.py`
- UG 插件 schema/adapter 测试

**实施**

1. 新字段全部可选，缺失时使用明确默认值。
2. 序列化后再反序列化不得丢失已知字段。
3. 旧版 JSONL 必须继续导入。
4. 未知附加字段按现有合同处理，不自行发明第二套事件格式。
5. 不修改 `nx_journal.py` 来识别 PowerMill 内容。

**验收**

- 旧 fixture 全部通过。
- 新字段 round-trip 测试通过。
- `cam.* / nx.* / powermill.*` 前缀测试通过。

### T03 多实例连接中心

**目标**

同时显示多个 PowerMill/NX 窗口，形成稳定的运行期实例描述和首页连接摘要。

**执行范围**

- `cam_automation/connection_monitor.py`
- `cam_automation/integrations.py`
- `cam_automation/web_server.py`
- Web UI
- `tests/test_connection_monitor.py`
- `tests/test_integrations.py`

**实例描述**

```json
{
  "instance_id": "nx:pid:window",
  "product": "nx|powermill",
  "pid": 1234,
  "window_title": "...",
  "project": "...",
  "version": "...",
  "foreground": false,
  "connection_state": "connected|degraded|disconnected",
  "last_response_ms": 42
}
```

**实施**

1. 同一进程的不同窗口拥有不同运行期实例 ID。
2. 无窗口的 CAM 进程仍显示为进程实例。
3. 首页摘要聚合数量，抽屉展示明细。
4. 日志映射不确定时返回 `unmapped`。
5. 不把前台窗口自动选为命令目标。

**验收**

- 模拟 2 个 NX 进程、3 个 PowerMill 窗口时数量正确。
- 重扫时未变化实例 ID 稳定。
- 单实例断连不影响其他实例状态。

### T04 自动记录生命周期

**目标**

本地记录插件安装并启用后自动启动，提供持续可见状态、分类暂停、恢复和游标续接。

**执行范围**

- `cam_automation/recorder.py`
- `cam_automation/integrations.py`
- `cam_automation/plugin_manager.py`
- `cam_automation/web_server.py`
- `tests/test_recorder.py`
- `tests/test_web_server.py`

**实施**

1. 复用现有 recorder 状态，不创建第二个记录服务。
2. 插件启用后的正常状态为自动连接和增量记录。
3. 支持分别控制 `log_capture / instance_detection / execution_audit`。
4. 状态至少包括 `recording / paused / source_interrupted / catching_up / error`。
5. 暂停后保存游标；恢复后从游标续接，不重复写入。
6. 不删除现有 recorder 回归测试来迁就实现。

**验收**

- 安装、应用重启、暂停、恢复、卸载、重装均有测试。
- 同一输入扫描两次不会产生重复事件。
- 源文件追加内容后只读取增量部分。

### T05 日志查询与筛选 API

**目标**

提供模式、层级、产品、实例、项目、动作、时间和文本的组合筛选。

**执行范围**

- `cam_automation/recorder.py`
- `cam_automation/web_server.py`
- `tests/test_web_server.py`
- 新增日志查询测试

**API 建议**

```http
GET /api/recorder/events
  ?source_mode=manual
  &view_level=L2
  &product=nx
  &instance_id=...
  &project=...
  &action=cam.*
  &q=...
  &cursor=...
  &limit=100
```

**实施**

1. 所有筛选使用参数化 SQL 或现有结构化查询 API。
2. 游标分页结果稳定，不因相同时间戳跳过事件。
3. 空筛选保持旧 API 兼容。
4. 返回 `items / next_cursor / total_estimate / applied_filters`。
5. 对组合筛选建立必要索引。

**验收**

- “仅手动 + L2 + 指定实例”测试通过。
- `manual + automation` 多选测试通过。
- 无效枚举、limit 和游标返回明确 4xx。

### T06 日志中心 UI

**目标**

在一个页面完成日志筛选、扫描、详情查看和常用视图保存。

**执行范围**

- `cam_automation/web/index.html`
- `cam_automation/web/app.js`
- `cam_automation/web/styles.css`
- 必要的 Web API 测试

**实施**

1. 来源模式用分段控件或多选菜单，默认提供“仅手动”快捷视图。
2. `L0-L4` 使用单独层级控件，不与模式混成一个下拉框。
3. 表格列包括时间、模式、层级、产品、实例、项目、动作和摘要。
4. 详情使用右侧面板，不打开新窗口。
5. 500 条以上使用分页或虚拟滚动。
6. 筛选条件写入 URL 或本地视图配置，刷新后可恢复。

**验收**

- 桌面与移动视口无重叠和横向失控。
- 键盘可到达筛选、表格行和详情关闭按钮。
- 空、加载、错误、断连和无匹配结果状态完整。

### T07 会话时间线

**目标**

把事件组织为可解释的编程会话，并允许合并、拆分和比较。

**执行范围**

- `cam_automation/parser.py`
- `cam_automation/recorder.py`
- `cam_automation/web_server.py`
- Web UI
- parser/recorder 测试

**实施**

1. 会话优先使用实例、项目和显式标记，其次才使用时间间隔。
2. 保存会话来源事件 ID，不复制原始事件。
3. 合并/拆分操作生成确定结果，可撤销到上一个会话版本。
4. 对比 2-5 个会话，显示公共步骤、缺失步骤、参数差异和耗时。
5. 专家示范标签由用户明确设置到会话或步骤。

**验收**

- 多实例交错事件不会错误合并。
- 同一输入重复会话化得到相同结果。
- 比较 API 和 UI 使用相同差异模型。

### T08 重复流程学习

**目标**

从多个会话发现稳定重复流程，并把变化值提升为类型化参数。

**执行范围**

- `cam_automation/learning.py`
- `plugins/ug-cam-copilot/src/ugcam_ai/mining.py`
- `tests/test_learning.py`
- UG mining 测试

**实施**

1. 使用规范动作和结构签名，不以完整原始字符串做匹配键。
2. 输出支持会话数、支持率、来源会话、公共步骤和分支。
3. 参数至少区分字符串、整数、浮点、布尔、枚举和对象引用。
4. 允许忽略噪声动作和设置最小支持度。
5. 相同输入必须得到确定排序和 recipe hash。

**验收**

- 三个相似会话可发现公共主流程。
- 返工步骤不被错误提升为稳定主流程。
- 10,000 会话性能测试有明确预算。

### T09 配方编辑器

**目标**

把学习候选变为可编辑、可追到来源、可生成预览的配方。

**执行范围**

- `cam_automation/models.py`
- `cam_automation/generator.py`
- `cam_automation/web_server.py`
- Web UI
- generator 测试

**实施**

1. 配方包含步骤、参数、条件、产品、版本范围、来源会话和 hash。
2. 三栏界面：步骤列表、参数/条件、来源/差异。
3. 步骤支持排序、启停和说明；参数支持类型、默认值和约束。
4. 保存时生成新版本，不静默覆盖已审阅版本。
5. PowerMill 和 NX 预览由各自生成器提供，共享编辑器只处理统一模型。

**验收**

- 修改参数后预览和 hash 同步更新。
- 每一步能追到来源事件。
- 控制字符、引用和特殊路径有回归测试。

### T10 PowerMill 适配器

**目标**

建立独立 PowerMill 输入/查询适配层，不修改 NX Journal 解析器。

**执行范围**

- `cam_automation/profiles/powermill.py`
- `cam_automation/parser.py` 中 PowerMill 入口
- `plugins/powermill-cam-copilot/`
- 新建适配器时使用 `powermill_macro.py`
- PowerMill 测试和 fixtures

**实施**

1. 支持 `.mac / .log / JSONL`。
2. 保留原始行、引号内空格、时间、命令和参数。
3. 记录宏缺少未修改默认值的证据限制。
4. 定义实例发现、连接、查询、响应、项目快照和预览接口。
5. 真实 API 未配置时使用 fixture/transport stub，不伪造已连接状态。

**验收**

- PowerMill 语法测试不触及 `nx_journal.py`。
- 两个实例的查询和响应不会串线。
- 断连、超时和错误响应有 fixture。

### T11 NX 适配器

**目标**

增强 NX Journal 静态解析和目标版本 NXOpen 查询，同时保持产品边界。

**执行范围**

- `plugins/ug-cam-copilot/src/ugcam_ai/adapters/nx_journal.py`
- `plugins/ug-cam-copilot/src/ugcam_ai/adapters/base.py`
- `plugins/ug-cam-copilot/src/ugcam_ai/store.py`
- `plugins/ug-cam-copilot/tests/`
- NX Skills references

**实施**

1. Journal 只做 AST 静态解析，不 `import`、`exec` 或运行用户文件。
2. 识别 Builder、参数赋值、Commit、Destroy 和 Undo Mark。
3. 将脆弱 `FindObject` 标识标记为待转换选择器。
4. 版本合同以目标 NX 安装自带 stubs 为准。
5. 输出统一 ActivityEvent，同时保留 NX 专属 `nx.*` 动作。

**验收**

- 恶意或异常 Python Journal 不会被执行。
- 现有 NX pipeline 和性能测试通过。
- PowerMill fixture 不进入 NX 解析路径。

### T12 命令中心与 dry-run

**目标**

为查询命令和配方 dry-run 提供明确目标、任务队列、响应和 DiffReport。

**执行范围**

- `cam_automation/execution.py`
- `cam_automation/integrations.py`
- `cam_automation/web_server.py`
- Web UI
- `tests/test_execution.py`
- `tests/test_web_server.py`

**任务模型**

```json
{
  "product": "nx|powermill",
  "target_version": "...",
  "target_instance_id": "...",
  "project": "...",
  "recipe_hash": "...",
  "parameters": {},
  "mode": "dry-run"
}
```

**实施**

1. 任务必须绑定产品、目标版本和目标实例。
2. 每实例使用独立串行队列。
3. 支持取消、进度、超时、原始响应和结构化响应。
4. dry-run 返回对象选择、参数变化、项目快照差异和仿真摘要。
5. 保留仓库现有运行边界和全量 execution 回归测试。

**验收**

- 同时提交到两个实例时响应归属正确。
- 未连接、版本不匹配、超时和取消均有状态。
- 学习日志不能直接成为机床 NC 输出。

### T13 Codex 插件与 Skills

**目标**

让 Codex 可以开箱读取结构化上下文、审阅配方并返回机器可读结果。

**执行范围**

- `cam_automation/codex_bridge.py`
- `.codex-plugin/plugin.json`
- `plugins/*/.codex-plugin/plugin.json`（如存在）
- `plugins/*/skills/`
- Skill scripts、references 和 tests

**实施**

1. 定义上下文包：配方、来源事件、差异、目标产品、版本和待确认项。
2. 统一 Codex 返回：`findings / suggested_parameters / questions / summary`。
3. NX Skill 只引用 NX adapter；PowerMill Skill 只引用 PowerMill adapter。
4. Skill 明确离线学习与 live 宿主命令的边界。
5. 更新插件 manifest、版本号、README 和个人 marketplace 条目。
6. 按 Codex 插件开发流程执行 cachebuster、重新安装和可发现性检查。

**验收**

- 两个 Skills 都能使用 fixture 生成相同结构的审阅包。
- manifest 和 Skill 目录通过校验。
- 插件更新后 Codex 能重新发现技能。

### T14 诊断、性能与任务统计

**目标**

用一个诊断页定位实例断连、日志延迟、队列阻塞和学习任务性能。

**执行范围**

- recorder/store/connection/execution
- `cam_automation/web_server.py`
- Web UI
- 性能测试

**指标**

- 实例心跳和最近响应。
- 日志源游标、最后事件时间和积压量。
- 事件写入速率、查询 p50/p95。
- 每实例队列长度和任务耗时。
- 会话数、manual 比例、候选流程数和配方复用率。

**实施**

1. 指标采集不得在主 UI 线程执行重查询。
2. 诊断页支持重连、重扫、取消任务和导出诊断包。
3. 添加 100,000 事件导入和查询基准。
4. 单实例故障不降低其他实例刷新频率。

**验收**

- 性能测试输出机器可读结果。
- 诊断操作有成功、失败和进行中状态。
- 统计口径写入文档并与 API 一致。

### T15 集成演示与发布

**目标**

完成从空白内核到配方 dry-run 的可重复演示，并同步文档、插件和 GitHub 分支。

**执行范围**

- 全仓库
- `README.md`
- `docs/mvp.md`
- 产品方案和任务书
- `.demo/` 或 `examples/`
- 插件 manifest 与 Skills

**演示步骤**

1. 空白启动并安装 PowerMill、NX、本地记录和 Codex 插件。
2. 模拟或连接多个 PowerMill/NX 实例。
3. 自动记录并筛选“仅手动 + L2”。
4. 比较三个会话并生成配方。
5. Codex 返回审阅结果。
6. 选择目标实例执行 dry-run。
7. 展示响应、DiffReport、诊断和统计。

**验收**

```powershell
python -m unittest discover -s tests -v
python -m unittest discover -s plugins/ug-cam-copilot/tests -v
git diff --check
```

- 演示从干净数据目录可重复运行。
- README、架构、API、插件 manifest 和 Skills 版本一致。
- 发布说明列出新增功能、兼容性和已知限制。
- 推送前工作区只包含本次任务文件。

## 6. AI 停止条件

出现以下情况时，AI 不得自行扩大范围：

- 需要改变 ActivityEvent 必填字段或删除旧字段。
- 需要在 NX 解析器中加入 PowerMill 语法。
- 需要接入本机真实 CAM live 传输但没有目标版本环境。
- 需要生成机床可用 NC。
- 发现用户已有修改与任务目标直接冲突，且无法兼容。
- 测试连续失败且原因来自未提供的外部软件、许可证或样本。

AI 应保留已完成的可验证工作，报告阻塞证据，并等待程序员决定。
