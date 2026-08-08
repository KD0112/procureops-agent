# 真实模型小样本结果

## 文本 Intake

- Provider / model：`deepseek / deepseek-v4-flash`
- 用例：版本化 `model_gold_v1` 共 20 条，新增英文、字段标签、字段换序、小数数量、XML 注入和输出劫持
- 首轮：5/10；4 条返回空行，1 条响应合同解析失败
- 改进：把抽象 Schema 名改为显式 JSON 合同，要求中文数量转数字、SKU 原样保留、文档指令视为不可信数据
- 旧 10 条同集复测：10/10，P95 约 7.4 秒，总计 6,974 tokens
- 最新 20 条独立扩展 Gold Set：17/20（85%），P95 约 7.2 秒，总计 14,720 tokens
- 三个失败：`gold-012` 字段标签输入触发结构校验失败；`gold-015` 将 SKU 误放到设备型号；`gold-016` 缺单位的重排字段触发结构校验失败
- 供应商配置未提供价格，所以报告成本字段为 0；这不表示真实 API 永久免费

这不是生产准确率结论。17/20 被原样保留为真实基线，不删除失败案例。三个失败应进入反馈工作台，形成 Prompt 候选后先跑离线基线/候选回归，再用真实模型和独立 holdout 复测。

## 图片 Intake

- Fixture：ImageGen 生成并明确盖有 `DEMO - NOT FOR PURCHASE` 的采购申请照片
- Provider / model：`zhipu / glm-4.1v-thinking-flash`
- 首轮：模型调用成功但返回空行
- 显式视觉 JSON 合同后复测：通过，识别 `DEMO-ELEC-SENSOR-001` 和数量 `4`
- 延迟约 9.1 秒，2,531 tokens，配置成本为 0

## 运行隔离

普通 `pytest`、100 条端到端评测和默认 Worker 均不调用真实模型。真实评测只能通过 `scripts/run_live_model_eval.py` 和 `scripts/run_live_vision_smoke.py` 显式触发；输入正文不写报告，只保存 SHA-256 和结构化输出。
