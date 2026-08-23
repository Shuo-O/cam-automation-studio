# CAM Automation Studio 并行开发合同

> 状态：Wave 0 冻结
>
> 合同版本：`cam.parallel.contracts.v1`
>
> Git 基线：`cce9fc07978d762eb703c9fb67e879654a79eab2`
>
> 冻结日期：2026-08-23

本文是 Wave 1 四个开发任务的共同接口基线。本文冻结最小 JSON
合同、兼容规则、产品解析边界和安全门禁，不表示对应 service、API
或 transport 已经实现。

文中的“必须”“不得”“应”是规范性要求。示例位于
`examples/contracts/`，所有示例都是独立、有效的 JSON 文档。

## 1. 适用范围

冻结以下合同：

- `ActivityEvent`
- `InstanceDescriptor`
- `EventQuery` / `EventPage`
- `Session` / `SessionDiff`
- `Recipe` / `RecipeStep` / `RecipeParameter`
- `CommandTask` / `CommandResponse` / `DiffReport`
- `CodexReviewRequest` / `CodexReviewResult`

Wave 0 不修改 Python DTO、JSON Schema、API、parser、前端、插件
manifest 或 Skill。Wave 1 实现必须以本文为依据，并保持现有输入和
现有测试兼容。

## 2. 全局约定

### 2.1 JSON 与版本

- 所有顶层合同都是 JSON object，`schema_version` 固定为整数 `1`。
- 字段名使用 `snake_case`。
- 时间使用 RFC 3339 字符串；新生产者应输出带时区的 UTC 时间。
- duration 使用非负整数毫秒。
- ID 和 cursor 对调用方是不透明字符串；调用方不得解析其内部格式。
- `null` 只在本文明确允许时使用。可选字段也可以完全省略。
- 消费者必须忽略其不理解的新增可选字段。
- 将来若需破坏性变更，必须新增合同主版本，不能静默改变 v1 含义。

### 2.2 产品与动作命名空间

- `product` 目前只能是 `nx` 或 `powermill`。
- 两个产品语义一致的规范动作使用 `cam.*`。
- NX 专属动作使用 `nx.*`。
- PowerMill 专属动作使用 `powermill.*`。
- 新生产者不得创建新的 `part.*` 或 `object.*` 动作。
- 为兼容已有 NX v1 日志，消费者仍必须接受既有 `part.*` 和
  `object.*` 动作；其迁移不能通过重命名旧事件完成。

NX Journal/NXOpen 语法只由
`plugins/ug-cam-copilot/src/ugcam_ai/adapters/nx_journal.py` 处理。
PowerMill `.mac`、命令日志和产品语法必须留在 PowerMill 应用或
独立 `powermill_macro.py` adapter。PowerMill 输入不得进入 NX
parser，共享 session、recipe 和 command 层不得解析任一产品语法。

### 2.3 安全边界

- 学习产物始终是 review-first 的配方或预览。
- `CommandTask` v1 只允许 `read_only` 或 `dry_run`，不定义 live
  生产执行模式。
- dry-run 成功不等于加工批准，也不等于仿真或碰撞检查通过。
- 配方必须经过人工审阅；生产使用前还必须通过目标 CAM 版本验证、
  CAM 仿真、碰撞/过切检查和车间批准。
- 从学习日志不得生成、后处理、返回或发送 machine-ready NC、
  G-code 或等价机床输出。
- `approved_for_simulation` 只允许进入仿真，绝不表示
  `approved_for_production`。

## 3. ActivityEvent

`ActivityEvent` 是产品中立的可观察 CAM 活动记录。v1 的既有字段
不得删除、重命名、改变类型或改变默认含义。

### 3.1 既有字段

