# 客户交付实施方案与并行开发约定

日期：2026-09-29。总指挥：当前对话。基线：1d96f69 加当前工作区已有 Cimatron 改动；禁止回滚这些改动。

## 交付目标

本轮交付一个 Windows 本地运行、断网可用的 CAM 工艺复用与审阅工作台：导入制造上下文 → 保存历史案例 → 检索适用工艺 → 生成参数化建议 → 唯一对象绑定 → 登记审阅/仿真/碰撞/现场批准证据 → 导出可追溯交付包。保留现有日志学习、Flow Studio 和插件功能。

默认客户场景为 PowerMill 三轴型腔工艺复用；NX 兼容。Cimatron 仅交付经过测试的静态解析能力，未实现的 Flow/宿主能力明确禁用。真实宿主连接以安装的软件与 SDK 验证结果为准，导入快照不是实时连接证明。生产动作始终 dry-run，不输出机床 NC。

## 五个开发对话与文件所有权

| 对话 | 实现与验收 | 独占文件 |
|---|---|---|
| A 基础能力与兼容修复 | 修复产品分派、Cimatron 能力声明/安装清单、既有回归及性能；保持未知语义不透明 | product_catalog.py、integrations.py、flow_validation.py、adapters/cimatron_journal.py、plugins/cimatron-cam-copilot/**；既有相关测试；docs/delivery/handoff-A.md |
| B 制造上下文与只读桥接 | 快照校验/持久化/哈希、语义选择器唯一绑定、宿主导出工具与能力说明 | cam_automation/manufacturing_context.py、host_bridges/**、tests/test_manufacturing_context.py、docs/delivery/handoff-B.md |
| C 案例复用与证据闭环 | SQLite 案例检索、可解释排序、确定性参数建议、证据完整性/失效检查、可追溯导出 | cam_automation/case_library.py、tests/test_case_library.py、docs/delivery/handoff-C.md |
| D 客户工作台 | 中文步骤式页面、输入校验、错误与空状态、案例/快照/建议/证据/下载操作；显式演示标识 | cam_automation/web/delivery/**、index.html 仅新增入口、tests/browser/customer-workbench*、docs/delivery/handoff-D.md |
| E 打包与客户资料 | 可复现 ZIP/安装启动、自检/备份/恢复、开源许可证清单、部署/操作/验收手册 | scripts/build_customer_delivery.py、scripts/customer_*.py、delivery/**、docs/customer/**、tests/test_customer_delivery.py、docs/delivery/handoff-E.md |
| 总指挥 | API/服务集成、完整回归、浏览器验收、交付包构建、最终汇总 | cam_automation/customer_api.py、web_server.py、pyproject.toml、README.md、tests/test_customer_api.py、docs/delivery/*（handoff 除外） |

其他已有文件需要修改时先向总指挥说明；不得自行扩大归属。共享目录协作，不切分支、不 reset、不 stash、不批量格式化、不提交其他人的文件。每个对话完成后写 handoff：修改、接口、测试命令/结果、限制。用户已授权本次多对话协调及回报总指挥。

## 冻结接口 v1

所有业务 JSON 均 schema_version=1；产品 nx/powermill；Cimatron 的实验能力不得借此获得生产支持。文件输入只解析数据，不执行源代码。未知额外字段可以保留但不能授权动作。服务方法返回 JSON 可序列化 dict/list，输入错误 ValueError、不存在 KeyError。线程安全且原子持久化，不接受任意本地路径读取。应用数据位于现有 app-data 下 customer 子目录。

### 制造上下文（B）

`ManufacturingContextService(root: Path)`：`import_snapshot(payload)->dict`、`list_snapshots()->list`、`get_snapshot(snapshot_id)->dict`、`resolve_selector(snapshot_id, selector)->dict`、`capabilities()->dict`。

快照最低输入：`product, instance_id, project_id, target_version, units ('mm'|'inch'), material, machine {axes:3}, objects [{object_id,kind,name,attributes:{}}]`。服务补充 snapshot_id、content_hash、captured_at、source（默认 imported；fixture 必须标识）、warnings。可选 stock、tools、fixtures、geometry_digest。哈希必须覆盖所有影响建议与绑定的内容。同名对象不得静默选择；选择器支持 object_id/name/kind/attributes，返回 status=resolved|ambiguous|unresolved、matches、snapshot_hash。任何“已连接”声明必须有真实宿主证据。

### 案例与建议（C）

`CaseLibraryService(root: Path)`：`add_case(payload)->dict`、`list_cases()->list`、`search_cases(snapshot, query='')->list`、`create_proposal(snapshot, case_id, parameters=None)->dict`、`get_proposal(proposal_id)->dict`、`record_evidence(proposal_id,payload)->dict`、`assess_proposal(proposal_id,snapshot)->dict`、`export_bundle(proposal_id,snapshot)->dict`。

案例输入：`name, product, material, units, machine {axes:3}, tags:[], parameters:{}, steps:[], provenance:{}`。案例必须有内容哈希及稳定 ID。检索隔离产品，解释匹配与缺失条件；不得把相似度称为成功率。建议固定 snapshot_hash、case_hash、parameters、proposal_hash、dry_run=true。证据输入至少 gate=review|simulation|collision|shop_approval、status=passed|failed、actor、note、proposal_hash、snapshot_hash；可选 artifact_digest/时间。证据本地登记不等于独立认证。fixture/imported 来源及验证局限传递至导出。任意参数/上下文变化都不能沿用旧证据。即使门禁齐全也不提供执行/NC 输出 API。

### HTTP 与前端（总指挥/D）

前缀 `/api/delivery`，本机限定，沿用既有 JSON 错误形状。GET `/status` 返回 capabilities 和交付模式；GET `/snapshots`、`/cases` 返回 `{items:[]}`；POST `/snapshots`、`/cases` 直接传上述输入并返回对象。GET `/snapshots/{id}`。POST `/selectors/resolve` 输入 `{snapshot_id,selector}`。POST `/cases/search` 输入 `{snapshot_id,query}` 返回 `{items:[]}`。POST `/proposals` 输入 `{snapshot_id,case_id,parameters}`。GET `/proposals/{id}`；POST `/proposals/{id}/evidence` 传证据；POST `/proposals/{id}/assess` 输入 `{snapshot_id}`；POST `/proposals/{id}/export` 同上返回可下载 JSON（Content-Disposition）。UI 独立路径 `/delivery/`。不自动安装既有插件，不把演示快照伪装成客户数据。

具体补充字段可加；变更上述方法签名或路由必须通知总指挥和依赖方。D 可先用 fixtures 做前端测试，最终必须接真实 HTTP。

## 开源复用与范围选择

复用现有 MIT 内核、Python 标准库、SQLite（公有领域）与原有前端；避免本轮引入完整 CAD 内核及联网必需服务。Autodesk PowerShapeAndPowerMillAPI（MIT）仅在明确版本/许可证/宿主接口验证后复用；不得把 .NET 历史版本示例当成已验证安装。NX 原生 SDK 由客户合法安装提供。CadQuery/OCCT 作为后续辅助夹具建模路线，不增加首轮部署成本。所有实际随包第三方代码必须有来源、版本与许可，不以研究论文模型替代可商用验证。

## 集成顺序与完成门槛

1. 固定方案与接口，启动 A–E；总指挥同时实现 API。
2. 领域单测覆盖错误产品、歧义绑定、过期证据、输入越界和持久化重启；修复当前基线 7 个失败，性能保持原阈值并单独测量。
3. UI 经真实 HTTP 完成导入→检索→建议→证据→导出；验证手机/桌面基本布局与错误状态。
4. 干净 Python 环境运行根测试、两产品插件测试、Node 测试；构建 ZIP 后从解压目录启动、自检、验证数据备份恢复。
5. 交付 ZIP、SHA-256、版本/许可/变更说明、中文安装与操作手册、验收报告、已知限制。不得将测试未运行表述成通过。
6. 真实 PowerMill/NX 现场验收单列：目标软件版本、真实零件/刀具库、只读快照对应性、仿真/碰撞记录、现场批准。本机缺少软件或样件时保持未验证，不阻碍可验证离线系统的交付。

预计按依赖分三波完成（基础与领域并行 → API/UI 集成 → 打包验收），不承诺未经测量的工期或全产品全版本覆盖。
