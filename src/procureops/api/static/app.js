const state = {
  tasks: [], selected: null, detail: null, memory: [], governance: null, models: null,
  token: window.sessionStorage.getItem("procureops_token"), identity: null,
};
const $ = (selector) => document.querySelector(selector);
const statusLabel = {
  draft: "草稿", ingesting: "解析中", needs_input: "待补充", matching: "目录匹配",
  sourcing: "供应商查询", calculating: "成本计算", risk_review: "风险检查",
  awaiting_approval: "待审批", approved: "已批准", po_drafted: "PO 已生成",
  completed: "已完成", failed_retryable: "可重试失败", failed_terminal: "已终止",
};

async function api(path, options = {}) {
  const auth = state.token ? { Authorization: `Bearer ${state.token}` } : {};
  const response = await fetch(path, { ...options, headers: { ...auth, ...(options.headers || {}) } });
  const payload = await response.json().catch(() => ({}));
  if (response.status === 401 && path !== "/api/auth/login") showLogin();
  if (!response.ok) throw new Error(payload.detail || `请求失败：${response.status}`);
  return payload;
}

function showLogin() {
  const dialog = $("#login-dialog");
  if (!dialog.open) dialog.showModal();
}

async function loadIdentity() {
  state.identity = await api("/api/auth/me");
  $("#identity-status").textContent = `${state.identity.actor_id} · ${state.identity.roles.join(" / ")}`;
}

async function bootstrap() {
  if (!state.token) { showLogin(); return; }
  try {
    await loadIdentity();
    await Promise.all([loadTasks(), loadModelStatus()]);
  } catch (error) {
    state.token = null; state.identity = null;
    window.sessionStorage.removeItem("procureops_token");
    showLogin(); toast(error.message);
  }
}

function toast(message) {
  const node = $("#toast"); node.textContent = message; node.classList.add("show");
  window.setTimeout(() => node.classList.remove("show"), 2400);
}

async function loadTasks(selectNewest = false) {
  const payload = await api("/api/tasks"); state.tasks = payload.items;
  if (selectNewest && state.tasks.length) state.selected = state.tasks[0].task_id;
  renderTaskList();
  if (state.selected) await loadDetail(state.selected); else showEmpty();
}

function renderTaskList() {
  const list = $("#task-list");
  if (!state.tasks.length) { list.innerHTML = '<div class="empty">暂无任务</div>'; return; }
  list.innerHTML = state.tasks.map((task) => {
    const req = task.request || {}; const title = req.filename || req.preview || "采购任务";
    return `<button class="task-card ${task.task_id === state.selected ? "active" : ""}" data-task="${task.task_id}">
      <strong>${escapeHtml(title)}</strong><small>${statusLabel[task.status] || task.status} · ${task.task_id.slice(0, 8)}</small></button>`;
  }).join("");
  document.querySelectorAll("[data-task]").forEach((button) => button.addEventListener("click", () => loadDetail(button.dataset.task)));
}

async function loadDetail(taskId) {
  state.selected = taskId; state.detail = await api(`/api/tasks/${taskId}`); renderTaskList(); renderDetail();
}

function showEmpty() { $("#empty-view").classList.remove("hidden"); $("#detail-view").classList.add("hidden"); }

function renderDetail() {
  const detail = state.detail; const task = detail.task; $("#empty-view").classList.add("hidden"); $("#detail-view").classList.remove("hidden");
  const request = task.request || {}; $("#detail-title").textContent = request.filename || request.preview || "采购任务";
  $("#detail-id").textContent = task.task_id; $("#detail-status").textContent = statusLabel[task.status] || task.status;
  $("#detail-architecture").textContent = architectureLabel(request.architecture || "single");
  $("#metric-lines").textContent = detail.items.length; $("#metric-evidence").textContent = detail.evidence.length; $("#metric-jobs").textContent = detail.jobs.length;
  $("#metric-cost").textContent = detail.po_draft ? `¥ ${detail.po_draft.total_amount}` : pendingCost(detail);
  renderAction(detail); renderItems(detail.items); renderEvidence(detail.evidence); renderEvents(detail.events); renderPo(detail.po_draft);
}

