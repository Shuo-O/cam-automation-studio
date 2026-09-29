(() => {
  "use strict";

  const API = "/api/delivery";
  const gates = [
    { key: "review", label: "人工审阅" },
    { key: "simulation", label: "CAM 仿真" },
    { key: "collision", label: "碰撞检查" },
    { key: "shop_approval", label: "现场批准" }
  ];
  const state = {
    status: null, snapshots: [], cases: [], selectedSnapshot: null,
    selectedCase: null, proposal: null, assessment: null, evidence: {}
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const text = (value, fallback = "未提供") => value === undefined || value === null || value === "" ? fallback : String(value);
  const escapeHtml = (value) => text(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[char]));
  const geometryKinds = new Set(["geometry", "body", "solid", "part", "component", "face", "edge", "surface", "mesh", "pocket", "hole", "slot", "feature"]);
  function collectionItems(value, prefix) {
    if (!value) return [];
    if (Array.isArray(value)) return value.map((item, index) => typeof item === "object" && item ? item : { name: String(item), object_id: `${prefix}-${index + 1}` });
    if (typeof value === "object") {
      if (value.object_id || value.name || value.kind) return [value];
      return Object.entries(value).map(([name, item], index) => typeof item === "object" && item ? { ...item, name: item.name || name, object_id: item.object_id || `${prefix}-${index + 1}` } : { name, object_id: `${prefix}-${index + 1}`, value: item });
    }
    return [{ name: String(value), object_id: `${prefix}-1` }];
  }
  function conditionList(values) {
    return Array.isArray(values) && values.length ? values.map((value) => `<span>${escapeHtml(value)}</span>`).join("") : `<span class="condition-none">无</span>`;
  }

  function showAlert(message, kind = "error") {
    const alert = $("#globalAlert");
    alert.textContent = message;
    alert.className = `alert ${kind === "info" ? "info" : ""}`;
    alert.hidden = false;
  }
  function clearAlert() { $("#globalAlert").hidden = true; }
  function setBusy(button, busy) {
    if (!button) return;
    if (busy) { button.dataset.label = button.textContent; button.disabled = true; button.textContent = "处理中…"; }
    else { button.disabled = false; if (button.dataset.label) button.textContent = button.dataset.label; }
  }
  async function request(path, options = {}) {
    const response = await fetch(`${API}${path}`, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options
    });
    let payload = null;
    try { payload = await response.json(); } catch (_) { payload = {}; }
    if (!response.ok) {
      const error = new Error(payload.error || `请求失败（${response.status}）`);
      error.payload = payload; error.status = response.status; throw error;
    }
    return payload;
  }
  async function readJsonFile(file) {
    const content = await file.text();
    try { return JSON.parse(content); } catch (_) { throw new Error("文件不是有效的 JSON。"); }
  }
  function provenance(value) {
    const source = String(value?.source || value?.provenance?.source || "imported").toLowerCase();
    return source === "fixture" ? "演示 fixture" : source === "imported" ? "客户导入" : source;
  }
  function hashShort(value) { return text(value, "").replace(/^sha256:/, "").slice(0, 12) || "未计算"; }
  function renderStatus() {
    const status = state.status || {};
    const caps = status.capabilities || {};
    const products = caps.products && typeof caps.products === "object" ? Object.keys(caps.products) : [];
    const productLabel = products.length ? `${products.length} 个产品 · ${products.map((product) => product === "powermill" ? "PowerMill" : product.toUpperCase()).join(" / ")}` : "待加载";
    $("#serviceDot").className = `status-dot ${state.status ? "ok" : "error"}`;
    $("#serviceLabel").textContent = state.status ? "本地 API 已就绪" : "本地 API 不可用";
    const items = [
      ["交付模式", text(status.mode, "offline_review"), "ok"],
      ["生产动作", status.dry_run === false ? "需核查" : "dry-run", status.dry_run === false ? "warn" : "ok"],
      ["机床输出", status.machine_output_enabled ? "已启用" : "已禁用", status.machine_output_enabled ? "warn" : "ok"],
      ["能力", productLabel, "ok"]
    ];
    $("#statusCards").innerHTML = items.map(([label, value, kind]) => `<div class="status-card"><small>${escapeHtml(label)}</small><strong class="${kind}">${escapeHtml(value)}</strong></div>`).join("");
    $("#sourceNotice").textContent = status.mode === "offline_review" ? "当前为本机离线审阅模式；宿主连接需另行验证。" : "真实数据与演示来源会在对象卡片中标明。";
  }
  function renderSnapshots() {
    const host = $("#snapshotList");
    $("#snapshotCount").textContent = `${state.snapshots.length} 个快照`;
    if (!state.snapshots.length) {
      host.className = "resource-list empty-state";
      host.innerHTML = `<span class="empty-icon">○</span><strong>还没有制造上下文</strong><p>导入 JSON 快照或先载入本地示例。</p>`;
      return;
    }
    host.className = "resource-list";
    host.innerHTML = state.snapshots.map((item) => {
      const selected = state.selectedSnapshot?.snapshot_id === item.snapshot_id ? " selected" : "";
      return `<article class="resource-card${selected}" data-snapshot-id="${escapeHtml(item.snapshot_id)}" tabindex="0" role="button">
        <header><h4>${escapeHtml(item.project_id || item.name || item.snapshot_id)}</h4><span class="tag ${provenance(item) === "演示 fixture" ? "warn" : "neutral"}">${escapeHtml(provenance(item))}</span></header>
        <p>${escapeHtml(item.product)} · ${escapeHtml(item.target_version)} · ${escapeHtml(item.material)}</p>
        <div class="resource-meta"><span>${escapeHtml(item.units || "mm")}</span><span>${Array.isArray(item.objects) ? item.objects.length : 0} 个对象</span><span title="${escapeHtml(item.content_hash)}">hash ${escapeHtml(hashShort(item.content_hash))}</span></div>
      </article>`;
    }).join("");
    $$(".resource-card[data-snapshot-id]", host).forEach((card) => {
      card.addEventListener("click", () => selectSnapshot(card.dataset.snapshotId));
      card.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); selectSnapshot(card.dataset.snapshotId); } });
    });
  }
  function renderBinding() {
    const host = $("#bindingContent");
    const snapshot = state.selectedSnapshot;
    if (!snapshot) {
      $("#bindingBadge").className = "tag neutral"; $("#bindingBadge").textContent = "未选择";
      host.className = "binding-content empty-state";
      host.innerHTML = `<span class="empty-icon">⌖</span><strong>选择一个快照</strong><p>选择后可检查几何、毛坯、夹具和刀具对象。</p>`;
      return;
    }
    const objects = Array.isArray(snapshot.objects) ? snapshot.objects : [];
    const kinds = [
      ["geometry", "几何/加工对象"], ["stock", "毛坯对象"], ["fixture", "夹具对象"], ["tool", "刀具对象"]
    ];
    const rows = kinds.map(([kind, label]) => {
      const field = kind === "stock" ? "stock" : kind === "fixture" ? "fixtures" : kind === "tool" ? "tools" : null;
      const matches = field
        ? objects.filter((object) => object.kind === kind).concat(collectionItems(snapshot[field], field))
        : objects.filter((object) => geometryKinds.has(String(object.kind || "").toLowerCase()) || object.kind === kind);
      const valid = matches.length === 1 ? "ok" : matches.length ? "warn" : "neutral";
      const stateLabel = matches.length === 1 ? "唯一绑定" : matches.length ? `${matches.length} 个候选` : "未提供";
      return `<div class="binding-row"><div><strong>${label}</strong><small>${matches.map((item) => escapeHtml(item.name || item.object_id || item.kind)).join("、") || "快照未提供此类对象"}</small></div><span class="binding-status ${valid}">${stateLabel}</span></div>`;
    });
    const allGood = kinds.every(([kind]) => {
      const field = kind === "stock" ? "stock" : kind === "fixture" ? "fixtures" : kind === "tool" ? "tools" : null;
      const matches = field ? objects.filter((object) => object.kind === kind).concat(collectionItems(snapshot[field], field)) : objects.filter((object) => geometryKinds.has(String(object.kind || "").toLowerCase()));
      return matches.length === 1;
    });
    $("#bindingBadge").className = `tag ${allGood ? "ok" : "warn"}`; $("#bindingBadge").textContent = allGood ? "唯一绑定" : "需补充";
    host.className = "binding-content"; host.innerHTML = rows.join("");
  }
  async function loadSnapshots() {
    try { const payload = await request("/snapshots"); state.snapshots = Array.isArray(payload.items) ? payload.items : []; renderSnapshots(); }
    catch (error) { showAlert(`无法加载快照：${error.message}`); }
  }
  async function selectSnapshot(snapshotId) {
    clearAlert();
    try {
      const loaded = await request(`/snapshots/${encodeURIComponent(snapshotId)}`);
      if (state.selectedSnapshot?.snapshot_id !== loaded.snapshot_id) {
        state.proposal = null; state.assessment = null; state.evidence = {}; state.selectedCase = null;
        localStorage.removeItem("cam.delivery.lastProposalId");
      }
      state.selectedSnapshot = loaded; renderSnapshots(); renderBinding();
      $("#searchContext").textContent = `${text(loaded.product)} · ${text(loaded.project_id)} 已选`;
      await searchCases();
      showAlert("已选择制造上下文，可继续检索案例。", "info");
    } catch (error) { showAlert(`无法读取快照：${error.message}`); }
  }
  async function importSnapshot(payload) {
    const created = await request("/snapshots", { method: "POST", body: JSON.stringify(payload) });
    state.proposal = null; state.assessment = null; state.evidence = {}; state.selectedCase = null;
    localStorage.removeItem("cam.delivery.lastProposalId");
    state.selectedSnapshot = created; await loadSnapshots(); renderBinding();
    $("#searchContext").textContent = `${text(created.product)} · ${text(created.project_id)} 已选`;
    showAlert("快照已导入并固定哈希。", "info");
  }
  async function searchCases() {
    if (!state.selectedSnapshot) { $("#caseList").className = "resource-list empty-state"; $("#caseList").innerHTML = `<span class="empty-icon">▱</span><strong>请先选择快照</strong><p>案例检索会按产品和制造条件隔离。</p>`; return; }
    try {
      const query = encodeURIComponent($("#caseQuery").value || "");
      const payload = await request("/cases/search", { method: "POST", body: JSON.stringify({ snapshot_id: state.selectedSnapshot.snapshot_id, query: decodeURIComponent(query) }) });
      state.cases = Array.isArray(payload.items) ? payload.items : []; renderCases();
    } catch (error) { showAlert(`案例检索失败：${error.message}`); }
  }
  function renderCases() {
    const host = $("#caseList");
    if (!state.cases.length) { host.className = "resource-list empty-state"; host.innerHTML = `<span class="empty-icon">▱</span><strong>没有适用案例</strong><p>换一个关键词，或导入经过审阅的历史案例。</p>`; return; }
    host.className = "resource-list";
    host.innerHTML = state.cases.map((item) => {
      const match = item.match || {};
      return `<article class="resource-card${state.selectedCase?.case_id === item.case_id ? " selected" : ""}" data-case-id="${escapeHtml(item.case_id)}" tabindex="0" role="button">
      <header><h4>${escapeHtml(item.name || item.case_id)}</h4><span class="tag neutral">${escapeHtml(item.product)}</span></header>
      <p>${escapeHtml(item.material)} · ${escapeHtml(item.units)} · ${item.machine?.axes ? `${escapeHtml(item.machine.axes)} 轴` : "机床能力未说明"}</p>
      <div class="resource-meta">${(item.tags || []).slice(0, 4).map((tag) => `<span>${escapeHtml(tag)}</span>`).join("")}<span class="provenance">${escapeHtml(provenance(item))}</span></div>
      <div class="match-details"><strong>匹配依据</strong><div class="condition-list matched">${conditionList(match.matched_conditions)}</div><strong>缺失条件</strong><div class="condition-list missing">${conditionList(match.missing_conditions)}</div>${match.explanation ? `<p class="match-explanation">${escapeHtml(match.explanation)}</p>` : ""}</div>
    </article>`;
    }).join("");
    $$(".resource-card[data-case-id]", host).forEach((card) => {
      const activate = async () => {
        state.selectedCase = state.cases.find((item) => item.case_id === card.dataset.caseId);
        renderCases(); goStep("proposal"); await createProposal();
      };
      card.addEventListener("click", activate); card.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); activate(); } });
    });
  }
  async function importCase(payload) {
    await request("/cases", { method: "POST", body: JSON.stringify(payload) });
    await searchCases(); showAlert("案例已导入，可在结果中选择。", "info");
  }
  async function createProposal(parameters = null) {
    if (!state.selectedSnapshot || !state.selectedCase) return;
    try {
      const payloadParameters = parameters || state.selectedCase.parameters || {};
      const proposal = await request("/proposals", { method: "POST", body: JSON.stringify({ snapshot_id: state.selectedSnapshot.snapshot_id, case_id: state.selectedCase.case_id, parameters: payloadParameters }) });
      state.proposal = proposal; state.assessment = null; state.evidence = {}; localStorage.setItem("cam.delivery.lastProposalId", proposal.proposal_id);
      renderProposal(); renderEvidence(); renderExport(); showAlert("建议已建立，状态仍需人工门禁。", "info");
    } catch (error) { showAlert(`建立建议失败：${error.message}`); }
  }
  function renderProposal() {
    const proposal = state.proposal;
    if (!proposal) { $("#proposalEmpty").hidden = false; $("#proposalView").hidden = true; $("#proposalBadge").className = "tag neutral"; $("#proposalBadge").textContent = "尚未建立"; return; }
    $("#proposalEmpty").hidden = true; $("#proposalView").hidden = false;
    $("#proposalBadge").className = `tag ${proposal.dry_run === false ? "danger" : "warn"}`; $("#proposalBadge").textContent = proposal.dry_run === false ? "需核查执行模式" : "dry-run / 待审阅";
    const values = [["建议 ID", proposal.proposal_id], ["案例哈希", hashShort(proposal.case_hash)], ["快照哈希", hashShort(proposal.snapshot_hash)], ["建议哈希", hashShort(proposal.proposal_hash)]];
    $("#proposalSummary").innerHTML = values.map(([label, value]) => `<div class="summary-item"><small>${escapeHtml(label)}</small><strong title="${escapeHtml(value)}">${escapeHtml(value)}</strong></div>`).join("");
    $("#proposalParameters").value = JSON.stringify(proposal.parameters || {}, null, 2);
    renderSelectorBindings(proposal);
    if (!state.assessment) { $("#assessmentBadge").className = "tag neutral"; $("#assessmentBadge").textContent = "待检查"; $("#assessmentContent").innerHTML = `<div class="empty-state"><strong>尚未检查</strong><p>点击“重新检查”获取绑定和缺失条件。</p></div>`; }
    else renderAssessment();
  }
  function renderSelectorBindings(proposal) {
    const host = $("#selectorBindingList");
    const bindings = Array.isArray(proposal?.selector_bindings) ? proposal.selector_bindings : [];
    if (!bindings.length) {
      host.innerHTML = `<div class="selector-empty">该建议没有带 selector 的步骤；仍需按案例步骤完成审阅。</div>`;
      return;
    }
    host.innerHTML = `<div class="selector-heading">案例步骤的真实对象绑定</div>${bindings.map((binding) => {
      const status = binding.status || "unresolved";
      const matches = Array.isArray(binding.matches) ? binding.matches : [];
      const statusLabel = status === "resolved" ? "唯一命中" : status === "ambiguous" ? "多重命中" : "未命中";
      const names = matches.map((item) => item.name || item.object_id || item.kind).filter(Boolean);
      return `<div class="selector-binding-row"><div><strong>${escapeHtml(binding.step_id || "未命名步骤")}</strong><small>selector：${escapeHtml(JSON.stringify(binding.selector || {}))}</small><small>matches：${escapeHtml(names.join("、") || "无")}</small></div><span class="binding-status ${status === "resolved" ? "ok" : status === "ambiguous" ? "warn" : "danger"}">${statusLabel}</span></div>`;
    }).join("")}`;
  }
  function renderAssessment() {
    const assessment = state.assessment || {};
    const blocked = assessment.ready === false || assessment.status === "blocked" || assessment.blocked;
    $("#assessmentBadge").className = `tag ${blocked ? "warn" : "ok"}`; $("#assessmentBadge").textContent = blocked ? "未就绪" : "条件齐全";
    const items = assessment.checks || assessment.conditions || assessment.missing_conditions || assessment.blocking_reasons || [];
    if (!items.length) { $("#assessmentContent").innerHTML = `<div class="empty-state"><strong>${blocked ? "仍有待核查项" : "检查完成"}</strong><p>${escapeHtml(assessment.reason || "服务未返回可展示的检查明细。")}</p></div>`; return; }
    $("#assessmentContent").innerHTML = `<ul class="assessment-list">${items.map((item) => {
      const ok = item.passed === true || item.status === "passed" || item.resolved === true;
      const rawLabel = typeof item === "string" ? item : (item.label || item.name || item.condition || item.reason || item);
      const label = translateGateDiagnostic(rawLabel);
      return `<li><span class="mark ${ok ? "ok" : "warn"}">${ok ? "✓" : "!"}</span><span>${escapeHtml(label)}</span></li>`;
    }).join("")}</ul>`;
  }
  function translateGateDiagnostic(value) {
    const label = String(value);
    const names = { review: "人工审阅", simulation: "CAM 仿真", collision: "碰撞检查", shop_approval: "现场批准" };
    const match = label.match(/^(review|simulation|collision|shop_approval) gate is (pending|failed|passed)\.?$/i);
    if (!match) return label;
    const stateLabels = { pending: "待登记", failed: "未通过", passed: "已通过" };
    return `${names[match[1]]}门禁${stateLabels[match[2].toLowerCase()]}`;
  }
  async function assessProposal() {
    if (!state.proposal || !state.selectedSnapshot) return;
    try {
      state.assessment = await request(`/proposals/${encodeURIComponent(state.proposal.proposal_id)}/assess`, { method: "POST", body: JSON.stringify({ snapshot_id: state.selectedSnapshot.snapshot_id }) });
      state.evidence = Object.fromEntries((state.assessment.evidence || []).map((item) => [item.gate, item]));
      renderProposal(); renderEvidence(); renderExport();
    }
    catch (error) { showAlert(`建议检查失败：${error.message}`); }
  }
  function renderEvidence() {
    const host = $("#evidenceGateList");
    if (!state.proposal) { host.innerHTML = `<div class="empty-state large"><span class="empty-icon">◇</span><strong>请先建立建议</strong><p>证据必须绑定具体建议和快照。</p></div>`; $("#evidenceSummary").textContent = "0 / 4 已登记"; return; }
    host.innerHTML = gates.map((gate, index) => {
      const current = state.evidence[gate.key] || {};
      return `<article class="evidence-card" data-gate="${gate.key}"><header><div><span class="gate-index">${index + 1}</span><h3 class="gate-name">${gate.label}</h3></div><span class="tag ${current.status === "passed" ? "ok" : current.status === "failed" ? "danger" : "neutral"} gate-status">${current.status === "passed" ? "已通过" : current.status === "failed" ? "未通过" : "未登记"}</span></header>
      <div class="evidence-form"><label>状态<select class="gate-input gate-state"><option value="">请选择</option><option value="passed"${current.status === "passed" ? " selected" : ""}>已通过</option><option value="failed"${current.status === "failed" ? " selected" : ""}>未通过</option></select></label><label>操作者<input class="gate-input gate-actor" type="text" value="${escapeHtml(current.actor)}" placeholder="姓名或工号"></label><label class="wide">备注<textarea class="gate-input gate-note" rows="2" placeholder="记录检查范围、版本或现场说明">${escapeHtml(current.note)}</textarea></label></div><button class="button quiet gate-save" type="button">登记证据</button></article>`;
    }).join("");
    $$(".gate-save", host).forEach((button) => button.addEventListener("click", () => saveEvidence(button.closest(".evidence-card"))));
    const count = gates.filter((gate) => state.evidence[gate.key]).length; $("#evidenceSummary").textContent = `${count} / 4 已登记`;
  }
  async function saveEvidence(card) {
    const gate = card.dataset.gate; const status = $(".gate-state", card).value; const actor = $(".gate-actor", card).value.trim(); const note = $(".gate-note", card).value.trim();
    if (!status || !actor || !note) { showAlert("请填写状态、操作者和备注后再登记。"); return; }
    try {
      const payload = { gate, status, actor, note, proposal_hash: state.proposal.proposal_hash, snapshot_hash: state.proposal.snapshot_hash };
      const saved = await request(`/proposals/${encodeURIComponent(state.proposal.proposal_id)}/evidence`, { method: "POST", body: JSON.stringify(payload) });
      state.evidence[gate] = saved; renderEvidence(); renderExport(); showAlert(`${card.querySelector(".gate-name").textContent}证据已登记。`, "info");
    } catch (error) { showAlert(`证据登记失败：${error.message}`); }
  }
  function renderExport() {
    const enabled = Boolean(state.proposal); $("#exportButton").disabled = !enabled;
    const host = $("#exportStatus"); const summary = $("#exportSummary"); const raw = $("#rawExportDetails");
    if (!enabled) { host.className = "export-status empty-state large"; host.innerHTML = `<span class="empty-icon">⇩</span><strong>建议建立后可导出</strong><p>导出内容会包含来源、哈希、证据和当前门禁状态。</p>`; summary.hidden = true; raw.hidden = true; return; }
    const count = Object.keys(state.evidence).length; const blocked = count < 4 || state.assessment?.ready === false;
    const snapshot = state.selectedSnapshot || {}; const assessment = state.assessment || {};
    const blocking = assessment.blocking_reasons || assessment.missing_conditions || [];
    summary.hidden = false; summary.innerHTML = [
      ["产品 / 项目", `${text(snapshot.product)} · ${text(snapshot.project_id)} · ${provenance(snapshot)}`, "来源与项目上下文"],
      ["案例", state.selectedCase?.name || proposalCaseLabel(state.proposal), `建议 ${hashShort(state.proposal.proposal_hash)}`],
      ["证据门禁", `${count} / 4 已登记`, count === 4 ? "四项门禁均有记录" : "未填证据不会自动通过"],
      ["未完成项", blocked ? `${blocking.length || 1} 项待处理` : "无", blocked ? "导出仍会标记 blocked" : "仍需现场流程确认"]
    ].map(([label, value, note]) => `<div class="export-summary-item"><small>${escapeHtml(label)}</small><strong title="${escapeHtml(value)}">${escapeHtml(value)}</strong><em>${escapeHtml(note)}</em></div>`).join("");
    host.className = "export-status ready"; host.innerHTML = `<div><span class="empty-icon">${blocked ? "!" : "✓"}</span><div><strong>${blocked ? "可下载审阅包，尚未就绪" : "证据已齐，可下载审阅包"}</strong><p>${blocked ? `当前登记 ${count} / 4 项证据；导出会保留阻塞原因。` : "请仍按客户流程完成独立仿真和现场批准记录。"}</p></div></div><span class="tag ${blocked ? "warn" : "ok"}">${blocked ? "blocked" : "review_ready"}</span>`; raw.hidden = true;
  }
  function proposalCaseLabel(proposal) { return proposal?.case_id || "案例未加载"; }
  async function exportBundle() {
    if (!state.proposal || !state.selectedSnapshot) return;
    const button = $("#exportButton"); setBusy(button, true);
    try {
      const bundle = await request(`/proposals/${encodeURIComponent(state.proposal.proposal_id)}/export`, { method: "POST", body: JSON.stringify({ snapshot_id: state.selectedSnapshot.snapshot_id }) });
      const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: "application/json;charset=utf-8" });
      const safeId = String(state.proposal.proposal_id).replace(/[^a-zA-Z0-9._-]+/g, "-");
      const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = `cam-delivery-${safeId}.json`; link.click(); URL.revokeObjectURL(url);
      $("#rawExportDetails").hidden = false; $("#exportPreview").textContent = JSON.stringify(bundle, null, 2); showAlert("交付审阅包已下载。", "info");
    } catch (error) { showAlert(`导出失败：${error.message}`); } finally { setBusy(button, false); }
  }
  function goStep(step) {
    $$(".step, .step-panel").forEach((element) => {
      if (element.classList.contains("step")) { element.classList.toggle("active", element.dataset.step === step); element.classList.toggle("done", ["context", "cases", "proposal", "evidence", "export"].indexOf(element.dataset.step) < ["context", "cases", "proposal", "evidence", "export"].indexOf(step)); }
      else { const active = element.dataset.panel === step; element.classList.toggle("active", active); element.hidden = !active; }
    });
    if (step === "evidence") renderEvidence();
    if (step === "export") renderExport();
  }
  function exampleSnapshot() {
    return { schema_version: 1, product: "powermill", instance_id: "pm-fixture", project_id: "demo-cavity", target_version: "PowerMill 2026", units: "mm", material: "P20", machine: { axes: 3 }, objects: [{ object_id: "cavity-1", kind: "pocket", name: "型腔", attributes: { role: "cavity" } }], source: "fixture" };
  }
  function exampleCase() {
    return { schema_version: 1, name: "三轴型腔示例", product: "powermill", material: "P20", units: "mm", machine: { axes: 3 }, tags: ["cavity"], parameters: { stepover: 0.5 }, steps: [{ action: "cam.operation.plan", selector: { kind: "pocket", attributes: { role: "cavity" } }, parameters: {} }], provenance: { source: "fixture", note: "演示案例，未验证加工" } };
  }
  function wireEvents() {
    $$(".step").forEach((button) => button.addEventListener("click", () => goStep(button.dataset.step)));
    $$("[data-go-step]").forEach((button) => button.addEventListener("click", () => goStep(button.dataset.goStep)));
    $("#refreshButton").addEventListener("click", () => initialize());
    $("#reloadSnapshots").addEventListener("click", loadSnapshots);
    $("#searchCases").addEventListener("click", searchCases);
    $("#caseQuery").addEventListener("keydown", (event) => { if (event.key === "Enter") searchCases(); });
    $("#reassessProposal").addEventListener("click", assessProposal);
    $("#regenerateProposal").addEventListener("click", async () => {
      try {
        const parameters = JSON.parse($("#proposalParameters").value || "{}");
        if (!parameters || Array.isArray(parameters) || typeof parameters !== "object") throw new Error("参数必须是 JSON 对象。");
        await createProposal(parameters);
      } catch (error) { showAlert(`参数无效，未重新生成：${error.message}`); }
    });
    $("#exportButton").addEventListener("click", exportBundle);
    $("#loadSnapshotExample").addEventListener("click", async () => { try { await importSnapshot(exampleSnapshot()); } catch (error) { showAlert(`示例快照导入失败：${error.message}`); } });
    $("#loadCaseExample").addEventListener("click", async () => { try { await importCase(exampleCase()); } catch (error) { showAlert(`示例案例导入失败：${error.message}`); } });
    $("#snapshotFile").addEventListener("change", async (event) => { const file = event.target.files[0]; if (!file) return; try { await importSnapshot(await readJsonFile(file)); } catch (error) { showAlert(`快照导入失败：${error.message}`); } event.target.value = ""; });
    $("#caseFile").addEventListener("change", async (event) => { const file = event.target.files[0]; if (!file) return; try { await importCase(await readJsonFile(file)); } catch (error) { showAlert(`案例导入失败：${error.message}`); } event.target.value = ""; });
  }
  async function initialize() {
    try { state.status = await request("/status"); renderStatus(); clearAlert(); }
    catch (error) { state.status = null; renderStatus(); showAlert(`本地 API 未连接：${error.message}`); }
    await loadSnapshots();
    const lastProposalId = localStorage.getItem("cam.delivery.lastProposalId");
    if (lastProposalId) {
      try {
        const restored = await request(`/proposals/${encodeURIComponent(lastProposalId)}`);
        const snapshotId = restored.snapshot_id;
        if (snapshotId) {
          state.selectedSnapshot = await request(`/snapshots/${encodeURIComponent(snapshotId)}`);
          state.proposal = restored;
          await searchCases();
          state.selectedCase = state.cases.find((item) => item.case_id === restored.case_id) || null;
          if (state.selectedSnapshot) state.assessment = await request(`/proposals/${encodeURIComponent(restored.proposal_id)}/assess`, { method: "POST", body: JSON.stringify({ snapshot_id: state.selectedSnapshot.snapshot_id }) });
          state.evidence = Object.fromEntries((state.assessment?.evidence || []).map((item) => [item.gate, item]));
        }
      } catch (_) {
        localStorage.removeItem("cam.delivery.lastProposalId");
      }
    }
    renderBinding(); renderProposal(); renderEvidence(); renderExport();
  }
  wireEvents(); initialize();
})();
