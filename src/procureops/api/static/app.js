const headers = {
  "X-Tenant-ID": "tenant_engineering_machinery",
  "X-Actor-ID": "local-demo-user",
  "X-Actor-Roles": "procurement_operator,department_approver,compliance_approver",
};
const state = { tasks: [], selected: null, detail: null };
const $ = (selector) => document.querySelector(selector);
const statusLabel = {
  draft: "草稿", ingesting: "解析中", needs_input: "待补充", matching: "目录匹配",
  sourcing: "供应商查询", calculating: "成本计算", risk_review: "风险检查",
  awaiting_approval: "待审批", approved: "已批准", po_drafted: "PO 已生成",
  completed: "已完成", failed_retryable: "可重试失败", failed_terminal: "已终止",
};

async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { ...headers, ...(options.headers || {}) } });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `请求失败：${response.status}`);
  return payload;
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

$("#new-task-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const text = $("#task-text").value.trim(); const file = $("#task-file").files[0];
  if (!text && !file) { $("#form-error").textContent = "请输入采购需求或选择文件。"; return; }
  const button = $("#submit-task"); button.disabled = true; button.textContent = "正在创建…";
  try {
    let result;
    if (file) { const form = new FormData(); form.append("file", file); result = await api("/api/tasks/upload", { method: "POST", body: form }); }
    else { result = await api("/api/tasks/text", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) }); }
    state.selected = result.task_id; $("#new-task-dialog").close(); $("#new-task-form").reset(); toast("任务已创建并持久化"); await loadTasks();
  } catch (error) { $("#form-error").textContent = error.message; }
  finally { button.disabled = false; button.textContent = "创建并进入队列"; }
});

function escapeHtml(value) { return String(value).replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]); }
$("#new-task-button").addEventListener("click", openDialog); $("#welcome-new-button").addEventListener("click", openDialog); $("#refresh-button").addEventListener("click", () => loadTasks()); $("#worker-button").addEventListener("click", runWorker);
loadTasks().catch((error) => toast(error.message)); window.setInterval(() => { if (state.selected) loadDetail(state.selected).catch(() => {}); }, 4000);
