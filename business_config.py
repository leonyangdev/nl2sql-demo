"""业务语义配置 —— 数据库告诉不了我们的那部分知识。

数据库只能告诉我们：

    sales_order_item.pay_amount  NUMERIC(18,2)

但永远告诉不了我们：

    pay_amount = 实际支付金额
    用户可能叫它：实付金额、成交金额、支付金额

这就是"物理元数据 -> 语义元数据"中间必须的人工治理环节。
生产环境里这份配置通常来自：数据字典 / 指标平台 / 数据治理系统。
"""

from __future__ import annotations

BUSINESS_METADATA = {
    "tables": {
        "sales_order": {
            "business_name": "销售订单",
            "description": "记录销售订单头信息，包括订单区域、订单日期以及订单状态",
            "business_domain": "sales",
            "grain": "一行代表一张销售订单",
            "columns": {
                "id": {
                    "business_name": "订单ID",
                    "description": "销售订单唯一标识",
                    "semantic_role": "identifier",
                    "synonyms": [],
                },
                "region_id": {
                    "business_name": "销售区域ID",
                    "description": "订单所属销售区域",
                    "semantic_role": "foreign_key",
                    "synonyms": ["区域ID", "大区ID"],
                },
                "order_date": {
                    "business_name": "订单日期",
                    "description": "销售订单发生日期",
                    "semantic_role": "time",
                    "synonyms": ["销售日期", "下单日期", "订单时间"],
                },
                "status": {
                    "business_name": "订单状态",
                    "description": "销售订单当前业务状态",
                    "semantic_role": "dimension",
                    "synonyms": ["订单状态", "单据状态"],
                    # 显式声明"这是个可枚举的字典字段"，才允许采样成 Value Metadata
                    "enumerable": True,
                },
                "created_at": {
                    "business_name": "创建时间",
                    "description": "订单记录创建时间",
                    "semantic_role": "time",
                    "synonyms": ["创建时刻"],
                },
            },
        },
        "sales_order_item": {
            "business_name": "销售订单明细",
            "description": "记录销售订单中的产品明细、销售数量及实际支付金额",
            "business_domain": "sales",
            "grain": "一行代表一张订单中的一个产品",
            "columns": {
                "id": {
                    "business_name": "明细ID",
                    "semantic_role": "identifier",
                },
                "order_id": {
                    "business_name": "订单ID",
                    "description": "当前明细所属销售订单",
                    "semantic_role": "foreign_key",
                },
                "product_id": {
                    "business_name": "产品ID",
                    "description": "当前销售明细对应产品",
                    "semantic_role": "foreign_key",
                },
                "quantity": {
                    "business_name": "销售数量",
                    "description": "商品实际成交数量",
                    "semantic_role": "measure",
                    "synonyms": ["销量", "销售件数", "成交数量"],
                },
                "pay_amount": {
                    "business_name": "实际支付金额",
                    "description": "订单商品扣除优惠后的实际支付金额",
                    "semantic_role": "measure",
                    "synonyms": ["实付金额", "成交金额", "支付金额"],
                },
            },
        },
        "product": {
            "business_name": "产品主数据",
            "description": "企业销售产品基础信息",
            "business_domain": "product",
            "grain": "一行代表一个产品",
            "columns": {
                "id": {
                    "business_name": "产品ID",
                    "semantic_role": "identifier",
                },
                "product_name": {
                    "business_name": "产品名称",
                    "description": "销售产品名称",
                    "semantic_role": "dimension",
                    "synonyms": ["产品", "商品", "商品名称"],
                },
                "category": {
                    "business_name": "产品分类",
                    "semantic_role": "dimension",
                    "synonyms": ["品类", "产品类别"],
                },
            },
        },
        "region": {
            "business_name": "销售区域",
            "description": "企业销售区域基础信息",
            "business_domain": "sales",
            "grain": "一行代表一个销售区域",
            "columns": {
                "id": {
                    "business_name": "区域ID",
                    "semantic_role": "identifier",
                },
                "region_name": {
                    "business_name": "区域名称",
                    "description": "销售区域名称，例如华南区、华东区",
                    "semantic_role": "dimension",
                    "synonyms": ["区域", "地区", "大区", "销售区域"],
                    # 显式声明"这是个可枚举的字典字段"，才允许采样成 Value Metadata
                    "enumerable": True,
                },
            },
        },
    },
    # ----------------------------------------------------------------------
    # Metric：主要来自业务定义，而不是数据库扫描
    # ----------------------------------------------------------------------
    "metrics": {
        "sales_amount": {
            "business_name": "销售额",
            "description": "已完成销售订单产生的实际支付金额",
            "business_domain": "sales",
            "aggregation": "SUM",
            "source": {"table": "sales_order_item", "column": "pay_amount"},
            "time_column": {"table": "sales_order", "column": "order_date"},
            "default_filters": [
                {
                    "table": "sales_order",
                    "column": "status",
                    "operator": "=",
                    "value": "completed",
                }
            ],
            "synonyms": [
                "销售金额",
                "成交金额",
                "成交额",
                "销售收入",
                "营业额",
                "卖得最好",
                "卖了多少钱",
                "多少金额",
            ],
        },
        # 第二个指标：用来验证"向量库到底有没有选对 Metric"
        "sales_quantity": {
            "business_name": "销量",
            "description": "已完成销售订单中的商品销售数量",
            "business_domain": "sales",
            "aggregation": "SUM",
            "source": {"table": "sales_order_item", "column": "quantity"},
            "time_column": {"table": "sales_order", "column": "order_date"},
            "default_filters": [
                {
                    "table": "sales_order",
                    "column": "status",
                    "operator": "=",
                    "value": "completed",
                }
            ],
            "synonyms": ["销售数量", "销售件数", "成交量", "卖了多少件"],
        },
    },
    # ----------------------------------------------------------------------
    # Value：低基数字段自动采样 + 这里补充人工同义词
    # 键格式："表名.字段名"
    # ----------------------------------------------------------------------
    "values": {
        "region.region_name": {
            "华南区": {
                "synonyms": ["华南", "南区", "华南地区", "Southern China"],
                "description": "覆盖广东、广西、海南等区域",
            },
            "华东区": {
                "synonyms": ["华东", "东区", "华东地区"],
            },
            "华北区": {
                "synonyms": ["华北", "北区", "华北地区"],
            },
        },
        "sales_order.status": {
            "completed": {
                "business_name": "已完成",
                "synonyms": ["完成", "已支付", "成交"],
            },
            "canceled": {
                "business_name": "已取消",
                "synonyms": ["取消", "作废"],
            },
        },
    },
}