| 字段 | 类型 | 必需 | 冻结含义 |
| --- | --- | --- | --- |
| `schema_version` | integer | 是 | 固定为 `1` |
| `session_id` | string | 是 | 生产者给出的来源会话 ID |
| `seq` | integer | 是 | 会话内从 `0` 开始的稳定顺序 |
| `product` | string | 是 | `nx` 或 `powermill` |
| `action` | string | 是 | 规范动作名称 |
| `category` | string | 是 | 便于筛选的稳定粗分类 |
| `mode` | string | 否 | 既有筛选字段：`manual`、`automation`、`system`；默认 `manual` |
| `params` | object | 否 | 产品中立参数或 adapter 保留的结构化证据；默认 `{}` |
| `source_file` | string | 否 | 原始来源或脱敏别名；默认 `""` |
| `source_line` | integer | 否 | 原始来源行；默认 `0` |
| `duration_ms` | integer 或 null | 否 | 已观测到的耗时 |
| `timestamp` | string 或 null | 否 | 原始事件时间 |

### 3.2 冻结的可选扩展

以下字段全部是可选字段。旧生产者可以不输出，旧 JSONL 必须继续
可导入和往返。

| 字段 | 类型 | 允许值/含义 |
| --- | --- | --- |
| `source_mode` | string | `manual`、`automation`、`system`、`execution_audit` |
| `view_level` | string | `L0`、`L1`、`L2`、`L3`、`L4` |
| `expertise_label` | string | `unlabeled`、`routine`、`expert` |
| `instance_id` | string | 产生或承载该事件的运行期 CAM 实例 |
| `project_id` | string 或 null | 稳定项目 ID；无法映射时为 null 或省略 |
| `target_version` | string | 事件所依据或面向的 CAM 版本 |
| `command_response` | object 或 null | 只用于结构化、脱敏的命令响应摘要 |
| `review_status` | string | `unreviewed`、`needs_review`、`needs_changes`、`approved_for_simulation`、`rejected` |
| `recipe_hash` | string 或 null | `sha256:<64 lowercase hex>`；事件尚未归属配方时为 null |

`source_mode` 与 `view_level` 是正交维度：

| `source_mode` | 含义 |
| --- | --- |
| `manual` | 用户直接操作或原生录制的人为动作 |
| `automation` | 宏、Journal、插件或配方产生的动作 |
| `system` | 进程、窗口、连接、文件发现和状态事件 |
| `execution_audit` | 命令请求、响应、耗时和结果审计 |

| `view_level` | 含义 |
| --- | --- |
| `L0` | 原始证据 |
| `L1` | 规范动作 |
| `L2` | 会话级活动 |
| `L3` | 重复配方候选 |
| `L4` | 带版本、实例、参数和预览结果的运行候选 |

兼容规则：

- 新生产者知道来源模式时应同时输出 `source_mode` 和既有 `mode`。
- `manual`、`automation`、`system` 在两个字段中使用同名值。
- `source_mode=execution_audit` 时，既有 `mode` 必须写
  `automation`，使旧消费者仍能归入自动化活动。
- 只含既有 `mode` 的旧事件，其 `source_mode` 可按同名值推断。
- `params.mode` 是既有冗余证据，可以保留，但顶层 `mode` 和
  `source_mode` 是新查询的权威字段。

示例：`examples/contracts/activity-event.json`。

## 4. InstanceDescriptor

`InstanceDescriptor` 描述一次运行期 CAM 宿主或其可见顶级窗口。

| 字段 | 类型 | 必需 | 冻结含义 |
| --- | --- | --- | --- |
| `schema_version` | integer | 是 | 固定为 `1` |
| `instance_id` | string | 是 | 运行期稳定、调用方不透明 |
| `product` | string | 是 | `nx` 或 `powermill` |
| `pid` | integer | 是 | 正整数 OS PID |
| `process_name` | string | 是 | OS 进程名 |
| `window_handle` | string 或 null | 是 | 无窗口进程为 null |
| `window_title` | string | 是 | 无窗口或未知时为 `""` |
| `is_foreground` | boolean | 是 | 是否为当前前台顶级窗口 |
| `window_state` | string | 是 | `foreground`、`visible` 或 `background` |
| `target_version` | string 或 null | 否 | 已检测的产品版本 |
| `project_id` | string 或 null | 否 | 当前项目的稳定 ID |
| `project_name` | string 或 null | 否 | 面向用户的项目名 |
| `connection_status` | string | 否 | `detected`、`connected`、`disconnected`、`error` |
| `capabilities` | array[string] | 否 | 实例 transport 已确认的能力 |
| `discovered_at` | string | 否 | 首次发现时间 |
| `last_seen_at` | string | 否 | 最近发现时间 |
| `metadata` | object | 否 | 产品或 OS 的非权威附加信息 |

