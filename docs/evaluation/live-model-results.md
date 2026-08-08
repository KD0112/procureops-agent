# 真实模型小样本结果

## 文本 Intake

- Provider / model：`deepseek / deepseek-v4-flash`
- 用例：10 条中文自然语言采购请求，包含别名、括号、紧急语气、提示注入和“不要猜价格”边界
- 首轮：5/10；4 条返回空行，1 条响应合同解析失败
- 改进：把抽象 Schema 名改为显式 JSON 合同，要求中文数量转数字、SKU 原样保留、文档指令视为不可信数据
- 同集复测：10/10，P95 约 7.4 秒，总计 6,974 tokens，供应商配置未提供价格所以报告成本为 0

这不是生产准确率结论。数据量很小，而且 Prompt 改进与复测使用同一集合，存在过拟合风险；下一步应建立独立 holdout 和人工标注业务样本。

## 图片 Intake

- Fixture：ImageGen 生成并明确盖有 `DEMO - NOT FOR PURCHASE` 的采购申请照片
- Provider / model：`zhipu / glm-4.1v-thinking-flash`
- 首轮：模型调用成功但返回空行
- 显式视觉 JSON 合同后复测：通过，识别 `DEMO-ELEC-SENSOR-001` 和数量 `4`
- 延迟约 9.1 秒，2,531 tokens，配置成本为 0

## 运行隔离

普通 `pytest`、100 条端到端评测和默认 Worker 均不调用真实模型。真实评测只能通过 `scripts/run_live_model_eval.py` 和 `scripts/run_live_vision_smoke.py` 显式触发；输入正文不写报告，只保存 SHA-256 和结构化输出。
