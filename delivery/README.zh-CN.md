# CAM Automation Studio 客户交付包

本包是 Windows 本地、断网可用的工艺复用与审阅工作台。运行前置条件是 **Python 3.10 或更高版本**；启动入口为 `start_customer.bat`。启动器会先执行自检，默认只监听 `127.0.0.1`。

## 安装与启动

1. 解压 ZIP 到本地目录，例如 `C:\CAMStudio`。
2. 在资源管理器双击 `delivery\start_customer.bat`，或在 PowerShell 执行：
   `delivery\start_customer.bat --port 8765`。
3. 浏览器打开 `http://127.0.0.1:8765/delivery/`。按工作台提示导入快照、检索案例、生成建议、登记证据并导出结果。
4. 应用数据默认保存于 `%LOCALAPPDATA%\CAM Automation Studio\app-data`，客户案例/快照在其 `customer` 子目录中；不会写入 ZIP 解压目录。可用 `--data-dir` 指定一个明确的应用数据根目录。

本包不自动安装 NX、PowerMill 或任何 CAD/CAM 软件插件。宿主连接和现场能力必须由客户按验收手册另行验证。

## 自检与备份

```powershell
python scripts\customer_self_check.py
python scripts\customer_self_check.py --json
# 备份前先停止 start_customer.bat 启动的服务，确保整个应用数据根目录不再写入
python scripts\customer_backup.py "$env:LOCALAPPDATA\CAM Automation Studio\app-data" .\customer-backup.zip
python scripts\customer_restore.py .\customer-backup.zip "$env:LOCALAPPDATA\CAM Automation Studio\app-data-restore"
```

备份前应停止服务；SQLite 文件使用 SQLite backup API 创建一致快照。备份和恢复默认不覆盖已有文件；只有确认目标后显式加 `--overwrite` 才会覆盖。恢复会拒绝绝对路径、`..` 越界路径、重复成员、Windows 保留设备名、ADS 冒号、末尾点/空格和符号链接。

## 安全与运行边界

- 生产动作始终为 `dry_run=true`，本交付包不生成或输出机床 NC 代码。
- 文件输入按数据解析，不执行导入文件中的源代码。
- 不包含开发 `build` 数据、`.git`、测试/研究中间文件、密钥或客户数据。
- 本地备份文件仍可能包含客户工艺信息，应按客户信息安全制度保存。

## 资料

- `docs/customer/操作手册.zh-CN.md`：日常操作。
- `docs/customer/验收手册.zh-CN.md`：离线验收与现场验收边界。
- `delivery/examples/`：与 `tests/test_customer_api.py` 一致的 fixture 快照/案例 JSON 模板。
- `delivery/THIRD_PARTY_NOTICES.md`：许可证和来源记录。
- `delivery/CHANGELOG.md`：本包变更记录。
- `delivery/BUILD-MANIFEST.json`：文件清单和 SHA-256（由构建器生成）。
