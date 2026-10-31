"""发票金额。

规格来源：``SPEC-INVOICE-301``、``SPEC-INVOICE-302``。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from order_pricing.discounts import apply_discounts
from order_pricing.models import Order
from order_pricing.money import quantize
from order_pricing.shipping import shipping_fee

#: SPEC-INVOICE-301 —— 税率。
TAX_RATE = Decimal("0.06")


@dataclass(frozen=True)
class Invoice:
    """一张订单的发票金额构成。"""

    discounted_subtotal: Decimal
    shipping: Decimal
    tax: Decimal
    total: Decimal


def invoice_total(order: Order) -> Invoice:
    """计算订单发票（SPEC-INVOICE-301 / SPEC-INVOICE-302）。"""

    discounts = apply_discounts(order)
    discounted_subtotal = discounts.discounted_subtotal
    shipping = shipping_fee(order, discounted_subtotal)
    taxable = discounted_subtotal + shipping
    tax = quantize(taxable * TAX_RATE)
    total = quantize(discounted_subtotal + shipping + tax)
    return Invoice(
        discounted_subtotal=discounted_subtotal,
        shipping=shipping,
        tax=tax,
        total=total,
    )


__all__ = ["Invoice", "TAX_RATE", "invoice_total"]
