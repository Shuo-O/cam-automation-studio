# 方案 C 交接：案例复用与证据闭环

日期：2026-09-29

## 修改文件

- `cam_automation/case_library.py`：新增 `CaseLibraryService`，使用 SQLite/stdlib 持久化案例、建议和证据。
- `tests/test_case_library.py`：覆盖冻结样例、产品隔离、确定性检索、selector 歧义、材料不匹配、过期快照、证据哈希、导出和重启持久化。

## 冻结接口

`CaseLibraryService(root: Path)` 提供：

- `add_case(payload) -> dict`
- `list_cases() -> list`
- `search_cases(snapshot, query="") -> list`
- `create_proposal(snapshot, case_id, parameters=None) -> dict`
- `get_proposal(proposal_id) -> dict`
- `record_evidence(proposal_id, payload) -> dict`
- `assess_proposal(proposal_id, snapshot) -> dict`
- `export_bundle(proposal_id, snapshot) -> dict`

案例 ID 为稳定的 `case:<sha256>`，建议 ID 为内容确定的 `proposal:<sha256>`。所有业务对象包含 `schema_version=1`。检索结果的 `match.score` 只用于稳定排序，`matched_conditions`/`missing_conditions`/`explanation` 解释产品、单位、材料、机床轴数和查询文本匹配，不代表成功率。

建议包含 `snapshot_id`、`snapshot_hash`、`snapshot_digest`、`case_hash`、`product`、`source`、`target_version`、`parameters`、`steps`、`selector_bindings`、`blocking_reasons`、`limitations` 和 `dry_run=true`。带 selector 的步骤会按快照对象重新唯一匹配；歧义、未解析、材料不匹配或 opaque 动作会阻塞 readiness。`assess_proposal` 不信任持久化的 ready/resolved 字段，会重新匹配并比较哈希/本地上下文指纹。

证据要求 `gate`（`review`、`simulation`、`collision`、`shop_approval`）、`status`（`passed`/`failed`）、`actor`、`note`、`proposal_hash`、`snapshot_hash`，并阻止跨建议或跨上下文复用。`assess_proposal` 仅表示证据记录齐全，不声明独立认证，返回 `verified=false`、`validation_scope=evidence_recorded_only`。`export_bundle` 顶层和 proposal 均带 `dry_run=true`，明确 fixture/imported 限制，不包含执行或 NC 输出。

动作隔离：`nx.*` 与 `powermill.*` 不能跨产品；含 NC、postprocess、execute 或机床输出字段的步骤直接拒绝；未知动作以 `action_status=opaque` 保存但阻塞 readiness。动作白名单是显式的 `cam.operation.plan`、`cam.operation.create`、`cam.toolpath.preview`、`cam.toolpath.simulate` 及对应产品的 `*.operation.plan/create`，其他 `cam.*` 也保持 opaque。NC 判断按完整 token，避免误拒 `sequence`/`reference` 等普通动作名。参数和步骤参数中的 NC/执行/后处理键也直接拒绝。空步骤阻塞建议，重复 `step_id` 拒绝。若 B 模块提供 `resolve_snapshot_selector(snapshot, selector)`，服务会惰性复用该解析器，否则使用本地确定性匹配。证据 `recorded_at` 由服务端时钟写入；兼容接收用户时间但存为 `observed_at`，并校验 ISO-8601 字符串类型。

## 测试

```text
C:\Users\s3272\AppData\Local\Temp\cam-assessment-20260929-venv\Scripts\python.exe -m unittest discover -s tests -p test_case_library.py -v
Ran 9 tests ... OK

C:\Users\s3272\AppData\Local\Temp\cam-assessment-20260929-venv\Scripts\python.exe -m py_compile cam_automation/case_library.py tests/test_case_library.py
```

## 限制与集成注意

- 本机未连接真实 PowerMill/NX；fixture/imported 快照始终显示为演示或导入上下文。
- 当前数据库文件为传入 `root/case_library.sqlite3`；主控应将 `root` 指向现有 app-data/customer 目录。
- 需要总指挥在 `customer_api.py` 中直接返回这些对象，不额外包 `proposal/data`；HTTP 层可自行套 `{items:[]}` 列表响应。
- selector 解析已预留 B 的公开函数；B 若采用其他模块路径，请在集成时补一个兼容导出。
