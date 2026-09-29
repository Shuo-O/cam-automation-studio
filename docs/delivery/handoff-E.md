# 方案 E 交接记录：客户分发与资料

日期：2026-09-29
范围：可复现客户 ZIP、Windows 启动、自检、客户数据备份/恢复、中文资料与许可证记录。

## 实际接口

- `scripts/build_customer_delivery.py`
  - `build_customer_zip(output, repo_root=...) -> Path`
  - `iter_source_files(repo_root=...)` 按白名单收集 `cam_automation/`、`host_bridges/`、`plugins/`、`delivery/`、`docs/customer/` 和根 `LICENSE`/`pyproject.toml`。
  - ZIP 固定文件顺序、时间戳和压缩级别；输出旁车 `<zip>.sha256`。
  - `delivery/BUILD-MANIFEST.json` 含 `schema_version=1`、`application_version`（当前从 pyproject 读取为 0.7.0）、文件 SHA-256、`requires_python >=3.10`、`dry_run_only=true` 和排除策略。
- `scripts/customer_self_check.py`
  - `check_installation(package_root=None, data_dir=None) -> dict`
  - 检查 Python >=3.10、核心应用/工作台/host bridge/插件资产和客户数据目录可写性。
- `scripts/customer_launcher.py`
  - `--host`、`--port`、`--data-dir`、`--skip-self-check`。
  - 自检通过后设置 `CAM_APP_DATA_DIR`，打印 `http://<host>:<port>/delivery/`，调用既有 `cam_automation.cli serve`。
- `scripts/customer_backup.py`
  - `create_backup(data_dir, output, overwrite=False) -> Path`
  - 通过 SQLite 文件头识别 `.db`、`.sqlite`、`.sqlite3`（包括 `case_library.sqlite3`），使用 `sqlite3.Connection.backup()` 生成一致快照，跳过对应 `-wal/-shm`；符号链接、Windows 不安全路径组件和数据目录内输出被拒绝。
- `scripts/customer_restore.py`
  - `restore_backup(backup, data_dir, overwrite=False) -> dict`
  - 恢复前验证 ZIP 成员、manifest、哈希、Windows 设备名/ADS/末尾点空格/大小写碰撞、父路径和所有目标冲突；默认不覆盖，预检失败时不向客户目录写入。

## 客户资料

- `delivery/start_customer.bat`
- `delivery/README.zh-CN.md`
- `delivery/examples/snapshot-fixture.json`、`delivery/examples/case-fixture.json`
- `delivery/THIRD_PARTY_NOTICES.md`
- `delivery/CHANGELOG.md`
- `docs/customer/操作手册.zh-CN.md`
- `docs/customer/验收手册.zh-CN.md`

资料明确 Python >=3.10 前置条件、默认应用数据根目录（客户数据在其 `customer` 子目录）、停止服务后备份整个 app-data、dry-run/无 NC 输出、插件不自动安装和真实 NX/PowerMill/Cimatron 现场验收限制。
操作手册按 UI 按钮给出导入、检索、参数重建、四道证据和导出步骤，并链接 `host_bridges/README.md`（该文件由 B 对话维护）。

## 验证

使用独立解释器 `C:\Users\s3272\AppData\Local\Temp\cam-assessment-20260929-venv\Scripts\python.exe`：

- `python -m unittest tests.test_customer_delivery -v`：9/9 通过（含真实 `CaseLibraryService` `.sqlite3` 建库、备份、恢复、检索）。
- `python -m py_compile scripts/build_customer_delivery.py scripts/customer_self_check.py scripts/customer_launcher.py scripts/customer_backup.py scripts/customer_restore.py tests/test_customer_delivery.py`：通过。
- 实际构建临时 ZIP 并解压冒烟：SHA-256 旁车匹配；解压后自检 `status=passed`；manifest `application_version=0.7.0`；`host_bridges/powermill.py` 存在。

## 限制与交接注意

- 构建器只收集源代码和插件资产，不包含开发 `build/`、`.git`、测试/研究中间文件或客户数据；最终 ZIP 应由主控在最终工作区状态重新构建。
- 启动器打印的是预设端口地址；若端口被占用，需由客户通过 `--port` 指定空闲端口。
- SQLite backup API 可在线生成一致快照，但客户手册仍要求先停止服务以避免业务写入竞态。
- Windows 设备名和大小写规则在恢复时主动执行，Linux 上也按 Windows 约束拒绝。
