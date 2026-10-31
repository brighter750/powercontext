"""订单计价：会员折扣与阶梯满减。

规格来源：

* ``SPEC-PRICING-101`` 会员等级折扣（既有行为）
* ``SPEC-PRICING-102`` 阶梯满减（本次任务实现的目标行为）
* ``SPEC-PRICING-103`` 叠加顺序
* ``SPEC-PRICING-105`` 结果可读性
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from order_pricing.models import Order

#: SPEC-PRICING-101 —— 会员等级折扣率。
MEMBER_DISCOUNT_RATES: dict[str, Decimal] = {
    "none": Decimal("0"),
    "silver": Decimal("0.03"),
    "gold": Decimal("0.05"),
}

#: SPEC-PRICING-102 —— 阶梯满减门槛与折扣率（门槛降序）。
#: 折扣率口径与会员折扣一致：``0.15`` 表示减去 15%。
TIER_THRESHOLDS: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("10000"), Decimal("0.15")),
    (Decimal("5000"), Decimal("0.10")),
    (Decimal("1000"), Decimal("0.05")),
)

#: SPEC-PRICING-102 —— 不满足任何门槛时使用的中性折扣率（不打折）。
NEUTRAL_DISCOUNT_RATE = Decimal("0")


@dataclass(frozen=True)
class DiscountResult:
    """一次计价过程中的折扣中间量与最终值。"""

    base_subtotal: Decimal
    member_rate: Decimal
    tier_rate: Decimal
    discounted_subtotal: Decimal


def subtotal(order: Order) -> Decimal:
    """返回订单的商品小计（未打折）。"""

    total = Decimal("0")
    for line in order.lines:
        total += line.unit_price * line.quantity
    return total


def member_discount_rate(order: Order) -> Decimal:
    """返回会员折扣率（SPEC-PRICING-101）。"""

    return MEMBER_DISCOUNT_RATES.get(order.member_tier, MEMBER_DISCOUNT_RATES["none"])


def member_discounted_subtotal(order: Order) -> Decimal:
    """返回会员折扣之后的小计（SPEC-PRICING-101）。"""

    return subtotal(order) * (Decimal("1") - member_discount_rate(order))


def tiered_discount_rate(discounted_subtotal: Decimal) -> Decimal:
    """返回阶梯满减折扣率。

    依据 ``SPEC-PRICING-102``，门槛按会员折扣之后的小计判断，只取最高一档。

    .. note::
       本函数当前尚未实现阶梯规则（始终返回中性折扣率 ``1``），
       对应的目标行为由本任务的代码修改补齐。
    """

    return NEUTRAL_DISCOUNT_RATE


def apply_discounts(order: Order) -> DiscountResult:
    """按 SPEC-PRICING-103 的顺序完成会员折扣与阶梯满减。"""

    base = subtotal(order)
    member_rate = member_discount_rate(order)
    after_member = base * (Decimal("1") - member_rate)
    tier_rate = tiered_discount_rate(after_member)
    return DiscountResult(
        base_subtotal=base,
        member_rate=member_rate,
        tier_rate=tier_rate,
        discounted_subtotal=after_member * (Decimal("1") - tier_rate),
    )


__all__ = [
    "DiscountResult",
    "MEMBER_DISCOUNT_RATES",
    "NEUTRAL_DISCOUNT_RATE",
    "TIER_THRESHOLDS",
    "apply_discounts",
    "member_discount_rate",
    "member_discounted_subtotal",
    "subtotal",
    "tiered_discount_rate",
]
