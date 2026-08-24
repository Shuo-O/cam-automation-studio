# CAM Automation Studio

这是一个插件优先、本地运行的 CAM 自动化工作台。`0.6.1` 的默认启动只加载基础内核：健康检查、插件目录、安装状态和静态界面。UG/NX、PowerMill、行为采集、执行网关和 Codex 审阅都必须由使用者在软件内明确安装后才会导入和启动。

每个软件模块位于 `plugins/<plugin-id>`，使用 `app-plugin.json` 声明功能、依赖和本地权限；Codex 插件继续使用 `.codex-plugin/plugin.json`。UG/NX 插件仍在 `plugins/ug-cam-copilot` 独立开发，PowerMill 使用 `plugins/powermill-cam-copilot`。

## Run

```powershell
python -m cam_automation serve --port 8765
```

浏览器启动后访问 `http://127.0.0.1:8765`。首次进入只显示插件中心，安装
UG/NX 或 PowerMill 工作流插件后才会开放工作台。软件不会自动安装业务模块。
本仓库默认服务图只注册离线 fixture transport；安装本地记录插件后会自动扫描打包的
本地演示事件源，便于在不连接真实 CAM 的情况下验证完整工作流。

无界面命令仍然可用：

```powershell
python -m cam_automation demo
python -m cam_automation learn examples/powermill/manual-session.log --output build/my-workflow
```

统一 HTTP 接口：

- `GET /api/plugins`：插件目录、依赖、权限、安装状态和插件运行/连接状态。
- `POST /api/plugins/install|uninstall`：在本机安装或移除模块；移除不会删除模块数据。
- `GET /api/capabilities`：产品、输入格式、dry-run 安全门禁。
- `GET /api/connections`：Codex、UG/NX、PowerMill 的本机桥接状态；经采集授权后包含每个可见窗口的实例 ID、PID、窗口标题和前台标记。首页使用 `include_uninstalled=1` 显示三项摘要，工作台保留详细实例列表。
- `POST /api/analyze`：`product=nx|powermill`，返回 recipe、ActivityEvent、预览和 Codex context。
- `POST /api/codex/context`：生成可交换的 Codex 审阅请求，可持久化到 `build/codex-exchange`。
- `POST /api/codex/review`：回传 `review_status`、`findings` 和 `required_gates`。

未安装对应插件时，业务接口返回 `409 plugin_not_installed`。旧的 `POST /api/learn` 仍作为 PowerMill 兼容入口保留，但需要先安装 PowerMill 插件。

## 插件目录

内置目录初始包含：

- `ug-cam-copilot`：UG/NX Journal 静态解析与 dry-run 预览。
- `powermill-cam-copilot`：PowerMill 宏/命令日志学习与配方生成。
- `cam-local-capture`：安装后默认授权、可撤销的本地行为记录、脱敏、筛选与导出。
- `cam-execution-gateway`：命令安全检查和 dry-run；自动安装采集审计依赖。
- `cam-codex-review`：结构化 Codex 上下文导出与审阅回传。

安装状态保存在 `build/app-data/installed-plugins.json`。删除该文件会回到零业务插件状态，但不会删除采集数据库。

## 后台行为记录

安装 `cam-local-capture` 后才会导入并启动 `CaptureService`。首次安装默认完成一次本机授权并开始记录，首页会直接显示 UG/NX、PowerMill 和 Codex 的连接摘要；工作台连接面板再展开每个实例。用户可以在“高级设置 · 分类授权”中分别关闭日志记录、软件窗口检测或执行审计，也可以随时撤销总授权。撤销会立即停止窗口检测和后台记录，但不会删除已保存的本地数据；曾撤销过的配置重新安装时不会自动恢复授权。

授权后，采集服务会自动检测 UG/NX 与 PowerMill 进程、多个可见软件窗口及受支持的日志源。每个运行实例都有临时 `instance_id`（产品、PID、窗口句柄），界面会显示实例数量并标记查询时位于前台的 CAM 窗口。采集线程只读源文件，不导入或执行 Journal，不向网络上传数据。

进程列表和窗口标题的读取属于采集插件的显式本地权限。标题只用于当前本机连接状态，不写入 ActivityEvent、学习配方或导出 JSONL。一个日志源无法可靠映射到多个同时运行的 CAM 窗口时，首版不会猜测归属。

默认入口：

