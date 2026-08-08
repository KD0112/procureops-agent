---
document_id: EM-GUIDE-COMPAT-001
tenant_id: tenant_engineering_machinery
document_type: product_compatibility_reference
version: 1.0.0
status: approved
owner: Catalog Management
effective_from: 2026-08-08
review_due: 2026-11-08
classification: internal
contains_dynamic_facts: false
allowed_roles: [requester, procurement_operator, procurement_specialist, catalog_manager, auditor]
source_kind: synthetic_seed_for_demo
---

# 合成配件兼容性参考

本文数据仅用于本地演示与自动化评测，不代表任何真实厂商目录、价格或库存。`DEMO-` 前缀可防止演示 SKU 与真实零件号混淆。

| 演示 SKU | 类别 | 常见叫法 | 适用系列标签 | 自动确认所需证据 | 缺失时处理 |
|---|---|---|---|---|---|
| DEMO-HYD-PUMP-001 | 液压泵 | 主泵、大泵 | EX200-A | 泵铭牌号 + 接口照片 | 只返回候选 |
| DEMO-HYD-VALVE-001 | 主控阀 | 分配阀、多路阀 | EX200-A | 阀体号 + 安装位置 | 人工核对 |
| DEMO-TRV-MOTOR-001 | 行走总成 | 行走马达、终传动 | EX200-A | 总成号 + 左右侧 | 询问左右侧 |
| DEMO-SWG-MOTOR-001 | 回转马达 | 旋转马达 | EX200-A | 总成号 + 接口布局 | 不得与行走马达互换 |
| DEMO-ENG-INJ-001 | 喷油器 | 油嘴、喷油嘴 | EN-6C-A | 发动机型号 + 喷油器号 | 询问发动机铭牌 |
| DEMO-ENG-TURBO-001 | 增压器 | 涡轮、涡轮增压器 | EN-6C-A | 增压器号 + 法兰照片 | 人工核对 |
| DEMO-UC-ROLLER-001 | 支重轮 | 下托轮、底轮 | UC-20T-A | 安装尺寸 + 机型 | 不按外观自动确认 |
| DEMO-UC-CARRIER-001 | 托链轮 | 上托轮、托轮 | UC-20T-A | 安装尺寸 + 机型 | 不与支重轮混用 |
| DEMO-UC-SPROCKET-001 | 驱动齿 | 齿圈、链轮 | UC-20T-A | 齿数 + 螺栓孔距 | 询问实测参数 |
| DEMO-ELEC-SENSOR-001 | 压力传感器 | 压力感应器 | ELEC-24V-A | 零件号 + 插头针脚 | 不按外形自动确认 |
| DEMO-ELEC-ECU-001 | 控制器 | 电脑板、ECU | ELEC-24V-A | 硬件号 + 软件版本 | 必须人工确认程序版本 |
| DEMO-FLT-KIT-001 | 保养滤芯包 | 一套滤芯 | SVC-2000H-A | 发动机型号 + 套装清单 | 拆分并确认每项数量 |

## 检索约束

- 系列标签只用于候选召回，不能替代原厂零件号或铭牌证据。
- 替代件、再制造件和高价值总成必须经过人工确认。
- 本文不得添加当前价格、可用库存、供应商报价、物流时效或订单状态。