`instance_id` 在进程存活期间必须稳定。窗口实例建议由
`product + pid + window_handle` 生成；无窗口进程建议使用
`product + pid + process`。窗口标题和项目名不得参与身份判断。
进程重启后可以产生新 ID。

示例 `examples/contracts/instance-descriptors.json` 同时包含两个 NX
和两个 PowerMill 实例，其中一个是无窗口进程。

## 5. EventQuery 与 EventPage

### 5.1 EventQuery

除 `schema_version` 外，查询条件均可省略。多个数组字段内部是 OR，
不同字段之间是 AND。

| 字段 | 类型 | 必需 | 冻结含义 |
| --- | --- | --- | --- |
| `schema_version` | integer | 是 | 固定为 `1` |
| `source_modes` | array[string] | 否 | `source_mode` 过滤 |
| `view_levels` | array[string] | 否 | `view_level` 过滤 |
| `products` | array[string] | 否 | 产品过滤 |
| `instance_ids` | array[string] | 否 | 实例过滤 |
| `project_ids` | array[string] | 否 | 项目过滤 |
| `session_ids` | array[string] | 否 | 来源会话过滤 |
| `actions` | array[string] | 否 | 动作精确过滤 |
| `categories` | array[string] | 否 | 类别精确过滤 |
| `from_time` | string 或 null | 否 | 含边界的 RFC 3339 起点 |
| `to_time` | string 或 null | 否 | 含边界的 RFC 3339 终点 |
| `text` | string | 否 | 对允许索引字段进行全文查询 |
| `cursor` | string 或 null | 否 | 上一页返回的不透明 cursor |
| `limit` | integer | 否 | `1..10000`，默认 `100` |
| `sort` | string | 否 | `asc` 或 `desc`，默认 `asc` |

空数组等价于未提供该过滤条件。`from_time` 晚于 `to_time`、无效 enum
或超限 `limit` 必须作为参数错误返回，不能静默修正。

### 5.2 EventPage

| 字段 | 类型 | 必需 | 冻结含义 |
| --- | --- | --- | --- |
| `schema_version` | integer | 是 | 固定为 `1` |
| `events` | array[ActivityEvent] | 是 | 当前页，顺序与 query 一致 |
| `returned_count` | integer | 是 | 必须等于 `events.length` |
| `has_more` | boolean | 是 | 是否存在下一页 |
| `next_cursor` | string 或 null | 是 | 无下一页时为 null |
| `total_estimate` | integer 或 null | 否 | 可省略；不得假装成精确总数 |

cursor 必须封装稳定排序键和查询方向，并与原查询绑定。调用方不得
自行拼接 cursor。相同查询、相同不可变数据和相同 cursor 必须返回
相同页；新追加事件不得造成已消费事件重复。

示例：

- `examples/contracts/event-query.json`
- `examples/contracts/event-page.json`

`event-page.json` 覆盖四个运行实例、四种 `source_mode` 和
`L0-L4` 全部层级。

## 6. Session 与 SessionDiff

### 6.1 Session

这里的 `Session` 是学习流水线的确定性会话，不是根
`cam_automation.models.Session` 当前用于 PowerMill parser 的内部
容器。

| 字段 | 类型 | 必需 | 冻结含义 |
| --- | --- | --- | --- |
| `schema_version` | integer | 是 | 固定为 `1` |
| `session_id` | string | 是 | 确定性会话 ID |
| `product` | string | 是 | 单一产品 |
| `instance_id` | string | 是 | 单一实例 |
| `project_id` | string 或 null | 是 | 未映射时为 null |
| `view_level` | string | 是 | 固定为 `L2` |
| `source_modes` | array[string] | 是 | 会话包含的来源模式 |
| `started_at` | string | 是 | 最早事件时间 |
| `ended_at` | string | 是 | 最晚事件时间 |
| `event_count` | integer | 是 | 必须等于 `event_refs.length` |
| `event_refs` | array[EventRef] | 是 | 有序来源引用 |
| `explicit_marker` | boolean | 是 | 是否由用户/源明确标记边界 |
| `labels` | array[string] | 否 | 例如 `expert`、`routine` |
| `metadata` | object | 否 | 非权威说明 |

