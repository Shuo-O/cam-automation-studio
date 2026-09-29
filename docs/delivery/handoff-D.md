# Handoff D：客户交付工作台

日期：2026-09-29

## 修改范围

- `cam_automation/web/delivery/index.html`：独立中文步骤式工作台；导入快照/案例、检索、建议、对象绑定、证据门禁和交付包摘要。
- `cam_automation/web/delivery/app.js`：仅调用冻结的 `/api/delivery` HTTP 接口；处理加载、空、忙、错误和 blocked 状态；本地保存最近 `proposal_id` 并在刷新后恢复建议与证据。
- `cam_automation/web/delivery/styles.css`：本地响应式样式，桌面/手机布局和可访问焦点状态；原始导出 JSON 使用可展开 `details` 且限高滚动。
- `cam_automation/web/index.html`：仅增加“客户交付”入口链接。
- `tests/browser/customer-workbench.spec.cjs`：Playwright 浏览器验收，包含 mock 错误状态和真实 HTTP 完整路径。

## 实际接口

页面使用：

- `GET /api/delivery/status`
- `GET /api/delivery/snapshots`
- `GET /api/delivery/snapshots/{snapshot_id}`
- `POST /api/delivery/snapshots`
- `GET /api/delivery/cases`
- `POST /api/delivery/cases`
- `POST /api/delivery/cases/search`
- `GET /api/delivery/proposals/{proposal_id}`
- `POST /api/delivery/proposals`
- `POST /api/delivery/proposals/{proposal_id}/assess`
- `POST /api/delivery/proposals/{proposal_id}/evidence`
- `POST /api/delivery/proposals/{proposal_id}/export`

成功对象按领域对象直接读取；列表读取 `{schema_version: 1, items: [...]}`；错误显示 `error/code`。所有建议和导出都保留 `dry_run=true`，不提供执行或 NC 输出。

案例卡展示服务返回的 `match.matched_conditions`、`match.missing_conditions` 和 `match.explanation`；建议页展示每个 `selector_bindings` 的 selector、status 与 matches。上下文绑定会读取 `objects` 以及可选 `stock/tools/fixtures`，并将 `pocket/body/face/hole/surface` 等加工对象归入几何/加工对象。

## 测试结果

Node/Playwright 运行环境使用本机 Chrome：

```powershell
$env:CAM_DELIVERY_BASE_URL = "http://127.0.0.1:8894/delivery/"
$env:CAM_DELIVERY_REAL_HTTP = "1"
$env:CAM_FLOW_CHROME = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$env:CAM_DELIVERY_SCREENSHOTS = "build/customer-browser-final4"
node --test tests/browser/customer-workbench.spec.cjs
```

结果：2 tests passed。真实 HTTP 流程使用独立端口 `8894` 和新 `CAM_APP_DATA_DIR`，实际覆盖导入→检索→匹配依据展示→建议 selector 绑定展示→登记 review→刷新恢复→登记其余三项→导出 JSON（校验 fixture 来源、4 条 actor、dry-run）→编辑参数重新生成并确认旧证据不沿用；另覆盖 API 错误状态、proposal 空状态隐藏、390px 手机无横向溢出。默认 mock 流程同样 2 tests passed。

产物：

- `build/customer-browser-final4/customer-workbench-desktop.png`
- `build/customer-browser-final4/customer-workbench-context.png`
- `build/customer-browser-final4/customer-workbench-proposal.png`
- `build/customer-browser-final4/customer-workbench-mobile.png`
- `build/customer-browser-final4/cam-delivery-*.json`

## 限制

- 浏览器测试依赖本机 Chrome 和主控已启动的领域 API；不证明真实 NX/PowerMill 宿主连接。
- fixture 快照和案例明确显示为演示来源；本地证据登记不是独立认证，仍需客户审阅、仿真、碰撞检查及现场批准。
- 工作台只管理 JSON 导出审阅包，不生成或发送机床 NC。
