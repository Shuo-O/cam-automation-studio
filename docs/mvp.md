# MVP 使用说明

## 启动

在仓库根目录运行：

```powershell
.\plugins\ug-cam-copilot\scripts\run-demo.ps1
```

浏览器打开 `http://127.0.0.1:8765/`。首次启动只显示插件中心，没有默认业务模块。也可以直接运行：

```powershell
python .\plugins\ug-cam-copilot\app\powermill_ai_demo.py --no-browser
```

## 演示闭环

1. 在插件中心安装 `PowerMill 工作流` 或 `UG / NX 工作流`。
2. 打开工作台，手动选择“载入样例”或导入自己的日志。
3. 点击“分析工作流”，查看动作、参数、风险和 dry-run 预览。
4. 按需返回插件中心安装本地记录、命令网关或 Codex 审阅。

导出的内容是**审阅草稿**，不会由 demo 自动执行。PowerMill 宏需在副本项目里验证，NXOpen 草稿需替换选择器和本地 API 调用。

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

## 代码边界

- `app/powermill_ai_demo.py`：解析、归一化、重复序列挖掘、建议与导出。
- `app/web/index.html`：零依赖本地 UI。
- `skills/powermill-workflow-learning`：Codex 可直接调用的 PowerMill 工作流 skill。
- `skills/nx-workflow-learning`：与现有 UG/NX 开发同步的 Journal 学习 skill。
- `tests/test_powermill_ai_demo.py`：核心行为回归测试。
- `src/ugcam_ai`：公共事件契约、NX AST 适配器、SQLite 和序列挖掘。
- `schemas/activity-event.schema.json`：PowerMill/NX 并行开发契约。

## 下一步路线

1. 增加 PowerMill 本地宿主 adapter：从 `PMMacro`/COM 读取当前项目、刀具、刀路和选择集。
2. 增加 NXOpen adapter：将稳定对象查询替换掉录制 journal 中的脆弱选择句柄。
3. 引入工艺约束和离线回放：碰撞、残料、刀具寿命、循环时间作为 recipe 评分信号。
4. 再考虑本地模型/RAG：仅在有脱敏日志和人工审核样本后训练参数推荐。
