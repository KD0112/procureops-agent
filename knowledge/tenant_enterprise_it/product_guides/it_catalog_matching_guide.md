---
document_id: IT-GUIDE-CATALOG-001
tenant_id: tenant_enterprise_it
document_type: product_matching_guide
version: 1.0.0
status: approved
owner: IT Asset Management
effective_from: 2026-08-08
review_due: 2026-11-08
classification: internal
contains_dynamic_facts: false
allowed_roles: [requester, procurement_operator, procurement_specialist, catalog_manager, auditor]
source_kind: synthetic_demo
---

# 企业 IT 设备目录匹配指南

## 匹配证据优先级

1. 已批准的资产标准 SKU；
2. 厂商料号、配置编码或维保 SKU；
3. CPU、内存、存储、接口、许可和维保等完整规格；
4. 设备类别、使用场景和部门简称；
5. 自然语言中的“高配”“开发机”“服务器”等模糊描述。

低优先级描述只用于召回，不能覆盖资产标准冲突。

## 研发笔记本

`IT-LAP-DEV-14` 是本租户的 14 英寸研发笔记本合成标准 SKU，目录核对重点包括内存、存储、三年上门维保和扩展坞兼容性。“研发笔记本”“开发笔记本”“程序员电脑”可以召回候选，但规格不完整时仍应追问。

## 服务器与存储

机架服务器需要核对 CPU 路数、内存条布局、RAID、磁盘接口、网卡、电源冗余和维保。企业级 SSD 还需核对容量、接口、耐久度和服务器兼容列表。

## 网络与安全设备

交换机需要核对端口数量、PoE 预算、堆叠、光模块和维保。防火墙需要核对吞吐、并发、HA、许可订阅和安全评审；硬件型号一致但许可不同不能视为同一 SKU。

## 必须人工确认

- 用户允许替代，但候选的维保、接口或许可不同；
- Top 2 候选接近且资产标准不同；
- 服务器、网络或安全设备缺少关键技术规格；
- 厂商声明、需求文本与资产标准存在冲突。