`EventRef` 固定为：

```json
{
  "event_session_id": "nx-session-a-source",
  "seq": 0
}
```

会话不得跨 `product` 或 `instance_id`。默认分组必须同时考虑实例、
项目、显式边界和时间；相同有序输入必须生成相同会话 ID 与顺序。

### 6.2 SessionDiff

| 字段 | 类型 | 必需 | 冻结含义 |
| --- | --- | --- | --- |
| `schema_version` | integer | 是 | 固定为 `1` |
| `diff_id` | string | 是 | 确定性或持久化差异 ID |
| `session_ids` | array[string] | 是 | `2..5` 个互异会话 |
| `baseline_session_id` | string | 是 | 必须属于 `session_ids` |
| `common_steps` | array[object] | 是 | 所有会话共有的有序动作 |
| `session_deltas` | array[object] | 是 | 每会话缺失和额外步骤 |
| `parameter_differences` | array[object] | 是 | 按参数/步骤组织的值差异 |
| `duration_ms_by_session` | object | 是 | session ID 到 duration 的映射 |
| `created_at` | string | 是 | 差异生成时间 |

`common_steps` 项至少包含 `step_key`、`order`、`action`；
`session_deltas` 项至少包含 `session_id`、`missing_step_keys`、
`extra_steps`；`parameter_differences` 项至少包含 `step_key`、
`parameter` 和 `values_by_session`。

示例：

- `examples/contracts/sessions.json`
- `examples/contracts/session-diff.json`

## 7. Recipe、RecipeStep 与 RecipeParameter

### 7.1 Recipe

| 字段 | 类型 | 必需 | 冻结含义 |
| --- | --- | --- | --- |
| `schema_version` | integer | 是 | 固定为 `1` |
| `recipe_id` | string | 是 | 配方逻辑身份 |
| `recipe_hash` | string | 是 | `sha256:<64 lowercase hex>` |
| `name` | string | 是 | 用户可编辑显示名 |
| `product` | string | 是 | 单一目标产品 |
| `status` | string | 是 | `draft`、`review_required`、`approved_for_simulation`、`rejected`、`retired` |
| `target_versions` | array[string] | 是 | 已声明适配的目标版本；未知时为空数组 |
| `source_session_ids` | array[string] | 是 | 证据会话 |
| `support` | object | 是 | `matched_sessions`、`total_sessions`、`ratio` |
| `parameters` | array[RecipeParameter] | 是 | 参数声明 |
| `steps` | array[RecipeStep] | 是 | 按 `order` 排序 |
| `required_gates` | array[string] | 是 | 至少包含审阅、仿真、碰撞和人工批准 |
| `created_at` | string | 是 | 创建时间 |
| `updated_at` | string | 是 | 最近编辑时间 |
| `description` | string | 否 | 用户说明 |
| `project_conditions` | object | 否 | 项目/材料/单位等显式前置条件 |
| `branches` | array[object] | 否 | 有证据支持的条件分支 |

`recipe_hash` 是语义哈希，不是文件哈希。生产者必须：

1. 构造只含 `schema_version`、`product`、排序后的
   `target_versions`、`project_conditions`、按 name 排序的参数语义、
   按 order 排序的步骤语义和排序后的 `required_gates` 的对象。
2. 排除 `recipe_id`、`recipe_hash`、显示名、状态、时间、支持度、
   来源引用和审阅批注。
3. 使用 RFC 8785 JSON Canonicalization Scheme 序列化。
4. 对 UTF-8 字节计算 SHA-256，并输出 `sha256:<lowercase hex>`。

任何影响运行含义的参数、条件、步骤顺序、启停、动作、arguments、
版本或门禁变化都必须改变 hash。

### 7.2 RecipeParameter

