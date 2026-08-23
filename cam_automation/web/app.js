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
    exportDiagnosticsButton: $("#exportDiagnosticsButton")
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
      loading: false
    },
    activeSessionId: fixture.sessions[0].session_id,
    selectedSessions: new Set(fixture.sessions.slice(0, 3).map((session) => session.session_id)),
    expertSessions: new Set(
      fixture.sessions.filter((session) => session.labels.includes("expert")).map((session) => session.session_id)
    ),
    commandMode: "query",
    commandRunning: false,
    recipeHash: fixture.recipe.recipe_hash
  };

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

  function installPlugins(pluginIds, label) {
    if (state.installing) {
      return;
    }
    state.installing = true;
    renderPlugins();
    window.setTimeout(() => {
      dependenciesFor(pluginIds).forEach((pluginId) => state.installed.add(pluginId));
      if (state.installed.has("cam-local-capture")) {
        state.recorderState = state.permissions.logs ? "recording" : "paused";
      }
      state.installing = false;
      renderAll();
      toast("安装完成", `${label} · 本地记录已开启`);
    }, 360);
  }

  function uninstallPlugin(pluginId) {
    state.installed.delete(pluginId);
    if (pluginId === "cam-local-capture") {
      fixture.plugins
        .filter((plugin) => plugin.dependencies.includes("cam-local-capture"))
        .forEach((plugin) => state.installed.delete(plugin.id));
      state.recorderState = "missing";
    }
    renderAll();
    toast("插件已卸载", fixture.plugins.find((plugin) => plugin.id === pluginId)?.name || pluginId, "warning");
    if (!moduleAvailable($(`.nav-item[data-view="${state.activeView}"]`)?.dataset.module)) {
      showView("plugins");
    }
  }

  function updatePlugin(pluginId) {
    const plugin = fixture.plugins.find((item) => item.id === pluginId);
    if (!plugin) {
      return;
    }
    state.installedVersions.set(pluginId, plugin.latest_version);
    renderPlugins();
    renderPluginSummary();
    toast("更新完成", `${plugin.name} ${plugin.latest_version}`);
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

  function togglePermission(key) {
    if (!state.authorized || !(key in state.permissions)) {
      return;
    }
    state.permissions[key] = !state.permissions[key];
    if (key === "logs" && state.installed.has("cam-local-capture")) {
      state.recorderState = state.permissions.logs ? "recording" : "paused";
    }
    renderPermissions();
    renderRecorder();
    renderPluginSummary();
    renderCommandControls();
    toast(
      state.permissions[key] ? "分类能力已恢复" : "分类能力已撤销",
      permissionDefinitions.find((item) => item.key === key)?.name || key,
      state.permissions[key] ? "success" : "warning"
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

  function renderEvents() {
    if (state.log.loading) {
      elements.eventRows.innerHTML = "";
      elements.eventState.hidden = false;
      elements.eventState.innerHTML = `${icon("refresh")}<strong>正在加载固定事件页</strong>`;
      return;
    }

    const filtered = applyLogFilters();
    const pageCount = Math.max(1, Math.ceil(filtered.length / state.log.pageSize));
    state.log.page = Math.min(Math.max(1, state.log.page), pageCount);
    const start = (state.log.page - 1) * state.log.pageSize;
    const page = filtered.slice(start, start + state.log.pageSize);
    elements.eventTotal.textContent = filtered.length;
    elements.eventRows.innerHTML = page
      .map((event) => {
        const time = event.timestamp.replace("2026-08-23T", "").replace("Z", "");
        const product = event.product === "nx" ? "NX" : "PowerMill";
        return `
          <tr data-event-id="${esc(event.event_id)}">
            <td><strong>${esc(time)}</strong><small>${esc(event.event_id)}</small></td>
            <td><strong>${product}</strong><small title="${esc(event.instance_id)}">${esc(event.instance_id)}</small></td>
            <td><span class="source-badge ${esc(event.source_mode)}">${esc(event.source_mode)}</span></td>
            <td><span class="level-badge">${esc(event.view_level)}</span></td>
            <td title="${esc(event.action)}"><code>${esc(event.action)}</code></td>
            <td title="${esc(event.project_id || "未映射")}">${esc(event.project_id || "未映射")}</td>
            <td>${event.duration_ms} ms</td>
          </tr>`;
      })
      .join("");

    elements.eventState.hidden = page.length > 0;
    if (!page.length) {
      elements.eventState.innerHTML = `${icon("search")}<strong>没有匹配结果</strong><span>当前筛选返回 0 条事件。</span>`;
    }
    elements.paginationSummary.textContent = filtered.length
      ? `${start + 1}-${Math.min(start + page.length, filtered.length)} / ${filtered.length} 条 · DOM ${page.length} 行`
      : "0 条结果 · DOM 0 行";
    elements.pageIndicator.textContent = `${state.log.page} / ${pageCount}`;
    elements.previousPage.disabled = state.log.page <= 1;
    elements.nextPage.disabled = state.log.page >= pageCount;
    elements.manualOnlyButton.classList.toggle("primary", state.log.sourceMode === "manual");
    elements.manualOnlyButton.classList.toggle("quiet", state.log.sourceMode !== "manual");
  }

  function loadLogView() {
    if (state.log.loading) {
      return;
    }
    state.log.loading = true;
    renderEvents();
    window.setTimeout(() => {
      state.log.loading = false;
      renderEvents();
    }, 120);
  }

  function syncLogState() {
    state.log.sourceMode = elements.sourceModeFilter.value;
    state.log.product = elements.productFilter.value;
    state.log.instance = elements.instanceFilter.value;
    state.log.project = elements.projectFilter.value;
    state.log.action = elements.actionFilter.value;
    state.log.fromTime = elements.fromTimeFilter.value;
    state.log.toTime = elements.toTimeFilter.value;
    state.log.text = elements.textFilter.value;
    state.log.page = 1;
    renderEvents();
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

  function compareSessions(silent = false) {
    const selected = fixture.sessions.filter((session) => state.selectedSessions.has(session.session_id));
    if (selected.length < 2 || selected.length > 5) {
      toast("请选择 2-5 个会话", "", "warning");
      return;
    }
    const baseline = selected[0];
    const toleranceValues = [0.02, 0.03, 0.015, 0.05, 0.04];
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
            <dt>时长</dt><dd>${formatDuration(session.duration_ms)}</dd>
            <dt>额外步骤</dt><dd>${session.steps.length > baseline.steps.length ? esc(session.steps.filter((step) => !baseline.steps.includes(step)).join("、") || "无") : "无"}</dd>
            <dt>标记</dt><dd>${state.expertSessions.has(session.session_id) ? "expert" : "routine"}</dd>
          </dl>
        </article>`)
      .join("");
    elements.comparisonPanel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    if (!silent) {
      toast("SessionDiff 已更新", `${selected.length} 个会话 · fixture`);
    }
  }

  function renderCandidates() {
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
          <code>${esc(fixture.recipe.steps[Math.min(index, fixture.recipe.steps.length - 1)].source_event_refs[0].event_session_id)}:${index}</code>
          <div class="diff-line">${index === 0 ? "基线 tolerance 0.020 mm" : index === 1 ? "差异 tolerance 0.030 mm；包含选择器返工" : "差异 tolerance 0.015 mm"}</div>
        </article>`)
      .join("");
  }

  async function updateRecipeHash() {
    const enabled = $$(".step-toggle", elements.recipeSteps).map((toggle) => ({
      id: toggle.dataset.recipeStep,
      enabled: toggle.checked
    }));
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
    const product = elements.commandProduct.value;
    const instances = fixture.instances.filter(
      (instance) => instance.product === product && instance.connection_status !== "disconnected"
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
      ? fixture.command_response.diff_report.recipe_hash
      : "null (query)";
    elements.commandOperation.value = dryRun ? "cam.recipe.preview" : "project.summary";
    elements.runCommandButton.innerHTML = `${icon("play")}${dryRun ? "运行 dry-run" : "运行只读查询"}`;
    const allowed = state.permissions.dry_run;
    elements.runCommandButton.disabled = state.commandRunning || !allowed;
    elements.runCommandButton.title = allowed ? "" : "命令 dry-run 分类能力已撤销";
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

  function runCommand() {
    if (state.commandRunning || !state.permissions.dry_run) {
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

    window.setTimeout(() => {
      state.commandRunning = false;
      renderCommandControls();
      elements.queueTotal.textContent = "1 等待";
      renderCommandResult(instance);
    }, 520);
  }

  function renderCommandResult(instance) {
    const dryRun = state.commandMode === "dry_run";
    const response = fixture.command_response;
    elements.resultTitle.textContent = dryRun ? "dry-run 已完成（fixture）" : "只读查询已完成（fixture）";
    elements.commandStatusBadge.className = "status-badge ready";
    elements.commandStatusBadge.textContent = "transport succeeded";

    const targetHash = dryRun ? response.diff_report.recipe_hash : "null";
    const diffMarkup = dryRun
      ? `
        <div class="diff-pane">
          <div class="change-row">
            <div><code>${esc(response.diff_report.changes[0].path)}</code><small>${response.diff_report.changes[0].before} → ${response.diff_report.changes[0].after} · proposed_update</small></div>
            <span class="status-badge review">review</span>
          </div>
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
          <dt>duration_ms</dt><dd>${dryRun ? response.duration_ms : 82}</dd>
          <dt>queue</dt><dd>instance queue · position 0</dd>
          <dt>status</dt><dd>transport succeeded only</dd>
        </dl>
        <pre class="raw-response">${esc(dryRun ? response.raw_response : "Fixture read-only project summary returned; no CAM command was sent.")}</pre>
      </div>
      ${diffMarkup}`;
    toast(dryRun ? "dry-run fixture 已返回" : "只读查询 fixture 已返回", "生产门禁状态未改变");
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
        name: "NX 2312 · nx:3102:B2",
        detail: "最近响应 08:12:06 · timeout 5.0 s",
        status: "断连",
        className: "disconnected"
      },
      {
        name: "PowerMill 2025 · powermill:4102:process",
        detail: "无可见窗口 · 项目未映射",
        status: "仅检测",
        className: "detected"
      },
      {
        name: "固定 API fixture",
        detail: "schema_version 1 · 612 events",
        status: "就绪",
        className: "ready"
      }
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
    elements.openPluginsButton.addEventListener("click", () => showView("plugins"));
    elements.navToggle.addEventListener("click", () => {
      const open = elements.appNav.classList.toggle("open");
      elements.navScrim.hidden = !open;
    });
    elements.navScrim.addEventListener("click", closeMobileNav);

    elements.connectionButton.addEventListener("click", openDrawer);
    elements.closeDrawerButton.addEventListener("click", closeDrawer);
    elements.drawerScrim.addEventListener("click", closeDrawer);
    elements.drawerRefreshButton.addEventListener("click", () => {
      toast("实例扫描完成", "5 个实例 · fixture");
    });
    elements.refreshButton.addEventListener("click", () => {
      elements.refreshButton.classList.add("spinning");
      window.setTimeout(() => elements.refreshButton.classList.remove("spinning"), 300);
      toast("状态已刷新", "固定 API fixture · 08:15:15");
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
      renderEvents();
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
    elements.previousPage.addEventListener("click", () => {
      state.log.page -= 1;
      renderEvents();
    });
    elements.nextPage.addEventListener("click", () => {
      state.log.page += 1;
      renderEvents();
    });
    elements.pageSizeSelect.addEventListener("change", () => {
      state.log.pageSize = Number(elements.pageSizeSelect.value);
      state.log.page = 1;
      renderEvents();
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
})();
