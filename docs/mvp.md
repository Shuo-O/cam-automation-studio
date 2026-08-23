# MVP 使用说明

## 启动

在仓库根目录运行：

```powershell
python -m cam_automation serve --host 127.0.0.1 --port 8765
```

浏览器打开 `http://127.0.0.1:8765/`。应用始终是一个顶级窗口，首次启动只显示空白
插件化内核。连接状态位于顶部状态条，点击后在同一窗口右侧抽屉展开四实例详情。

## 演示闭环

1. 在插件中心安装本地记录、UG/NX、PowerMill、命令网关和 Codex 审阅。
2. 本地记录插件完成一次授权后自动扫描打包事件源；可在高级设置中暂停分类或撤销。
3. 在日志页选择 `manual` 与 `L2`，使用游标分页检查本地脱敏事件。
4. 在会话页选择 3-5 个同产品会话，查看 `SessionDiff`。
5. 打开学习候选和配方，确认共同步骤、参数、来源与稳定 recipe hash。
6. 查看 Codex 结构化 `needs_changes` 审阅。
7. 在命令页选择明确目标实例，提交只读查询或 recipe dry-run，轮询或取消任务。
8. 查看 `CommandResponse`、`DiffReport` 和诊断页的实例队列/耗时。

默认演示只使用离线 fixture transports，不调用真实 CAM，不向网络上传日志。

## NX Journal 离线 Demo

NX 路径提供了 AST 静态解析、SQLite WAL 事件库和跨会话序列挖掘：

```powershell
.\plugins\ug-cam-copilot\scripts\run-nx-demo.ps1
```

导入真实 Journal：

```powershell
python .\plugins\ug-cam-copilot\scripts\ugcam.py ingest .\journals --product nx
python .\plugins\ug-cam-copilot\scripts\ugcam.py mine --product nx --min-support 2
python .\plugins\ug-cam-copilot\scripts\ugcam.py scaffold --product nx --out .\generated\nx-candidate
```

生成的 `nx_preview_journal.py` 只展示候选动作，不修改 NX 对象。

## 日志模式与层级

- `source_mode`：`manual`、`automation`、`system`、`execution_audit`。
- `view_level`：`L0` 到 `L4`。
- `execution_audit` 保留旧 `mode=automation` 兼容投影。
- 查询还可以绑定产品、实例、项目、动作、时间和文本。

## 插件与 Skills

UG/NX 和 PowerMill 各提供活动查询预览、会话比较、配方审阅、工作流学习四个
Codex Skills。所有 Skills 都要求 review-first、dry-run、仿真、碰撞检查和人工批准；
产品 Skills 不跨 parser 边界。

## 现场验证边界

- 当前没有真实 PowerMill COM/in-process 或 NXOpen live transport。
- 不生成或发送 machine-ready NC、G-code 或 postprocess 输出。
- 目标版本、项目快照、稳定对象选择器必须在客户环境验证。
- CAM 仿真、碰撞/过切检查和车间批准保持独立人工门禁。
- 现场 adapter 通过以上门禁前，生产执行能力不成立。