function pendingCost(detail) {
  const req = detail.pending_approval && detail.pending_approval.approval_requirement;
  return req ? `¥ ${req.total_amount}` : "—";
}

function renderAction(detail) {
  const panel = $("#action-panel"); const status = detail.task.status; panel.classList.add("hidden"); panel.innerHTML = "";
  if (status === "awaiting_approval") {
    const req = detail.pending_approval.approval_requirement; panel.classList.remove("hidden");
    panel.innerHTML = `<div><h3>需要人工审批 · ¥ ${req.total_amount} ${req.currency}</h3><p>规则 ${req.ruleset_version} 要求角色：${req.required_roles.join("、")}</p></div><div class="action-buttons"><button class="secondary danger" id="reject-button">拒绝</button><button class="primary" id="approve-button">批准并生成 PO 草稿</button></div>`;
    $("#approve-button").addEventListener("click", () => decide("approve")); $("#reject-button").addEventListener("click", () => decide("reject"));
  } else if (status === "needs_input") {
    panel.classList.remove("hidden"); panel.innerHTML = `<div><h3>任务需要补充或修订信息</h3><p>请提供包含品名、数量、单位及零件号/设备型号的完整需求，系统会保留原始证据。</p></div><div class="action-buttons"><button class="primary" id="answer-button">补充信息</button></div>`;
    $("#answer-button").addEventListener("click", answerTask);
  } else if (["draft", "failed_retryable"].includes(status) || detail.jobs.some((job) => ["pending", "retry"].includes(job.status))) {
    panel.classList.remove("hidden"); panel.innerHTML = `<div><h3>任务已进入持久化队列</h3><p>即使进程退出，Job 仍保存在 SQLite；重新运行 Worker 会继续处理。</p></div><button class="primary" id="process-button">立即处理</button>`;
    $("#process-button").addEventListener("click", runWorker);
  }
}

function renderItems(items) {
  $("#items-body").innerHTML = items.length ? items.map((item) => `<tr><td>${item.line_number}</td><td><strong>${escapeHtml(item.description)}</strong><br><code>${escapeHtml(item.requested_part_number || "—")}</code></td><td>${item.quantity} ${escapeHtml(item.unit)}</td><td>${escapeHtml(item.matched_product_id || "待匹配")}</td><td>${escapeHtml(item.selected_supplier_id || "待选择")}</td><td>${item.match_confidence || "—"}</td></tr>`).join("") : '<tr><td colspan="6" class="empty">Worker 尚未解析出采购行</td></tr>';
}

function renderEvidence(items) {
  $("#evidence-list").innerHTML = items.length ? items.slice().reverse().map((item) => `<div class="feed-item"><strong>${escapeHtml(item.field_name)} · ${Number(item.confidence).toFixed(2)}</strong><span>${escapeHtml(item.source_type)} / ${escapeHtml(item.locator)}</span><span>${escapeHtml(item.producer)}</span></div>`).join("") : '<div class="empty">暂无证据</div>';
}

function renderEvents(items) {
  $("#event-list").innerHTML = items.length ? items.slice().reverse().map((item) => `<div class="timeline-item"><strong>${escapeHtml(item.event_type)}</strong><span>${new Date(item.occurred_at).toLocaleString("zh-CN")}</span></div>`).join("") : '<div class="empty">暂无工作流事件</div>';
}

function renderPo(po) {
  const panel = $("#po-panel"); if (!po) { panel.classList.add("hidden"); return; }
  panel.classList.remove("hidden"); $("#po-content").textContent = JSON.stringify(po, null, 2);
}

