# order-pricing — 订单计价与结算业务工程

本目录是 MiniMax × OceanBase 赛道的**验证样例业务工程**：一个可运行、可测试、
带规格文档的最小真实业务系统，用于证明上下文管理模块确实能驱动一次真实的代码修改。

## 目录结构

```text
business_project/
├── specs/                          # 规格文档（SDD 的事实来源）
│   ├── SPEC-001-pricing.md        # 计价规格（含本次要实现的阶梯满减）
│   ├── SPEC-002-shipping-invoice.md# 运费与发票规格
│   └── SPEC-003-compatibility.md   # 必须保持的既有行为（回归红线）
├── src/order_pricing/              # 实现
│   ├── models.py                   # Order / OrderLine
│   ├── money.py                    # 金额舍入（SPEC-PRICING-104）
│   ├── discounts.py                # 会员折扣 + 阶梯满减（SPEC-PRICING-101/102/103/105）
│   ├── shipping.py                 # 运费（SPEC-SHIPPING-201/202）
│   └── invoice.py                  # 发票（SPEC-INVOICE-301/302）
└── tests/                          # pytest 测试
    ├── test_money.py               # 回归
    ├── test_member_discount.py     # 回归
    ├── test_shipping.py            # 回归
    ├── test_invoice.py             # 回归
    └── test_tiered_discount.py     # ★ 本次任务新增的目标功能测试
```

## 规格与代码的对应关系

| 规格编号 | 内容 | 实现位置 |
| --- | --- | --- |
| SPEC-PRICING-101 | 会员等级折扣（既有行为） | `discounts.member_discount_rate` |
| **SPEC-PRICING-102** | **阶梯满减（本次目标行为）** | **`discounts.tiered_discount_rate`** |
| SPEC-PRICING-103 | 会员折扣与阶梯满减的叠加顺序 | `discounts.apply_discounts` |
| SPEC-PRICING-104 | 金额舍入 | `money.quantize` |
| SPEC-PRICING-105 | 结果可读性 | `discounts.DiscountResult` |
| SPEC-SHIPPING-201/202 | 免运费门槛与运费金额 | `shipping.shipping_fee` |
| SPEC-INVOICE-301/302 | 税率、舍入与发票构成 | `invoice.invoice_total` |
| SPEC-COMPAT-401…404 | 既有行为与函数签名红线 | 全部模块 |

## 运行测试

```bash
cd minimax/business_project
python -m pytest tests -q
```

不需要安装本包即可运行：`tests/conftest.py` 会把 `src/` 加入 `sys.path`；
也可以 `pip install -e .` 后再运行。

## 本次任务的验证点

* `tests/test_tiered_discount.py` 是**本次任务新增**的目标功能测试，
  在阶梯满减实现之前会因“目标行为未实现”而失败；
* 其余测试文件是**既有回归测试**，在本次任务中不做任何修改，
  用于证明改动没有破坏兼容行为；
* 修改前后使用**同一组测试文件**，未删除断言、未跳过用例、未修改预期结果。
