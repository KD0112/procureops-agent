---
document_id: EM-GUIDE-CATALOG-001
tenant_id: tenant_engineering_machinery
document_type: product_matching_guide
version: 1.0.0
status: approved
owner: Catalog Management
effective_from: 2026-08-08
review_due: 2026-11-08
classification: internal
contains_dynamic_facts: false
allowed_roles: [requester, procurement_operator, procurement_specialist, catalog_manager, auditor]
source_kind: adapted_from_day1_static_knowledge
---

# 工程机械配件目录匹配指南

## 适配证据优先级

1. 原厂零件号或可核验的旧件号。
2. 总成铭牌、发动机号、泵号或阀体号。
3. 设备品牌、完整型号、年份和配置。
4. 安装位置、接口、尺寸和清晰照片。
5. 客户简称、故障现象和使用经验。

低优先级证据不能覆盖高优先级冲突。只有“PC200 液压泵”之类描述时，可以召回候选，但不得自动确认唯一 SKU。

## 主要类别与确认字段

### 液压系统

液压泵、主控阀、行走马达和回转马达需要核对总成编号、铭牌、接口和安装位置。行走马达与回转马达不能因为都被简称为“马达”而混用。

### 发动机与燃油系统

喷油器、喷油泵、涡轮增压器和大修件需要核对发动机型号和零件号，不能只按整机型号匹配。

### 底盘件

支重轮、托链轮、引导轮、驱动齿、链轨和履带板需要核对机型、安装尺寸、链节参数和工况。尺寸相近的普通低强度螺栓不能替代专用紧固件。

### 电器件

传感器、电磁阀、电脑板和显示屏需要核对零件号、插头针脚、接口和程序版本。外观相似不代表兼容。

### 保养耗材

滤芯类仍需要确认设备型号、发动机型号和滤芯编号；“一套滤芯”必须明确包含哪些部件和数量。

## 同义词示例

- 液压泵：主泵、大泵、泵总成
- 主控阀：分配阀、多路阀、阀总成
- 行走马达：终传动、行走总成
- 支重轮：下托轮、底轮
- 托链轮：上托轮、托轮

同义词只用于召回候选，不是自动适配证据。

## 必须人工确认

- 零件号与设备型号冲突。
- 候选 Top 2 分数接近且关键规格不同。
- 替代件或再制造件。
- 高价值液压、发动机和电器总成。
- 图片模糊、铭牌缺失或尺寸不完整。
