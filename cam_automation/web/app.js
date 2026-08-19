const elements = {
  sourceInput: document.querySelector("#sourceInput"),
  sourceLabel: document.querySelector("#sourceLabel"),
  sourceFormat: document.querySelector("#sourceFormat"),
  recipeName: document.querySelector("#recipeName"),
  analyzeButton: document.querySelector("#analyzeButton"),
  sampleButton: document.querySelector("#sampleButton"),
  fileButton: document.querySelector("#fileButton"),
  fileInput: document.querySelector("#fileInput"),
  allowReview: document.querySelector("#allowReview"),
  applyParameters: document.querySelector("#applyParameters"),
  analysisState: document.querySelector("#analysisState"),
  lineCount: document.querySelector("#lineCount"),
  sessionMetric: document.querySelector("#sessionMetric"),
  commandMetric: document.querySelector("#commandMetric"),
  parameterMetric: document.querySelector("#parameterMetric"),
  reviewMetric: document.querySelector("#reviewMetric"),
  blockedMetric: document.querySelector("#blockedMetric"),
  matchLabel: document.querySelector("#matchLabel"),
  stepList: document.querySelector("#stepList"),
  parameterRows: document.querySelector("#parameterRows"),
  diagnosticList: document.querySelector("#diagnosticList"),
  recipeOutput: document.querySelector("#recipeOutput"),
  previewOutput: document.querySelector("#previewOutput"),
  previewTabLabel: document.querySelector("#previewTabLabel"),
  previewFileLabel: document.querySelector("#previewFileLabel"),
  copyRecipe: document.querySelector("#copyRecipe"),
  copyPreview: document.querySelector("#copyPreview"),
  downloadPreview: document.querySelector("#downloadPreview"),
  canvas: document.querySelector("#workflowCanvas"),
  connectionList: document.querySelector("#connectionList"),
  refreshConnections: document.querySelector("#refreshConnections"),
  exportContext: document.querySelector("#exportContext"),
  copyContext: document.querySelector("#copyContext"),
  submitReview: document.querySelector("#submitReview"),
  reviewStatus: document.querySelector("#reviewStatus"),
  reviewFindings: document.querySelector("#reviewFindings"),
  contextOutput: document.querySelector("#contextOutput"),
  codexState: document.querySelector("#codexState"),
  logProductFilter: document.querySelector("#logProductFilter"),
  logCategoryFilter: document.querySelector("#logCategoryFilter"),
  logActionFilter: document.querySelector("#logActionFilter"),
  logEventCount: document.querySelector("#logEventCount"),
  logEventRows: document.querySelector("#logEventRows"),
};

const state = {
  product: "nx",
  result: null,
  parameters: {},
  context: null,
  events: [],
  logFilters: {
    mode: "all",
    product: "all",
    category: "all",
    action: "all",
  },
};

const MAX_VISIBLE_LOGS = 500;
const MODE_LABELS = {
  manual: "手动",
  automation: "自动",
  system: "系统",
};

const PRODUCT_UI = {
  nx: {
    name: "UG / NX",
    format: "nx_journal",
    label: "NX Open Journal",
    recipe: "nx-workflow",
    preview: "NX 预览",
    file: "nx_preview_journal.py",
  },
  powermill: {
    name: "PowerMill",
    format: "powermill_log",
    label: "PowerMill 宏 / JSONL",
    recipe: "powermill-workflow",
    preview: "PowerMill 宏",
    file: "workflow.mac",
  },
};

function setStatus(message, isError = false) {
  elements.analysisState.textContent = message;
  elements.analysisState.classList.toggle("error", isError);
}

function updateLineCount() {
  const value = elements.sourceInput.value;
  elements.lineCount.textContent = `${value ? value.split(/\r?\n/).length : 0} 行`;
}

function collectParameters() {
  const values = {};
  document.querySelectorAll("[data-parameter]").forEach((input) => {
    values[input.dataset.parameter] = input.value;
  });
  return values;
}

function setProduct(product) {
  state.product = product;
  const config = PRODUCT_UI[product];
  document.querySelectorAll("[data-product]").forEach((button) => {
    button.classList.toggle("active", button.dataset.product === product);
  });
  elements.sourceFormat.replaceChildren();
  const formats = product === "nx"
    ? [["nx_journal", "NX Open Journal (.py)"], ["jsonl", "ActivityEvent JSONL"]]
    : [["powermill_log", "PowerMill 宏 / 命令日志"], ["jsonl", "ActivityEvent JSONL"]];
  formats.forEach(([value, label]) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    elements.sourceFormat.append(option);
  });
  elements.recipeName.value = config.recipe;
  elements.sourceLabel.textContent = config.label;
  elements.previewTabLabel.textContent = config.preview;
  elements.previewFileLabel.textContent = config.file;
  elements.fileInput.accept = product === "nx" ? ".py,.jsonl" : ".log,.txt,.mac,.jsonl";
  loadSample();
}

