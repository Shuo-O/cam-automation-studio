# CAM Automation Studio

这是一个本地优先的 CAM 软件模块：从 UG/NX Journal、PowerMill 宏/命令日志或共享 JSONL 行为记录中学习重复工作流，归纳跨会话参数，并生成必须人工复核的配方与 dry-run 预览。工作台可以把结构化审阅上下文交给 Codex，再把审阅结论回传到本地模块。

UG/NX 插件仍保留在 `plugins/ug-cam-copilot` 独立开发。共享能力位于 `cam_automation`，以后 NX 只需增加自己的命令 profile 和执行 adapter。

## Run

```powershell
python -m cam_automation serve --port 8765
```

浏览器工作台启动后访问 `http://127.0.0.1:8765`。左侧选择产品，粘贴 Journal/宏，点击“分析工作流”；在 Codex 审阅页导出 `cam.codex.bridge.v1` 上下文，审阅结论通过同一页回传。

无界面命令仍然可用：

```powershell
python -m cam_automation demo
python -m cam_automation learn examples/powermill/manual-session.log --output build/my-workflow
```

统一 HTTP 接口：

- `GET /api/capabilities`：产品、输入格式、dry-run 安全门禁。
- `GET /api/connections`：Codex、UG/NX、PowerMill 的本机桥接状态。
- `POST /api/analyze`：`product=nx|powermill`，返回 recipe、ActivityEvent、预览和 Codex context。
- `POST /api/codex/context`：生成可交换的 Codex 审阅请求，可持久化到 `build/codex-exchange`。
- `POST /api/codex/review`：回传 `review_status`、`findings` 和 `required_gates`。

旧的 `POST /api/learn` 仍作为 PowerMill 兼容入口保留。

## MCP

工作台新增 **MCP** 页：搜索 FreeCAD、CadQuery、OpenSCAD、Fusion、STEP 检查器及 NX 相关项目，查看来源与安装条件，并导出所选配置。本项目也提供可选的 stdio MCP 服务，复用现有 NX/PowerMill 分析能力：

```sh
python -m pip install -e '.[mcp]'
python -m cam_automation mcp-config cam-studio step-inspector
python -m cam_automation mcp
```

外部条目是配置模板；安装、宿主启动和实机验收状态不会混为“已连接”。详细工具、资料来源、使用方法及验证范围见 [MCP 集成](docs/cad-mcp.md)。

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

当前模块提供本机桥接状态和审阅交换协议，但不自动附着或控制正在运行的 PowerMill/NX 进程，不发送命令，也不生成或下发机床 NC 代码。实际 PowerMill 进程内插件需要目标版本安装中的 `PowerMill.dll`、`PluginFramework.dll`、COM 注册和现场项目验证；NX 需要目标版本的 NXOpen Python stubs 与现场验证。

NX 首版同样只生成 dry-run 配方和预览 Journal，不修改零件、不生成或下发机床 NC 代码。实际 NXOpen 实现必须对照目标版本自带的 Python stubs，并通过碰撞/过切检查、机床仿真和人工审批。

技术边界见 [architecture.md](docs/architecture.md)，Codex 交换协议实现见 `cam_automation/codex_bridge.py`，产品适配入口见 `cam_automation/integrations.py`。

## Test

```powershell
python -m unittest discover -s tests -v
```