| 字段 | 类型 | 必需 | 冻结含义 |
| --- | --- | --- | --- |
| `name` | string | 是 | 配方内唯一，`^[a-z][a-z0-9_]*$` |
| `value_type` | string | 是 | `string`、`integer`、`number`、`boolean`、`enum`、`path`、`object_selector` |
| `required` | boolean | 是 | 运行前是否必须有值 |
| `default` | 任意 JSON 值 | 是 | 类型必须匹配；允许显式 null |
| `samples` | array | 是 | 去重后的来源值 |
| `description` | string | 是 | 人可读含义 |
| `constraints` | object | 否 | min/max/pattern/units 等 |
| `enum_values` | array | 否 | `value_type=enum` 时使用 |
| `source_event_refs` | array[EventRef] | 否 | 参数证据 |

### 7.3 RecipeStep

| 字段 | 类型 | 必需 | 冻结含义 |
| --- | --- | --- | --- |
| `step_id` | string | 是 | 配方内稳定且唯一 |
| `order` | integer | 是 | 从 `1` 开始、无重复 |
| `action` | string | 是 | `cam.*`、`nx.*` 或 `powermill.*` |
| `enabled` | boolean | 是 | 是否进入预览 |
| `risk` | string | 是 | `safe`、`review`、`blocked` |
| `review_status` | string | 是 | `unreviewed`、`needs_review`、`accepted`、`rejected` |
| `arguments` | object | 是 | 产品中立的类型化参数绑定 |
| `condition` | object 或 null | 是 | 显式条件；无条件为 null |
| `source_event_refs` | array[EventRef] | 是 | 证据引用 |
| `notes` | string | 否 | 人工说明 |
| `adapter_payload` | object | 否 | 仅由目标产品 adapter 解释 |

`blocked` 步骤无论 `enabled` 或 `review_status` 为何都不得发送给
transport。共享 recipe service 不得读取 `adapter_payload` 中的
PowerMill/NX 语法。

示例：`examples/contracts/recipe.json`。

## 8. CommandTask、CommandResponse 与 DiffReport

### 8.1 CommandTask

| 字段 | 类型 | 必需 | 冻结含义 |
| --- | --- | --- | --- |
| `schema_version` | integer | 是 | 固定为 `1` |
| `task_id` | string | 是 | 全局唯一任务 ID |
| `task_type` | string | 是 | `query` 或 `recipe_preview` |
| `execution_mode` | string | 是 | `read_only` 或 `dry_run` |
| `product` | string | 是 | `nx` 或 `powermill` |
| `target_version` | string | 是 | 明确的目标 CAM 版本 |
| `target_instance_id` | string | 是 | 明确的运行期实例 |
| `project_id` | string | 是 | 明确的测试/快照项目 |
| `recipe_hash` | string 或 null | 是 | preview 必须非 null；query 可为 null |
| `operation` | string | 是 | namespaced adapter operation |
| `arguments` | object | 是 | 类型化参数 |
| `status` | string | 是 | `queued`、`running`、`succeeded`、`rejected`、`failed`、`cancelled`、`timed_out`、`disconnected` |
| `submitted_at` | string | 是 | 提交时间 |
| `timeout_ms` | integer | 是 | 正整数 |
| `requested_by` | string | 是 | 发起者身份 |
| `review` | object 或 null | 是 | preview 的人工审阅记录；query 可为 null |

`recipe_preview` 必须使用 `dry_run`、非空 `recipe_hash` 和已识别
reviewer。v1 不允许 `live`。任务进入 transport 前必须再次验证
product、version、instance、project 和 hash。

### 8.2 CommandResponse

| 字段 | 类型 | 必需 | 冻结含义 |
| --- | --- | --- | --- |
| `schema_version` | integer | 是 | 固定为 `1` |
| `response_id` | string | 是 | 唯一响应 ID |
| `task_id` | string | 是 | 对应任务 |
| `product` | string | 是 | 必须与任务一致 |
| `target_version` | string | 是 | 必须与任务一致 |
| `target_instance_id` | string | 是 | 必须与任务一致 |
| `project_id` | string | 是 | 必须与任务一致 |
| `status` | string | 是 | 与任务终态相同 |
| `started_at` | string | 是 | 开始时间 |
| `completed_at` | string | 是 | 完成时间 |
| `duration_ms` | integer | 是 | 非负耗时 |
| `raw_response` | string 或 null | 是 | 脱敏原始响应 |
| `structured_response` | object 或 null | 是 | adapter 结构化结果 |
| `diff_report` | DiffReport 或 null | 是 | query 可以为 null |
| `error` | object 或 null | 是 | 失败码与消息 |