async function analyze() {
  if (!elements.sourceInput.value.trim()) {
    setStatus("输入为空", true);
    return;
  }
  elements.analyzeButton.disabled = true;
  setStatus("正在分析...");
  try {
    const response = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        product: state.product,
        source: elements.sourceInput.value,
        source_format: elements.sourceFormat.value,
        source_name: elements.fileInput.files[0]?.name || "pasted-input",
        name: elements.recipeName.value.trim() || PRODUCT_UI[state.product].recipe,
        parameters: collectParameters(),
        allow_review_steps: elements.allowReview.checked,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "分析失败");
    state.result = payload;
    state.context = payload.codex_context;
    state.events = payload.activity_events || [];
    render(payload);
    setStatus("分析完成");
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    elements.analyzeButton.disabled = false;
  }
}

function render(payload) {
  const recipe = payload.recipe;
  const review = recipe.review;
  elements.sessionMetric.textContent = recipe.source.sessions_analyzed;
  elements.commandMetric.textContent = payload.parse.commands;
  elements.parameterMetric.textContent = recipe.parameters.length;
  elements.reviewMetric.textContent = review.review_steps;
  elements.blockedMetric.textContent = review.blocked_steps;
  elements.matchLabel.textContent =
    `${recipe.source.sessions_matched.length} / ${recipe.source.sessions_analyzed} 会话`;
  elements.recipeOutput.textContent = JSON.stringify(recipe, null, 2);
  elements.previewOutput.textContent = payload.output.text;
  elements.contextOutput.textContent = JSON.stringify(payload.codex_context, null, 2);
  elements.codexState.textContent = "可导出";
  renderSteps(recipe.steps);
  renderParameters(recipe.parameters);
  renderDiagnostics(recipe.diagnostics, payload.parse.diagnostics);
  rebuildLogFilters();
  drawWorkflow();
}

function eventMode(event) {
  return event.mode || event.params?.mode || "manual";
}

function setLogOptions(select, values, allLabel) {
  const previous = select.value;
  select.replaceChildren();
  const all = document.createElement("option");
  all.value = "all";
  all.textContent = allLabel;
  select.append(all);
  [...new Set(values.filter(Boolean))]
    .sort((left, right) => left.localeCompare(right, "zh-CN"))
    .forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      select.append(option);
    });
  select.value = [...select.options].some((option) => option.value === previous)
    ? previous
    : "all";
}

function rebuildLogFilters() {
  const byMode = state.events.filter((event) =>
    state.logFilters.mode === "all" || eventMode(event) === state.logFilters.mode
  );
  setLogOptions(
    elements.logProductFilter,
    byMode.map((event) => event.product),
    "全部产品",
  );
  state.logFilters.product = elements.logProductFilter.value;

  const byProduct = byMode.filter((event) =>
    state.logFilters.product === "all" || event.product === state.logFilters.product
  );
  setLogOptions(
    elements.logCategoryFilter,
    byProduct.map((event) => event.category),
    "全部类别",
  );
  state.logFilters.category = elements.logCategoryFilter.value;

  const byCategory = byProduct.filter((event) =>
    state.logFilters.category === "all" || event.category === state.logFilters.category
  );
  setLogOptions(
    elements.logActionFilter,
    byCategory.map((event) => event.action),
    "全部动作",
  );
  state.logFilters.action = elements.logActionFilter.value;
  renderLogEvents();
}

function logEventMatches(event) {
  return (state.logFilters.mode === "all" || eventMode(event) === state.logFilters.mode)
    && (state.logFilters.product === "all" || event.product === state.logFilters.product)
    && (state.logFilters.category === "all" || event.category === state.logFilters.category)
    && (state.logFilters.action === "all" || event.action === state.logFilters.action);
}

