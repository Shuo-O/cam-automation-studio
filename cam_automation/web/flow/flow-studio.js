(function attachCamFlowStudio(global) {
  "use strict";

  const core = global.CAM_FLOW_CORE;
  const fixtures = global.CAM_FLOW_FIXTURES;
  if (!core || !fixtures) {
    throw new Error("Flow Studio requires CAM_FLOW_CORE and CAM_FLOW_FIXTURES.");
  }

  const NODE_WIDTH = 204;
  const NODE_HEIGHT = 126;
  const WORLD_WIDTH = 1800;
  const WORLD_HEIGHT = 900;
  const STATUS_LABELS = {
    safe: "安全下限",
    review: "需要审阅",
    blocked: "已阻断",
    supported: "版本支持",
    unsupported: "版本不支持",
    capability_unavailable: "能力不可用",
    accepted: "已审阅",
    needs_review: "待审阅",
    warning: "警告",
    blocker: "阻断",
    passed: "通过",
    required: "必需",
    not_run: "未完成",
    none: "无传输"
  };

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function icon(name) {
    const paths = {
      add: '<path d="M12 5v14M5 12h14"/>',
      ai: '<path d="M12 3v3M12 18v3M3 12h3M18 12h3"/><circle cx="12" cy="12" r="4"/><path d="m5.6 5.6 2.1 2.1m8.6 8.6 2.1 2.1m0-12.8-2.1 2.1m-8.6 8.6-2.1 2.1"/>',
      alert: '<path d="M12 3 2.8 20h18.4L12 3Z"/><path d="M12 9v4M12 17h.01"/>',
      asset: '<path d="M4 5h6l2 2h8v12H4V5Z"/><path d="M4 10h16"/>',
      check: '<path d="m5 12 4 4L19 6"/>',
      chevron: '<path d="m9 18 6-6-6-6"/>',
      close: '<path d="m6 6 12 12M18 6 6 18"/>',
      code: '<path d="m8 9-3 3 3 3m8-6 3 3-3 3m-3-9-2 12"/>',
      compare: '<path d="M7 4v16M17 4v16M3 8h8M13 16h8"/>',
      fit: '<path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5"/>',
      link: '<path d="M10 13a5 5 0 0 0 7.5.5l2-2a5 5 0 0 0-7-7l-1.1 1.1"/><path d="M14 11a5 5 0 0 0-7.5-.5l-2 2a5 5 0 0 0 7 7l1.1-1.1"/>',
      lock: '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
      minus: '<path d="M5 12h14"/>',
      module: '<rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/><path d="M10 7h4a3 3 0 0 1 3 3v4"/>',
      redo: '<path d="M17 7h-7a5 5 0 0 0-5 5v3"/><path d="m14 4 3 3-3 3"/>',
      source: '<path d="M6 3h9l4 4v14H6V3Z"/><path d="M15 3v5h4M9 12h7M9 16h7"/>',
      test: '<path d="M9 3h6M10 3v5l-5 9a3 3 0 0 0 2.6 4h8.8a3 3 0 0 0 2.6-4l-5-9V3"/><path d="M8 15h8"/>',
      undo: '<path d="M7 7h7a5 5 0 0 1 5 5v3"/><path d="m10 4-3 3 3 3"/>',
      version: '<path d="M12 3a9 9 0 1 1-8.5 6"/><path d="M3 3v6h6"/><path d="M12 7v5l3 2"/>',
      zoomIn: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5M10.5 7.5v6M7.5 10.5h6"/>',
      zoomOut: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5M7.5 10.5h6"/>'
    };
    return `<svg class="flow-icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${paths[name] || paths.module}</svg>`;
  }

  class FlowStudio {
    constructor(root, options) {
      if (!root) {
        throw new TypeError("Flow Studio requires a mount element.");
      }
      if (!options || !options.api) {
        throw new TypeError("Flow Studio requires an injected API adapter.");
      }
      this.root = root;
      this.api = options.api;
      this.bundle = this.api.bundle;
      const graphResponse = this.api.getGraph(this.bundle.graph.graph_id);
      this.store = new core.GraphStore(graphResponse.graph);
      this.versions = this.api.listVersions(graphResponse.graph.graph_id).items;
      this.state = {
        selectedNodeId: "node:roughing",
        selectedAssetId: this.bundle.automation_assets[0].asset_id,
        bottomTab: "problems",
        bottomCollapsed: false,
        relationMode: false,
        selectedEdgeKind: "control",
        pendingConnection: null,
        pendingPointer: null,
        dragDraft: null,
        sourceCandidates: [],
        activeSourceLines: [],
        baseVersionId: this.versions[this.versions.length - 1].version_id,
        aiEnabled: false,
        previewPlan: null,
        testReport: null,
        performance: null,
        notice: "",
        target: {
          product: "",
          target_version: "",
          target_instance_id: "",
          project_id: "",
          project_snapshot_hash: ""
        },
        viewport: {
          x: 10,
          y: 10,
          scale: 0.88,
          width: 900,
          height: 560
        }
      };
      this.versionSequence = this.versions.length;
      this.unsubscribe = this.store.subscribe((graph, event) => this.onGraphChange(graph, event));
      this.renderShell();
      this.bindEvents();
      this.measureCanvas();
      this.renderAll();
      this.root.setAttribute("aria-busy", "false");
    }

    renderShell() {
      this.root.innerHTML = `
        <section class="flow-app" aria-label="CAM Flow Studio fixture 工作台">
          <header class="flow-topbar">
            <div class="flow-brand">
              <span class="flow-brand__mark" aria-hidden="true">CF</span>
              <span><strong>CAM Flow Studio</strong><small>离线 fixture 编辑器</small></span>
            </div>
            <div class="flow-history" role="group" aria-label="GraphCommand 历史">
              <button class="flow-icon-button" type="button" data-action="undo" title="撤销 GraphCommand" aria-label="撤销 GraphCommand">${icon("undo")}</button>
              <button class="flow-icon-button" type="button" data-action="redo" title="重做 GraphCommand" aria-label="重做 GraphCommand">${icon("redo")}</button>
            </div>
            <label class="flow-toolbar-field flow-version-field">
              <span>Diff 基线</span>
              <select data-field="base-version" aria-label="选择 Diff 基线版本"></select>
            </label>
            <button class="flow-button flow-button--quiet" type="button" data-action="record-version">${icon("version")}记录版本</button>
            <div class="flow-topbar__spacer"></div>
            <span class="flow-state-chip flow-state-chip--safe">${icon("lock")}offline · transport none</span>
            <label class="flow-switch">
              <input type="checkbox" data-field="ai-toggle">
              <span>AI 解释</span>
            </label>
          </header>

          <section class="flow-targetbar" aria-label="fixture 目标绑定">
            <div class="flow-targetbar__title">
              <strong>预览目标</strong>
              <small>不会根据前台窗口或列表顺序自动选择</small>
            </div>
            <label><span>产品</span><select data-target="product"><option value="">请选择</option><option value="powermill">PowerMill</option><option value="nx">NX</option></select></label>
            <label><span>目标版本</span><select data-target="target_version"><option value="">请选择</option></select></label>
            <label><span>fixture 实例</span><select data-target="target_instance_id"><option value="">请选择</option></select></label>
            <label><span>测试项目</span><select data-target="project_id"><option value="">请选择</option></select></label>
            <button class="flow-button flow-button--primary" type="button" data-action="preview" disabled>生成 fixture 预览</button>
          </section>

          <div class="flow-workspace">
            <aside class="flow-library" aria-label="资产与模块库">
              <div class="flow-pane-heading">
                <span><strong>资产 / 模块库</strong><small>当前产品与能力锁</small></span>
              </div>
              <label class="flow-search">
                <span class="sr-only">搜索模块</span>
                <input type="search" data-field="library-search" placeholder="搜索步骤、类型或状态" autocomplete="off">
              </label>
              <div class="flow-library__scroll">
                <section class="flow-library-section">
                  <h2>${icon("asset")}已导入资产</h2>
                  <div data-region="assets"></div>
                </section>
                <section class="flow-library-section">
                  <h2>${icon("module")}CAM 步骤</h2>
                  <div data-region="modules"></div>
                </section>
                <section class="flow-library-section flow-version-list">
                  <h2>${icon("version")}版本列表</h2>
                  <div data-region="versions"></div>
                </section>
              </div>
            </aside>

            <section class="flow-canvas-panel" aria-label="流程画布区域">
              <div class="flow-canvas-toolbar">
                <div class="flow-segmented" role="group" aria-label="关系显示模式">
                  <button class="is-active" type="button" data-relation-mode="control">主 control</button>
                  <button type="button" data-relation-mode="all">全部关系</button>
                </div>
                <label class="flow-toolbar-field">
                  <span>新关系</span>
                  <select data-field="edge-kind">
                    <option value="control">control</option>
                    <option value="data">data</option>
                    <option value="dependency">dependency</option>
                  </select>
                </label>
                <span class="flow-canvas-toolbar__hint" data-region="connect-hint">拖动端口，或依次选择两个 typed port</span>
                <div class="flow-canvas-toolbar__spacer"></div>
                <button class="flow-icon-button" type="button" data-action="zoom-out" title="缩小画布" aria-label="缩小画布">${icon("zoomOut")}</button>
                <output data-region="zoom" aria-live="polite">88%</output>
                <button class="flow-icon-button" type="button" data-action="zoom-in" title="放大画布" aria-label="放大画布">${icon("zoomIn")}</button>
                <button class="flow-icon-button" type="button" data-action="fit" title="复位画布" aria-label="复位画布">${icon("fit")}</button>
                <button class="flow-button flow-button--quiet flow-button--compact" type="button" data-action="performance">500 节点 hook</button>
              </div>
              <div
                id="flowCanvas"
                class="flow-canvas"
                role="application"
                tabindex="0"
                aria-label="FlowGraph 画布。方向键移动选中节点，加减号缩放，零键复位。"
                data-region="canvas"
              >
                <div class="flow-canvas__world" data-region="canvas-world">
                  <svg class="flow-edges" data-region="edges" width="${WORLD_WIDTH}" height="${WORLD_HEIGHT}" aria-hidden="true"></svg>
                  <div class="flow-nodes" data-region="nodes"></div>
                </div>
              </div>
              <div class="flow-canvas-status">
                <span data-region="canvas-summary"></span>
                <span data-region="performance"></span>
              </div>
            </section>

            <aside class="flow-inspector" aria-label="Schema inspector">
              <div class="flow-pane-heading">
                <span><strong>当前步骤</strong><small>schema-driven inspector</small></span>
                <button class="flow-icon-button" type="button" data-action="locate-source" title="定位到源码" aria-label="定位当前步骤到源码">${icon("source")}</button>
              </div>
              <div class="flow-inspector__scroll" data-region="inspector"></div>
            </aside>
          </div>

          <section class="flow-bottom" aria-label="证据与检查底栏">
            <div class="flow-bottom-tabs" role="tablist" aria-label="底栏视图">
              <button type="button" role="tab" data-bottom-tab="problems">问题 <span data-count="problems">0</span></button>
              <button type="button" role="tab" data-bottom-tab="source">源码</button>
              <button type="button" role="tab" data-bottom-tab="graph-diff">图 Diff</button>
              <button type="button" role="tab" data-bottom-tab="source-diff">源码 Diff</button>
              <button type="button" role="tab" data-bottom-tab="test">Test</button>
              <button type="button" role="tab" data-bottom-tab="preview">Preview</button>
              <button type="button" role="tab" data-bottom-tab="gates">门禁</button>
              <span class="flow-bottom-tabs__spacer"></span>
              <button class="flow-icon-button" type="button" data-action="toggle-bottom" title="折叠或展开底栏" aria-label="折叠或展开底栏">${icon("chevron")}</button>
            </div>
            <div class="flow-bottom-content" role="tabpanel" data-region="bottom-content"></div>
            <footer class="flow-safety-strip">
              <strong>fixture_dry_run</strong>
              <code>transport=none</code>
              <code>commands_sent=0</code>
              <code>journal_executed=false</code>
              <code>macro_executed=false</code>
              <code>machine_output_count=0</code>
              <span>仿真、碰撞检查与车间批准保持未完成</span>
            </footer>
          </section>
          <div class="flow-live-region sr-only" data-region="live" aria-live="polite" aria-atomic="true"></div>
        </section>`;
      this.elements = {
        app: this.root.querySelector(".flow-app"),
        assets: this.root.querySelector('[data-region="assets"]'),
        modules: this.root.querySelector('[data-region="modules"]'),
        versions: this.root.querySelector('[data-region="versions"]'),
        inspector: this.root.querySelector('[data-region="inspector"]'),
        canvas: this.root.querySelector('[data-region="canvas"]'),
        canvasWorld: this.root.querySelector('[data-region="canvas-world"]'),
        edges: this.root.querySelector('[data-region="edges"]'),
        nodes: this.root.querySelector('[data-region="nodes"]'),
        canvasSummary: this.root.querySelector('[data-region="canvas-summary"]'),
        performance: this.root.querySelector('[data-region="performance"]'),
        zoom: this.root.querySelector('[data-region="zoom"]'),
        connectHint: this.root.querySelector('[data-region="connect-hint"]'),
        bottomContent: this.root.querySelector('[data-region="bottom-content"]'),
        live: this.root.querySelector('[data-region="live"]'),
        search: this.root.querySelector('[data-field="library-search"]'),
        baseVersion: this.root.querySelector('[data-field="base-version"]'),
        edgeKind: this.root.querySelector('[data-field="edge-kind"]'),
        aiToggle: this.root.querySelector('[data-field="ai-toggle"]'),
        previewButton: this.root.querySelector('[data-action="preview"]'),
        targets: Object.fromEntries(
          Array.from(this.root.querySelectorAll("[data-target]")).map((item) => [item.dataset.target, item])
        )
      };
    }

    bindEvents() {
      this.root.addEventListener("click", (event) => this.handleClick(event));
      this.root.addEventListener("change", (event) => this.handleChange(event));
      this.root.addEventListener("input", (event) => this.handleInput(event));
      this.root.addEventListener("keydown", (event) => this.handleKeydown(event));
      this.elements.canvas.addEventListener("dragover", (event) => event.preventDefault());
      this.elements.canvas.addEventListener("drop", (event) => this.handleDrop(event));
      this.elements.modules.addEventListener("dragstart", (event) => {
        const item = event.target.closest("[data-module-type]");
        if (!item) {
          return;
        }
        event.dataTransfer.effectAllowed = "copy";
        event.dataTransfer.setData("application/x-cam-flow-module", item.dataset.moduleType);
      });
      this.elements.canvas.addEventListener("wheel", (event) => {
        if (!event.ctrlKey) {
          return;
        }
        event.preventDefault();
        this.setZoom(this.state.viewport.scale + (event.deltaY < 0 ? 0.08 : -0.08));
      }, { passive: false });
      this.elements.canvas.addEventListener("pointerdown", (event) => this.handleCanvasPointerDown(event));
      window.addEventListener("resize", () => {
        this.measureCanvas();
        this.renderCanvas();
      });
    }

    handleClick(event) {
      const action = event.target.closest("[data-action]")?.dataset.action;
      if (action) {
        const actions = {
          undo: () => this.store.undo(),
          redo: () => this.store.redo(),
          "record-version": () => this.recordVersion(),
          preview: () => this.createPreview(),
          "zoom-out": () => this.setZoom(this.state.viewport.scale - 0.1),
          "zoom-in": () => this.setZoom(this.state.viewport.scale + 0.1),
          fit: () => this.fitCanvas(),
          performance: () => this.measurePerformanceHook(),
          "locate-source": () => this.locateSelectedNodeSource(),
          "toggle-bottom": () => this.toggleBottom()
        };
        if (actions[action]) {
          actions[action]();
        }
        return;
      }

      const relation = event.target.closest("[data-relation-mode]")?.dataset.relationMode;
      if (relation) {
        this.state.relationMode = relation === "all";
        this.root.querySelectorAll("[data-relation-mode]").forEach((button) => {
          button.classList.toggle("is-active", button.dataset.relationMode === relation);
        });
        this.renderCanvas();
        return;
      }

      const tab = event.target.closest("[data-bottom-tab]")?.dataset.bottomTab;
      if (tab) {
        this.state.bottomTab = tab;
        this.state.bottomCollapsed = false;
        this.renderBottom();
        return;
      }

      const asset = event.target.closest("[data-asset-id]");
      if (asset) {
        this.state.selectedAssetId = asset.dataset.assetId;
        this.renderAssets();
        this.state.bottomTab = "source";
        this.renderBottom();
        return;
      }

      const nodeElement = event.target.closest("[data-node-id]");
      if (nodeElement && !event.target.closest("[data-port-id]")) {
        this.selectNode(nodeElement.dataset.nodeId);
        return;
      }
      const portElement = event.target.closest("[data-port-id]");
      if (portElement) {
        this.handlePortClick(portElement);
        return;
      }

      const issue = event.target.closest("[data-object-ref]");
      if (issue) {
        this.selectNode(issue.dataset.objectRef);
        this.locateSelectedNodeSource();
        return;
      }

      const line = event.target.closest("[data-source-line]");
      if (line) {
        this.locateSourceLine(Number(line.dataset.sourceLine));
        return;
      }

      const version = event.target.closest("[data-version-id]");
      if (version) {
        this.state.baseVersionId = version.dataset.versionId;
        this.elements.baseVersion.value = this.state.baseVersionId;
        this.state.bottomTab = "graph-diff";
        this.renderVersions();
        this.renderBottom();
      }
    }

    handleChange(event) {
      const field = event.target.dataset.field;
      if (field === "base-version") {
        this.state.baseVersionId = event.target.value;
        this.renderVersions();
        this.renderBottom();
        return;
      }
      if (field === "edge-kind") {
        this.state.selectedEdgeKind = event.target.value;
        return;
      }
      if (field === "ai-toggle") {
        const before = this.store.canonicalJson();
        this.state.aiEnabled = event.target.checked;
        const after = this.store.canonicalJson();
        if (before !== after) {
          throw new Error("AI presentation state changed canonical FlowGraph JSON.");
        }
        this.renderInspector();
        this.announce(`AI 解释已${this.state.aiEnabled ? "开启" : "关闭"}；FlowGraph 未改变。`);
        return;
      }
      if (event.target.matches("[data-target]")) {
        this.updateTarget(event.target.dataset.target, event.target.value);
        return;
      }
      if (event.target.matches("[data-config-property]")) {
        const property = event.target.dataset.configProperty;
        let value = event.target.type === "checkbox"
          ? event.target.checked
          : event.target.type === "number"
            ? Number(event.target.value)
            : event.target.value;
        try {
          this.store.execute(core.updateConfigurationCommand(this.state.selectedNodeId, property, value));
          this.announce(`参数 ${property} 已形成 GraphCommand。`);
        } catch (error) {
          this.reportError(error);
        }
        return;
      }
      if (event.target.matches("[data-node-enabled]")) {
        try {
          this.store.execute(core.toggleNodeCommand(this.state.selectedNodeId, event.target.checked));
        } catch (error) {
          this.reportError(error);
        }
      }
    }

    handleInput(event) {
      if (event.target === this.elements.search) {
        this.renderModules();
      }
    }

    handleKeydown(event) {
      const editable = event.target.matches("input, select, textarea");
      if ((event.ctrlKey || event.metaKey) && !editable && event.key.toLowerCase() === "z") {
        event.preventDefault();
        if (event.shiftKey) {
          this.store.redo();
        } else {
          this.store.undo();
        }
        return;
      }
      if ((event.ctrlKey || event.metaKey) && !editable && event.key.toLowerCase() === "y") {
        event.preventDefault();
        this.store.redo();
        return;
      }
      const moduleElement = event.target.closest("[data-module-type]");
      if (moduleElement && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault();
        const center = {
          x: (this.state.viewport.width / 2 - this.state.viewport.x) / this.state.viewport.scale,
          y: (this.state.viewport.height / 2 - this.state.viewport.y) / this.state.viewport.scale
        };
        this.addModule(moduleElement.dataset.moduleType, center);
        return;
      }
      const nodeElement = event.target.closest("[data-node-id]");
      if (nodeElement && ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(event.key)) {
        event.preventDefault();
        const amount = event.shiftKey ? 48 : 12;
        const position = this.nodePosition(nodeElement.dataset.nodeId);
        const delta = {
          ArrowUp: { x: 0, y: -amount },
          ArrowDown: { x: 0, y: amount },
          ArrowLeft: { x: -amount, y: 0 },
          ArrowRight: { x: amount, y: 0 }
        }[event.key];
        this.store.execute(
          core.moveNodeCommand(nodeElement.dataset.nodeId, {
            x: Math.max(0, position.x + delta.x),
            y: Math.max(0, position.y + delta.y)
          })
        );
        return;
      }
      if (nodeElement && event.key === "Home") {
        event.preventDefault();
        this.selectNode(nodeElement.dataset.nodeId);
        this.locateSelectedNodeSource();
        return;
      }
      if (!editable && (event.key === "+" || event.key === "=")) {
        event.preventDefault();
        this.setZoom(this.state.viewport.scale + 0.1);
      } else if (!editable && event.key === "-") {
        event.preventDefault();
        this.setZoom(this.state.viewport.scale - 0.1);
      } else if (!editable && event.key === "0") {
        event.preventDefault();
        this.fitCanvas();
      } else if (event.key === "Escape" && this.state.pendingConnection) {
        this.state.pendingConnection = null;
        this.state.pendingPointer = null;
        this.renderCanvas();
        this.announce("typed port 连接已取消。");
      }
    }

    handleCanvasPointerDown(event) {
      const portElement = event.target.closest("[data-port-id]");
      if (portElement) {
        this.beginPortPointer(event, portElement);
        return;
      }
      const dragHandle = event.target.closest("[data-drag-handle]");
      if (!dragHandle || event.button !== 0) {
        return;
      }
      const nodeId = dragHandle.closest("[data-node-id]").dataset.nodeId;
      const startPosition = this.nodePosition(nodeId);
      const origin = { x: event.clientX, y: event.clientY };
      const pointerId = event.pointerId;
      dragHandle.setPointerCapture(pointerId);
      this.selectNode(nodeId);
      const move = (moveEvent) => {
        if (moveEvent.pointerId !== pointerId) {
          return;
        }
        const scale = this.state.viewport.scale;
        this.state.dragDraft = {
          nodeId,
          x: Math.max(0, startPosition.x + (moveEvent.clientX - origin.x) / scale),
          y: Math.max(0, startPosition.y + (moveEvent.clientY - origin.y) / scale)
        };
        this.positionRenderedNodes();
        this.renderEdges();
      };
      const up = (upEvent) => {
        if (upEvent.pointerId !== pointerId) {
          return;
        }
        dragHandle.removeEventListener("pointermove", move);
        dragHandle.removeEventListener("pointerup", up);
        dragHandle.removeEventListener("pointercancel", up);
        if (this.state.dragDraft) {
          const position = { x: this.state.dragDraft.x, y: this.state.dragDraft.y };
          this.state.dragDraft = null;
          this.store.execute(core.moveNodeCommand(nodeId, position));
        }
      };
      dragHandle.addEventListener("pointermove", move);
      dragHandle.addEventListener("pointerup", up);
      dragHandle.addEventListener("pointercancel", up);
    }

    beginPortPointer(event, portElement) {
      if (portElement.dataset.direction !== "output" || event.button !== 0) {
        return;
      }
      const origin = { x: event.clientX, y: event.clientY };
      const pointerId = event.pointerId;
      let moved = false;
      const connection = this.connectionFromPort(portElement);
      const move = (moveEvent) => {
        if (moveEvent.pointerId !== pointerId) {
          return;
        }
        moved = moved || Math.hypot(moveEvent.clientX - origin.x, moveEvent.clientY - origin.y) > 5;
        if (!moved) {
          return;
        }
        this.state.pendingConnection = connection;
        this.state.pendingPointer = this.clientToWorld(moveEvent.clientX, moveEvent.clientY);
        this.renderEdges();
      };
      const up = (upEvent) => {
        if (upEvent.pointerId !== pointerId) {
          return;
        }
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
        if (!moved) {
          return;
        }
        const target = document.elementFromPoint(upEvent.clientX, upEvent.clientY)?.closest("[data-port-id]");
        if (target && target.dataset.direction === "input") {
          this.completeConnection(target);
        } else {
          this.state.pendingConnection = null;
          this.state.pendingPointer = null;
          this.renderCanvas();
          this.announce("未找到目标输入端口。");
        }
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    }

    connectionFromPort(portElement) {
      const definition = fixtures.registry[core.graphNode(this.store.graph, portElement.dataset.nodeId).node_type];
      const port = definition.ports.find((item) => item.port_id === portElement.dataset.portId);
      const kind = port.edge_kinds.includes(this.state.selectedEdgeKind)
        ? this.state.selectedEdgeKind
        : port.edge_kinds[0];
      return {
        kind,
        source: {
          node_id: portElement.dataset.nodeId,
          port_id: portElement.dataset.portId
        }
      };
    }

    handlePortClick(portElement) {
      if (portElement.dataset.direction === "output") {
        this.state.pendingConnection = this.connectionFromPort(portElement);
        this.state.pendingPointer = null;
        this.renderCanvas();
        this.announce(`已选择输出端口 ${portElement.dataset.portId}，请选择兼容的输入端口。`);
      } else if (this.state.pendingConnection) {
        this.completeConnection(portElement);
      } else {
        this.announce("请先选择输出端口。");
      }
    }

    completeConnection(targetElement) {
      const connection = {
        ...this.state.pendingConnection,
        target: {
          node_id: targetElement.dataset.nodeId,
          port_id: targetElement.dataset.portId
        }
      };
      try {
        this.store.execute(core.connectCommand(connection, fixtures.registry));
        this.announce(`已建立 ${connection.kind} typed 关系。`);
      } catch (error) {
        this.reportError(error);
      } finally {
        this.state.pendingConnection = null;
        this.state.pendingPointer = null;
        this.renderCanvas();
      }
    }

    handleDrop(event) {
      event.preventDefault();
      const nodeType = event.dataTransfer.getData("application/x-cam-flow-module");
      if (!nodeType || !fixtures.registry[nodeType]) {
        return;
      }
      this.addModule(nodeType, this.clientToWorld(event.clientX, event.clientY));
    }

    addModule(nodeType, world) {
      const typeCount = core.graphFlow(this.store.graph).nodes.filter((node) => node.node_type === nodeType).length;
      const nodeId = `node:ui:${nodeType.replaceAll(".", "-")}:${String(typeCount + 1).padStart(2, "0")}`;
      const node = fixtures.createNode(nodeType, nodeId, {
        review_status: "needs_review",
        compatibility_status: fixtures.registry[nodeType].fidelity === "opaque_preserved"
          ? "capability_unavailable"
          : "needs_review",
        source_mapping_ids: []
      });
      try {
        this.store.execute(core.addNodeCommand(node, {
          x: Math.max(0, world.x - NODE_WIDTH / 2),
          y: Math.max(0, world.y - 30)
        }));
        this.selectNode(nodeId);
        this.announce(`${fixtures.registry[nodeType].display_name} 已添加，来源状态为 synthetic。`);
      } catch (error) {
        this.reportError(error);
      }
    }

    onGraphChange(_graph, event) {
      this.state.notice = "";
      this.state.previewPlan = null;
      this.state.testReport = null;
      this.renderAll();
      if (event.command) {
        this.announce(`${event.command.label} · ${event.type}`);
      }
    }

    renderAll() {
      this.renderAssets();
      this.renderModules();
      this.renderVersions();
      this.renderTargetControls();
      this.renderCanvas();
      this.renderInspector();
      this.renderBottom();
      this.root.querySelector('[data-action="undo"]').disabled = !this.store.canUndo;
      this.root.querySelector('[data-action="redo"]').disabled = !this.store.canRedo;
    }

    renderAssets() {
      this.elements.assets.innerHTML = this.bundle.automation_assets.map((asset) => `
        <button class="flow-asset-row ${asset.asset_id === this.state.selectedAssetId ? "is-active" : ""}" type="button" data-asset-id="${escapeHtml(asset.asset_id)}">
          ${icon("asset")}
          <span><strong>${escapeHtml(asset.display_name)}</strong><small>${escapeHtml(asset.product)} · immutable · ${escapeHtml(asset.rights.status)}</small></span>
          <span class="flow-status flow-status--safe">F0</span>
        </button>`).join("");
    }

    renderModules() {
      const query = this.elements.search.value.trim().toLowerCase();
      const matching = this.bundle.modules.filter((module) =>
        `${module.display_name} ${module.node_type} ${module.category} ${module.description}`.toLowerCase().includes(query)
      );
      this.elements.modules.innerHTML = matching.map((module) => {
        const permission = this.bundle.permissions[module.permission];
        const unavailable = permission !== "granted";
        return `
          <article
            class="flow-module-row ${unavailable ? "is-unavailable" : ""}"
            draggable="true"
            tabindex="0"
            data-module-type="${escapeHtml(module.node_type)}"
            aria-label="${escapeHtml(module.display_name)}，拖到画布添加"
          >
            <span class="flow-module-row__glyph">${icon(module.fidelity === "opaque_preserved" ? "lock" : "module")}</span>
            <span><strong>${escapeHtml(module.display_name)}</strong><small>${escapeHtml(module.category)} · ${escapeHtml(module.node_type)}</small></span>
            <span class="flow-status flow-status--${unavailable ? "blocked" : module.static_risk_floor}">
              ${unavailable ? "权限缺失" : escapeHtml(STATUS_LABELS[module.static_risk_floor])}
            </span>
          </article>`;
      }).join("") || '<p class="flow-empty">没有匹配的 CAM 步骤。</p>';
    }

    renderVersions() {
      this.elements.baseVersion.innerHTML = this.versions.map((version) =>
        `<option value="${escapeHtml(version.version_id)}">${escapeHtml(version.message)} · ${escapeHtml(version.status)}</option>`
      ).join("");
      this.elements.baseVersion.value = this.state.baseVersionId;
      this.elements.versions.innerHTML = this.versions.map((version) => `
        <button class="flow-version-row ${version.version_id === this.state.baseVersionId ? "is-active" : ""}" type="button" data-version-id="${escapeHtml(version.version_id)}">
          <span><strong>${escapeHtml(version.message)}</strong><small>${escapeHtml(version.version_id)}<br>${escapeHtml(version.status)}</small></span>
          <span class="flow-status flow-status--${version.status === "reviewed_for_fixture" ? "safe" : "review"}">${version.parent_version_ids.length ? "revision" : "root"}</span>
        </button>`).join("");
    }

    renderTargetControls() {
      const target = this.state.target;
      this.elements.targets.product.value = target.product;
      const versions = Array.from(new Set(
        this.bundle.targets.filter((item) => item.product === target.product).map((item) => item.target_version)
      )).sort();
      this.elements.targets.target_version.innerHTML = '<option value="">请选择</option>'
        + versions.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("");
      this.elements.targets.target_version.value = target.target_version;

      const instances = this.bundle.targets.filter(
        (item) => item.product === target.product && (!target.target_version || item.target_version === target.target_version)
      );
      this.elements.targets.target_instance_id.innerHTML = '<option value="">请选择</option>'
        + instances.map((item) => `
          <option value="${escapeHtml(item.instance_id)}">
            ${escapeHtml(item.display_name)}${item.is_foreground ? " · 临时前台提示" : ""}
          </option>`).join("");
      this.elements.targets.target_instance_id.value = target.target_instance_id;

      const projects = this.bundle.targets.filter(
        (item) =>
          item.product === target.product
          && item.target_version === target.target_version
          && item.instance_id === target.target_instance_id
      );
      this.elements.targets.project_id.innerHTML = '<option value="">请选择</option>'
        + projects.map((item) => `<option value="${escapeHtml(item.project_id)}">${escapeHtml(item.project_name)}</option>`).join("");
      this.elements.targets.project_id.value = target.project_id;
      this.elements.previewButton.disabled = !this.targetComplete();
    }

    updateTarget(field, value) {
      const target = this.state.target;
      this.state.notice = "";
      target[field] = value;
      if (field === "product") {
        target.target_version = "";
        target.target_instance_id = "";
        target.project_id = "";
        target.project_snapshot_hash = "";
      } else if (field === "target_version") {
        target.target_instance_id = "";
        target.project_id = "";
        target.project_snapshot_hash = "";
      } else if (field === "target_instance_id") {
        target.project_id = "";
        target.project_snapshot_hash = "";
      } else if (field === "project_id") {
        const match = this.bundle.targets.find(
          (item) =>
            item.product === target.product
            && item.target_version === target.target_version
            && item.instance_id === target.target_instance_id
            && item.project_id === value
        );
        target.project_snapshot_hash = match ? match.project_snapshot_hash : "";
      }
      this.state.previewPlan = null;
      this.renderTargetControls();
      this.renderBottom();
    }

    targetComplete() {
      return ["product", "target_version", "target_instance_id", "project_id", "project_snapshot_hash"]
        .every((field) => Boolean(this.state.target[field]));
    }

    nodePosition(nodeId) {
      if (this.state.dragDraft && this.state.dragDraft.nodeId === nodeId) {
        return { x: this.state.dragDraft.x, y: this.state.dragDraft.y };
      }
      return core.clone(this.store.graph.layout?.nodes?.[nodeId] || { x: 0, y: 0 });
    }

    renderCanvas() {
      const graph = this.store.graph;
      const flow = core.graphFlow(graph);
      const view = core.visibleNodes(graph, {
        ...this.state.viewport,
        width: this.state.viewport.width,
        height: this.state.viewport.height
      }, { width: NODE_WIDTH, height: NODE_HEIGHT });
      const visibleIds = new Set(view.nodes.map((node) => node.node_id));
      this.elements.nodes.innerHTML = view.nodes.map((node) => this.renderNode(node)).join("");
      this.positionRenderedNodes();
      this.renderEdges(visibleIds);
      this.applyViewport();
      this.elements.canvasSummary.textContent =
        `${flow.nodes.length} 节点 · ${flow.edges.length} 关系 · viewport ${view.visible_nodes}/${view.total_nodes}`;
      this.elements.zoom.textContent = `${Math.round(this.state.viewport.scale * 100)}%`;
      this.elements.connectHint.textContent = this.state.pendingConnection
        ? `${this.state.pendingConnection.kind} · 已选择 ${this.state.pendingConnection.source.node_id}#${this.state.pendingConnection.source.port_id}`
        : "拖动端口，或依次选择两个 typed port";
      if (this.state.performance) {
        this.elements.performance.textContent =
          `500 hook · visible ${this.state.performance.visible_nodes}/${this.state.performance.total_nodes} · ${this.state.performance.duration_ms} ms`;
      } else {
        this.elements.performance.textContent = "";
      }
    }

    renderNode(node) {
      const definition = fixtures.registry[node.node_type] || {
        display_name: node.node_type,
        ports: [],
        category: "未知能力"
      };
      const selected = node.node_id === this.state.selectedNodeId;
      const ports = definition.ports.filter(
        (item) => this.state.relationMode || item.edge_kinds.includes("control")
      );
      const inputPorts = ports.filter((item) => item.direction === "input");
      const outputPorts = ports.filter((item) => item.direction === "output");
      const status = node.opaque
        ? "opaque · 只读"
        : node.compatibility_status === "supported"
          ? STATUS_LABELS[node.review_status] || node.review_status
          : STATUS_LABELS[node.compatibility_status] || node.compatibility_status;
      return `
        <article
          class="flow-node ${selected ? "is-selected" : ""} ${node.opaque ? "is-opaque" : ""} ${node.enabled ? "" : "is-disabled"}"
          style="width:${NODE_WIDTH}px;height:${NODE_HEIGHT}px"
          data-node-id="${escapeHtml(node.node_id)}"
          tabindex="0"
          role="button"
          aria-pressed="${selected}"
          aria-label="${escapeHtml(definition.display_name)}，${escapeHtml(status)}，方向键可移动"
        >
          <div class="flow-node__drag" data-drag-handle>
            <span class="flow-node__kind">${escapeHtml(definition.category)}</span>
            <span class="flow-status flow-status--${node.opaque ? "blocked" : node.risk}">${escapeHtml(STATUS_LABELS[node.risk] || node.risk)}</span>
          </div>
          <strong title="${escapeHtml(definition.display_name)}">${escapeHtml(definition.display_name)}</strong>
          <code title="${escapeHtml(node.node_type)}">${escapeHtml(node.node_type)}</code>
          <span class="flow-node__state">${node.opaque ? icon("lock") : icon("check")}${escapeHtml(status)}</span>
          <div class="flow-node__ports flow-node__ports--input">
            ${inputPorts.map((item) => this.renderPort(node, item)).join("")}
          </div>
          <div class="flow-node__ports flow-node__ports--output">
            ${outputPorts.map((item) => this.renderPort(node, item)).join("")}
          </div>
        </article>`;
    }

    renderPort(node, port) {
      const pending = this.state.pendingConnection;
      let compatibility = "";
      if (pending && port.direction === "input") {
        const issue = core.connectionIssue(this.store.graph, fixtures.registry, {
          ...pending,
          target: { node_id: node.node_id, port_id: port.port_id }
        });
        compatibility = issue ? " is-incompatible" : " is-compatible";
      }
      return `
        <button
          class="flow-port flow-port--${escapeHtml(port.edge_kinds[0])}${compatibility}"
          type="button"
          data-node-id="${escapeHtml(node.node_id)}"
          data-port-id="${escapeHtml(port.port_id)}"
          data-direction="${escapeHtml(port.direction)}"
          title="${escapeHtml(port.direction)} · ${escapeHtml(port.type_ref)} · ${escapeHtml(port.cardinality)}"
          aria-label="${escapeHtml(node.node_id)} ${escapeHtml(port.port_id)}，${escapeHtml(port.direction)}，${escapeHtml(port.type_ref)}"
        ><span>${escapeHtml(port.port_id)}</span></button>`;
    }

    positionRenderedNodes() {
      this.elements.nodes.querySelectorAll("[data-node-id]").forEach((element) => {
        if (!element.classList.contains("flow-node")) {
          return;
        }
        const position = this.nodePosition(element.dataset.nodeId);
        element.style.transform = `translate(${position.x}px, ${position.y}px)`;
      });
    }

    portAnchor(nodeId, portId, direction) {
      const node = core.graphNode(this.store.graph, nodeId);
      const definition = node && fixtures.registry[node.node_type];
      const visiblePorts = (definition?.ports || []).filter(
        (item) => (this.state.relationMode || item.edge_kinds.includes("control")) && item.direction === direction
      );
      const index = Math.max(0, visiblePorts.findIndex((item) => item.port_id === portId));
      const position = this.nodePosition(nodeId);
      return {
        x: position.x + (direction === "output" ? NODE_WIDTH : 0),
        y: position.y + 70 + index * 24
      };
    }

    renderEdges(visibleIds = null) {
      const flow = core.graphFlow(this.store.graph);
      const edges = flow.edges.filter(
        (edge) =>
          (this.state.relationMode || edge.kind === "control")
          && (!visibleIds || visibleIds.has(edge.source.node_id) || visibleIds.has(edge.target.node_id))
      );
      const markup = edges.map((edge) => {
        const start = this.portAnchor(edge.source.node_id, edge.source.port_id, "output");
        const end = this.portAnchor(edge.target.node_id, edge.target.port_id, "input");
        return `<path class="flow-edge flow-edge--${escapeHtml(edge.kind)}" d="${this.edgePath(start, end)}"><title>${escapeHtml(edge.kind)} · ${escapeHtml(edge.edge_id)}</title></path>`;
      });
      if (this.state.pendingConnection && this.state.pendingPointer) {
        const start = this.portAnchor(
          this.state.pendingConnection.source.node_id,
          this.state.pendingConnection.source.port_id,
          "output"
        );
        markup.push(
          `<path class="flow-edge flow-edge--pending" d="${this.edgePath(start, this.state.pendingPointer)}"></path>`
        );
      }
      this.elements.edges.innerHTML = markup.join("");
    }

    edgePath(start, end) {
      const distance = Math.max(60, Math.abs(end.x - start.x) * 0.45);
      return `M ${start.x} ${start.y} C ${start.x + distance} ${start.y}, ${end.x - distance} ${end.y}, ${end.x} ${end.y}`;
    }

    applyViewport() {
      const viewport = this.state.viewport;
      this.elements.canvasWorld.style.transform =
        `translate(${viewport.x}px, ${viewport.y}px) scale(${viewport.scale})`;
    }

    clientToWorld(clientX, clientY) {
      const rect = this.elements.canvas.getBoundingClientRect();
      return {
        x: (clientX - rect.left - this.state.viewport.x) / this.state.viewport.scale,
        y: (clientY - rect.top - this.state.viewport.y) / this.state.viewport.scale
      };
    }

    setZoom(value) {
      this.state.viewport.scale = Math.min(1.6, Math.max(0.45, Number(value.toFixed(2))));
      this.renderCanvas();
    }

    fitCanvas() {
      this.state.viewport.x = 10;
      this.state.viewport.y = 10;
      this.state.viewport.scale = this.state.viewport.width < 700 ? 0.58 : 0.88;
      this.renderCanvas();
    }

    measureCanvas() {
      const rect = this.elements.canvas.getBoundingClientRect();
      this.state.viewport.width = Math.max(320, rect.width || 900);
      this.state.viewport.height = Math.max(300, rect.height || 560);
    }

    selectNode(nodeId) {
      if (!core.graphNode(this.store.graph, nodeId)) {
        return;
      }
      this.state.selectedNodeId = nodeId;
      this.renderCanvas();
      this.renderInspector();
    }

    renderInspector() {
      const node = core.graphNode(this.store.graph, this.state.selectedNodeId);
      if (!node) {
        this.elements.inspector.innerHTML = '<p class="flow-empty">选择画布节点查看 schema。</p>';
        return;
      }
      const definition = fixtures.registry[node.node_type];
      const mappings = core.sourceMappingsForNode(this.store.graph, node.node_id);
      const permissionState = this.bundle.permissions[definition.permission] || "unknown";
      const fields = Object.entries(definition.configuration_schema.properties || {}).map(
        ([name, schema]) => this.renderInspectorField(node, name, schema)
      ).join("");
      this.elements.inspector.innerHTML = `
        <section class="flow-inspector-identity">
          <span class="flow-inspector-identity__glyph">${icon(node.opaque ? "lock" : "module")}</span>
          <span><strong>${escapeHtml(definition.display_name)}</strong><code>${escapeHtml(node.node_type)}@${escapeHtml(node.node_type_version)}</code></span>
        </section>
        <dl class="flow-facts">
          <div><dt>node_id</dt><dd>${escapeHtml(node.node_id)}</dd></div>
          <div><dt>fidelity</dt><dd>${escapeHtml(node.fidelity)}</dd></div>
          <div><dt>compatibility</dt><dd><span class="flow-status flow-status--${node.compatibility_status === "supported" ? "safe" : "blocked"}">${escapeHtml(STATUS_LABELS[node.compatibility_status] || node.compatibility_status)}</span></dd></div>
          <div><dt>permission</dt><dd><span class="flow-status flow-status--${permissionState === "granted" ? "safe" : "blocked"}">${escapeHtml(definition.permission)} · ${escapeHtml(permissionState)}</span></dd></div>
          <div><dt>source</dt><dd>${mappings.length ? `${mappings.length} mapping · ${escapeHtml(mappings[0].mapping_quality)}` : "synthetic / unmapped"}</dd></div>
        </dl>
        ${node.opaque ? `
          <div class="flow-callout flow-callout--blocked">
            ${icon("alert")}
            <span><strong>不透明 / unsupported / 只读</strong><small>${escapeHtml(node.opaque.reason)} · 原始 span 按 F3 保留；AI 不会猜测其语义。</small></span>
          </div>` : `
          <section class="flow-schema-fields">
            <h2>参数</h2>
            ${fields || '<p class="flow-empty">此步骤没有可编辑参数。</p>'}
          </section>
          <label class="flow-check-row">
            <input type="checkbox" data-node-enabled ${node.enabled ? "checked" : ""}>
            <span><strong>纳入当前 FlowVersion</strong><small>变更形成可撤销 GraphCommand</small></span>
          </label>`}
        <section class="flow-evidence">
          <h2>证据与门禁</h2>
          ${definition.required_gates.map((gate) => `<span>${icon("lock")}<strong>${escapeHtml(gate)}</strong><small>${gate === "recipe_review" ? "fixture 审阅" : "required / not_run"}</small></span>`).join("")}
        </section>
        <div class="flow-ai-note ${this.state.aiEnabled ? "is-enabled" : ""}">
          ${icon("ai")}
          <span><strong>AI 解释：${this.state.aiEnabled ? "开启" : "关闭"}</strong><small>${escapeHtml(this.bundle.ai.explanation)} authority=${escapeHtml(this.bundle.ai.authority)}</small></span>
        </div>`;
    }

    renderInspectorField(node, name, schema) {
      const value = node.configuration[name];
      if (schema.type === "boolean") {
        return `
          <label class="flow-check-row">
            <input type="checkbox" data-config-property="${escapeHtml(name)}" ${value ? "checked" : ""}>
            <span><strong>${escapeHtml(schema.title || name)}</strong><small>boolean</small></span>
          </label>`;
      }
      if (schema.enum) {
        return `
          <label class="flow-field">
            <span>${escapeHtml(schema.title || name)}</span>
            <select data-config-property="${escapeHtml(name)}">
              ${schema.enum.map((option) => `<option value="${escapeHtml(option)}" ${option === value ? "selected" : ""}>${escapeHtml(option)}</option>`).join("")}
            </select>
          </label>`;
      }
      if (schema.type === "number") {
        return `
          <label class="flow-field">
            <span>${escapeHtml(schema.title || name)}</span>
            <span class="flow-unit-input">
              <input
                type="number"
                data-config-property="${escapeHtml(name)}"
                value="${escapeHtml(value)}"
                min="${escapeHtml(schema.minimum)}"
                max="${escapeHtml(schema.maximum)}"
                step="${escapeHtml(schema.step || "any")}"
              >
              <em>${escapeHtml(schema.unit || "")}</em>
            </span>
          </label>`;
      }
      return `
        <label class="flow-field">
          <span>${escapeHtml(schema.title || name)}</span>
          <input type="text" data-config-property="${escapeHtml(name)}" value="${escapeHtml(value)}">
        </label>`;
    }

    renderBottom() {
      const validation = this.api.validateGraph(this.store.graph);
      this.root.querySelector('[data-count="problems"]').textContent =
        validation.diagnostics.length + (this.state.notice ? 1 : 0);
      this.root.querySelectorAll("[data-bottom-tab]").forEach((button) => {
        const active = button.dataset.bottomTab === this.state.bottomTab;
        button.classList.toggle("is-active", active);
        button.setAttribute("aria-selected", String(active));
        button.tabIndex = active ? 0 : -1;
      });
      this.root.querySelector(".flow-bottom").classList.toggle("is-collapsed", this.state.bottomCollapsed);
      if (this.state.bottomCollapsed) {
        return;
      }
      const renderers = {
        problems: () => this.renderProblems(validation),
        source: () => this.renderSource(),
        "graph-diff": () => this.renderGraphDiff(),
        "source-diff": () => this.renderSourceDiff(),
        test: () => this.renderTest(),
        preview: () => this.renderPreview(),
        gates: () => this.renderGates()
      };
      this.elements.bottomContent.innerHTML = renderers[this.state.bottomTab]();
    }

    renderProblems(validation) {
      if (!validation.diagnostics.length && !this.state.notice) {
        return `<div class="flow-empty-state">${icon("check")}<span><strong>确定性检查通过</strong><small>没有 blocker；生产门禁仍独立存在。</small></span></div>`;
      }
      return `
        <div class="flow-problem-list">
          ${this.state.notice ? `
            <div class="flow-problem-notice">
              <span class="flow-problem-icon">${icon("alert")}</span>
              <span><strong>当前操作未接受</strong><small>${escapeHtml(this.state.notice)}</small></span>
              <span class="flow-status flow-status--blocker">阻断</span>
            </div>` : ""}
          ${validation.diagnostics.map((item) => `
            <button type="button" data-object-ref="${escapeHtml(item.object_ref)}">
              <span class="flow-problem-icon">${icon(item.severity === "blocker" ? "alert" : "lock")}</span>
              <span><strong>${escapeHtml(item.code)}</strong><small>${escapeHtml(item.message)}</small></span>
              <span class="flow-status flow-status--${escapeHtml(item.severity)}">${escapeHtml(STATUS_LABELS[item.severity] || item.severity)}</span>
              <span class="flow-source-ref">${escapeHtml(item.source_mapping_ids.join(", ") || "unmapped")}</span>
            </button>`).join("")}
        </div>`;
    }

    renderSource() {
      const active = new Set(this.state.activeSourceLines);
      const candidates = this.state.sourceCandidates;
      return `
        <div class="flow-source-layout">
          <div class="flow-source-code" role="list" aria-label="${escapeHtml(this.bundle.source.display_name)} 源码">
            ${this.bundle.source.lines.map((line, index) => {
              const lineNumber = index + 1;
              const refs = core.nodesForSourceLine(
                this.store.graph,
                this.bundle.source.asset_revision_id,
                lineNumber
              );
              return `
                <button
                  type="button"
                  role="listitem"
                  class="${active.has(lineNumber) ? "is-active" : ""} ${refs.some((item) => item.mapping_quality === "ambiguous") ? "is-ambiguous" : ""}"
                  data-source-line="${lineNumber}"
                  aria-label="第 ${lineNumber} 行，${refs.length} 个映射候选"
                >
                  <span>${lineNumber}</span><code>${escapeHtml(line)}</code><em>${refs.length ? `${refs.length} map` : ""}</em>
                </button>`;
            }).join("")}
          </div>
          <aside class="flow-source-detail">
            <strong>双向定位</strong>
            <p>图到源码使用 node/property mapping；源码到图按最小 span、exact 优先稳定排序。</p>
            ${candidates.length > 1 ? `
              <div class="flow-callout flow-callout--review">
                ${icon("alert")}
                <span><strong>SOURCE_MAPPING_AMBIGUOUS</strong><small>此行有多个候选，未自动选择。</small></span>
              </div>
              <div class="flow-candidate-list">
                ${candidates.map((item) => `<button type="button" data-object-ref="${escapeHtml(item.node_id)}">${escapeHtml(item.node_id)} · ${escapeHtml(item.mapping_quality)}${item.property_path ? ` · ${escapeHtml(item.property_path)}` : ""}</button>`).join("")}
              </div>` : candidates.length === 1 ? `
              <dl class="flow-facts">
                <div><dt>node</dt><dd>${escapeHtml(candidates[0].node_id)}</dd></div>
                <div><dt>quality</dt><dd>${escapeHtml(candidates[0].mapping_quality)}</dd></div>
                <div><dt>property</dt><dd>${escapeHtml(candidates[0].property_path || "node")}</dd></div>
              </dl>` : '<p class="flow-empty">选择源码行或从 inspector 定位。</p>'}
          </aside>
        </div>`;
    }

    renderGraphDiff() {
      const diff = this.api.diffGraph(this.state.baseVersionId, this.store.graph);
      return `
        <div class="flow-report-header">
          <span><strong>Graph Diff</strong><small>${escapeHtml(diff.base_revision_id)} → ${escapeHtml(diff.candidate_revision_id)}</small></span>
          <span class="flow-status flow-status--${diff.semantic_changed ? "review" : "safe"}">${diff.semantic_changed ? `${diff.changes.length} 语义变化` : "无语义变化"}</span>
          <code>layout_excluded=true</code>
        </div>
        <div class="flow-diff-list">
          ${diff.changes.length ? diff.changes.map((change) => `
            <div><span class="flow-diff-marker">~</span><code>${escapeHtml(change.object_ref)}</code><strong>${escapeHtml(change.kind)}</strong><pre>${escapeHtml(JSON.stringify({ before: change.before, after: change.after }, null, 2))}</pre></div>
          `).join("") : '<p class="flow-empty">当前图与所选版本的语义相同。</p>'}
        </div>`;
    }

    renderSourceDiff() {
      const diff = this.api.sourceDiff(this.state.baseVersionId, this.store.graph);
      return `
        <div class="flow-report-header">
          <span><strong>Source Diff</strong><small>${escapeHtml(diff.asset_revision_id)}</small></span>
          <span class="flow-status flow-status--safe">untouched exact</span>
          <span class="flow-status flow-status--safe">opaque preserved</span>
          <span class="flow-status flow-status--safe">reparsed</span>
        </div>
        <div class="flow-source-diff">
          ${diff.changes.length ? diff.changes.map((change) => `
            <div><span>${change.line}</span><code class="is-removed">- ${escapeHtml(change.before)}</code></div>
            <div><span>${change.line}</span><code class="is-added">+ ${escapeHtml(change.after)}</code></div>
          `).join("") : '<p class="flow-empty">当前版本没有 source candidate 变化。</p>'}
        </div>`;
    }

    renderTest() {
      const report = this.state.testReport;
      return `
        <div class="flow-report-header">
          <span><strong>Fixture Test</strong><small>Schema、类型、source map、round-trip 与资源边界</small></span>
          <button class="flow-button flow-button--quiet" type="button" data-action="test-graph">${icon("test")}检查当前图</button>
        </div>
        ${report ? `
          <div class="flow-check-list">
            ${report.checks.map((check) => `
              <div>${icon(check.status === "blocked" ? "alert" : "check")}<strong>${escapeHtml(check.name)}</strong><span class="flow-status flow-status--${check.status === "passed" || check.status === "none" ? "safe" : "blocked"}">${escapeHtml(STATUS_LABELS[check.status] || check.status)}</span></div>
            `).join("")}
          </div>` : '<p class="flow-empty">选择“检查当前图”生成确定性 fixture 报告。</p>'}`;
    }

    renderPreview() {
      const plan = this.state.previewPlan;
      if (!plan) {
        return `
          <div class="flow-empty-state">${icon("lock")}<span><strong>尚未生成 fixture 预览</strong><small>先显式选择产品、版本、fixture 实例和测试项目。</small></span></div>`;
      }
      return `
        <div class="flow-report-header">
          <span><strong>PreviewPlan</strong><small>${escapeHtml(plan.plan_id)} · ${escapeHtml(plan.status)}</small></span>
          <span class="flow-status flow-status--safe">${escapeHtml(plan.execution_mode)}</span>
          <code>transport=${escapeHtml(plan.transport)}</code>
        </div>
        <div class="flow-preview-grid">
          <dl class="flow-facts">
            <div><dt>product</dt><dd>${escapeHtml(plan.target.product)}</dd></div>
            <div><dt>version</dt><dd>${escapeHtml(plan.target.target_version)}</dd></div>
            <div><dt>instance</dt><dd>${escapeHtml(plan.target.target_instance_id)}</dd></div>
            <div><dt>project</dt><dd>${escapeHtml(plan.target.project_id)}</dd></div>
          </dl>
          <div class="flow-zero-fields">
            <code>commands_sent=${plan.commands_sent}</code>
            <code>journal_executed=${plan.journal_executed}</code>
            <code>macro_executed=${plan.macro_executed}</code>
            <code>machine_output_count=${plan.machine_output_count}</code>
          </div>
        </div>`;
    }

    renderGates() {
      const gates = this.state.previewPlan?.gate_results || [
        { gate: "recipe_review", status: "required" },
        { gate: "target_version_validation", status: "required" },
        { gate: "cam_simulation", status: "not_run" },
        { gate: "collision_check", status: "required" },
        { gate: "shop_approval", status: "required" }
      ];
      return `
        <div class="flow-gate-list">
          ${gates.map((gate) => `
            <div>${icon(gate.status === "passed" ? "check" : "lock")}<span><strong>${escapeHtml(gate.gate)}</strong><small>${gate.status === "passed" ? "仅绑定当前 fixture hashes" : "不由 PreviewPlan 替代"}</small></span><span class="flow-status flow-status--${gate.status === "passed" ? "safe" : "review"}">${escapeHtml(STATUS_LABELS[gate.status] || gate.status)}</span></div>
          `).join("")}
        </div>`;
    }

    runTest() {
      this.state.testReport = this.api.testGraph(this.store.graph);
      this.renderBottom();
      this.announce(`Fixture Test ${this.state.testReport.status}。`);
    }

    createPreview() {
      try {
        this.state.previewPlan = this.api.createPreview({
          graph: this.store.snapshot(),
          flow_version_id: this.versions[0].version_id,
          target: core.clone(this.state.target)
        });
        this.state.notice = "";
        this.state.bottomTab = "preview";
        this.renderBottom();
        this.announce("fixture 预览已生成；零执行字段保持不变。");
      } catch (error) {
        this.state.previewPlan = null;
        this.state.bottomTab = "problems";
        this.reportError(error);
        this.renderBottom();
      }
    }

    recordVersion() {
      this.versionSequence += 1;
      const graph = this.store.snapshot();
      const parent = this.versions[0];
      const version = {
        schema_version: 1,
        contract: "cam.flow_version.v1",
        version_id: `flow-version:ui:${String(this.versionSequence).padStart(3, "0")}`,
        graph_id: graph.graph_id,
        revision_id: graph.revision_id,
        parent_version_ids: [parent.version_id],
        semantic_hash: graph.semantic_hash,
        artifact_hash: core.sha256Canonical(graph),
        source_snapshot_hash: graph.source_snapshot_hash,
        capability_lock_hash: core.sha256Canonical(graph.capability_lock),
        status: "draft",
        author_ref: "user:fixture",
        message: `本地 draft ${String(this.versionSequence).padStart(2, "0")}`,
        round_trip_report_id: "roundtrip:ui:not-checked",
        compatibility_report_id: "compatibility:ui:not-checked",
        created_at: "2026-08-24T00:00:03Z",
        immutable: true,
        extensions: {}
      };
      this.versions.unshift(version);
      this.api.bundle.versions.unshift(core.clone(version));
      this.api.bundle.version_snapshots[version.version_id] = graph;
      this.state.baseVersionId = version.version_id;
      this.renderVersions();
      this.renderBottom();
      this.announce(`${version.message} 已记录；原版本未覆盖。`);
    }

    locateSelectedNodeSource() {
      const mappings = core.sourceMappingsForNode(this.store.graph, this.state.selectedNodeId);
      this.state.activeSourceLines = mappings.map((mapping) => mapping.source_line);
      this.state.sourceCandidates = mappings.map((mapping) => ({
        node_id: mapping.target.node_id,
        mapping_id: mapping.mapping_id,
        mapping_quality: mapping.mapping_quality,
        property_path: mapping.target.property_path || null
      }));
      this.state.bottomTab = "source";
      this.state.bottomCollapsed = false;
      this.renderBottom();
      this.announce(mappings.length ? `已定位 ${mappings.length} 个 source mapping。` : "当前节点没有 source mapping。");
    }

    locateSourceLine(line) {
      const candidates = core.nodesForSourceLine(
        this.store.graph,
        this.bundle.source.asset_revision_id,
        line
      );
      this.state.activeSourceLines = [line];
      this.state.sourceCandidates = candidates;
      if (candidates.length === 1) {
        this.state.selectedNodeId = candidates[0].node_id;
        this.renderCanvas();
        this.renderInspector();
        this.announce(`源码第 ${line} 行已定位到 ${candidates[0].node_id}。`);
      } else if (candidates.length > 1) {
        this.announce(`源码第 ${line} 行存在 ${candidates.length} 个映射候选，未自动选择。`);
      } else {
        this.announce(`源码第 ${line} 行没有映射。`);
      }
      this.renderBottom();
    }

    measurePerformanceHook() {
      const graph = fixtures.makePerformanceGraph(500);
      this.state.performance = core.visibleNodes(graph, {
        x: 0,
        y: 0,
        scale: 1,
        width: 1200,
        height: 700
      }, { width: NODE_WIDTH, height: NODE_HEIGHT });
      this.renderCanvas();
      this.announce(
        `500 节点 viewport hook：${this.state.performance.visible_nodes} 可见，${this.state.performance.duration_ms} 毫秒。`
      );
    }

    toggleBottom() {
      this.state.bottomCollapsed = !this.state.bottomCollapsed;
      this.renderBottom();
    }

    reportError(error) {
      const code = error.code || "FLOW_UI_ERROR";
      this.state.notice = `${code}: ${error.message}`;
      this.announce(this.state.notice);
      this.elements.canvas.classList.add("has-error");
      window.setTimeout(() => this.elements.canvas.classList.remove("has-error"), 700);
    }

    announce(message) {
      this.elements.live.textContent = "";
      window.setTimeout(() => {
        this.elements.live.textContent = message;
      }, 0);
    }

    destroy() {
      if (this.unsubscribe) {
        this.unsubscribe();
      }
      this.root.innerHTML = "";
    }
  }

  // Late-bound action is kept outside the static map so it works after bottom rerenders.
  const originalHandleClick = FlowStudio.prototype.handleClick;
  FlowStudio.prototype.handleClick = function handleClickWithReports(event) {
    const action = event.target.closest("[data-action]")?.dataset.action;
    if (action === "test-graph") {
      this.runTest();
      return;
    }
    originalHandleClick.call(this, event);
  };

  function mount(root, options) {
    return new FlowStudio(root, options);
  }

  const api = Object.freeze({ FlowStudio, mount });
  global.CAM_FLOW_STUDIO = api;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
})(globalThis);
