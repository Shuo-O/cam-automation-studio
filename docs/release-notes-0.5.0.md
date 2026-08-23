# CAM Automation Studio 0.5.0 Release Notes

发布日期：2026-08-24

## 发布内容

- 空白插件化内核与单窗口工作台；业务模块默认未安装。
- 两个 NX、两个 PowerMill 的多实例发现与右侧连接抽屉。
- 默认本地自动记录、一次授权、分类暂停/恢复和可撤销授权。
- `manual|automation|system|execution_audit` 模式、L0-L4 层级和游标分页。
- 2-5 会话比较、确定性工作流学习、版本化 recipe hash 和 adapter-only preview。
- 严格结构化 Codex 审阅以及 NX/PowerMill 各 4 个 review-first Skills。
- 按实例串行的只读/dry-run 任务、轮询、取消、超时、DiffReport 和 diagnostics。
- 旧 ActivityEvent、`execution_audit -> mode=automation` 和同步执行路由兼容。

## 安全边界

- NX 与 PowerMill parser 隔离；共享动作使用 `cam.*`，产品动作使用 `nx.*` 或
  `powermill.*`。
- 每个任务绑定产品、目标实例、目标版本、项目和 recipe hash。
- 请求/响应二次扫描阻断危险编码、Codex 注入、Journal live、机床控制、
  NC/G-code 和 postprocess 输出。
- 内置 transport 仅离线 fixture。不会静默上传，不连接真实 CAM，不执行 live 命令。
- `cam_simulation=not_run`、`collision_check=required`、
  `shop_approval=required`；不生成或发送 machine-ready NC。

## 验证

- Python：根 117、UG/NX 31、PowerMill 6；focused 集成/安全 53。
- 前端：4 项通过；1920x1080、1280x720、390x844 浏览器验收通过。
- Fixtures：20 JSON、2 JSONL（26 行）有效。
- Packaging：5 个插件和 8 个 Skill 的官方 validator 通过。
- 性能：100,000 事件导入 5.489 秒，查询 p95 0.404 ms；
  10,000 会话学习 1.021 秒且结果确定；1,000 任务 0.770 秒且顺序稳定。

## 现场验证项

真实 NXOpen/PowerMill transport、目标版本 API、项目映射、稳定对象选择器、CAM 仿真、
碰撞/过切检查和车间批准不在本次 fixture 发布范围内。上述验证完成前，不能将本版本
描述为 production live execution。

## 本机安装阻塞

5 个插件 manifest 和 8 个 Skill 已通过官方 validator。CLI 重装未完成：本机
`codex.exe` 位于 WindowsApps，执行只读 `codex plugin list` 即返回“拒绝访问”；
默认个人 marketplace 文件也不存在。发布过程未修改个人 marketplace/config，未伪造
插件安装成功。