async function decide(decision) {
  try { await api(`/api/tasks/${state.selected}/approval`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision }) }); toast(decision === "approve" ? "审批已签发，等待 Worker 恢复" : "任务已拒绝"); await loadDetail(state.selected); } catch (error) { toast(error.message); }
}

async function answerTask() {
  const text = window.prompt("请输入完整修订后的采购需求："); if (!text) return;
  try { await api(`/api/tasks/${state.selected}/answers`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) }); toast("补充信息已进入队列"); await loadDetail(state.selected); } catch (error) { toast(error.message); }
}

async function runWorker() {
  try { const result = await api("/api/admin/worker/run-once", { method: "POST" }); toast(result.processed ? `已处理：${result.outcome.task_status || result.outcome.queue_status}` : "当前没有待处理 Job"); await loadTasks(); } catch (error) { toast(error.message); }
}

function openDialog() { $("#form-error").textContent = ""; $("#new-task-dialog").showModal(); }

function architectureLabel(value) {
  return ({ single: "单 Agent", multi: "确定性多 Agent", multi_llm: "模型多 Agent" })[value] || value;
}

async function loadModelStatus() {
  state.models = await api("/api/models/status");
  const text = state.models.text; const vision = state.models.vision;
  $("#model-status").textContent = `文本 ${text.configured ? `${text.provider}/${text.model}` : "未配置"} · 视觉 ${vision.configured ? `${vision.provider}/${vision.model}` : "未配置"}`;
}

async function openMemory() {
  $("#memory-dialog").showModal();
  await loadMemory();
}

async function loadMemory() {
  const payload = await api("/api/memory"); state.memory = payload.items; renderMemory();
}

function renderMemory() {
  const list = $("#memory-list");
  if (!state.memory.length) { list.innerHTML = '<div class="empty">还没有记忆。可在采购需求中明确说“以后送货请安排在工作日上午”。</div>'; return; }
  list.innerHTML = state.memory.map((item) => `<article class="governance-item">
    <div><strong>${escapeHtml(item.memory_key)}</strong><span class="mini-status ${item.status}">${escapeHtml(item.status)}</span></div>
    <p>${escapeHtml(JSON.stringify(item.value))}</p><small>置信度 ${Number(item.confidence).toFixed(2)} · 到期 ${new Date(item.expires_at).toLocaleDateString("zh-CN")}</small>
    <div class="item-actions">${item.status === "candidate" ? `<button class="primary compact" data-memory-action="confirm" data-id="${item.record_id}">确认</button>` : ""}${item.status === "confirmed" ? `<button class="secondary compact" data-memory-action="correct" data-id="${item.record_id}">纠错</button>` : ""}${["candidate", "confirmed"].includes(item.status) ? `<button class="secondary compact danger" data-memory-action="delete" data-id="${item.record_id}">删除</button>` : ""}</div>
  </article>`).join("");
}