function renderLogEvents() {
  const matching = state.events.filter(logEventMatches);
  const visible = matching.slice(0, MAX_VISIBLE_LOGS);
  const limited = matching.length > visible.length ? ` · 仅渲染前 ${visible.length} 条` : "";
  elements.logEventCount.textContent =
    `${visible.length} / ${matching.length} 匹配 / ${state.events.length} 总计${limited}`;
  elements.logEventRows.replaceChildren();

  if (!visible.length) {
    const empty = document.createElement("div");
    empty.className = "event-empty";
    empty.textContent = "当前筛选条件下没有日志";
    elements.logEventRows.append(empty);
    return;
  }

  visible.forEach((event) => {
    const params = event.params || {};
    const row = document.createElement("div");
    row.className = "event-row";

    const origin = document.createElement("span");
    origin.className = "event-origin";
    origin.textContent = `${event.session_id} / ${Number(event.seq || 0) + 1}`;
    origin.title = event.timestamp || `源文件第 ${event.source_line || 0} 行`;

    const mode = document.createElement("span");
    const modeValue = eventMode(event);
    mode.className = `mode-badge ${modeValue}`;
    mode.textContent = MODE_LABELS[modeValue] || modeValue;

    const hierarchy = document.createElement("span");
    hierarchy.className = "event-hierarchy";
    hierarchy.textContent = `${event.product} / ${event.category} / ${event.action}`;
    hierarchy.title = hierarchy.textContent;

    const command = document.createElement("code");
    command.className = "event-command";
    command.textContent = params.command || params.api || event.action;
    command.title = command.textContent;

    const risk = document.createElement("span");
    const riskValue = params.risk || "safe";
    risk.className = `risk ${riskValue}`;
    risk.textContent = {
      safe: "可预览",
      review: "需复核",
      blocked: "已阻断",
    }[riskValue] || riskValue;

    row.append(origin, mode, hierarchy, command, risk);
    elements.logEventRows.append(row);
  });
}

function renderSteps(steps) {
  elements.stepList.replaceChildren();
  steps.forEach((step) => {
    const row = document.createElement("div");
    row.className = "step-row";
    const risk = document.createElement("span");
    risk.className = `risk ${step.risk}`;
    risk.textContent = { safe: "可预览", review: "需复核", blocked: "已阻断" }[step.risk];
    const operation = document.createElement("strong");
    operation.textContent = step.operation;
    const command = document.createElement("code");
    command.textContent = step.template;
    command.title = step.template;
    row.append(risk, operation, command);
    elements.stepList.append(row);
  });
}

function renderParameters(parameters) {
  const previous = collectParameters();
  elements.parameterRows.replaceChildren();
  if (!parameters.length) {
    const row = document.createElement("tr");
    row.innerHTML = '<td class="empty-row" colspan="3">未发现跨会话变化参数</td>';
    elements.parameterRows.append(row);
    return;
  }
  parameters.forEach((parameter) => {
    const row = document.createElement("tr");
    const nameCell = document.createElement("td");
    const code = document.createElement("code");
    code.textContent = parameter.name;
    nameCell.append(code);
    const typeCell = document.createElement("td");
    typeCell.textContent = parameter.value_type;
    const valueCell = document.createElement("td");
    const input = document.createElement("input");
    input.className = "parameter-input";
    input.dataset.parameter = parameter.name;
    input.value = previous[parameter.name] ?? parameter.default;
    input.setAttribute("aria-label", parameter.name);
    valueCell.append(input);
    row.append(nameCell, typeCell, valueCell);
    elements.parameterRows.append(row);
  });
}

function renderDiagnostics(recipeDiagnostics, parseDiagnostics) {
  elements.diagnosticList.replaceChildren();
  const messages = [
    ...recipeDiagnostics,
    ...parseDiagnostics.map((item) => `第 ${item.line} 行：${item.message}`),
  ];
  messages.forEach((message) => {
    const item = document.createElement("div");
    item.className = "diagnostic";
    item.textContent = message;
    elements.diagnosticList.append(item);
  });
}

function drawWorkflow() {
  const canvas = elements.canvas;
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.floor(rect.width * ratio));
  canvas.height = Math.max(1, Math.floor(rect.height * ratio));
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, rect.width, rect.height);
  const steps = state.result?.recipe?.steps || [];
  if (!steps.length) {
    context.fillStyle = "#68716f";
    context.font = '12px "Segoe UI"';
    context.fillText("等待工作流", 18, 28);
    return;
  }
  const colors = { safe: "#0c725c", review: "#a46100", blocked: "#b43d35" };
  const padding = 30;
  const y = rect.height / 2;
  const usable = Math.max(1, rect.width - padding * 2);
  const gap = steps.length > 1 ? usable / (steps.length - 1) : 0;
  context.lineWidth = 2;
  context.strokeStyle = "#c5ccca";
  context.beginPath();
  context.moveTo(padding, y);
  context.lineTo(rect.width - padding, y);
  context.stroke();
  steps.forEach((step, index) => {
    const x = steps.length === 1 ? rect.width / 2 : padding + index * gap;
    context.fillStyle = colors[step.risk];
    context.beginPath();
    context.arc(x, y, 8, 0, Math.PI * 2);
    context.fill();
    context.strokeStyle = "#ffffff";
    context.lineWidth = 2;
    context.stroke();
    const label = step.operation.length > 16 ? `${step.operation.slice(0, 14)}…` : step.operation;
    context.save();
    context.translate(x, y + 18);
    context.rotate(-Math.PI / 4);
    context.fillStyle = "#4d5855";
    context.font = '10px "Segoe UI"';
    context.textAlign = index === 0 ? "left" : index === steps.length - 1 ? "right" : "center";
    context.fillText(label, 0, 0, 84);
    context.restore();
  });
}

