# 第三方许可证与来源记录

本交付包当前只依赖 Python 标准库和仓库内自有代码。Python 解释器由客户按 Python 官方许可单独安装；本 ZIP 不重新分发解释器。

| 组件/来源 | 版本或范围 | 许可证 | 本包用途 | 验证说明 |
|---|---|---|---|---|
| Python 标准库 | Python >= 3.10 | Python Software Foundation License | HTTP 服务、JSON、SQLite/ZIP、文件安全 | 使用客户安装的解释器，启动前检查版本 |
| SQLite（Python `sqlite3`） | 随 Python 提供 | Public Domain | 本地数据持久化（如启用） | 未引入独立二进制 |
| 本仓库 CAM Automation Studio | 客户包清单中的应用版本（当前 0.7.0） | MIT，见根目录 `LICENSE` | 工作台、host bridge 和插件框架 | 仅随包分发源代码 |
| `plugins/ug-cam-copilot` | 随包版本；目录内含 `LICENSE` | MIT | Siemens NX 插件资产 | 许可证文件随包保留；PowerMill 适配不由此项声明 |

PowerMill、NX、Cimatron 及其 SDK/宿主软件不随包分发，也不因本包声明已安装或已连接。客户应按各厂商许可和现场版本独立确认。

PowerMill bridge 文档引用 Autodesk PowerShapeAndPowerMillAPI MIT 项目作为 API 参考；本包未分发其代码或二进制，版本/安装兼容性尚未验证，因此不把该引用写成已安装的运行时依赖。