async function handleMemoryAction(event) {
  const button = event.target.closest("[data-memory-action]"); if (!button) return;
  const item = state.memory.find((entry) => entry.record_id === button.dataset.id); if (!item) return;
  try {
    if (button.dataset.memoryAction === "confirm") await api(`/api/memory/${item.record_id}/confirm`, { method: "POST" });
    if (button.dataset.memoryAction === "delete") await api(`/api/memory/${item.record_id}`, { method: "DELETE" });
    if (button.dataset.memoryAction === "correct") {
      const raw = window.prompt("输入纠正后的值（字符串或 JSON）：", JSON.stringify(item.value)); if (raw === null) return;
      let value; try { value = JSON.parse(raw); } catch { value = raw; }
      await api(`/api/memory/${item.record_id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ value }) });
    }
    toast("记忆状态已更新"); await loadMemory();
  } catch (error) { toast(error.message); }
}

async function openGovernance() {
  $("#governance-dialog").showModal(); await loadGovernance();
}

async function loadGovernance() {
  const [governance, models] = await Promise.all([api("/api/governance"), api("/api/models/status")]);
  state.governance = governance; state.models = models; renderGovernance();
}

function renderGovernance() {
  const data = state.governance; const active = data.active_prompt;
  $("#active-prompt").innerHTML = `<strong>${escapeHtml(active.prompt_version)}</strong><p>作用域 ${escapeHtml(active.scope)}</p><small>SHA-256 ${escapeHtml(active.prompt_hash.slice(0, 16))}…</small>${active.prompt_version !== "1.0.0" ? `<button class="secondary compact danger" id="rollback-button" data-release="${active.release_id}">回滚当前版本</button>` : ""}`;
  const textRoutes = (state.models.routes?.text || []).map((item) => `${item.provider}/${item.model}`).join(" → ") || "未配置";
  const visionRoutes = (state.models.routes?.vision || []).map((item) => `${item.provider}/${item.model}`).join(" → ") || "未配置";
  $("#model-routing").innerHTML = `<strong>${state.models.live_models_enabled ? "实时模型已启用" : "实时模型默认关闭"}</strong><p>文本路由：${escapeHtml(textRoutes)}</p><p>视觉路由：${escapeHtml(visionRoutes)}</p><small>配置 DashScope 后 Qwen 自动成为首选；失败会按路由降级并进入熔断审计。</small>`;
  const feedback = data.feedback;
  $("#feedback-list").innerHTML = feedback.length ? feedback.map((item) => `<article class="governance-item"><div><strong>${escapeHtml(item.feedback_type)}</strong><span class="mini-status ${item.status}">${escapeHtml(item.status)}</span></div><p>${escapeHtml(item.summary)}</p><small>${new Date(item.created_at).toLocaleString("zh-CN")}</small></article>`).join("") : '<div class="empty">暂无反馈</div>';
  const candidates = data.candidates.filter((item) => item.candidate_version !== "1.0.0");
  $("#candidate-list").innerHTML = candidates.length ? candidates.map((item) => `<article class="governance-item"><div><strong>${escapeHtml(item.candidate_version)}</strong><span class="mini-status ${item.status}">${escapeHtml(item.status)}</span></div><p>基于 ${escapeHtml(item.base_version)} · ${escapeHtml(item.evaluation_mode || "尚未评测")}</p><small>${escapeHtml(item.prompt_hash.slice(0, 16))}…</small><div class="item-actions">${item.status === "proposed" ? `<button class="secondary compact" data-candidate-action="evaluate" data-id="${item.candidate_id}">离线评测</button>` : ""}${item.status === "evaluated" && item.evaluation_passed ? `<button class="secondary compact" data-candidate-action="approve" data-id="${item.candidate_id}">合规审批</button>` : ""}${item.status === "approved" ? `<button class="primary compact" data-candidate-action="release" data-id="${item.candidate_id}">人工发布</button>` : ""}</div></article>`).join("") : '<div class="empty">还没有 Prompt 候选</div>';
  const rollback = $("#rollback-button"); if (rollback) rollback.addEventListener("click", rollbackRelease);
}

async function createCandidateFromFeedback() {
  const open = state.governance.feedback.filter((item) => item.status === "open");
  if (!open.length) { toast("请先提交一条待处理反馈"); return; }
  const selected = open[0]; const active = state.governance.active_prompt;
  const version = `candidate-${new Date().toISOString().replace(/[-:.TZ]/g, "").slice(0, 14)}`;
  const promptText = `${active.prompt_text} Pay special attention to the reviewed feedback category while preserving every safety and JSON contract above.`;
  if (!window.confirm(`将反馈“${selected.summary}”关联到候选 ${version}。候选不会自动上线，是否继续？`)) return;
  try {
    await api("/api/governance/prompt-candidates", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ candidate_version: version, prompt_text: promptText, feedback_ids: [selected.feedback_id] }) });
    toast("候选已创建，等待离线评测"); await loadGovernance();
  } catch (error) { toast(error.message); }
}

async function handleCandidateAction(event) {
  const button = event.target.closest("[data-candidate-action]"); if (!button) return;
  try {
    await api(`/api/governance/prompt-candidates/${button.dataset.id}/${button.dataset.candidateAction}`, { method: "POST" });
    toast("治理状态已推进"); await loadGovernance();
  } catch (error) { toast(error.message); }
}

async function rollbackRelease(event) {
  if (!window.confirm("确认回滚当前 Prompt 版本？后续新任务会恢复前一版本。")) return;
  try { await api(`/api/governance/releases/${event.currentTarget.dataset.release}/rollback`, { method: "POST" }); toast("已回滚至前一版本"); await loadGovernance(); } catch (error) { toast(error.message); }
}

$("#new-task-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const text = $("#task-text").value.trim(); const file = $("#task-file").files[0]; const architecture = $("#task-architecture").value;
  if (!text && !file) { $("#form-error").textContent = "请输入采购需求或选择文件。"; return; }
  const button = $("#submit-task"); button.disabled = true; button.textContent = "正在创建…";
  try {
    let result;
    if (file) { const form = new FormData(); form.append("file", file); form.append("architecture", architecture); result = await api("/api/tasks/upload", { method: "POST", body: form }); }
    else { result = await api("/api/tasks/text", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, architecture }) }); }
    state.selected = result.task_id; $("#new-task-dialog").close(); $("#new-task-form").reset(); toast("任务已创建并持久化"); await loadTasks();
  } catch (error) { $("#form-error").textContent = error.message; }
  finally { button.disabled = false; button.textContent = "创建并进入队列"; }
});

function escapeHtml(value) { return String(value).replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]); }
$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault(); $("#login-error").textContent = "";
  try {
    const session = await api("/api/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: $("#login-email").value, password: $("#login-password").value, tenant_id: "tenant_engineering_machinery" }),
    });
    state.token = session.token; window.sessionStorage.setItem("procureops_token", session.token);
    $("#login-password").value = ""; $("#login-dialog").close();
    await loadIdentity(); await Promise.all([loadTasks(), loadModelStatus()]); toast("已使用服务端身份登录");
  } catch (error) { $("#login-error").textContent = error.message; }
});
$("#switch-account-button").addEventListener("click", async () => {
  try { if (state.token) await api("/api/auth/logout", { method: "POST" }); } catch {}
  state.token = null; state.identity = null; window.sessionStorage.removeItem("procureops_token");
  $("#identity-status").textContent = "未登录"; showLogin();
});
$("#feedback-form").addEventListener("submit", async (event) => { event.preventDefault(); const summary = $("#feedback-summary").value.trim(); if (!summary) return; try { await api("/api/governance/feedback", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ feedback_type: $("#feedback-type").value, summary, correction: {} }) }); event.target.reset(); toast("反馈已进入治理队列"); await loadGovernance(); } catch (error) { toast(error.message); } });
$("#memory-list").addEventListener("click", handleMemoryAction); $("#candidate-list").addEventListener("click", handleCandidateAction); $("#new-candidate-button").addEventListener("click", createCandidateFromFeedback);
document.querySelectorAll(".dialog-close").forEach((button) => button.addEventListener("click", () => button.closest("dialog").close()));
$("#new-task-button").addEventListener("click", openDialog); $("#welcome-new-button").addEventListener("click", openDialog); $("#memory-button").addEventListener("click", () => openMemory().catch((error) => toast(error.message))); $("#governance-button").addEventListener("click", () => openGovernance().catch((error) => toast(error.message))); $("#refresh-button").addEventListener("click", () => loadTasks()); $("#worker-button").addEventListener("click", runWorker);
bootstrap(); window.setInterval(() => { if (state.token && state.selected) loadDetail(state.selected).catch(() => {}); }, 4000);