- `build/app-data/capture/inbox/nx`：NX Open `.py` Journal 或 ActivityEvent `.jsonl`。
- `build/app-data/capture/inbox/powermill`：PowerMill `.mac`、`.log` 或 ActivityEvent `.jsonl`。
- `CAM_NX_LOG_PATHS`：用系统路径分隔符指定额外 NX 文件或目录。
- `CAM_POWERMILL_LOG_PATHS`：用系统路径分隔符指定额外 PowerMill 文件或目录。
- `CAM_CAPTURE_DIR`：覆盖本地采集数据库与配置目录。

绝对路径在写入 SQLite 前会变成安装内稳定的伪匿名标记；源文件不会被改写。操作者可以把当前会话标为“日常操作”或“专家示范”，系统不会根据姓名或动作内容自行推断专家身份。

相关本机接口：

- `GET /api/recorder`：授权、运行状态、兼容进程布尔值、实例列表、日志源和计数。
- `POST /api/recorder/consent|control|settings|label|scan`：授权、暂停/继续、分类设置、标注和立即扫描。
- `GET /api/recorder/events?limit=500`：读取最近的脱敏 ActivityEvent。
- `GET /api/recorder/events?...`：按 `source_mode`、`view_level`、产品、实例、项目、
  动作、时间和文本进行稳定游标分页。
- `GET /api/recorder/export`：导出本地 JSONL。
- `POST /api/recorder/clear`：使用确认令牌清除采集库。
- `GET /api/sessions`、`POST /api/sessions/compare`：生成会话并比较 2-5 个同产品会话。
- `POST /api/workflows/mine`：从选定会话生成确定性的学习候选。
- `POST /api/recipes`、`GET /api/recipes/:id`、`POST /api/recipes/:id/preview`：
  保存版本化配方并且只通过产品 adapter 生成预览。
- `POST /api/execution/tasks`、`GET /api/execution/tasks/:id`、
  `POST /api/execution/tasks/:id/cancel`：提交、轮询和取消按实例隔离的任务。
- `GET /api/diagnostics`：查看连接心跳、实例队列、延迟和任务指标。

## 命令执行门禁

安装 `cam-execution-gateway` 时会自动安装 `cam-local-capture` 审计依赖。
`CommandTaskService` 接受结构化查询或配方预览任务，为每个实例维护独立串行队列，
并把请求、目标实例、拒绝原因、响应和耗时写入同一 `execution_audit` 链。
`POST /api/execution/run` 作为旧同步路由继续兼容。当前 fixture transport 只支持
`read_only` 和 `dry_run`，不会向 CAM 发送命令。

执行请求至少包含：

```json
{
  "product": "powermill",
  "action": "cam.model.import",
  "command": "IMPORT MODEL 'part.dmt'",
  "risk": "safe",
  "recipe_hash": "reviewed-recipe-sha256",
  "target_version": "PowerMill 2026",
  "test_project": true,
  "target_instance_id": "powermill:4321:1A02F4",
  "mode": "dry-run"
}
```

任务必须同时绑定产品、目标实例、目标版本、项目和 recipe hash。请求和响应都会再次
检查删除、退出、外部进程、Journal live、机床控制、NC/G-code 和 postprocess
特征，不能只依赖调用方风险标签或 Codex 审阅文本。生产门禁保持
`cam_simulation=not_run`、`collision_check=required`、`shop_approval=required`；
machine-ready NC 在入口和响应扫描中始终阻断。

## Codex Skills

产品插件提供 10 个结构化、review-first Skills：

- NX：活动查询预览、会话比较、配方审阅、工作流学习、Flow Studio。
- PowerMill：活动查询预览、会话比较、配方审阅、工作流学习、Flow Studio。

NX Skills 只引用 NX adapter，PowerMill Skills 只引用 PowerMill adapter。Codex 交换
严格使用 `CodexReviewRequest` / `CodexReviewResult` JSON，不执行自由文本，不接受
live 指令，也不生成 machine-ready NC。

## 低代码 Flow Studio

`0.6.0` 增加单窗口低代码 Flow Studio，用于把已有 NX Journal、PowerMill 宏和脱敏
ActivityEvent 日志离线投影为可理解、可拖动、可版本化的 FlowGraph。NX 与 PowerMill
分别使用独立 parser 和 capability manifest；共享语义继续使用 `cam.*`，产品专属节点
使用 `nx.*` 或 `powermill.*`。

