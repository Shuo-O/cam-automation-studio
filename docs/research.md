# PowerMill / UG CAM AI 插件调研结论

更新时间：2026-08-20

## 资料与事实

### PowerMill 官方

- [Recording macros in PowerMill](https://help.autodesk.com/cloudhelp/2025/ENU/PWRM-ReferenceHelp/files/GUID-8B22DBDB-509A-4E66-8AD4-D06BA6720DFC.htm)：官方说明录制宏会记录用户在对话框中改变的值，操作入口是 Macros -> Record / Stop。
- [Macro files](https://help.autodesk.com/cloudhelp/2023/ENU/PWRM-ReferenceHelp/files/GUID-8D834204-AB5F-4FBF-B30B-3A44E0462EB8.htm)：宏是包含命令序列的 `.mac` 文件，可从 Explorer 的 Macro 节点运行；`pmuser.mac` 可作为启动宏。
- [PowerMill 2024 Offline Help and Macros and Plugin Documentation](https://www.autodesk.com/support/technical/article/caas/tsarticles/ts/26zXUqtRe1Tur1KdK1pctn.html)：Autodesk 将 Macro Programming Guide 和插件资料作为离线/在线帮助提供。
- [Autodesk PowerShapeAndPowerMillAPI](https://github.com/Autodesk/PowerShapeAndPowerMillAPI)：官方 GitHub API 样例包含 `PMMacro`、`PMToolpath`、`PMProject` 等对象，说明后续可以从“文本宏学习”升级到 COM/.NET 宿主适配层。

### Siemens NX / UG 官方与开源

- [Siemens NX Journals](https://blogs.sw.siemens.com/nx-manufacturing/Learn-to-use-Journal-files-in-NX/)：NX Journal 是可记录、编辑、回放交互操作的快速自动化工具。
- [Siemens documentation and downloads](https://www.siemens.com/en-us/support/documentation-downloads/)：NXOpen 文档按开发语言提供，正式实现应以本地安装版本为准。
- [NX Open Python Reference Guide](https://docs.sw.siemens.com/en-US/doc/209349590/PL20231101866122454.custom_api.nxopen_python_ref)：正式 API 入口；生成代码需要与目标 NX 版本的本地 stubs 交叉核对。
- [NX Open Programmer's Guide](https://docs.sw.siemens.com/en-US/doc/209349590/PL20220512394070742.nxopen_prog_guide/xid1124929)：NX Open 编程与执行约束。
- [cfs-energy/nxlib](https://github.com/cfs-energy/nxlib)：开源实现把本地 Python 与 NX 自带解释器分离，并以无第三方依赖的 NX 侧代码支持图形/无头执行和测试。
- [nxopen-python-cookbook](https://github.com/ahmet6141/nxopen-python-cookbook)：开源的 NX 2506 实机验证配方，特别强调不同 NX 版本 API 会变化，必须查看本机 stubs。
- [NXOpen Python tutorials](https://github.com/Foadsf/NXOpen_Python_tutorials)：社区样例适合学习 API 形态，但不能替代本地 NXOpen 版本文档。

### Codex 插件与 Skills

- [OpenAI 插件打包文档](https://developers.openai.com/plugins/build/plugins)：当前插件以 `.codex-plugin/plugin.json` 和内置 Skills 交付。
- [OpenAI：将工作流保存为 Skill](https://learn.chatgpt.com/use-cases/reusable-codex-skills)：Skill 应保存可复用工作流、脚本和审阅规则。
- 旧 [openai/skills](https://github.com/openai/skills) 仓库已标记弃用并指向插件工作流，因此本项目不复制旧目录，而是以插件内 Skill 交付。

### 行业产品形态

- [CloudNC CAM Assist](https://www.cloudnc.com/)：嵌入既有 CAM，生成加工策略/刀路并估算加工时间，目标是缩短编程时间。
- [Engineering.com 对 CAM Assist 的报道](https://www.engineering.com/cloudnc-cam-assist-software-creates-toolpaths-with-ai/)：其能力从 3 轴扩展到 3+2 轴，仍强调工程师在 CAM 中审阅结果。

## 对本项目的启示

| 观察 | 第一版决策 |
| --- | --- |
| PowerMill 的最稳定入口是录制 `.mac` | 先做 `.mac`/JSON 日志导入，不依赖 PowerMill 安装 |
| NX 的入口是 Journal/NXOpen | 保留相同事件模型，导出 NXOpen Python 草稿 |
| CAM AI 产品嵌入宿主并保留人工控制 | 只生成 recipe 和草稿，不自动执行宏 |
| 生产结果需要验证 | UI 展示 support/confidence、变量候选和 REVIEW 标记 |
| 官方 API 是下一阶段 | 用 adapter 接口替换文本草稿，接入 `PMMacro`/COM 和 NXOpen |

## 目前不做的事情

第一版不训练大模型、不上传客户日志、不直接修改 PowerMill/NX 工程、不承诺碰撞安全或刀路质量。原因是这些都需要本地宿主版本、机床/刀具库、工艺规则和可回放验证环境。
