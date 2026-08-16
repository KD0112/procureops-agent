# ProcureOps 面试演示脚本（8 分钟）

## 演示前准备

在项目根目录运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify.ps1
& ".\.venv\Scripts\python.exe" scripts\run_api.py
```

浏览器打开 `http://127.0.0.1:8030`。工作台中的“运行下一项 Worker”按钮用于逐步演示；也可以在第二个终端运行：

```powershell
& ".\.venv\Scripts\python.exe" scripts\run_worker.py --loop
```

图片或完全非结构化文本需要真实模型时，只在当前终端显式启用：

```powershell
$env:PROCUREOPS_ENABLE_LIVE_MODELS="1"
& ".\.venv\Scripts\python.exe" scripts\run_api.py
```

普通测试和默认 Worker 不调用付费模型。

## 0:00–1:00：先讲边界，不先讲聊天

“这个系统以采购任务为中心。左侧是持久化任务队列，中间是结构化明细、证据、状态机和 PO 草稿。LLM 只负责非确定性提取；价格、库存、成本、权限、审批和写入都不交给模型决定。”

指出三条核心不变量：动态事实只来自数据库工具；高风险写入必须绑定审批；重复执行不能产生第二张 PO。

## 1:00–3:20：Happy path 与证据

上传 `demo_assets/requests/procurement_request.pdf`，点击“运行下一项 Worker”。展示：

1. PDF 被解析为两条采购行；
2. 零件号匹配企业目录；
3. 供应商、报价和库存来自 SQLite 工具；
4. 每个关键字段都有来源、定位、时间、置信度和 producer；
5. 状态停在 `awaiting_approval`，没有 PO 副作用。

强调 RAG 只补充制度/手册引用，当前价格和库存从不进入 RAG。

## 3:20–4:30：审批绑定与幂等

点击批准，再运行一次 Worker。展示 `completed`、PO 草稿、金额和幂等键。

解释审批不是布尔值：Grant 绑定 tenant、task、action、规范化参数哈希、审批角色和有效期。任何供应商、金额或采购行变化都会使旧审批失效。再次恢复同一任务时返回同一 `po_draft_id`，不会重复写入。

## 4:30–5:20：缺失信息与失败关闭

创建 `demo_assets/scenarios/02_needs_input.txt`，运行 Worker。展示 `needs_input`，说明系统没有猜零件号、没有查询供应商、没有写 PO。点击“补充信息”，输入完整 CSV 行，再恢复相同任务。

## 5:20–6:10：攻击与权限边界

使用 `03_prompt_injection.txt`：文件中的“忽略规则”只是数据，无法绕过 Tool Gateway。再用 `04_high_value_approval.txt` 说明金额跨阈值后要求 `department_approver`；API 集成测试覆盖 operator-only 返回 403。

## 6:10–7:10：评测驱动架构，而不是堆 Agent

打开 `reports/latest_ab_comparison.json`：同一 100 条数据分别运行单 Agent + 工具和 Supervisor + 专业 Agent。两者完成质量和安全门禁相同，多 Agent 没有带来可证明收益，因此默认保留单 Agent，Supervisor 只作为实验路径。

再打开 `reports/latest_live_model_eval.json`：真实文本模型 10/10；`latest_live_vision_smoke.json`：生成图片字段提取通过。说明真实模型测试独立运行，CI 使用 FakeModel。

## 7:10–8:00：可靠执行与主动承认边界

“本机 Profile 使用 SQLite 队列，语义是 at-least-once delivery + idempotent side effect。Job 有 lease、超时回收、有界重试和 dead-letter；进程退出不会丢任务。SQLite 适合个人可完成的演示，不声称等同生产集群。迁移到 PostgreSQL/队列服务时保持任务、工具、审批和幂等合同不变。”

最后主动说明：当前是合成工程机械数据；第二租户暂缓；生产还需要企业 SSO、真实 ERP/供应商适配器、密钥托管、对象存储和业务标注集。