首版支持：

- 从离线资产建立节点、typed ports、连线、参数和来源行定位。
- 真实 PointerEvent 拖拽、Undo/Redo、响应式桌面/移动布局。
- 版本快照、语义 diff、round-trip、目标版本兼容性和缺失 capability 诊断。
- 把审阅通过的图投影为 NX 或 PowerMill recipe 草稿，再生成 fixture-only preview。
- 500 节点编辑与校验性能屏障，以及 1,000 个 preview plan 的实例隔离屏障。

创建 reviewed version 或 preview 前必须通过真实 RoundTrip、Compatibility 和产品
Projection 审查。所有 preview 固定 `transport=none`，执行计数、发送计数、NC 计数和
机床控制计数保持为零；Flow Studio 不导入 live Journal、不运行 PowerMill 宏，也不把
学习结果直接变成机床可执行代码。

NX 与 PowerMill 目标版本按 `verified`、`review_required`、`opaque_only` 和
`unsupported/unknown` 分级。只有 capability manifest 顶层精确列出的 fixture
审阅版本可能成为 `compatible`；较新、较老和历史内部版本号可继续离线读取，但会按
证据降级，不能自动进入 preview。完整矩阵见
[cam-version-compatibility-policy.md](docs/cam-version-compatibility-policy.md)。

## Siemens NX / UG Demo

NX 适配器静态解析录制的 NX Open Python Journal，不会导入或执行 Journal。它使用 SQLite WAL 保存事件，并按独立会话支持度发现重复流程：

```powershell
.\plugins\ug-cam-copilot\scripts\run-nx-demo.ps1
```

真实 Journal：

```powershell
python .\plugins\ug-cam-copilot\scripts\ugcam.py ingest .\journals --product nx
python .\plugins\ug-cam-copilot\scripts\ugcam.py mine --product nx --min-support 2
python .\plugins\ug-cam-copilot\scripts\ugcam.py scaffold --product nx --out .\generated\nx-candidate
```

PowerMill 公共工作流内核位于 `cam_automation`；NX 的 AST/SQLite 适配内核位于 `plugins/ug-cam-copilot/src/ugcam_ai`。双方通过 `plugins/ug-cam-copilot/schemas/activity-event.schema.json` 对齐事件语义，产品解析器互不修改。

产物：

- `recipe.json`: 结构化步骤、参数、来源行和风险等级。
- `events.jsonl`: 与 UG/NX 开发共用的 `ActivityEvent v1` 投影，可由现有 JSONL adapter 导入。
- `workflow.mac`: 可审查 PowerMill 宏；保存/导出步骤默认注释，破坏性步骤始终阻断。
- `report.md`: 会话匹配、参数、步骤和诊断报告。

## 运行边界

只有安装并授权采集插件后，软件才会检测 CAM 进程并读取已配置的日志源。即使安装执行网关，也不会在缺少版本化原生传输时附着或控制 PowerMill/NX。实际 PowerMill 进程内插件需要目标版本安装中的 `PowerMill.dll`、`PluginFramework.dll`、COM 注册和现场项目验证；NX 需要目标版本的 NXOpen Python stubs 与现场验证。

NX 首版同样只生成 dry-run 配方和预览 Journal，不修改零件、不生成或下发机床 NC 代码。实际 NXOpen 实现必须对照目标版本自带的 Python stubs，并通过碰撞/过切检查、机床仿真和人工审批。

仓库发布验收只使用两个 NX、两个 PowerMill 的 fixture 实例。真实 PowerMill/NX
连接、项目映射、目标版本 API、机床仿真、碰撞检查和现场批准都必须在客户环境单独
验证；当前结果不代表 live execution 已实现或获批。

技术边界见 [architecture.md](docs/architecture.md)，Codex 交换协议实现见 `cam_automation/codex_bridge.py`，产品适配入口见 `cam_automation/integrations.py`。

## Test

```powershell
python -m unittest discover -s tests -v
python -m unittest discover -s plugins/ug-cam-copilot/tests -v
python -m unittest discover -s plugins/powermill-cam-copilot/tests -v
node --test cam_automation/web/components/fixtures.test.cjs
```

本次发布结果见 [release-notes-0.6.1.md](docs/release-notes-0.6.1.md)。
