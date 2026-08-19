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

## 后台行为记录

工作台包含一个本地 `CaptureService`。首次明确授权后，它会在后续启动时默认继续记录，并自动检测 UG/NX 与 PowerMill 进程及受支持的日志源。采集线程只读源文件，不导入或执行 Journal，不向网络上传数据。

默认入口：

- `build/capture/inbox/nx`：NX Open `.py` Journal 或 ActivityEvent `.jsonl`。
- `build/capture/inbox/powermill`：PowerMill `.mac`、`.log` 或 ActivityEvent `.jsonl`。
- `CAM_NX_LOG_PATHS`：用系统路径分隔符指定额外 NX 文件或目录。
- `CAM_POWERMILL_LOG_PATHS`：用系统路径分隔符指定额外 PowerMill 文件或目录。
- `CAM_CAPTURE_DIR`：覆盖本地采集数据库与配置目录。

绝对路径在写入 SQLite 前会变成安装内稳定的伪匿名标记；源文件不会被改写。操作者可以把当前会话标为“日常操作”或“专家示范”，系统不会根据姓名或动作内容自行推断专家身份。

相关本机接口：

- `GET /api/recorder`：授权、运行状态、进程、日志源和计数。
- `POST /api/recorder/consent|control|label|scan`：授权、暂停/继续、标注和立即扫描。
- `GET /api/recorder/events?limit=500`：读取最近的脱敏 ActivityEvent。
- `GET /api/recorder/export`：导出本地 JSONL。
- `POST /api/recorder/clear`：使用确认令牌清除采集库。

## 命令执行门禁

`POST /api/execution/run` 接受结构化执行请求，并把请求、拒绝原因、响应和耗时写入同一采集审计链。当前仅注册 `dry-run` 传输；`live` 会在没有目标版本原生适配器时失败关闭。

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

当前模块会检测 CAM 进程并自动读取已配置的日志源，但不会在缺少版本化原生传输时附着或控制 PowerMill/NX。实际 PowerMill 进程内插件需要目标版本安装中的 `PowerMill.dll`、`PluginFramework.dll`、COM 注册和现场项目验证；NX 需要目标版本的 NXOpen Python stubs 与现场验证。

NX 首版同样只生成 dry-run 配方和预览 Journal，不修改零件、不生成或下发机床 NC 代码。实际 NXOpen 实现必须对照目标版本自带的 Python stubs，并通过碰撞/过切检查、机床仿真和人工审批。

技术边界见 [architecture.md](docs/architecture.md)，Codex 交换协议实现见 `cam_automation/codex_bridge.py`，产品适配入口见 `cam_automation/integrations.py`。

## Test

```powershell
python -m unittest discover -s tests -v
```
