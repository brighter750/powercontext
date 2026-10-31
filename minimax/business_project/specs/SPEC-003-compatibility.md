# SPEC-003 兼容性规格：必须保持的既有行为

> 本文件是回归红线。任何改动都不得破坏下列既有行为。
> 对应回归测试位于 `tests/test_money.py`、`tests/test_member_discount.py`、
> `tests/test_shipping.py`、`tests/test_invoice.py`。

## SPEC-COMPAT-401 既有公开函数签名

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

## SPEC-COMPAT-402 既有数据模型

- `OrderLine` 与 `Order` 保持冻结数据类（frozen dataclass），字段不得重命名。
- `Order.member_tier` 默认 `none`；`Order.region` 默认 `domestic`。
- `DiscountResult` 与 `Invoice` 的既有字段名保持不变。

## SPEC-COMPAT-403 既有边界行为

- 空订单（`lines` 为空）商品小计为 `0`，不抛异常。
- 数量为 `0` 的订单行对小计无贡献。
- 负单价属于非法输入，必须抛出 `ValueError`。
- 未知会员等级按 `none` 处理（见 SPEC-PRICING-101）。

## SPEC-COMPAT-404 不允许的“通过方式”

- 不得删除、跳过（`skip`/`xfail`）既有测试来让测试通过。
- 不得修改既有测试的断言值或预期结果。
- 不得修改既有测试文件中的既有用例。