`status=succeeded` 只表示 transport 完成，不代表仿真、碰撞或车间
批准通过。响应不得包含 machine-ready NC 内容；adapter 遇到该内容
必须拒绝、丢弃敏感 payload 并返回结构化错误。

### 8.3 DiffReport

| 字段 | 类型 | 必需 | 冻结含义 |
| --- | --- | --- | --- |
| `schema_version` | integer | 是 | 固定为 `1` |
| `diff_id` | string | 是 | 唯一差异 ID |
| `task_id` | string | 是 | 对应命令任务 |
| `product` | string | 是 | 目标产品 |
| `target_instance_id` | string | 是 | 目标实例 |
| `project_id` | string | 是 | 目标测试项目 |
| `recipe_hash` | string | 是 | 已审阅语义版本 |
| `status` | string | 是 | `no_change`、`changes_detected`、`unavailable`、`failed` |
| `before_snapshot` | object 或 null | 是 | preview 前快照引用 |
| `after_snapshot` | object 或 null | 是 | preview 后快照引用 |
| `changes` | array[object] | 是 | path/kind/before/after/severity |
| `gate_results` | array[object] | 是 | 门禁状态和证据 |
| `summary` | string | 是 | 人可读摘要 |

门禁状态只允许 `passed`、`failed`、`required`、`not_run`、
`unavailable`。未实际运行的仿真或碰撞检查必须写 `not_run` 或
`required`，不得推断为 `passed`。

示例：

- `examples/contracts/command-task.json`
- `examples/contracts/command-response.json`
- `examples/contracts/diff-report.json`

## 9. CodexReviewRequest 与 CodexReviewResult

### 9.1 CodexReviewRequest

| 字段 | 类型 | 必需 | 冻结含义 |
| --- | --- | --- | --- |
| `schema_version` | integer | 是 | 固定为 `1` |
| `protocol` | string | 是 | 固定 `cam.codex.bridge.v1` |
| `request_id` | string | 是 | 唯一请求 ID |
| `request_type` | string | 是 | 固定 `workflow_review` |
| `created_at` | string | 是 | 请求时间 |
| `execution_mode` | string | 是 | 固定 `dry-run` |
| `product` | string | 是 | 目标产品 |
| `target_version` | string | 是 | 待审目标版本 |
| `recipe` | Recipe | 是 | 完整结构化配方 |
| `source_events` | array[ActivityEvent] | 是 | 最小必要证据 |
| `session_diff` | SessionDiff 或 null | 是 | 无对比时为 null |
| `questions` | array[string] | 是 | 发起人待确认问题 |
| `context_metadata` | object | 否 | 脱敏、大小和来源说明 |

请求不得包含未脱敏项目路径、凭据、machine-ready NC 或要求 Codex
直接执行 CAM 的指令。

### 9.2 CodexReviewResult

| 字段 | 类型 | 必需 | 冻结含义 |
| --- | --- | --- | --- |
| `schema_version` | integer | 是 | 固定为 `1` |
| `protocol` | string | 是 | 固定 `cam.codex.bridge.v1` |
| `request_id` | string | 是 | 必须匹配请求 |
| `review_status` | string | 是 | `needs_review`、`needs_changes`、`approved_for_simulation`、`rejected` |
| `findings` | array[object] | 是 | 结构化 finding |
| `suggested_parameters` | array[object] | 是 | 结构化参数建议 |
| `questions` | array[object] | 是 | 结构化待确认问题 |
| `summary` | string | 是 | 审阅摘要 |
| `required_gates` | array[string] | 是 | 后续门禁 |
| `reviewer` | string | 是 | 审阅器/模型标识 |
| `reviewed_at` | string | 是 | 审阅时间 |

finding 至少包含 `finding_id`、`severity`、`code`、`message`、
`step_ids`、`evidence_refs`；severity 为 `info`、`warning`、
`error` 或 `blocker`。