async function loadSample() {
  try {
    const response = await fetch(`/api/sample?product=${state.product}`);
    const payload = await response.json();
    elements.sourceInput.value = payload.source || payload.log || "";
    updateLineCount();
    await analyze();
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function loadConnections() {
  try {
    const response = await fetch("/api/connections");
    const payload = await response.json();
    elements.connectionList.replaceChildren();
    payload.connections.forEach((connection) => {
      const row = document.createElement("div");
      row.className = "connection-row";
      const dot = document.createElement("span");
      dot.className = `connection-dot ${connection.status}`;
      const label = document.createElement("strong");
      label.textContent = connection.label;
      const detail = document.createElement("small");
      detail.textContent = connection.detail;
      row.append(dot, label, detail);
      elements.connectionList.append(row);
    });
  } catch (error) {
    elements.connectionList.textContent = error.message;
  }
}

async function exportContext() {
  if (!state.context) {
    setStatus("请先分析工作流", true);
    return;
  }
  const response = await fetch("/api/codex/context", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ context: state.context, persist: true }),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "导出失败");
  elements.contextOutput.textContent = JSON.stringify(payload, null, 2);
  elements.codexState.textContent = payload.files ? "已导出到本地" : "已准备";
  setStatus("Codex 上下文已导出");
}

async function submitReview() {
  const findings = elements.reviewFindings.value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
  const response = await fetch("/api/codex/review", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      review_status: elements.reviewStatus.value,
      findings,
      required_gates: findings,
      reviewer: "Codex / operator",
    }),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "保存失败");
  elements.codexState.textContent = payload.review_status;
  setStatus("审阅结论已保存");
}

async function copyText(text, button) {
  await navigator.clipboard.writeText(text);
  const original = button.textContent;
  button.textContent = "已复制";
  setTimeout(() => { button.textContent = original; }, 1000);
}

document.querySelectorAll(".segment").forEach((button) => {
  button.addEventListener("click", () => setProduct(button.dataset.product));
});
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((item) => item.classList.remove("active"));
    tab.classList.add("active");
    document.querySelector(`[data-panel="${tab.dataset.tab}"]`).classList.add("active");
    if (tab.dataset.tab === "overview") requestAnimationFrame(drawWorkflow);
  });
});
elements.sourceInput.addEventListener("input", updateLineCount);
elements.sampleButton.addEventListener("click", loadSample);
elements.analyzeButton.addEventListener("click", analyze);
elements.applyParameters.addEventListener("click", analyze);
elements.allowReview.addEventListener("change", analyze);
elements.refreshConnections.addEventListener("click", loadConnections);
document.querySelectorAll("[data-log-mode]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-log-mode]").forEach((item) => {
      item.classList.toggle("active", item === button);
    });
    state.logFilters.mode = button.dataset.logMode;
    rebuildLogFilters();
  });
});
elements.logProductFilter.addEventListener("change", () => {
  state.logFilters.product = elements.logProductFilter.value;
  state.logFilters.category = "all";
  state.logFilters.action = "all";
  rebuildLogFilters();
});
elements.logCategoryFilter.addEventListener("change", () => {
  state.logFilters.category = elements.logCategoryFilter.value;
  state.logFilters.action = "all";
  rebuildLogFilters();
});
elements.logActionFilter.addEventListener("change", () => {
  state.logFilters.action = elements.logActionFilter.value;
  renderLogEvents();
});
elements.fileButton.addEventListener("click", () => elements.fileInput.click());
elements.fileInput.addEventListener("change", async () => {
  const [file] = elements.fileInput.files;
  if (!file) return;
  elements.sourceInput.value = await file.text();
  updateLineCount();
  await analyze();
});
elements.copyRecipe.addEventListener("click", () => copyText(elements.recipeOutput.textContent, elements.copyRecipe));
elements.copyPreview.addEventListener("click", () => copyText(elements.previewOutput.textContent, elements.copyPreview));
elements.copyContext.addEventListener("click", () => copyText(elements.contextOutput.textContent, elements.copyContext));
elements.exportContext.addEventListener("click", () => exportContext().catch((error) => setStatus(error.message, true)));
elements.submitReview.addEventListener("click", () => submitReview().catch((error) => setStatus(error.message, true)));
elements.downloadPreview.addEventListener("click", () => {
  const blob = new Blob([elements.previewOutput.textContent], { type: "text/plain;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = PRODUCT_UI[state.product].file;
  link.click();
  URL.revokeObjectURL(link.href);
});

new ResizeObserver(drawWorkflow).observe(elements.canvas);
setProduct("nx");
loadConnections();
