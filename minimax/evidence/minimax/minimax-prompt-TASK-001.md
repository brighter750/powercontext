# SYSTEM

你是一名资深 Python 工程师，正在按规格驱动开发（SDD）的方式修改一个已有业务工程。

规则：
1. 严格遵守下面给出的“适用的规格条目”，不得擅自改变规格语义。
2. 不得删除、跳过（skip/xfail）或修改既有测试的断言与预期结果。
3. 只修改“需要修改的代码文件或符号”中列出的文件；如果确实需要改动其他文件，先在回复中说明理由。
4. 保持既有行为兼容（见兼容性规格）。
5. 输出必须是一个 ```diff 代码块，内容为标准 unified diff，路径相对业务工程根目录，例如：
   a/src/order_pricing/discounts.py
   b/src/order_pricing/discounts.py
6. diff 之后不要再输出解释性文字；若需要解释，放在 diff 代码块之前。


# PROMPT

下面是本次开发任务的工程上下文。请据此完成代码修改。

## 本次任务

实现阶梯满减折扣：按会员折后小计命中 1000/5000/10000 门槛档位，只取最高一档，不叠加；会员折扣先于阶梯满减计算。

## 工程上下文

# 开发上下文：TASK-001

## 任务描述

实现阶梯满减折扣：按会员折后小计命中 1000/5000/10000 门槛档位，只取最高一档，不叠加；会员折扣先于阶梯满减计算。

## 适用的规格条目

### SPEC-PRICING-102 阶梯满减（本次任务要实现的目标行为）
- 来源：`specs/SPEC-001-pricing.md:13`（规格修订版本 revision=5）
- 标签：PRICING, 目标行为

- 阶梯门槛按**会员折扣之后的小计**判断（即 `discounted_subtotal`）。
- 折扣率口径与会员折扣一致，均为“减去的比例”（`0.15` 表示减 15%）。
- 折扣档位（只取最高一档，不叠加）：

  | `discounted_subtotal` 门槛 | 满减折扣率 | 折后系数 |
  | --- | --- | --- |
  | `>= 10000` | `0.15` | `0.85` |
  | `>= 5000` | `0.10` | `0.90` |
  | `>= 1000` | `0.05` | `0.95` |
  | `< 1000` | `0`（不打折） | `1` |

- 门槛是**闭区间**：`discounted_subtotal` 恰好等于 `1000` 时适用 `0.05`。
- 阶梯满减同样只作用于商品小计，不作用于运费与税额。

### SPEC-PRICING-103 叠加顺序
- 来源：`specs/SPEC-001-pricing.md:29`（规格修订版本 revision=1）
- 标签：PRICING

- 计算顺序固定为：
  1. 计算商品小计 `subtotal`；
  2. 应用会员折扣，得到 `discounted_subtotal`；
  3. 用 `discounted_subtotal` 判断阶梯满减档位，得到 `tier_rate`；
  4. 最终商品金额 `= 会员折后小计 × (1 - tier_rate)`。
- 会员折扣与阶梯满减不得互相抵消，也不得各自作用于不同的小计口径。

### SPEC-COMPAT-401 既有公开函数签名
- 来源：`specs/SPEC-003-compatibility.md:7`（规格修订版本 revision=1）
- 标签：COMPAT

下列函数必须继续以相同名称与签名存在：

| 函数 | 模块 | 约束 |
| --- | --- | --- |
| `subtotal(order)` | `order_pricing.discounts` | 返回商品小计 |
| `member_discount_rate(order)` | `order_pricing.discounts` | 返回会员折扣率 |
| `member_discounted_subtotal(order)` | `order_pricing.discounts` | 返回会员折后小计 |
| `tiered_discount_rate(discounted_subtotal)` | `order_pricing.discounts` | 返回阶梯满减折扣率 |
| `apply_discounts(order)` | `order_pricing.discounts` | 返回 `DiscountResult` |
| `shipping_fee(order, discounted_subtotal)` | `order_pricing.shipping` | 返回运费 |
| `invoice_total(order)` | `order_pricing.invoice` | 返回 `Invoice` |
| `quantize(amount)` | `order_pricing.money` | 两位小数 `ROUND_HALF_UP` |

### SPEC-PRICING-105 结果可读性
- 来源：`specs/SPEC-001-pricing.md:43`（规格修订版本 revision=1）
- 标签：PRICING

- 计价结果必须同时暴露：商品小计、会员折扣率、阶梯满减折扣率、最终折后小计，
  以便上层展示与测试断言。

### SPEC-SHIPPING-201 免运费门槛（既有行为，必须保持）
- 来源：`specs/SPEC-002-shipping-invoice.md:5`（规格修订版本 revision=1）
- 标签：SHIPPING, 既有行为

- 折后小计 `discounted_subtotal` **大于等于** `99` 时免运费，运费为 `0`。
- 折后小计小于 `99` 时收取运费 `10`。
- 门槛比较使用折后小计，不使用原始小计，也不使用含税金额。

### SPEC-PRICING-101 会员等级折扣（既有行为，必须保持）
- 来源：`specs/SPEC-001-pricing.md:6`（规格修订版本 revision=1）
- 标签：PRICING, 既有行为

- 会员等级取值 `gold` / `silver` / `none`。
- `gold` 的会员折扣率为 `0.05`，`silver` 为 `0.03`，`none` 为 `0`。
- 会员折扣只作用于**商品小计**（`subtotal`），不作用于运费与税额。
- 未知等级按 `none` 处理，不得抛出异常。

### SPEC-INVOICE-302 发票总额构成（既有行为，必须保持）
- 来源：`specs/SPEC-002-shipping-invoice.md:22`（规格修订版本 revision=1）
- 标签：INVOICE, 既有行为

- 发票总额 `= 折后小计 + 运费 + 税额`。
- 发票必须同时给出：折后小计、运费、税额、总额。
- 总额按 `ROUND_HALF_UP` 保留两位小数。

### SPEC-COMPAT-403 既有边界行为
- 来源：`specs/SPEC-003-compatibility.md:28`（规格修订版本 revision=1）
- 标签：COMPAT

- 空订单（`lines` 为空）商品小计为 `0`，不抛异常。
- 数量为 `0` 的订单行对小计无贡献。
- 负单价属于非法输入，必须抛出 `ValueError`。
- 未知会员等级按 `none` 处理（见 SPEC-PRICING-101）。

## 需要修改的代码文件或符号

### `src/order_pricing/discounts.py`

- L38 `DiscountResult` (class) — `class DiscountResult:`
- L56 `member_discount_rate` (function) — `def member_discount_rate(order: Order) -> Decimal:`
- L62 `member_discounted_subtotal` (function) — `def member_discounted_subtotal(order: Order) -> Decimal:`
- L68 `tiered_discount_rate` (function) — `def tiered_discount_rate(discounted_subtotal: Decimal) -> Decimal:`
- L81 `apply_discounts` (function) — `def apply_discounts(order: Order) -> DiscountResult:`
### `src/order_pricing/invoice.py`

- L21 `Invoice` (class) — `class Invoice:`
- L30 `invoice_total` (function) — `def invoice_total(order: Order) -> Invoice:`
### `src/order_pricing/shipping.py`

- L19 `shipping_fee` (function) — `def shipping_fee(order: Order, discounted_subtotal: Decimal) -> Decimal:`
### `src/order_pricing/discounts.py`

- L19 `MEMBER_DISCOUNT_RATES` (constant) — `MEMBER_DISCOUNT_RATES: dict[str, Decimal] = {`
- L27 `TIER_THRESHOLDS` (constant) — `TIER_THRESHOLDS: tuple[tuple[Decimal, Decimal], ...] = (`
- L34 `NEUTRAL_DISCOUNT_RATE` (constant) — `NEUTRAL_DISCOUNT_RATE = Decimal("0")`
- L47 `subtotal` (function) — `def subtotal(order: Order) -> Decimal:`

## 用于验证功能与兼容性的测试

- `tests/test_invoice.py:19` `test_invoice_contains_subtotal_shipping_tax_and_total` — 覆盖规格：SPEC-INVOICE-302
- `tests/test_invoice.py:38` `test_invoice_applies_member_discount` — 覆盖规格：SPEC-INVOICE-301, SPEC-PRICING-101
- `tests/test_member_discount.py:18` `test_no_member_has_no_discount` — 覆盖规格：SPEC-PRICING-101
- `tests/test_member_discount.py:97` `test_apply_discounts_exposes_intermediate_values` — 覆盖规格：SPEC-PRICING-105
- `tests/test_shipping.py:15` `test_shipping_is_free_at_threshold` — 覆盖规格：SPEC-SHIPPING-201
- `tests/test_shipping.py:21` `test_shipping_is_charged_below_threshold` — 覆盖规格：SPEC-SHIPPING-201
- `tests/test_tiered_discount.py:32` `test_at_1000_threshold_applies_five_percent` — 覆盖规格：SPEC-PRICING-102
- `tests/test_tiered_discount.py:38` `test_between_1000_and_5000_applies_five_percent` — 覆盖规格：SPEC-PRICING-102
- `tests/test_tiered_discount.py:50` `test_between_5000_and_10000_applies_ten_percent` — 覆盖规格：SPEC-PRICING-102
- `tests/test_tiered_discount.py:56` `test_at_10000_threshold_applies_fifteen_percent` — 覆盖规格：SPEC-PRICING-102
- `tests/test_tiered_discount.py:62` `test_only_the_highest_tier_applies` — 覆盖规格：SPEC-PRICING-102
- `tests/test_tiered_discount.py:71` `test_tier_threshold_uses_member_discounted_subtotal` — 覆盖规格：SPEC-PRICING-103
- `tests/test_tiered_discount.py:84` `test_tier_applies_when_member_discounted_subtotal_reaches_threshold` — 覆盖规格：SPEC-PRICING-103
- `tests/test_tiered_discount.py:105` `test_apply_discounts_applies_member_then_tier` — 覆盖规格：SPEC-PRICING-103
- `tests/test_member_discount.py:26` `test_silver_member_discount_is_three_percent` — 覆盖规格：SPEC-PRICING-101
- `tests/test_member_discount.py:38` `test_gold_member_discount_is_five_percent` — 覆盖规格：SPEC-PRICING-101
- `tests/test_member_discount.py:50` `test_unknown_member_tier_falls_back_to_none` — 覆盖规格：SPEC-COMPAT-403, SPEC-PRICING-101
- `tests/test_member_discount.py:61` `test_subtotal_multiplies_price_by_quantity` — 覆盖规格：SPEC-PRICING-101
- `tests/test_member_discount.py:71` `test_empty_order_subtotal_is_zero` — 覆盖规格：SPEC-COMPAT-403
- `tests/test_member_discount.py:77` `test_zero_quantity_line_contributes_nothing` — 覆盖规格：SPEC-COMPAT-403
- `tests/test_member_discount.py:90` `test_negative_unit_price_is_rejected` — 覆盖规格：SPEC-COMPAT-403
- `tests/test_money.py:25` `test_quantize_rejects_non_decimal` — 覆盖规格：SPEC-COMPAT-403
- `tests/test_shipping.py:27` `test_shipping_is_free_above_threshold` — 覆盖规格：SPEC-SHIPPING-201, SPEC-SHIPPING-202
- `tests/test_tiered_discount.py:26` `test_below_threshold_has_no_tier_discount` — 覆盖规格：SPEC-PRICING-102
- `tests/test_tiered_discount.py:44` `test_at_5000_threshold_applies_ten_percent` — 覆盖规格：SPEC-PRICING-102
- `tests/test_tiered_discount.py:95` `test_apply_discounts_reports_tier_rate_and_final_amount` — 覆盖规格：SPEC-PRICING-103, SPEC-PRICING-105
- `tests/test_invoice.py:29` `test_invoice_charges_shipping_below_free_threshold` — 覆盖规格：SPEC-INVOICE-301, SPEC-SHIPPING-202

## 任务进展

- 任务状态：in_progress
- 任务描述：实现阶梯满减折扣：按会员折后小计命中 1000/5000/10000 门槛档位，只取最高一档，不叠加；会员折扣先于阶梯满减计算。

### 已完成事项

- 已导入规格、代码与测试资料到 seekdb 资料库
- 已通过数据库倒排索引检索定位适用规格与相关代码符号

### 待办事项

- 实现 tiered_discount_rate 的阶梯档位判定（SPEC-PRICING-102）
- 运行目标功能测试与回归测试
- 记录验证结果并更新任务状态

### 已有验证结果

_（无）_

## 资料库检索命中（来自 seekdb / OceanBase 的真实查询）

| 类型 | 条目 | 词频得分 |
| --- | --- | --- |
| spec | `SPEC-PRICING-102` | 41 |
| spec | `SPEC-PRICING-103` | 23 |
| test | `tests/test_tiered_discount.py::test_tier_threshold_uses_member_discounted_subtotal` | 20 |
| spec | `SPEC-COMPAT-401` | 14 |
| spec | `SPEC-PRICING-105` | 12 |
| spec | `SPEC-SHIPPING-201` | 12 |
| spec | `SPEC-PRICING-101` | 10 |
| test | `tests/test_member_discount.py::test_apply_discounts_exposes_intermediate_values` | 7 |
| spec | `SPEC-INVOICE-302` | 6 |
| test | `tests/test_tiered_discount.py::test_apply_discounts_applies_member_then_tier` | 6 |
| test | `tests/test_tiered_discount.py::test_between_1000_and_5000_applies_five_percent` | 6 |
| test | `tests/test_tiered_discount.py::test_between_5000_and_10000_applies_ten_percent` | 6 |
| test | `tests/test_tiered_discount.py::test_only_the_highest_tier_applies` | 6 |
| test | `tests/test_tiered_discount.py::test_tier_applies_when_member_discounted_subtotal_reaches_threshold` | 6 |
| test | `tests/test_tiered_discount.py::test_at_1000_threshold_applies_five_percent` | 4 |
| spec | `SPEC-COMPAT-403` | 3 |
| spec | `SPEC-INVOICE-301` | 3 |
| test | `tests/test_invoice.py::test_invoice_applies_member_discount` | 3 |
| test | `tests/test_invoice.py::test_invoice_contains_subtotal_shipping_tax_and_total` | 3 |
| test | `tests/test_member_discount.py::test_no_member_has_no_discount` | 3 |

## 输出要求

请只输出一个 ```diff 代码块，内容为可直接应用的标准 unified diff。
