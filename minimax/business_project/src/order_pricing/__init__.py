"""订单计价与结算业务包。

模块划分与规格的对应关系：

===================== ==========================================
模块                  规格
===================== ==========================================
``money``             SPEC-PRICING-104
``models``            SPEC-COMPAT-402 / SPEC-COMPAT-403
``discounts``         SPEC-PRICING-101 / 102 / 103 / 105
``shipping``          SPEC-SHIPPING-201 / 202
``invoice``           SPEC-INVOICE-301 / 302
===================== ==========================================
"""

from __future__ import annotations

from order_pricing.discounts import (
    DiscountResult,
    apply_discounts,
    member_discount_rate,
    member_discounted_subtotal,
    subtotal,
    tiered_discount_rate,
)
from order_pricing.invoice import Invoice, invoice_total
from order_pricing.models import Order, OrderLine
from order_pricing.money import quantize
from order_pricing.shipping import shipping_fee

__all__ = [
    "DiscountResult",
    "Invoice",
    "Order",
    "OrderLine",
    "apply_discounts",
    "invoice_total",
    "member_discount_rate",
    "member_discounted_subtotal",
    "quantize",
    "shipping_fee",
    "subtotal",
    "tiered_discount_rate",
]
