"""运费计算。

规格来源：``SPEC-SHIPPING-201``、``SPEC-SHIPPING-202``。
"""

from __future__ import annotations

from decimal import Decimal

from order_pricing.models import Order

#: SPEC-SHIPPING-201 —— 折后小计达到该值即免运费。
FREE_SHIPPING_THRESHOLD = Decimal("99")

#: SPEC-SHIPPING-202 —— 标准运费。
STANDARD_SHIPPING_FEE = Decimal("10")


def shipping_fee(order: Order, discounted_subtotal: Decimal) -> Decimal:
    """返回订单运费。

    免运费判断使用折后小计（SPEC-SHIPPING-201）。
    """

    if discounted_subtotal >= FREE_SHIPPING_THRESHOLD:
        return Decimal("0")
    return STANDARD_SHIPPING_FEE


__all__ = ["FREE_SHIPPING_THRESHOLD", "STANDARD_SHIPPING_FEE", "shipping_fee"]
