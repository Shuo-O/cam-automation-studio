(function attachCamFixture(global) {
  "use strict";

  const plugins = [
    {
      id: "cam-local-capture",
      name: "本地记录",
      version: "0.2.0",
      latest_version: "0.2.0",
      category: "基础能力",
      description: "增量记录日志、实例映射和执行审计，仅保存在本机。",
      features: ["日志增量记录", "实例发现", "执行审计"],
      dependencies: []
    },
    {
      id: "ug-cam-copilot",
      name: "UG / NX 学习",
      version: "0.2.0",
      latest_version: "0.2.0",
      category: "业务模块",
      description: "从 NX Journal 与 NXOpen 事件整理会话和配方候选。",
      features: ["NX 会话", "配方预览", "版本约束"],
      dependencies: ["cam-local-capture"]
    },
    {
      id: "powermill-cam-copilot",
      name: "PowerMill 学习",
      version: "0.1.0",
      latest_version: "0.2.0",
      category: "业务模块",
      description: "从宏、命令和结构化响应中发现可复用工作流。",
      features: ["宏解析", "工作流学习", "项目快照"],
      dependencies: ["cam-local-capture"]
    },
    {
      id: "cam-execution-gateway",
      name: "命令与诊断",
      version: "0.2.0",
      latest_version: "0.2.0",
      category: "受控能力",
      description: "按明确实例提交只读查询和 dry-run，返回差异与队列状态。",
      features: ["只读查询", "dry-run", "DiffReport"],
      dependencies: ["cam-local-capture"]
    },
    {
      id: "cam-codex-review",
      name: "Codex 审阅",
      version: "0.1.0",
      latest_version: "0.1.0",
      category: "审阅能力",
      description: "交换脱敏的结构化配方上下文与审阅结论。",
      features: ["结构化发现", "参数建议", "审阅门禁"],
      dependencies: []
    }
  ];

  const bundles = [
    {
      id: "expert-learning",
      name: "专家学习组合",
      description: "PowerMill + NX + 本地记录 + 命令 dry-run + Codex 审阅",
      plugin_ids: plugins.map((plugin) => plugin.id)
    },
    {
      id: "nx-starter",
      name: "NX 记录组合",
      description: "本地记录、NX 学习与 Codex 审阅",
      plugin_ids: ["cam-local-capture", "ug-cam-copilot", "cam-codex-review"]
    }
  ];

  const instances = [
    {
      schema_version: 1,
      instance_id: "codex:local:1",
      product: "codex",
      pid: 22840,
      process_name: "Codex.exe",
      window_handle: "0x00C01",
      window_title: "Codex - CAM Automation Studio",
      is_foreground: false,
      window_state: "visible",
      target_version: "GPT-5",
      project_id: "cam-studio",
      project_name: "CAM Automation Studio",
      connection_status: "connected",
      connection_response: "42 ms",
      capabilities: ["review.context", "review.result"],
      discovered_at: "2026-08-23T08:59:50Z",
      last_seen_at: "2026-08-24T08:15:12Z",
      metadata: { headless: false, transport: "local_fixture" }
    },
    {
      schema_version: 1,
      instance_id: "nx:3101:A1",
      product: "nx",
      pid: 3101,
      process_name: "ugraf.exe",
      window_handle: "0x00A1",
      window_title: "NX 2406 - Cavity A",
      is_foreground: true,
      window_state: "foreground",
      target_version: "NX 2406",
      project_id: "nx-project-a",
      project_name: "Cavity A",
      connection_status: "connected",
      connection_response: "86 ms",
      capabilities: ["journal.parse", "session.query", "project.snapshot"],
      discovered_at: "2026-08-23T08:59:52Z",
      last_seen_at: "2026-08-24T08:15:11Z",
      metadata: { headless: false, transport: "fixture" }
    },
    {
      schema_version: 1,
      instance_id: "nx:3102:B2",
      product: "nx",
      pid: 3102,
      process_name: "ugraf.exe",
      window_handle: "0x00B2",
      window_title: "NX 2312 - Electrode B",
      is_foreground: false,
      window_state: "visible",
      target_version: "NX 2312",
      project_id: "nx-project-b",
      project_name: "Electrode B",
      connection_status: "disconnected",
      connection_response: "无响应 · 5.0 s",
      capabilities: ["journal.parse"],
      discovered_at: "2026-08-23T08:59:53Z",
      last_seen_at: "2026-08-24T08:12:06Z",
      metadata: { headless: false, transport: "fixture" }
    },
    {
      schema_version: 1,
      instance_id: "powermill:4101:C1",
      product: "powermill",
      pid: 4101,
      process_name: "pmill.exe",
      window_handle: "0x00C1",
      window_title: "Cavity A - PowerMill 2026",
      is_foreground: false,
      window_state: "visible",
      target_version: "PowerMill 2026",
      project_id: "pm-project-a",
      project_name: "Cavity A",
      connection_status: "connected",
      connection_response: "63 ms",
      capabilities: ["macro.parse", "fixture.query", "project.snapshot"],
      discovered_at: "2026-08-23T08:59:54Z",
      last_seen_at: "2026-08-24T08:15:10Z",
      metadata: { headless: false, transport: "fixture" }
    },
    {
      schema_version: 1,
      instance_id: "powermill:4102:process",
      product: "powermill",
      pid: 4102,
      process_name: "pmill.exe",
      window_handle: null,
      window_title: "",
      is_foreground: false,
      window_state: "background",
      target_version: "PowerMill 2025",
      project_id: null,
      project_name: null,
      connection_status: "detected",
      connection_response: "只发现进程",
      capabilities: ["macro.parse"],
      discovered_at: "2026-08-23T08:59:56Z",
      last_seen_at: "2026-08-24T08:15:08Z",
      metadata: { headless: true, transport: "fixture" }
    }
  ];

  const sourceModes = ["manual", "automation", "system", "execution_audit"];
  const viewLevels = ["L0", "L1", "L2", "L3", "L4"];
  const actions = [
    "cam.operation.create",
    "cam.parameter.set",
    "cam.toolpath.generate",
    "nx.builder.commit",
    "powermill.toolpath.calculate",
    "cam.session.observed",
    "cam.recipe.candidate",
    "cam.execution.preview.audit"
  ];
  const projects = ["nx-project-a", "nx-project-b", "pm-project-a", "pm-project-b"];
  const productInstances = {
    nx: ["nx:3101:A1", "nx:3102:B2"],
    powermill: ["powermill:4101:C1", "powermill:4102:process"]
  };

  function buildEvents(count) {
    return Array.from({ length: count }, (_, index) => {
      const product = index % 5 < 3 ? "nx" : "powermill";
      const sourceMode = sourceModes[index % sourceModes.length];
      const viewLevel = viewLevels[(index * 3) % viewLevels.length];
      const instancePool = productInstances[product];
      const instanceId = instancePool[index % instancePool.length];
      const projectId = projects[index % projects.length];
      const minute = String(Math.floor(index / 60) % 60).padStart(2, "0");
      const second = String(index % 60).padStart(2, "0");
      const action = actions[index % actions.length];
      return {
        schema_version: 1,
        event_id: `event:${String(index + 1).padStart(4, "0")}`,
        session_id: `${product}-manual-${String((index % 8) + 1).padStart(2, "0")}`,
        seq: index,
        product,
        action,
        category: action.split(".")[1] || "activity",
        mode: sourceMode === "execution_audit" ? "automation" : sourceMode,
        source_mode: sourceMode,
        view_level: viewLevel,
        expertise_label: index % 7 === 0 ? "expert" : "routine",
        instance_id: instanceId,
        project_id: instanceId.includes("process") ? null : projectId,
        target_version: product === "nx" ? (index % 2 ? "NX 2312" : "NX 2406") : (index % 2 ? "PowerMill 2025" : "PowerMill 2026"),
        timestamp: `2026-08-23T${String(8 + (index % 10)).padStart(2, "0")}:${minute}:${second}Z`,
        duration_ms: 18 + ((index * 37) % 2400),
        source_file: product === "nx" ? `nx:<SOURCE_${(index % 4) + 1}>.py` : `powermill:<SOURCE_${(index % 4) + 1}>.mac`,
        source_line: 12 + (index % 96),
        review_status: viewLevel === "L4" ? "needs_review" : "unreviewed",
        recipe_hash: viewLevel === "L4" ? "sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08" : null,
        params: {
          operation_type: product === "nx" ? "mill_planar" : "area_clearance",
          tolerance: Number((0.015 + (index % 5) * 0.005).toFixed(3)),
          fixture_sequence: index + 1
        }
      };
    });
  }

  const sessions = [
    {
      session_id: "session:nx:a",
      product: "nx",
      instance_id: "nx:3101:A1",
      project_id: "nx-project-a",
      started_at: "2026-08-23T09:00:00Z",
      ended_at: "2026-08-23T09:08:21Z",
      event_count: 42,
      duration_ms: 501000,
      labels: ["expert"],
      source_modes: ["manual"],
      explicit_marker: true,
      steps: ["创建平面铣工序", "设置公差 0.020 mm", "生成刀路", "检查余量"]
    },
    {
      session_id: "session:nx:b",
      product: "nx",
      instance_id: "nx:3101:A1",
      project_id: "nx-project-a",
      started_at: "2026-08-23T10:00:00Z",
      ended_at: "2026-08-23T10:09:10Z",
      event_count: 47,
      duration_ms: 550000,
      labels: ["routine"],
      source_modes: ["manual"],
      explicit_marker: true,
      steps: ["创建平面铣工序", "设置公差 0.030 mm", "回退选择器", "生成刀路", "检查余量"]
    },
    {
      session_id: "session:nx:c",
      product: "nx",
      instance_id: "nx:3102:B2",
      project_id: "nx-project-b",
      started_at: "2026-08-23T11:00:00Z",
      ended_at: "2026-08-23T11:07:48Z",
      event_count: 39,
      duration_ms: 468000,
      labels: ["expert"],
      source_modes: ["manual"],
      explicit_marker: true,
      steps: ["创建平面铣工序", "设置公差 0.015 mm", "生成刀路", "检查余量"]
    },
    {
      session_id: "session:powermill:a",
      product: "powermill",
      instance_id: "powermill:4101:C1",
      project_id: "pm-project-a",
      started_at: "2026-08-23T12:00:00Z",
      ended_at: "2026-08-23T12:12:05Z",
      event_count: 56,
      duration_ms: 725000,
      labels: ["expert"],
      source_modes: ["manual", "execution_audit"],
      explicit_marker: true,
      steps: ["选择毛坯", "创建区域清除", "设置公差 0.050 mm", "计算刀路", "检查余量"]
    },
    {
      session_id: "session:powermill:b",
      product: "powermill",
      instance_id: "powermill:4102:process",
      project_id: null,
      started_at: "2026-08-23T13:00:00Z",
      ended_at: "2026-08-23T13:14:12Z",
      event_count: 61,
      duration_ms: 852000,
      labels: ["routine"],
      source_modes: ["manual"],
      explicit_marker: false,
      steps: ["选择毛坯", "创建区域清除", "修改边界", "设置公差 0.040 mm", "计算刀路", "重算刀路"]
    }
  ];

  const candidates = [
    {
      candidate_id: "candidate:nx:planar",
      name: "NX 平面铣工序",
      product: "nx",
      support: { matched_sessions: 3, total_sessions: 3, ratio: 1 },
      source_session_ids: ["session:nx:a", "session:nx:b", "session:nx:c"],
      main_flow: ["创建工序", "绑定稳定选择器", "设置公差", "生成刀路"],
      branches: ["NX 2312 使用旧 Builder family"],
      rework: ["session:nx:b · 回退录制对象 ID"],
      parameters: ["tolerance", "operation_selector"]
    },
    {
      candidate_id: "candidate:powermill:clearance",
      name: "PowerMill 区域清除",
      product: "powermill",
      support: { matched_sessions: 2, total_sessions: 2, ratio: 1 },
      source_session_ids: ["session:powermill:a", "session:powermill:b"],
      main_flow: ["选择毛坯", "创建区域清除", "设置公差", "计算刀路"],
      branches: ["有边界时应用边界条件"],
      rework: ["session:powermill:b · 修改边界后重算"],
      parameters: ["tolerance", "stepdown", "boundary_selector"]
    }
  ];

  const recipe = {
    schema_version: 1,
    recipe_id: "recipe:nx:planar-operation",
    recipe_hash: "sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    name: "NX 平面铣工序预览",
    product: "nx",
    status: "review_required",
    target_versions: ["NX 2312", "NX 2406"],
    source_session_ids: ["session:nx:a", "session:nx:b", "session:nx:c"],
    support: { matched_sessions: 3, total_sessions: 3, ratio: 1 },
    required_gates: ["recipe_review", "target_version_validation", "cam_simulation", "collision_check", "shop_approval"],
    parameters: [
      {
        name: "tolerance",
        value_type: "number",
        required: true,
        default: 0.02,
        samples: [0.015, 0.02, 0.03],
        description: "工序公差",
        constraints: { minimum: 0.001, maximum: 0.1, units: "mm" }
      },
      {
        name: "operation_selector",
        value_type: "object_selector",
        required: true,
        default: { strategy: "reviewed_name", value: "PLANAR_OP" },
        samples: [{ strategy: "recorded_id", value: "PLANAR_OP_A" }],
        description: "经审阅的稳定工序选择器",
        constraints: { allow_recorded_id: false }
      }
    ],
    steps: [
      {
        step_id: "step-001",
        order: 1,
        action: "cam.operation.create",
        enabled: true,
        risk: "review",
        review_status: "needs_review",
        arguments: { operation_type: "mill_planar", tolerance: { parameter: "tolerance" } },
        condition: null,
        source_event_refs: [{ event_session_id: "nx-manual-a", seq: 0 }, { event_session_id: "nx-manual-b", seq: 0 }],
        notes: "通过审阅选择器解析父级 CAM 组。"
      },
      {
        step_id: "step-002",
        order: 2,
        action: "cam.parameter.set",
        enabled: true,
        risk: "safe",
        review_status: "accepted",
        arguments: { tolerance: { parameter: "tolerance" } },
        condition: { all: [{ path: "project.units", equals: "mm" }] },
        source_event_refs: [{ event_session_id: "nx-manual-c", seq: 1 }],
        notes: "使用毫米单位约束。"
      },
      {
        step_id: "step-003",
        order: 3,
        action: "cam.toolpath.generate",
        enabled: true,
        risk: "review",
        review_status: "needs_review",
        arguments: { operation: { parameter: "operation_selector" } },
        condition: { all: [{ path: "step-001.review_status", equals: "accepted" }] },
        source_event_refs: [{ event_session_id: "nx-manual-a", seq: 2 }],
        notes: "仅预览，不保存部件，不生成 NC。"
      }
    ],
    project_conditions: { test_copy_required: true, units: "mm" },
    branches: [{ when: "target_version == 'NX 2312'", use: "legacy_builder_family" }]
  };

  const commandResponse = {
    schema_version: 1,
    response_id: "response-pm-preview-001",
    task_id: "task-pm-preview-001",
    product: "powermill",
    target_version: "PowerMill 2026",
    target_instance_id: "powermill:4101:C1",
    project_id: "pm-project-a",
    status: "succeeded",
    started_at: "2026-08-23T09:03:00.010Z",
    completed_at: "2026-08-23T09:03:00.155Z",
    duration_ms: 145,
    raw_response: "Fixture dry-run completed; no CAM command was sent.",
    structured_response: {
      transport: "fixture",
      execution_mode: "dry_run",
      objects_considered: 1,
      commands_sent: 0
    },
    diff_report: {
      schema_version: 1,
      diff_id: "diff-pm-preview-001",
      task_id: "task-pm-preview-001",
      product: "powermill",
      target_instance_id: "powermill:4101:C1",
      project_id: "pm-project-a",
      recipe_hash: "sha256:2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
      status: "changes_detected",
      before_snapshot: { snapshot_id: "snapshot-pm-a-before" },
      after_snapshot: { snapshot_id: "snapshot-pm-a-preview" },
      changes: [
        {
          path: "toolpaths/ROUGH_A/parameters/tolerance",
          kind: "proposed_update",
          before: 0.06,
          after: 0.05,
          severity: "review"
        }
      ],
      gate_results: [
        { gate: "recipe_review", status: "passed", evidence_refs: ["operator:reviewer-01"] },
        { gate: "target_version_validation", status: "passed", evidence_refs: ["fixture:powermill-2026"] },
        { gate: "cam_simulation", status: "not_run", evidence_refs: [] },
        { gate: "collision_check", status: "required", evidence_refs: [] },
        { gate: "shop_approval", status: "required", evidence_refs: [] }
      ],
      summary: "Dry-run 提议一处参数变化；仿真、碰撞检查和车间批准仍未完成。"
    },
    error: null
  };

  const diagnostics = {
    captured_events: 612,
    cursor: "cursor:2026-08-23T17:58:11Z:0612",
    log_lag_ms: 184,
    query_p95_ms: 76,
    queues: [
      { instance_id: "nx:3101:A1", queued: 0, running: 0, last_duration_ms: 121 },
      { instance_id: "nx:3102:B2", queued: 0, running: 0, last_duration_ms: null },
      { instance_id: "powermill:4101:C1", queued: 1, running: 0, last_duration_ms: 145 },
      { instance_id: "powermill:4102:process", queued: 0, running: 0, last_duration_ms: null }
    ],
    recent_tasks: [
      { task_id: "task-pm-preview-001", status: "succeeded", mode: "dry_run", duration_ms: 145 },
      { task_id: "task-nx-query-009", status: "timed_out", mode: "read_only", duration_ms: 5000 },
      { task_id: "task-pm-query-012", status: "cancelled", mode: "read_only", duration_ms: 73 }
    ]
  };

  const fixture = {
    schema_version: 1,
    transport: "fixed_frontend_fixture",
    generated_at: "2026-08-24T08:15:15Z",
    plugins,
    bundles,
    instances,
    events: buildEvents(612),
    sessions,
    candidates,
    recipe,
    command_response: commandResponse,
    diagnostics
  };

  global.CAM_FIXTURE = fixture;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = fixture;
  }
})(globalThis);