参数建议至少包含 `name`、`proposed_value`、`rationale`、
`confidence`、`evidence_refs`，confidence 范围为 `0..1`。

question 至少包含 `question_id`、`text` 和 `required`。结果必须按
JSON 结构解析，不得依赖自由文本正则。

示例：

- `examples/contracts/codex-review-request.json`
- `examples/contracts/codex-review-result.json`

## 10. Fixture 清单

| 文件 | 覆盖合同 |
| --- | --- |
| `activity-event.json` | ActivityEvent 既有字段与可选扩展 |
| `instance-descriptors.json` | 四个 InstanceDescriptor；双 NX、双 PowerMill |
| `event-query.json` | EventQuery 组合过滤和 cursor |
| `event-page.json` | EventPage、多实例、四模式、L0-L4 |
| `sessions.json` | 四个 Session |
| `session-diff.json` | 两会话 SessionDiff |
| `recipe.json` | Recipe、RecipeStep、RecipeParameter |
| `command-task.json` | 绑定版本、实例、项目和 hash 的 dry-run |
| `command-response.json` | CommandResponse 与内嵌 DiffReport |
| `diff-report.json` | 独立 DiffReport 和未完成门禁 |
| `codex-review-request.json` | 结构化上下文包 |
| `codex-review-result.json` | findings/parameters/questions 结构化结果 |

Fixture 是合同示例，不声明真实 PowerMill/NX 连接、仿真或现场验证
已经完成。

## 11. 固定起点基线盘点

### 11.1 根 API 路由

当前 `cam_automation/web_server.py` 已实现：

| 方法 | 路由 |
| --- | --- |
| GET | `/api/health` |
| GET | `/api/plugins` |
| GET | `/api/sample` |
| GET | `/api/capabilities` |
| GET | `/api/connections` |
| GET | `/api/recorder` |
| GET | `/api/recorder/events` |
| GET | `/api/recorder/export` |
| GET | `/api/state` |
| POST | `/api/learn` |
| POST | `/api/analyze` |
| POST | `/api/codex/context` |
| POST | `/api/codex/review` |
| POST | `/api/plugins/install` |
| POST | `/api/plugins/uninstall` |
| POST | `/api/recorder/consent` |
| POST | `/api/recorder/control` |
| POST | `/api/recorder/settings` |
| POST | `/api/recorder/label` |
| POST | `/api/recorder/scan` |
| POST | `/api/recorder/clear` |
| POST | `/api/execution/run` |

未实现的计划路由包括 sessions、session compare、workflow mine、
recipe CRUD、异步 execution task status 和 diagnostics。

### 11.2 插件 manifests

| ID | 版本 | 主要 features | dependencies |
| --- | --- | --- | --- |
| `ug-cam-copilot` | `0.2.0` | `analysis:nx`、`preview:nx`、`connection:nx` | 无 |
| `powermill-cam-copilot` | `0.1.0` | `analysis:powermill`、`preview:powermill`、`connection:powermill` | 无 |
| `cam-local-capture` | `0.2.0` | local capture、log filter、multi-instance | 无 |
| `cam-execution-gateway` | `0.2.0` | dry-run、target-instance、execution audit | `cam-local-capture` |
| `cam-codex-review` | `0.1.0` | Codex review、context export | 无 |

当前只有应用内 `app-plugin.json`，没有根或插件内
`.codex-plugin/plugin.json`。

### 11.3 当前 Skills

| 所属插件 | Skill | 当前边界 |
| --- | --- | --- |
| `ug-cam-copilot` | `nx-workflow-learning` | NX Journal 静态解析和 review-first 草稿 |
| `ug-cam-copilot` | `powermill-workflow-learning` | 旧的 PowerMill 离线演示 Skill |
| `powermill-cam-copilot` | `powermill-cam-workflow-learning` | 根 PowerMill parser 的离线学习包装 |

NX Skill 明确禁止 PowerMill 输入进入 NX parser；两个 PowerMill
Skill 目前重叠，Wave 2 才处理 manifest、版本和 discoverability，
Wave 0 不调整。

### 11.4 测试基线

