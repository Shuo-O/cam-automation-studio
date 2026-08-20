# CAM Automation Studio

这是一个插件优先、本地运行的 CAM 自动化工作台。`0.5.0` 的默认启动只加载基础内核：健康检查、插件目录、安装状态和静态界面。UG/NX、PowerMill、行为采集、执行网关和 Codex 审阅都必须由使用者在软件内明确安装后才会导入和启动。

每个软件模块位于 `plugins/<plugin-id>`，使用 `app-plugin.json` 声明功能、依赖和本地权限；Codex 插件继续使用 `.codex-plugin/plugin.json`。UG/NX 插件仍在 `plugins/ug-cam-copilot` 独立开发，PowerMill 使用 `plugins/powermill-cam-copilot`。

## Run

```powershell
python -m cam_automation serve --port 8765
```

浏览器启动后访问 `http://127.0.0.1:8765`。首次进入只显示插件中心，安装 UG/NX 或 PowerMill 工作流插件后才会开放工作台。软件不会自动载入样例，也不会自动安装业务模块。

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
- `GET /api/recorder/export`：导出本地 JSONL。
- `POST /api/recorder/clear`：使用确认令牌清除采集库。

## 命令执行门禁

安装 `cam-execution-gateway` 时会自动安装 `cam-local-capture` 审计依赖。`POST /api/execution/run` 接受结构化执行请求，并把请求、目标实例、拒绝原因、响应和耗时写入同一采集审计链。当前仅注册 `dry-run` 传输；`live` 除目标版本原生适配器外，还必须提供明确的 `target_instance_id`，避免同时打开多个 CAM 窗口时把命令发错目标。

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

原始命令会再次检查删除、退出、外部进程和 NC/postprocess 特征，不能只依赖调用方提交的风险标签。`review`、所有 live 请求和生产变更仍需要识别到具体审批人、测试项目快照及目标版本适配器；NC 输出在此门禁中始终阻断。

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

技术边界见 [architecture.md](docs/architecture.md)，Codex 交换协议实现见 `cam_automation/codex_bridge.py`，产品适配入口见 `cam_automation/integrations.py`。

## Test

```powershell
python -m unittest discover -s tests -v
```
