(function startWorkbench() {
  "use strict";

  const fixture = globalThis.CAM_FIXTURE;
  const { icon } = globalThis.CAM_ICONS;
  if (!fixture || !icon) {
    document.body.textContent = "前端 fixture 加载失败。";
    return;
  }

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const esc = (value) =>
    String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");

  const elements = {
    appNav: $("#appNav"),
    navToggle: $("#navToggle"),
    navScrim: $("#navScrim"),
    connectionButton: $("#connectionButton"),
    connectionDrawer: $("#connectionDrawer"),
    drawerScrim: $("#drawerScrim"),
    closeDrawerButton: $("#closeDrawerButton"),
    drawerRefreshButton: $("#drawerRefreshButton"),
    instanceList: $("#instanceList"),
    refreshButton: $("#refreshButton"),
    settingsButton: $("#settingsButton"),
    coreEmptyState: $("#coreEmptyState"),
    installedCount: $("#installedCount"),
    updateCount: $("#updateCount"),
    navPluginCount: $("#navPluginCount"),
    pluginGrid: $("#pluginGrid"),
    pluginSearch: $("#pluginSearch"),
    bundleList: $("#bundleList"),
    recorderChip: $("#recorderChip"),
    recorderTopState: $("#recorderTopState"),
    installModal: $("#installModal"),
    modalScrim: $("#modalScrim"),
    installIntent: $("#installIntent"),
    authorizeInstallButton: $("#authorizeInstallButton"),
    cancelInstallButton: $("#cancelInstallButton"),
    declineInstallButton: $("#declineInstallButton"),
    settingsModal: $("#settingsModal"),
    closeSettingsButton: $("#closeSettingsButton"),
    permissionSettings: $("#permissionSettings"),
    toastRegion: $("#toastRegion"),
    lockedTitle: $("#lockedTitle"),
    lockedDescription: $("#lockedDescription"),
    openPluginsButton: $("#openPluginsButton"),
    sourceModeFilter: $("#sourceModeFilter"),
    levelFilter: $("#levelFilter"),
    manualOnlyButton: $("#manualOnlyButton"),
    productFilter: $("#productFilter"),
    instanceFilter: $("#instanceFilter"),
    projectFilter: $("#projectFilter"),
    actionFilter: $("#actionFilter"),
    fromTimeFilter: $("#fromTimeFilter"),
    toTimeFilter: $("#toTimeFilter"),
    textFilter: $("#textFilter"),
    clearFiltersButton: $("#clearFiltersButton"),
    eventRows: $("#eventRows"),
    eventState: $("#eventState"),
    eventTotal: $("#eventTotal"),
    cursorLabel: $("#cursorLabel"),
    paginationSummary: $("#paginationSummary"),
    previousPage: $("#previousPage"),
    nextPage: $("#nextPage"),
    pageIndicator: $("#pageIndicator"),
    pageSizeSelect: $("#pageSizeSelect"),
    sessionList: $("#sessionList"),
    sessionTimeline: $("#sessionTimeline"),
    timelineMeta: $("#timelineMeta"),
    timelineDuration: $("#timelineDuration"),
    sessionSelectionCount: $("#sessionSelectionCount"),
    compareCount: $("#compareCount"),
    compareSessionsButton: $("#compareSessionsButton"),
    markExpertButton: $("#markExpertButton"),
    splitSessionButton: $("#splitSessionButton"),
    mergeSessionsButton: $("#mergeSessionsButton"),
    comparisonPanel: $("#comparisonPanel"),
    comparisonTitle: $("#comparisonTitle"),
    comparisonGrid: $("#comparisonGrid"),
    candidateList: $("#candidateList"),
    candidateCount: $("#candidateCount"),
    candidateSessionCount: $("#candidateSessionCount"),
    recipeSteps: $("#recipeSteps"),
    recipeParameters: $("#recipeParameters"),
    recipeEvidence: $("#recipeEvidence"),
    recipeHash: $("#recipeHash"),
    copyHashButton: $("#copyHashButton"),
    commandProduct: $("#commandProduct"),
    commandInstance: $("#commandInstance"),
    commandVersion: $("#commandVersion"),
    commandProject: $("#commandProject"),
    commandHash: $("#commandHash"),
    commandOperation: $("#commandOperation"),
    commandArguments: $("#commandArguments"),
    runCommandButton: $("#runCommandButton"),
    queueList: $("#queueList"),
    queueTotal: $("#queueTotal"),
    commandResult: $("#commandResult"),
    commandResultBody: $("#commandResultBody"),
    resultTitle: $("#resultTitle"),
    commandStatusBadge: $("#commandStatusBadge"),
    healthList: $("#healthList"),
    taskHistory: $("#taskHistory"),
    stateMatrix: $("#stateMatrix"),
    exportDiagnosticsButton: $("#exportDiagnosticsButton"),
    flowStudio: $("#flowStudio"),
    flowInstallGate: $("#flowInstallGate"),
    flowOpenPluginsButton: $("#flowOpenPluginsButton")
  };

  const recorderLabels = {
    missing: "未安装",
    recording: "记录中",
    paused: "已暂停",
    source_interrupted: "来源中断",
    catching_up: "追赶中",
    error: "错误"
  };

  const permissionDefinitions = [
    { key: "logs", name: "日志记录", description: "增量读取本地日志；撤销后记录状态变为 paused。" },
    { key: "instances", name: "实例连接", description: "发现进程、窗口、版本和项目映射。" },
    { key: "dry_run", name: "命令 dry-run", description: "允许只读查询与 dry-run，不提供 live 模式。" },
    { key: "codex_review", name: "Codex 审阅", description: "仅由用户显式提交脱敏结构化上下文。" },
    { key: "data_export", name: "数据导出", description: "允许用户主动下载本地日志和诊断。" }
  ];

  const state = {
    apiReady: false,
    apiError: null,
    activeView: "plugins",
    installed: new Set(),
    installedVersions: new Map(fixture.plugins.map((plugin) => [plugin.id, plugin.version])),
    authorized: false,
    pendingInstallIds: [],
    pendingInstallLabel: "",
    permissions: Object.fromEntries(permissionDefinitions.map(({ key }) => [key, true])),
    recorderState: "missing",
    installing: false,
    log: {
      sourceMode: "all",
      viewLevel: "all",
      product: "all",
      instance: "all",
      project: "all",
      action: "all",
      fromTime: "",
      toTime: "",
      text: "",
      page: 1,
      pageSize: 25,
      loading: false,
      cursor: null,
      nextCursor: null,
      cursors: [null],
      hasMore: false,
      remote: false
    },
    activeSessionId: fixture.sessions[0].session_id,
    selectedSessions: new Set(fixture.sessions.slice(0, 3).map((session) => session.session_id)),
    expertSessions: new Set(
      fixture.sessions.filter((session) => session.labels.includes("expert")).map((session) => session.session_id)
    ),
    commandMode: "query",
    commandRunning: false,
    commandTaskId: null,
    recipeHash: fixture.recipe.recipe_hash,
    sessionDiff: null,
    codexReviewStatus: null,
    flowStudioInstance: null
  };

  async function requestJson(path, options = {}) {
    const request = {
      method: options.method || "GET",
      headers: { Accept: "application/json" }
    };
    if (options.body !== undefined) {
      request.headers["Content-Type"] = "application/json";
      request.body = JSON.stringify(options.body);
    }
    const response = await fetch(path, request);
    const payload = await response.json().catch(() => ({
      code: "invalid_response",
      error: "API returned a non-JSON response."
    }));
    if (!response.ok) {
      const error = new Error(payload.error || payload.message || `HTTP ${response.status}`);
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  function reportApiError(error, action) {
    state.apiError = error;
    const message = error?.payload?.error || error?.message || "本地 API 不可用";
    toast(`${action}失败`, message, "warning");
  }

  function mergePluginStatus(payload) {
    const byId = new Map(payload.plugins.map((plugin) => [plugin.id, plugin]));
    fixture.plugins.forEach((plugin) => {
      const current = byId.get(plugin.id);
      if (!current) {
        return;
      }
      plugin.name = current.name || plugin.name;
      plugin.description = current.summary || current.description || plugin.description;
      plugin.category = current.category || plugin.category;
      plugin.features = current.features || plugin.features;
      plugin.dependencies = current.dependencies || plugin.dependencies;
      plugin.version = current.version || plugin.version;
      plugin.latest_version = current.update?.available_version || current.version || plugin.latest_version;
      plugin.api = current;
    });
    state.installed = new Set(
      payload.plugins.filter((plugin) => plugin.installed).map((plugin) => plugin.id)
    );
    payload.plugins.forEach((plugin) => {
      if (plugin.installed) {
        state.installedVersions.set(
          plugin.id,
          plugin.update?.installed_version || plugin.version
        );
      }
    });
    const capture = byId.get("cam-local-capture");
    state.authorized = Boolean(capture?.authorization?.granted);
    if (!state.installed.has("cam-local-capture")) {
      state.recorderState = "missing";
    }
  }

  function normalizeInstances(payload) {
    fixture.instances = payload.instances.map((instance) => ({
      ...instance,
      connection_response: instance.last_response?.duration_ms == null
        ? instance.connection_status === "connected" ? "fixture ready" : "未建立连接"
        : `${instance.last_response.duration_ms} ms`,
      metadata: {
        headless: Boolean(instance.metadata?.headless),
        ...instance.metadata
      }
    }));
  }

  function normalizeSessions(sessions) {
    const fallbackSteps = [
      "创建 CAM 工序",
      "设置审阅参数",
      "生成刀路预览"
    ];
    return sessions.map((session) => {
      const started = Date.parse(session.started_at);
      const ended = Date.parse(session.ended_at);
      return {
        ...session,
        duration_ms: Number.isFinite(started) && Number.isFinite(ended)
          ? Math.max(0, ended - started)
          : 0,
        labels: session.labels || [],
        steps: session.steps || fallbackSteps
      };
    });
  }

  async function refreshConnections(force = false) {
    const payload = await requestJson(
      `/api/connections?include_uninstalled=1&refresh=${force ? 1 : 0}`
    );
    normalizeInstances(payload);
    renderInstances();
    populateLogFilters();
    renderCommandControls();
    return payload;
  }

  async function refreshDiagnostics() {
    const payload = await requestJson("/api/diagnostics");
    const instances = payload.diagnostics.instances || [];
    fixture.diagnostics.queues = instances.map((item) => ({
      instance_id: item.instance_id,
      queued: item.queue_length,
      running: item.running_count,
      last_duration_ms: item.duration?.count ? item.duration.average_ms : null
    }));
    fixture.diagnostics.api = payload.diagnostics;
    renderQueues();
    renderDiagnostics();
    return payload;
  }

  async function refreshSessions() {
    const payload = await requestJson("/api/sessions?limit=10000&sort=asc");
    fixture.sessions = normalizeSessions(payload.sessions);
    const existing = new Set(fixture.sessions.map((session) => session.session_id));
    state.selectedSessions = new Set(
      Array.from(state.selectedSessions).filter((sessionId) => existing.has(sessionId))
    );
    const nxSessions = fixture.sessions.filter((session) => session.product === "nx");
    if (state.selectedSessions.size < 2) {
      state.selectedSessions = new Set(
        nxSessions.slice(0, 3).map((session) => session.session_id)
      );
    }
    if (!existing.has(state.activeSessionId)) {
      state.activeSessionId = fixture.sessions[0]?.session_id || "";
    }
    state.expertSessions = new Set(
      fixture.sessions
        .filter((session) => session.labels.includes("expert"))
        .map((session) => session.session_id)
    );
    renderSessions();
    return payload;
  }

  function normalizeCandidates(candidates) {
    return candidates.map((candidate) => ({
      ...candidate,
      name: candidate.name || `${candidate.product === "nx" ? "NX" : "PowerMill"} 重复工作流`,
      main_flow: (candidate.common_steps || []).map((step) => step.action),
      branches: (candidate.branches || []).map((branch) =>
        typeof branch === "string" ? branch : JSON.stringify(branch)
      ),
      rework: (candidate.differences || []).map((difference) =>
        typeof difference === "string" ? difference : JSON.stringify(difference)
      ),
      parameters: (candidate.parameters || []).map((parameter) =>
        typeof parameter === "string" ? parameter : parameter.name
      )
    }));
  }

  async function ensureWorkflowAssets() {
    if (
      !state.installed.has("cam-local-capture")
      || !state.installed.has("ug-cam-copilot")
    ) {
      return;
    }
    const nxSessions = fixture.sessions
      .filter((session) => session.product === "nx" && session.source_modes.includes("manual"))
      .map((session) => session.session_id);
    if (nxSessions.length >= 3) {
      const learned = await requestJson("/api/workflows/mine", {
        method: "POST",
        body: { session_ids: nxSessions.slice(0, 5) }
      });
      fixture.candidates = normalizeCandidates(learned.candidates);
      renderCandidates();
    }

    const recipePayload = JSON.parse(JSON.stringify(fixture.recipe));
    recipePayload.source_session_ids = nxSessions.slice(0, 3);
    const saved = await requestJson("/api/recipes", {
      method: "POST",
      body: { recipe: recipePayload }
    });
    fixture.recipe = saved.recipe;
    state.recipeHash = saved.recipe.recipe_hash;
    renderRecipe();

    if (state.installed.has("cam-codex-review")) {
      const sourceEvent = fixture.events.find((event) => event.product === "nx");
      const sessionIds = nxSessions.slice(0, 3);
      const reviewRecipe = JSON.parse(JSON.stringify(fixture.recipe));
      reviewRecipe.description = "Review-first offline workflow draft.";
      reviewRecipe.parameters = reviewRecipe.parameters.filter(
        (parameter) => parameter.value_type !== "object_selector"
      );
      reviewRecipe.steps = reviewRecipe.steps.map((step) => ({
        ...step,
        notes: "Preview-only reviewed step."
      }));
      const context = {
        schema_version: 1,
        protocol: "cam.codex.bridge.v1",
        request_id: `codex-ui-${state.recipeHash.slice(7, 23)}`,
        request_type: "workflow_review",
        created_at: new Date().toISOString(),
        execution_mode: "dry-run",
        product: "nx",
        target_version: "NX 2406",
        recipe: reviewRecipe,
        source_events: sourceEvent ? [sourceEvent] : [],
        session_diff: state.sessionDiff || {
          schema_version: 1,
          diff_id: "session-diff:ui-fallback",
          session_ids: sessionIds,
          baseline_session_id: sessionIds[0],
          common_steps: [],
          session_deltas: sessionIds.map((sessionId) => ({
            session_id: sessionId,
            missing_step_keys: [],
            extra_steps: []
          })),
          parameter_differences: [],
          duration_ms_by_session: Object.fromEntries(sessionIds.map((sessionId) => [sessionId, 0])),
          created_at: new Date().toISOString()
        },
        questions: ["Confirm stable selectors before simulation."],
        context_metadata: {
          redaction: "redacted-local-v1",
          source_event_count: sourceEvent ? 1 : 0,
          machine_ready_nc_included: false
        }
      };
      const request = await requestJson("/api/codex/context", {
        method: "POST",
        body: { context }
      });
      const reviewed = await requestJson("/api/codex/review", {
        method: "POST",
        body: {
          review: {
            request_id: request.request_id,
            review_status: "needs_changes",
            findings: [
              "Stable object selectors and production safety gates still require human review."
            ],
            required_gates: [
              "cam_simulation",
              "collision_check",
              "shop_approval"
            ],
            reviewer: "Codex fixture reviewer",
            notes: "Structured offline review imported; no execution was authorized."
          }
        }
      });
      state.codexReviewStatus = reviewed.review_status;
      renderRecipe();
    }
  }

  async function hydrateFromApi({ force = false } = {}) {
    try {
      const plugins = await requestJson("/api/plugins");
      mergePluginStatus(plugins);
      await refreshConnections(force);
      if (state.installed.has("cam-local-capture")) {
        const recorder = await requestJson("/api/recorder");
        state.recorderState = recorder.state;
        state.permissions.logs = Boolean(recorder.enabled && recorder.categories.logs);
        state.permissions.instances = Boolean(recorder.categories.instances);
        await Promise.all([loadEventsFromApi(true), refreshSessions()]);
      }
      await refreshDiagnostics();
      state.apiReady = true;
      state.apiError = null;
      renderAll();
      if (fixture.sessions.length >= 2 && state.installed.has("cam-local-capture")) {
        await compareSessions(true);
      }
      if (state.installed.has("ug-cam-copilot")) {
        try {
          await ensureWorkflowAssets();
        } catch (error) {
          reportApiError(error, "构建审阅上下文");
        }
      }
    } catch (error) {
      state.apiReady = false;
      state.apiError = error;
      renderAll();
      reportApiError(error, "加载本地状态");
    }
  }

  function hydrateIcons(root = document) {
    $$("[data-icon]", root).forEach((target) => {
      const name = target.dataset.icon;
      target.innerHTML = icon(name);
      target.removeAttribute("data-icon");
    });
  }

  function toast(title, detail = "", tone = "success") {
    const node = document.createElement("div");
    node.className = `toast ${tone}`;
    node.innerHTML = `${icon(tone === "warning" ? "alert" : "check")}<div><strong>${esc(title)}</strong>${detail ? `<small>${esc(detail)}</small>` : ""}</div>`;
    elements.toastRegion.append(node);
    window.setTimeout(() => node.remove(), 3200);
  }

  function moduleAvailable(requirement) {
    if (!requirement) {
      return true;
    }
    return requirement.split("|").some((pluginId) => state.installed.has(pluginId));
  }

  function closeMobileNav() {
    elements.appNav.classList.remove("open");
    elements.navScrim.hidden = true;
  }

  function showView(view, options = {}) {
    const navItem = $(`.nav-item[data-view="${view}"]`);
    const requirement = navItem?.dataset.module;
    let panelName = view;
    if (!options.force && requirement && !moduleAvailable(requirement)) {
      panelName = "locked";
      const names = requirement
        .split("|")
        .map((id) => fixture.plugins.find((plugin) => plugin.id === id)?.name)
        .filter(Boolean)
        .join(" 或 ");
      elements.lockedTitle.textContent = `${viewLabel(view)}能力未安装`;
      elements.lockedDescription.textContent = `需要安装 ${names}。`;
    }

    $$("[data-view-panel]").forEach((panel) => {
      const active = panel.dataset.viewPanel === panelName;
      panel.hidden = !active;
      panel.classList.toggle("active", active);
    });
    $$(".nav-item").forEach((item) => {
      item.classList.toggle("active", item.dataset.view === view);
    });
    state.activeView = view;
    closeMobileNav();
    $(".main-content").scrollTop = 0;

    if (panelName === "logs") {
      loadLogView();
    }
  }

  function viewLabel(view) {
    return {
      logs: "日志",
      sessions: "会话",
      learning: "学习",
      recipe: "配方",
      command: "命令"
    }[view] || "业务";
  }

  function renderNavigation() {
    $$(".nav-item[data-module]").forEach((item) => {
      const available = moduleAvailable(item.dataset.module);
      item.classList.toggle("locked", !available);
      const lockIcon = item.lastElementChild;
      if (lockIcon) {
        lockIcon.style.visibility = available ? "hidden" : "visible";
      }
    });
    elements.navPluginCount.textContent = state.installed.size;
  }

  function renderFlowWorkspace() {
    const available = state.installed.has("powermill-cam-copilot");
    elements.flowInstallGate.hidden = available;
    elements.flowStudio.hidden = !available;
    elements.flowStudio.setAttribute("aria-busy", available ? "false" : "true");

    if (!available) {
      if (state.flowStudioInstance) {
        state.flowStudioInstance.destroy();
        state.flowStudioInstance = null;
      }
      return;
    }
    if (state.flowStudioInstance) {
      return;
    }
    const flowFixtures = globalThis.CAM_FLOW_FIXTURES;
    const flowStudio = globalThis.CAM_FLOW_STUDIO;
    if (!flowFixtures?.FixtureFlowApi || !flowStudio?.mount) {
      elements.flowStudio.hidden = true;
      elements.flowInstallGate.hidden = false;
      $("strong", elements.flowInstallGate).textContent = "流程工作台加载失败";
      $("p", elements.flowInstallGate).textContent = "Flow fixture 组件不可用，请刷新本地页面。";
      return;
    }
    state.flowStudioInstance = flowStudio.mount(elements.flowStudio, {
      api: new flowFixtures.FixtureFlowApi()
    });
  }

  function dependenciesFor(pluginIds) {
    const result = new Set(pluginIds);
    let changed = true;
    while (changed) {
      changed = false;
      Array.from(result).forEach((pluginId) => {
        const plugin = fixture.plugins.find((item) => item.id === pluginId);
        (plugin?.dependencies || []).forEach((dependency) => {
          if (!result.has(dependency)) {
            result.add(dependency);
            changed = true;
          }
        });
      });
    }
    return Array.from(result);
  }

  function queueInstall(pluginIds, label) {
    const ids = dependenciesFor(pluginIds);
    if (!state.authorized) {
      state.pendingInstallIds = Array.from(new Set(["cam-local-capture", ...ids]));
      state.pendingInstallLabel = label;
      elements.installIntent.textContent = `安装“${label}”，并默认启用所需本地能力。`;
      openModal(elements.installModal);
      return;
    }
    installPlugins(ids, label);
  }

  async function installPlugins(pluginIds, label) {
    if (state.installing) {
      return;
    }
    state.installing = true;
    renderPlugins();
    try {
      for (const pluginId of dependenciesFor(pluginIds)) {
        await requestJson("/api/plugins/install", {
          method: "POST",
          body: { plugin_id: pluginId }
        });
      }
      await hydrateFromApi({ force: true });
      toast("安装完成", `${label} · 本地记录已开启`);
    } catch (error) {
      reportApiError(error, "安装");
    } finally {
      state.installing = false;
      renderAll();
    }
  }

  async function uninstallPlugin(pluginId) {
    try {
      await requestJson("/api/plugins/uninstall", {
        method: "POST",
        body: { plugin_id: pluginId }
      });
      await hydrateFromApi({ force: true });
      toast("插件已卸载", fixture.plugins.find((plugin) => plugin.id === pluginId)?.name || pluginId, "warning");
      if (!moduleAvailable($(`.nav-item[data-view="${state.activeView}"]`)?.dataset.module)) {
        showView("plugins");
      }
    } catch (error) {
      reportApiError(error, "卸载");
    }
  }

  async function updatePlugin(pluginId) {
    const plugin = fixture.plugins.find((item) => item.id === pluginId);
    if (!plugin) {
      return;
    }
    try {
      await requestJson("/api/plugins/update", {
        method: "POST",
        body: { plugin_id: pluginId }
      });
      await hydrateFromApi();
      toast("更新检查完成", `${plugin.name} ${plugin.latest_version}`);
    } catch (error) {
      reportApiError(error, "更新");
    }
  }

  function renderPluginSummary() {
    const installedCount = state.installed.size;
    const updates = fixture.plugins.filter(
      (plugin) => state.installed.has(plugin.id) && state.installedVersions.get(plugin.id) !== plugin.latest_version
    ).length;
    elements.installedCount.textContent = installedCount;
    elements.updateCount.textContent = updates;
    elements.coreEmptyState.classList.toggle("installed", installedCount > 0);
    const title = $("strong", elements.coreEmptyState);
    const description = $("p", elements.coreEmptyState);
    const coreState = $(".core-state", elements.coreEmptyState);
    if (installedCount > 0) {
      title.textContent = "业务模块已启用";
      description.textContent = state.recorderState === "recording"
        ? "本地记录已开启 · 未发生静默上传。"
        : `记录状态：${recorderLabels[state.recorderState]}`;
      coreState.textContent = `${installedCount} MODULES`;
    } else {
      title.textContent = "业务模块尚未安装";
      description.textContent = "连接摘要、插件管理与诊断可用。";
      coreState.textContent = "CORE READY";
    }
  }

  function renderBundles() {
    elements.bundleList.innerHTML = fixture.bundles
      .map((bundle) => {
        const installed = bundle.plugin_ids.every((id) => state.installed.has(id));
        return `
          <article class="bundle-row">
            <div>
              <strong>${esc(bundle.name)}</strong>
              <p>${esc(bundle.description)}</p>
            </div>
            <div class="bundle-actions">
              ${installed
                ? '<span class="status-badge ready">已安装</span>'
                : `<button class="button primary compact" type="button" data-bundle-id="${esc(bundle.id)}">${icon("install")}安装组合</button>`}
            </div>
          </article>`;
      })
      .join("");
  }

  function renderPlugins() {
    const query = elements.pluginSearch.value.trim().toLowerCase();
    const matching = fixture.plugins.filter((plugin) =>
      `${plugin.name} ${plugin.id} ${plugin.description} ${plugin.features.join(" ")}`.toLowerCase().includes(query)
    );
    elements.pluginGrid.innerHTML = matching
      .map((plugin) => {
        const installed = state.installed.has(plugin.id);
        const currentVersion = state.installedVersions.get(plugin.id);
        const hasUpdate = installed && currentVersion !== plugin.latest_version;
        const dependencyText = plugin.dependencies.length
          ? `依赖 ${plugin.dependencies.map((id) => fixture.plugins.find((item) => item.id === id)?.name || id).join("、")}`
          : "无额外依赖";
        return `
          <article class="plugin-card ${installed ? "installed" : ""}">
            <div class="plugin-title">
              <span class="plugin-glyph">${icon(plugin.id.includes("capture") ? "database" : plugin.id.includes("execution") ? "terminal" : plugin.id.includes("codex") ? "shield" : "boxes")}</span>
              <div><strong>${esc(plugin.name)}</strong><span>${esc(plugin.id)} · ${esc(currentVersion)}</span></div>
            </div>
            <p>${esc(plugin.description)}</p>
            <div class="feature-list">${plugin.features.map((feature) => `<span>${esc(feature)}</span>`).join("")}</div>
            <div class="plugin-actions">
              <span class="plugin-status ${hasUpdate ? "update" : installed ? "ready" : ""}">${installed ? (hasUpdate ? `可更新 ${plugin.latest_version}` : "已就绪") : dependencyText}</span>
              <div>
                ${state.installing && !installed
                  ? '<button class="button quiet compact" type="button" disabled>安装中</button>'
                  : installed
                    ? `${hasUpdate ? `<button class="button quiet compact" type="button" data-plugin-action="update" data-plugin-id="${esc(plugin.id)}">${icon("refresh")}更新</button>` : ""}<button class="button danger compact" type="button" data-plugin-action="uninstall" data-plugin-id="${esc(plugin.id)}">${icon("trash")}卸载</button>`
                    : `<button class="button quiet compact" type="button" data-plugin-action="install" data-plugin-id="${esc(plugin.id)}">${icon("install")}安装</button>`}
              </div>
            </div>
          </article>`;
      })
      .join("");
  }

  function renderRecorder() {
    elements.recorderChip.className = `recorder-chip ${state.recorderState}`;
    elements.recorderTopState.textContent = recorderLabels[state.recorderState];
  }

  function renderPermissions() {
    elements.permissionSettings.innerHTML = permissionDefinitions
      .map(({ key, name, description }) => {
        const installed = state.authorized;
        const enabled = state.permissions[key] && installed;
        return `
          <div class="permission-setting">
            <div><strong>${esc(name)}</strong><small>${esc(description)}</small></div>
            <button class="toggle-button ${enabled ? "enabled" : ""}" type="button" data-permission="${esc(key)}" ${installed ? "" : "disabled"}>
              ${installed ? (enabled ? "撤销" : "恢复") : "未安装"}
            </button>
          </div>`;
      })
      .join("");
  }

  async function togglePermission(key) {
    if (!state.authorized || !(key in state.permissions)) {
      return;
    }
    const enabled = !state.permissions[key];
    try {
      if (key === "logs" && state.installed.has("cam-local-capture")) {
        const recorder = await requestJson("/api/recorder/control", {
          method: "POST",
          body: { enabled }
        });
        state.recorderState = recorder.state;
      } else if (key === "instances" && state.installed.has("cam-local-capture")) {
        await requestJson("/api/recorder/settings", {
          method: "POST",
          body: { detect_instances: enabled }
        });
        await refreshConnections(true);
      }
      state.permissions[key] = enabled;
    } catch (error) {
      reportApiError(error, enabled ? "恢复分类能力" : "撤销分类能力");
      return;
    }
    renderPermissions();
    renderRecorder();
    renderPluginSummary();
    renderCommandControls();
    toast(
      enabled ? "分类能力已恢复" : "分类能力已撤销",
      permissionDefinitions.find((item) => item.key === key)?.name || key,
      enabled ? "success" : "warning"
    );
  }

  function openModal(modal) {
    elements.modalScrim.hidden = false;
    modal.hidden = false;
    window.setTimeout(() => $("button:not([disabled])", modal)?.focus(), 0);
  }

  function closeModals() {
    elements.modalScrim.hidden = true;
    elements.installModal.hidden = true;
    elements.settingsModal.hidden = true;
    state.pendingInstallIds = [];
    state.pendingInstallLabel = "";
  }

  function renderInstances() {
    const productNames = { codex: "Codex", nx: "NX", powermill: "PowerMill" };
    const codexCount = $("#codexCount");
    const nxCount = $("#nxCount");
    const powermillCount = $("#powermillCount");
    if (codexCount) {
      codexCount.textContent = state.installed.has("cam-codex-review") ? "1" : "0";
    }
    if (nxCount) {
      nxCount.textContent = fixture.instances.filter((item) => item.product === "nx").length;
    }
    if (powermillCount) {
      powermillCount.textContent = fixture.instances.filter((item) => item.product === "powermill").length;
    }
    const connected = fixture.instances.filter((item) => item.connection_status === "connected").length;
    const detected = fixture.instances.filter((item) => item.connection_status === "detected").length;
    const unavailable = fixture.instances.length - connected - detected;
    const drawerSummary = $(".drawer-summary");
    if (drawerSummary) {
      drawerSummary.innerHTML = `
        <span><span class="status-dot ok"></span>${connected} 已连接</span>
        <span><span class="status-dot warn"></span>${detected} 仅检测</span>
        <span><span class="status-dot danger"></span>${unavailable} 断连</span>`;
    }
    elements.instanceList.innerHTML = fixture.instances
      .map((instance) => {
        const headless = Boolean(instance.metadata?.headless);
        const statusClass = instance.connection_status === "connected"
          ? "ready"
          : instance.connection_status === "disconnected"
            ? "disconnected"
            : "detected";
        return `
          <article class="instance-row">
            <div class="instance-heading">
              <span class="instance-product ${esc(instance.product)}">${instance.product === "powermill" ? "PM" : instance.product === "codex" ? "CX" : "NX"}</span>
              <div>
                <strong>${esc(productNames[instance.product])} · PID ${instance.pid}</strong>
                <small>${esc(instance.window_title || "无可见窗口")}</small>
              </div>
              <span class="status-badge ${statusClass}">${esc(instance.connection_status)}</span>
            </div>
            <dl class="instance-details">
              <div><dt>product</dt><dd>${esc(instance.product)}</dd></div>
              <div><dt>PID</dt><dd>${instance.pid}</dd></div>
              <div><dt>window</dt><dd>${esc(instance.window_handle || "none")}</dd></div>
              <div><dt>project</dt><dd title="${esc(instance.project_id || "未映射")}">${esc(instance.project_name || instance.project_id || "未映射")}</dd></div>
              <div><dt>version</dt><dd>${esc(instance.target_version || "未知")}</dd></div>
              <div><dt>foreground</dt><dd>${instance.is_foreground ? "yes" : "no"}</dd></div>
              <div><dt>headless</dt><dd>${headless ? "yes" : "no"}</dd></div>
              <div><dt>response</dt><dd>${esc(instance.connection_response)}</dd></div>
              <div><dt>last_seen</dt><dd>${esc(instance.last_seen_at.replace("2026-08-24T", "").replace("Z", ""))}</dd></div>
            </dl>
          </article>`;
      })
      .join("");
  }

  function openDrawer() {
    elements.drawerScrim.hidden = false;
    elements.connectionDrawer.hidden = false;
    elements.closeDrawerButton.focus();
  }

  function closeDrawer() {
    elements.drawerScrim.hidden = true;
    elements.connectionDrawer.hidden = true;
    elements.connectionButton.focus();
  }

  function populateLogFilters() {
    const options = (values, label) =>
      `<option value="all">${label}</option>${Array.from(new Set(values.filter(Boolean))).sort().map((value) => `<option value="${esc(value)}">${esc(value)}</option>`).join("")}`;
    elements.instanceFilter.innerHTML = options(fixture.events.map((event) => event.instance_id), "全部实例");
    elements.projectFilter.innerHTML = options(fixture.events.map((event) => event.project_id), "全部项目");
    elements.actionFilter.innerHTML = options(fixture.events.map((event) => event.action), "全部动作");
  }

  function applyLogFilters() {
    const from = state.log.fromTime ? new Date(state.log.fromTime).getTime() : null;
    const to = state.log.toTime ? new Date(state.log.toTime).getTime() : null;
    const text = state.log.text.trim().toLowerCase();
    return fixture.events.filter((event) => {
      const eventTime = new Date(event.timestamp).getTime();
      return (
        (state.log.sourceMode === "all" || event.source_mode === state.log.sourceMode) &&
        (state.log.viewLevel === "all" || event.view_level === state.log.viewLevel) &&
        (state.log.product === "all" || event.product === state.log.product) &&
        (state.log.instance === "all" || event.instance_id === state.log.instance) &&
        (state.log.project === "all" || event.project_id === state.log.project) &&
        (state.log.action === "all" || event.action === state.log.action) &&
        (from === null || eventTime >= from) &&
        (to === null || eventTime <= to) &&
        (!text || JSON.stringify(event).toLowerCase().includes(text))
      );
    });
  }

  async function loadEventsFromApi(reset = false) {
    if (!state.installed.has("cam-local-capture")) {
      return;
    }
    if (reset) {
      state.log.page = 1;
      state.log.cursor = null;
      state.log.nextCursor = null;
      state.log.cursors = [null];
    }
    const params = new URLSearchParams({
      limit: String(state.log.pageSize),
      sort: "asc"
    });
    const fields = {
      source_mode: state.log.sourceMode,
      view_level: state.log.viewLevel,
      product: state.log.product,
      instance_id: state.log.instance,
      project_id: state.log.project,
      action: state.log.action
    };
    Object.entries(fields).forEach(([name, value]) => {
      if (value && value !== "all") {
        params.set(name, value);
      }
    });
    if (state.log.fromTime) {
      params.set("from_time", new Date(state.log.fromTime).toISOString());
    }
    if (state.log.toTime) {
      params.set("to_time", new Date(state.log.toTime).toISOString());
    }
    if (state.log.text.trim()) {
      params.set("text", state.log.text.trim());
    }
    if (state.log.cursor) {
      params.set("cursor", state.log.cursor);
    }
    state.log.loading = true;
    renderEvents();
    try {
      const payload = await requestJson(`/api/recorder/events?${params}`);
      fixture.events = payload.events;
      state.log.nextCursor = payload.next_cursor;
      state.log.hasMore = payload.has_more;
      state.log.remote = true;
      elements.cursorLabel.textContent = payload.next_cursor
        ? payload.next_cursor.slice(-8)
        : "END";
    } finally {
      state.log.loading = false;
      renderEvents();
    }
  }

  function renderEvents() {
    if (state.log.loading) {
      elements.eventRows.innerHTML = "";
      elements.eventState.hidden = false;
      elements.eventState.innerHTML = `${icon("refresh")}<strong>正在加载固定事件页</strong>`;
      return;
    }

    const remote = state.log.remote && state.installed.has("cam-local-capture");
    const filtered = remote ? fixture.events : applyLogFilters();
    const pageCount = remote
      ? state.log.page + (state.log.hasMore ? 1 : 0)
      : Math.max(1, Math.ceil(filtered.length / state.log.pageSize));
    state.log.page = Math.min(Math.max(1, state.log.page), pageCount);
    const start = (state.log.page - 1) * state.log.pageSize;
    const page = remote ? filtered : filtered.slice(start, start + state.log.pageSize);
    elements.eventTotal.textContent = remote
      ? `${start + page.length}${state.log.hasMore ? "+" : ""}`
      : filtered.length;
    elements.eventRows.innerHTML = page
      .map((event) => {
        const eventId = event.event_id || `${event.session_id}:${event.seq}`;
        const time = String(event.timestamp || "").replace("2026-08-23T", "").replace("Z", "");
        const product = event.product === "nx" ? "NX" : "PowerMill";
        return `
          <tr data-event-id="${esc(eventId)}">
            <td><strong>${esc(time)}</strong><small>${esc(eventId)}</small></td>
            <td><strong>${product}</strong><small title="${esc(event.instance_id)}">${esc(event.instance_id)}</small></td>
            <td><span class="source-badge ${esc(event.source_mode)}">${esc(event.source_mode)}</span></td>
            <td><span class="level-badge">${esc(event.view_level)}</span></td>
            <td title="${esc(event.action)}"><code>${esc(event.action)}</code></td>
            <td title="${esc(event.project_id || "未映射")}">${esc(event.project_id || "未映射")}</td>
            <td>${event.duration_ms == null ? "—" : `${event.duration_ms} ms`}</td>
          </tr>`;
      })
      .join("");

    elements.eventState.hidden = page.length > 0;
    if (!page.length) {
      elements.eventState.innerHTML = `${icon("search")}<strong>没有匹配结果</strong><span>当前筛选返回 0 条事件。</span>`;
    }
    elements.paginationSummary.textContent = page.length
      ? remote
        ? `${start + 1}-${start + page.length} · API 游标页 · DOM ${page.length} 行`
        : `${start + 1}-${Math.min(start + page.length, filtered.length)} / ${filtered.length} 条 · DOM ${page.length} 行`
      : "0 条结果 · DOM 0 行";
    elements.pageIndicator.textContent = `${state.log.page} / ${pageCount}`;
    elements.previousPage.disabled = state.log.page <= 1;
    elements.nextPage.disabled = remote ? !state.log.hasMore : state.log.page >= pageCount;
    elements.manualOnlyButton.classList.toggle("primary", state.log.sourceMode === "manual");
    elements.manualOnlyButton.classList.toggle("quiet", state.log.sourceMode !== "manual");
  }

  async function loadLogView() {
    if (state.log.loading) {
      return;
    }
    if (state.apiReady && state.installed.has("cam-local-capture")) {
      try {
        await loadEventsFromApi(false);
      } catch (error) {
        reportApiError(error, "加载日志");
      }
      return;
    }
    renderEvents();
  }

  async function syncLogState() {
    state.log.sourceMode = elements.sourceModeFilter.value;
    state.log.product = elements.productFilter.value;
    state.log.instance = elements.instanceFilter.value;
    state.log.project = elements.projectFilter.value;
    state.log.action = elements.actionFilter.value;
    state.log.fromTime = elements.fromTimeFilter.value;
    state.log.toTime = elements.toTimeFilter.value;
    state.log.text = elements.textFilter.value;
    state.log.page = 1;
    if (state.apiReady && state.installed.has("cam-local-capture")) {
      try {
        await loadEventsFromApi(true);
      } catch (error) {
        reportApiError(error, "筛选日志");
      }
    } else {
      renderEvents();
    }
  }

  function clearLogFilters() {
    elements.sourceModeFilter.value = "all";
    elements.productFilter.value = "all";
    elements.instanceFilter.value = "all";
    elements.projectFilter.value = "all";
    elements.actionFilter.value = "all";
    elements.fromTimeFilter.value = "";
    elements.toTimeFilter.value = "";
    elements.textFilter.value = "";
    state.log.viewLevel = "all";
    $$(".segment", elements.levelFilter).forEach((button) => {
      button.classList.toggle("active", button.dataset.level === "all");
    });
    syncLogState();
  }

  function formatDuration(ms) {
    const minutes = Math.floor(ms / 60000);
    const seconds = Math.floor((ms % 60000) / 1000);
    return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }

  function renderSessions() {
    elements.sessionList.innerHTML = fixture.sessions
      .map((session) => {
        const selected = state.selectedSessions.has(session.session_id);
        const active = state.activeSessionId === session.session_id;
        const expert = state.expertSessions.has(session.session_id);
        const product = session.product === "nx" ? "NX" : "PowerMill";
        return `
          <article class="session-row ${active ? "active" : ""}">
            <input type="checkbox" data-session-select="${esc(session.session_id)}" aria-label="选择 ${esc(session.session_id)} 进行对比" ${selected ? "checked" : ""}>
            <div class="session-row-main">
              <button type="button" data-session-open="${esc(session.session_id)}">${esc(session.session_id)}</button>
              <span>${product} · ${esc(session.project_id || "项目未映射")}</span>
              <small>${session.event_count} 事件 · ${esc(session.source_modes.join(" + "))}</small>
            </div>
            <div class="session-row-meta">
              ${expert ? '<span class="status-badge ready">expert</span>' : '<span class="status-badge muted">routine</span>'}
              <span>${formatDuration(session.duration_ms)}</span>
            </div>
          </article>`;
      })
      .join("");
    renderTimeline();
    renderSessionSelection();
  }

  function renderTimeline() {
    const session = fixture.sessions.find((item) => item.session_id === state.activeSessionId) || fixture.sessions[0];
    const product = session.product === "nx" ? "NX" : "PowerMill";
    elements.timelineMeta.textContent = `${product} · ${session.project_id || "项目未映射"}`;
    elements.timelineDuration.textContent = formatDuration(session.duration_ms);
    elements.sessionTimeline.innerHTML = session.steps
      .map((step, index) => {
        const elapsed = Math.floor((session.duration_ms / Math.max(1, session.steps.length - 1)) * index);
        return `
          <li class="timeline-step">
            <time>+${formatDuration(elapsed)}</time>
            <div><strong>${esc(step)}</strong><small>${index === 0 ? session.instance_id : `event ${index + 1} · manual`}</small></div>
          </li>`;
      })
      .join("");
  }

  function renderSessionSelection() {
    const count = state.selectedSessions.size;
    elements.sessionSelectionCount.textContent = `已选 ${count}`;
    elements.compareCount.textContent = count;
    elements.compareSessionsButton.disabled = count < 2 || count > 5;
    elements.mergeSessionsButton.disabled = count < 2;
  }

  function toggleSessionSelection(sessionId, checked) {
    if (checked && state.selectedSessions.size >= 5) {
      toast("最多选择 5 个会话", "请先取消一个已选会话。", "warning");
      renderSessions();
      return;
    }
    if (checked) {
      state.selectedSessions.add(sessionId);
    } else {
      state.selectedSessions.delete(sessionId);
    }
    renderSessionSelection();
  }

  async function compareSessions(silent = false) {
    const selected = fixture.sessions.filter((session) => state.selectedSessions.has(session.session_id));
    if (selected.length < 2 || selected.length > 5) {
      toast("请选择 2-5 个会话", "", "warning");
      return;
    }
    const baseline = selected[0];
    if (new Set(selected.map((session) => session.product)).size !== 1) {
      toast("会话产品必须一致", "SessionDiff 不跨 NX 与 PowerMill。", "warning");
      return;
    }
    if (state.apiReady && state.installed.has("cam-local-capture")) {
      try {
        const payload = await requestJson("/api/sessions/compare", {
          method: "POST",
          body: {
            session_ids: selected.map((session) => session.session_id),
            baseline_session_id: baseline.session_id
          }
        });
        state.sessionDiff = payload.diff;
      } catch (error) {
        reportApiError(error, "会话对比");
        return;
      }
    }
    const toleranceValues = [0.02, 0.03, 0.015, 0.05, 0.04];
    const durationBySession = state.sessionDiff?.duration_ms_by_session || {};
    elements.comparisonTitle.textContent = `${selected.length} 会话对比`;
    $(".status-badge", elements.comparisonPanel).textContent = `基线 ${baseline.session_id}`;
    elements.comparisonGrid.style.setProperty("--compare-columns", selected.length);
    elements.comparisonGrid.innerHTML = selected
      .map((session, index) => `
        <article class="comparison-column">
          <h3>${esc(session.session_id)}</h3>
          <dl>
            <dt>工序创建</dt><dd>共同步骤</dd>
            <dt>公差</dt><dd>${toleranceValues[index].toFixed(3)} mm</dd>
            <dt>时长</dt><dd>${formatDuration(durationBySession[session.session_id] ?? session.duration_ms)}</dd>
            <dt>额外步骤</dt><dd>${session.steps.length > baseline.steps.length ? esc(session.steps.filter((step) => !baseline.steps.includes(step)).join("、") || "无") : "无"}</dd>
            <dt>标记</dt><dd>${state.expertSessions.has(session.session_id) ? "expert" : "routine"}</dd>
          </dl>
        </article>`)
      .join("");
    elements.comparisonPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    if (!silent) {
      toast("SessionDiff 已更新", `${selected.length} 个会话 · ${state.apiReady ? "本地 API" : "fixture 回退"}`);
    }
  }

  function renderCandidates() {
    const sourceSessions = new Set(
      fixture.candidates.flatMap((candidate) => candidate.source_session_ids || [])
    );
    elements.candidateCount.textContent = String(fixture.candidates.length);
    elements.candidateSessionCount.textContent = String(sourceSessions.size);
    elements.candidateList.innerHTML = fixture.candidates
      .map((candidate) => `
        <article class="candidate-row">
          <div>
            <span class="status-badge ${candidate.product === "nx" ? "ready" : "review"}">${candidate.product === "nx" ? "NX" : "PowerMill"}</span>
            <h2>${esc(candidate.name)}</h2>
            <p>${candidate.source_session_ids.length} 个来源 · ${esc(candidate.parameters.join("、"))}</p>
          </div>
          <div class="support-meter">
            <span class="section-kicker">支持度</span>
            <strong>${Math.round(candidate.support.ratio * 100)}%</strong>
            <div class="support-track"><span style="width:${candidate.support.ratio * 100}%"></span></div>
            <small>${candidate.support.matched_sessions} / ${candidate.support.total_sessions} 会话</small>
          </div>
          <div class="candidate-block">
            <h3>主流程</h3>
            <div class="candidate-flow">${candidate.main_flow.map((step) => `<span>${esc(step)}</span>`).join("")}</div>
          </div>
          <div class="candidate-block candidate-notes">
            <h3>分支 / 返工</h3>
            <div><strong>分支</strong><br>${esc(candidate.branches.join("；"))}</div>
            <div><strong>返工</strong><br>${esc(candidate.rework.join("；"))}</div>
          </div>
        </article>`)
      .join("");
  }

  function shortHash(hash) {
    return `${hash.slice(0, 11)}…${hash.slice(-5)}`;
  }

  function renderRecipe() {
    const recipeTitle = $("#recipeTitle");
    if (recipeTitle) {
      recipeTitle.textContent = fixture.recipe.name;
    }
    const reviewBadge = $(".recipe-header .status-badge");
    if (reviewBadge) {
      reviewBadge.textContent = state.codexReviewStatus
        ? `Codex ${state.codexReviewStatus}`
        : fixture.recipe.status;
    }
    elements.recipeHash.textContent = shortHash(state.recipeHash);
    elements.recipeHash.title = state.recipeHash;
    elements.recipeSteps.innerHTML = fixture.recipe.steps
      .map((step) => `
        <article class="recipe-step">
          <span class="step-order">${step.order}</span>
          <div>
            <strong>${esc(step.step_id)}</strong>
            <code>${esc(step.action)}</code>
            <small>${esc(step.notes)}</small>
            <span class="status-badge ${step.review_status === "accepted" ? "ready" : "review"}">${esc(step.review_status)}</span>
          </div>
          <input class="step-toggle" type="checkbox" data-recipe-step="${esc(step.step_id)}" aria-label="启用 ${esc(step.step_id)}" ${step.enabled ? "checked" : ""}>
        </article>`)
      .join("");
    elements.recipeParameters.innerHTML = fixture.recipe.parameters
      .map((parameter) => `
        <div class="parameter-row">
          <div><strong>${esc(parameter.name)}</strong><small>${esc(parameter.value_type)} · ${parameter.required ? "required" : "optional"}</small></div>
          <div><code class="parameter-value">${esc(typeof parameter.default === "object" ? JSON.stringify(parameter.default) : parameter.default)}</code><small>${esc(parameter.description)}</small></div>
        </div>`)
      .join("");
    elements.recipeEvidence.innerHTML = fixture.recipe.source_session_ids
      .map((sessionId, index) => `
        <article class="evidence-row">
          <strong>${esc(sessionId)}</strong>
          <span>${index + 1} 个关键事件引用 · expert ${index === 1 ? "no" : "yes"}</span>
          <code>${esc(fixture.recipe.steps[Math.min(index, fixture.recipe.steps.length - 1)]?.source_event_refs?.[0]?.event_session_id || sessionId)}:${index}</code>
          <div class="diff-line">${index === 0 ? "基线 tolerance 0.020 mm" : index === 1 ? "差异 tolerance 0.030 mm；包含选择器返工" : "差异 tolerance 0.015 mm"}</div>
        </article>`)
      .join("");
  }

  async function updateRecipeHash() {
    const enabled = $$(".step-toggle", elements.recipeSteps).map((toggle) => ({
      id: toggle.dataset.recipeStep,
      enabled: toggle.checked
    }));
    fixture.recipe.steps = fixture.recipe.steps.map((step) => ({
      ...step,
      enabled: enabled.find((item) => item.id === step.step_id)?.enabled ?? step.enabled
    }));
    if (state.apiReady && state.installed.has("ug-cam-copilot")) {
      try {
        const saved = await requestJson("/api/recipes", {
          method: "POST",
          body: { recipe: fixture.recipe }
        });
        fixture.recipe = saved.recipe;
        state.recipeHash = saved.recipe.recipe_hash;
        renderRecipe();
        toast("配方语义 hash 已更新", shortHash(state.recipeHash), "warning");
        return;
      } catch (error) {
        reportApiError(error, "保存配方版本");
        renderRecipe();
        return;
      }
    }
    const payload = JSON.stringify({
      schema_version: 1,
      product: fixture.recipe.product,
      target_versions: [...fixture.recipe.target_versions].sort(),
      parameters: fixture.recipe.parameters.map(({ name, default: defaultValue }) => ({ name, default: defaultValue })),
      steps: enabled,
      required_gates: [...fixture.recipe.required_gates].sort()
    });
    try {
      const bytes = new TextEncoder().encode(payload);
      const digest = await crypto.subtle.digest("SHA-256", bytes);
      state.recipeHash = `sha256:${Array.from(new Uint8Array(digest)).map((value) => value.toString(16).padStart(2, "0")).join("")}`;
    } catch {
      state.recipeHash = `sha256:${"1".repeat(64)}`;
    }
    elements.recipeHash.textContent = shortHash(state.recipeHash);
    elements.recipeHash.title = state.recipeHash;
    toast("配方语义 hash 已更新", shortHash(state.recipeHash), "warning");
  }

  function renderCommandControls() {
    if (state.commandMode === "dry_run") {
      elements.commandProduct.value = fixture.recipe.product;
    }
    const product = elements.commandProduct.value;
    const instances = fixture.instances.filter(
      (instance) => instance.product === product && instance.connection_status === "connected"
    );
    const previous = elements.commandInstance.value;
    elements.commandInstance.innerHTML = instances
      .map((instance) => `<option value="${esc(instance.instance_id)}">${esc(instance.instance_id)}</option>`)
      .join("");
    if (instances.some((instance) => instance.instance_id === previous)) {
      elements.commandInstance.value = previous;
    }
    syncCommandTarget();

    const dryRun = state.commandMode === "dry_run";
    elements.commandHash.value = dryRun
      ? state.recipeHash
      : "null (query)";
    const operation = dryRun
      ? "cam.recipe.preview"
      : product === "nx"
        ? "nx.session.describe"
        : "powermill.project.info";
    elements.commandOperation.innerHTML = `<option value="${esc(operation)}">${esc(operation)}</option>`;
    elements.runCommandButton.innerHTML = state.commandRunning
      ? `${icon("x")}取消任务`
      : `${icon("play")}${dryRun ? "运行 dry-run" : "运行只读查询"}`;
    const allowed = state.permissions.dry_run;
    elements.runCommandButton.disabled = !allowed || instances.length === 0;
    elements.runCommandButton.title = allowed ? "" : "命令 dry-run 分类能力已撤销";
    elements.commandProduct.disabled = dryRun;
  }

  function syncCommandTarget() {
    const instance = fixture.instances.find((item) => item.instance_id === elements.commandInstance.value);
    if (!instance) {
      return;
    }
    elements.commandVersion.value = instance.target_version || "未知";
    elements.commandProject.value = instance.project_id || "未映射";
  }

  function renderQueues() {
    elements.queueList.innerHTML = fixture.diagnostics.queues
      .map((queue) => {
        const instance = fixture.instances.find((item) => item.instance_id === queue.instance_id);
        const stateLabel = instance?.connection_status === "disconnected"
          ? "断连"
          : queue.queued
            ? `${queue.queued} 等待`
            : "空闲";
        const stateClass = instance?.connection_status === "disconnected" ? "disconnected" : queue.queued ? "required" : "ready";
        return `
          <div class="queue-row">
            <div><code title="${esc(queue.instance_id)}">${esc(queue.instance_id)}</code><span class="status-badge ${stateClass}">${stateLabel}</span></div>
            <small>running ${queue.running} · 最近 ${queue.last_duration_ms === null ? "—" : `${queue.last_duration_ms} ms`}</small>
          </div>`;
      })
      .join("");
  }

  async function runCommand() {
    if (!state.permissions.dry_run) {
      return;
    }
    if (state.commandRunning && state.commandTaskId) {
      try {
        const payload = await requestJson(
          `/api/execution/tasks/${encodeURIComponent(state.commandTaskId)}/cancel`,
          { method: "POST", body: {} }
        );
        state.commandRunning = false;
        state.commandTaskId = null;
        renderCommandControls();
        renderCommandResult(
          fixture.instances.find((item) => item.instance_id === payload.task.target_instance_id),
          payload.task.response,
          payload.task
        );
        toast("任务已取消", payload.task.task_id, "warning");
      } catch (error) {
        reportApiError(error, "取消任务");
      }
      return;
    }
    const instance = fixture.instances.find((item) => item.instance_id === elements.commandInstance.value);
    if (!instance) {
      toast("目标实例无效", "", "warning");
      return;
    }
    state.commandRunning = true;
    renderCommandControls();
    elements.resultTitle.textContent = "任务已进入实例队列";
    elements.commandStatusBadge.className = "status-badge required";
    elements.commandStatusBadge.textContent = "queued";
    elements.commandResultBody.className = "result-empty";
    elements.commandResultBody.innerHTML = `${icon("refresh")}<p>fixture transport 正在处理；未发送 CAM 命令。</p>`;
    elements.queueTotal.textContent = "1 运行";
    try {
      let argumentsValue = {};
      try {
        argumentsValue = JSON.parse(elements.commandArguments.value || "{}");
      } catch {
        throw new Error("参数必须是有效 JSON。");
      }
      const dryRun = state.commandMode === "dry_run";
      if (dryRun) {
        const allowedParameters = new Set(
          fixture.recipe.parameters.map((parameter) => parameter.name)
        );
        argumentsValue = Object.fromEntries(
          Object.entries(argumentsValue).filter(([name]) => allowedParameters.has(name))
        );
      }
      const taskId = `task:ui:${Date.now()}:${Math.random().toString(16).slice(2, 10)}`;
      const task = {
        schema_version: 1,
        task_id: taskId,
        task_type: dryRun ? "recipe_preview" : "query",
        execution_mode: dryRun ? "dry_run" : "read_only",
        product: instance.product,
        target_version: instance.target_version,
        target_instance_id: instance.instance_id,
        project_id: instance.project_id,
        recipe_hash: dryRun ? state.recipeHash : null,
        operation: elements.commandOperation.value,
        arguments: dryRun ? { parameters: argumentsValue } : argumentsValue,
        status: "queued",
        submitted_at: new Date().toISOString(),
        timeout_ms: 5000,
        requested_by: "operator:studio-ui",
        review: dryRun
          ? {
              status: "accepted",
              reviewer: "operator:studio-ui",
              scope: "dry_run_only"
            }
          : null
      };
      const submitted = await requestJson("/api/execution/run", {
        method: "POST",
        body: task
      });
      state.commandTaskId = submitted.task.task_id;
      const submittedTaskId = state.commandTaskId;
      renderCommandControls();
      let current = submitted.task;
      for (let attempt = 0; attempt < 100 && state.commandRunning; attempt += 1) {
        if (!["queued", "running"].includes(current.status)) {
          break;
        }
        await new Promise((resolve) => window.setTimeout(resolve, 50));
        const payload = await requestJson(
          `/api/execution/tasks/${encodeURIComponent(submittedTaskId)}`
        );
        current = payload.task;
      }
      if (state.commandTaskId !== submittedTaskId) {
        return;
      }
      state.commandRunning = false;
      state.commandTaskId = null;
      renderCommandControls();
      renderCommandResult(instance, current.response, current);
      await refreshDiagnostics();
    } catch (error) {
      state.commandRunning = false;
      state.commandTaskId = null;
      renderCommandControls();
      reportApiError(error, "提交任务");
    }
  }

  function renderCommandResult(instance, apiResponse = null, task = null) {
    if (!instance) {
      return;
    }
    const dryRun = state.commandMode === "dry_run";
    const response = apiResponse || fixture.command_response;
    const status = task?.status || response.status;
    if (task?.task_id) {
      fixture.diagnostics.recent_tasks = [
        {
          task_id: task.task_id,
          status,
          mode: task.execution_mode,
          duration_ms: response?.duration_ms ?? task.elapsed_ms ?? 0
        },
        ...fixture.diagnostics.recent_tasks.filter((item) => item.task_id !== task.task_id)
      ].slice(0, 8);
    }
    elements.resultTitle.textContent = dryRun ? "dry-run 任务结果" : "只读查询结果";
    elements.commandStatusBadge.className = `status-badge ${status === "succeeded" ? "ready" : status === "cancelled" ? "cancelled" : "required"}`;
    elements.commandStatusBadge.textContent = status;

    const targetHash = dryRun ? response?.diff_report?.recipe_hash || state.recipeHash : "null";
    const diffMarkup = dryRun && response?.diff_report
      ? `
        <div class="diff-pane">
          ${(response.diff_report.changes || []).map((change) => `<div class="change-row"><div><code>${esc(change.path)}</code><small>${esc(change.before)} → ${esc(change.after)} · ${esc(change.kind)}</small></div><span class="status-badge review">review</span></div>`).join("")}
          <div class="gate-list">${response.diff_report.gate_results.map((gate) => `<div class="gate-row"><strong>${esc(gate.gate)}</strong><span class="status-badge ${esc(gate.status)}">${esc(gate.status)}</span></div>`).join("")}</div>
        </div>`
      : `
        <div class="diff-pane">
          <div class="result-empty"><span>${icon("fileText")}</span><p>query 不产生 DiffReport。</p></div>
        </div>`;

    elements.commandResultBody.className = "result-grid";
    elements.commandResultBody.innerHTML = `
      <div class="response-pane">
        <dl class="response-facts">
          <dt>product</dt><dd>${esc(instance.product)}</dd>
          <dt>target_instance_id</dt><dd><code>${esc(instance.instance_id)}</code></dd>
          <dt>target_version</dt><dd>${esc(instance.target_version)}</dd>
          <dt>project_id</dt><dd>${esc(instance.project_id || "未映射")}</dd>
          <dt>recipe_hash</dt><dd><code>${esc(targetHash)}</code></dd>
          <dt>execution_mode</dt><dd>${dryRun ? "dry_run" : "read_only"}</dd>
          <dt>duration_ms</dt><dd>${response?.duration_ms ?? task?.elapsed_ms ?? 0}</dd>
          <dt>queue</dt><dd>instance queue · position 0</dd>
          <dt>status</dt><dd>${esc(status)} · fixture transport only</dd>
        </dl>
        <pre class="raw-response">${esc(response?.raw_response || JSON.stringify(response?.structured_response || task?.error || {}, null, 2))}</pre>
      </div>
      ${diffMarkup}`;
    toast(dryRun ? "dry-run 已返回" : "只读查询已返回", "生产门禁状态未改变");
  }

  function renderDiagnostics() {
    const health = [
      {
        name: "Recorder 状态机",
        detail: "recording / paused / source_interrupted / catching_up / error",
        status: state.recorderState === "missing" ? "能力未安装" : recorderLabels[state.recorderState],
        className: state.recorderState === "recording" ? "ready" : state.recorderState === "missing" ? "muted" : "required"
      },
      {
        name: "本地服务图",
        detail: `schema_version 1 · ${fixture.instances.length} CAM 实例`,
        status: "就绪",
        className: "ready"
      },
      ...fixture.instances.map((instance) => ({
        name: `${instance.product === "nx" ? "NX" : "PowerMill"} · ${instance.instance_id}`,
        detail: `${instance.target_version || "版本未知"} · ${instance.project_id || "项目未映射"}`,
        status: instance.connection_status,
        className: instance.connection_status === "connected" ? "ready" : instance.connection_status
      }))
    ];
    elements.healthList.innerHTML = health
      .map((item) => `<div class="health-row"><div><strong>${esc(item.name)}</strong><small>${esc(item.detail)}</small></div><span class="status-badge ${item.className}">${esc(item.status)}</span></div>`)
      .join("");

    elements.taskHistory.innerHTML = fixture.diagnostics.recent_tasks
      .map((task) => {
        const statusClass = task.status === "succeeded" ? "ready" : task.status;
        return `<div class="task-row"><div><strong>${esc(task.task_id)}</strong><small>${esc(task.mode)} · ${task.duration_ms} ms</small></div><span class="status-badge ${statusClass}">${esc(task.status)}</span></div>`;
      })
      .join("");

    const states = [
      ["refresh", "加载中", "固定高度骨架与进度状态"],
      ["boxes", "空状态", "无业务模块或无本地事件"],
      ["search", "无匹配", "筛选返回 0 条"],
      ["alert", "错误", "结构化错误码与恢复入口"],
      ["unplug", "断连", "实例隔离，不阻塞其他队列"],
      ["clock", "超时", "timed_out · 保留目标上下文"],
      ["x", "取消", "cancelled · 不产生 DiffReport"],
      ["lock", "能力未安装", "返回插件中心安装"]
    ];
    elements.stateMatrix.innerHTML = states
      .map(([iconName, name, detail]) => `<div class="state-item">${icon(iconName)}<div><strong>${esc(name)}</strong><small>${esc(detail)}</small></div></div>`)
      .join("");
  }

  function exportDiagnostics() {
    if (!state.permissions.data_export && state.authorized) {
      toast("数据导出已撤销", "可在高级设置恢复。", "warning");
      return;
    }
    const payload = {
      schema_version: 1,
      exported_at: new Date().toISOString(),
      transport: fixture.transport,
      recorder_state: state.recorderState,
      diagnostics: fixture.diagnostics,
      instances: fixture.instances.map(({ instance_id, product, connection_status, last_seen_at }) => ({
        instance_id,
        product,
        connection_status,
        last_seen_at
      }))
    };
    const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "cam-diagnostics-fixture.json";
    anchor.click();
    URL.revokeObjectURL(url);
    toast("本地诊断已导出", "未上传数据");
  }

  async function copyText(value, label) {
    try {
      await navigator.clipboard.writeText(value);
      toast(`${label}已复制`);
    } catch {
      toast("剪贴板不可用", "浏览器未授予写入权限。", "warning");
    }
  }

  function renderAll() {
    renderFlowWorkspace();
    renderPluginSummary();
    renderBundles();
    renderPlugins();
    renderRecorder();
    renderNavigation();
    renderPermissions();
    renderSessions();
    renderCandidates();
    renderRecipe();
    renderCommandControls();
    renderQueues();
    renderDiagnostics();
  }

  function bindEvents() {
    $$(".nav-item").forEach((button) => {
      button.addEventListener("click", () => showView(button.dataset.view));
    });
    elements.flowOpenPluginsButton.addEventListener("click", () => showView("plugins"));
    elements.openPluginsButton.addEventListener("click", () => showView("plugins"));
    elements.navToggle.addEventListener("click", () => {
      const open = elements.appNav.classList.toggle("open");
      elements.navScrim.hidden = !open;
    });
    elements.navScrim.addEventListener("click", closeMobileNav);

    elements.connectionButton.addEventListener("click", openDrawer);
    elements.closeDrawerButton.addEventListener("click", closeDrawer);
    elements.drawerScrim.addEventListener("click", closeDrawer);
    elements.drawerRefreshButton.addEventListener("click", async () => {
      try {
        const payload = await refreshConnections(true);
        toast("实例扫描完成", `${payload.instance_count} 个 CAM 实例 · 本地 API`);
      } catch (error) {
        reportApiError(error, "实例扫描");
      }
    });
    elements.refreshButton.addEventListener("click", async () => {
      elements.refreshButton.classList.add("spinning");
      await hydrateFromApi({ force: true });
      elements.refreshButton.classList.remove("spinning");
      toast("状态已刷新", "本地服务图");
    });

    elements.settingsButton.addEventListener("click", () => {
      renderPermissions();
      openModal(elements.settingsModal);
    });
    elements.closeSettingsButton.addEventListener("click", closeModals);
    elements.cancelInstallButton.addEventListener("click", closeModals);
    elements.declineInstallButton.addEventListener("click", closeModals);
    elements.modalScrim.addEventListener("click", closeModals);
    elements.authorizeInstallButton.addEventListener("click", () => {
      const ids = [...state.pendingInstallIds];
      const label = state.pendingInstallLabel;
      state.authorized = true;
      permissionDefinitions.forEach(({ key }) => {
        state.permissions[key] = true;
      });
      closeModals();
      installPlugins(ids, label);
    });
    elements.permissionSettings.addEventListener("click", (event) => {
      const button = event.target.closest("[data-permission]");
      if (button) {
        togglePermission(button.dataset.permission);
      }
    });

    elements.bundleList.addEventListener("click", (event) => {
      const button = event.target.closest("[data-bundle-id]");
      if (!button) {
        return;
      }
      const bundle = fixture.bundles.find((item) => item.id === button.dataset.bundleId);
      if (bundle) {
        queueInstall(bundle.plugin_ids, bundle.name);
      }
    });
    elements.pluginGrid.addEventListener("click", (event) => {
      const button = event.target.closest("[data-plugin-action]");
      if (!button) {
        return;
      }
      const plugin = fixture.plugins.find((item) => item.id === button.dataset.pluginId);
      if (!plugin) {
        return;
      }
      if (button.dataset.pluginAction === "install") {
        queueInstall([plugin.id], plugin.name);
      } else if (button.dataset.pluginAction === "uninstall") {
        uninstallPlugin(plugin.id);
      } else if (button.dataset.pluginAction === "update") {
        updatePlugin(plugin.id);
      }
    });
    elements.pluginSearch.addEventListener("input", renderPlugins);

    elements.manualOnlyButton.addEventListener("click", () => {
      elements.sourceModeFilter.value = "manual";
      syncLogState();
    });
    elements.levelFilter.addEventListener("click", (event) => {
      const button = event.target.closest("[data-level]");
      if (!button) {
        return;
      }
      state.log.viewLevel = button.dataset.level;
      $$(".segment", elements.levelFilter).forEach((item) => item.classList.toggle("active", item === button));
      state.log.page = 1;
      if (state.apiReady && state.installed.has("cam-local-capture")) {
        loadEventsFromApi(true).catch((error) => reportApiError(error, "筛选日志"));
      } else {
        renderEvents();
      }
    });
    [
      elements.sourceModeFilter,
      elements.productFilter,
      elements.instanceFilter,
      elements.projectFilter,
      elements.actionFilter,
      elements.fromTimeFilter,
      elements.toTimeFilter
    ].forEach((control) => control.addEventListener("change", syncLogState));
    elements.textFilter.addEventListener("input", syncLogState);
    elements.clearFiltersButton.addEventListener("click", clearLogFilters);
    elements.previousPage.addEventListener("click", async () => {
      if (state.log.remote) {
        state.log.page = Math.max(1, state.log.page - 1);
        state.log.cursor = state.log.cursors[state.log.page - 1] || null;
        await loadEventsFromApi(false).catch((error) => reportApiError(error, "加载上一页"));
      } else {
        state.log.page -= 1;
        renderEvents();
      }
    });
    elements.nextPage.addEventListener("click", async () => {
      if (state.log.remote) {
        if (!state.log.nextCursor) {
          return;
        }
        state.log.cursors[state.log.page] = state.log.nextCursor;
        state.log.page += 1;
        state.log.cursor = state.log.cursors[state.log.page - 1];
        await loadEventsFromApi(false).catch((error) => reportApiError(error, "加载下一页"));
      } else {
        state.log.page += 1;
        renderEvents();
      }
    });
    elements.pageSizeSelect.addEventListener("change", () => {
      state.log.pageSize = Number(elements.pageSizeSelect.value);
      state.log.page = 1;
      if (state.apiReady && state.installed.has("cam-local-capture")) {
        loadEventsFromApi(true).catch((error) => reportApiError(error, "更改分页"));
      } else {
        renderEvents();
      }
    });

    elements.sessionList.addEventListener("click", (event) => {
      const checkbox = event.target.closest("[data-session-select]");
      if (checkbox) {
        toggleSessionSelection(checkbox.dataset.sessionSelect, checkbox.checked);
        return;
      }
      const openButton = event.target.closest("[data-session-open]");
      if (openButton) {
        state.activeSessionId = openButton.dataset.sessionOpen;
        renderSessions();
      }
    });
    elements.compareSessionsButton.addEventListener("click", compareSessions);
    elements.markExpertButton.addEventListener("click", () => {
      if (state.expertSessions.has(state.activeSessionId)) {
        state.expertSessions.delete(state.activeSessionId);
        toast("专家标记已移除", state.activeSessionId, "warning");
      } else {
        state.expertSessions.add(state.activeSessionId);
        toast("已标记为专家会话", state.activeSessionId);
      }
      renderSessions();
    });
    elements.splitSessionButton.addEventListener("click", () => {
      toast("会话已拆分（fixture）", `${state.activeSessionId} · 原事件引用保持可追溯`);
    });
    elements.mergeSessionsButton.addEventListener("click", () => {
      if (state.selectedSessions.size >= 2) {
        toast("会话已合并（fixture）", `${state.selectedSessions.size} 个会话 · 不跨产品/实例`);
      }
    });

    elements.recipeSteps.addEventListener("change", (event) => {
      if (event.target.matches("[data-recipe-step]")) {
        updateRecipeHash();
      }
    });
    elements.copyHashButton.addEventListener("click", () => copyText(state.recipeHash, "配方 hash"));

    $$(".mode-button").forEach((button) => {
      button.addEventListener("click", () => {
        state.commandMode = button.dataset.commandMode;
        $$(".mode-button").forEach((item) => item.classList.toggle("active", item === button));
        renderCommandControls();
      });
    });
    elements.commandProduct.addEventListener("change", renderCommandControls);
    elements.commandInstance.addEventListener("change", syncCommandTarget);
    elements.runCommandButton.addEventListener("click", runCommand);
    elements.exportDiagnosticsButton.addEventListener("click", exportDiagnostics);

    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") {
        return;
      }
      if (!elements.installModal.hidden || !elements.settingsModal.hidden) {
        closeModals();
      } else if (!elements.connectionDrawer.hidden) {
        closeDrawer();
      } else {
        closeMobileNav();
      }
    });
  }

  hydrateIcons();
  populateLogFilters();
  renderInstances();
  bindEvents();
  renderAll();
  compareSessions(true);
  hydrateFromApi();
})();