系统 PATH 上的 `python` 是不可用的 WindowsApps 执行别名，两条原始
`python -m unittest ...` 命令无输出并以退出码 1 结束。使用当前
Codex workspace bundled Python
`C:\Users\s3272\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`
执行同等命令：

```powershell
& 'C:\Users\s3272\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -s tests -v
& 'C:\Users\s3272\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m unittest discover -s plugins/ug-cam-copilot/tests -v
```

固定起点结果：

- 根仓库：37 tests，全部通过。
- UG/NX 插件：9 tests，全部通过。
- 合计：46 tests，全部通过。

### 11.5 已知未实现项

- 根和 UG 的 ActivityEvent 模型/schema 尚无本合同可选字段；
  `execution_audit` 尚不是 UG 模型允许的 source mode。
- 当前 recorder 只有 `limit + product` recent query，没有
  `EventQuery` 组合过滤和稳定 cursor `EventPage`。
- 当前连接发现返回 dict，没有完整 `InstanceDescriptor` DTO、
  target version、project mapping 或产品 transport。
- 当前根 `Session` 是 parser 内部容器，没有确定性 session service、
  merge/split 或 `SessionDiff`。
- 当前 `WorkflowRecipe` 是 `schema_version=0.1` 的离线产物，没有
  v1 recipe service、版本、分支或稳定 `recipe_hash`。
- PowerMill 尚无隔离的 `.mac/.log/JSONL` adapter protocol 和查询
  transport；live API 未配置。
- NX parser 保持 AST 静态解析，但尚无目标版本 stubs contract、
  transport 或脆弱 `FindObject` 选择器建议 DTO。
- 当前 execution gateway 是同步请求，没有 `CommandTask` 队列、
  取消、超时状态机、结果缓存或 `DiffReport` service。
- 当前 Codex bridge 只校验字符串 findings/gates，没有结构化
  suggested parameters、questions、session diff 或统一 review DTO。
- sessions、recipes、task status 和 diagnostics API 尚未实现。
- `.codex-plugin` manifest、marketplace 元数据、Skill cachebuster 和
  重新安装可发现性检查属于 Wave 2，当前未实现。
- 真实 PowerMill/NX 多版本连接、CAM 仿真、碰撞检查和车间批准均
  未在此离线基线上验证。

## 12. Wave 1 四任务共同基线

四个 Wave 1 worktree 必须从本 Wave 0 提交的同一 SHA 创建，并先
读取本文。共同不变量：

1. ActivityEvent 只新增本文列出的可选字段；旧 JSONL 必须继续导入
   和往返。
2. `source_mode` 和 `view_level` 是独立过滤维度；
   `execution_audit` 对旧 `mode` 镜像为 `automation`。
3. 共享动作只用 `cam.*`，新 NX/PowerMill 专属动作分别只用
   `nx.*` / `powermill.*`。
4. NX parser 和 PowerMill parser 保持物理与语义隔离。
5. session 不跨产品或实例；recipe hash 遵循第 7 节。
6. command 必须绑定 product、target version、target instance、
   project 和 recipe hash，并保持 read-only/dry-run。
7. 所有学习和生成结果 review-first；不得生成或发送 machine-ready
   NC。
8. 未在真实 CAM 环境验证的能力必须明确标为 fixture/offline。

任务交付边界：

| 任务 | 只负责 | 必须交付给后续任务 |
| --- | --- | --- |
| W1-A A-REC | ActivityEvent 扩展、记录状态、EventQuery/EventPage、cursor | `RecorderService.status/query_events/pause/resume/scan_now` |
| W1-B A-PM | InstanceDescriptor、多实例、独立 PowerMill adapter/transport | `ConnectionMonitor`、`PowerMillAdapter`、`PowerMillTransport` |
| W1-C A-NX | NX Journal AST、stubs contract、NX transport、选择器提示 | `NxJournalAdapter`、`NxVersionContract`、`NxTransport` |
| W1-D A-LEARN | Session/SessionDiff、重复流程、Recipe/hash/preview protocol | `SessionService`、`WorkflowLearner`、`RecipeService` |

Wave 1 不得修改其他任务的独占文件。任何合同冲突先报告 COORD；
不能通过复制 DTO、修改公共入口或把产品语法放入共享层解决。
