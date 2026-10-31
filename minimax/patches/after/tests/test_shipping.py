"""运费回归测试 —— SPEC-SHIPPING-201 / SPEC-SHIPPING-202。"""

from __future__ import annotations

from decimal import Decimal

from order_pricing.models import Order, OrderLine
from order_pricing.shipping import shipping_fee


def _order() -> Order:
    return Order(order_id="S-1", lines=(OrderLine("SKU-1", Decimal("10"), 1),))


def test_shipping_is_free_at_threshold() -> None:
    """SPEC-SHIPPING-201：折后小计恰好 99 时免运费。"""

    assert shipping_fee(_order(), Decimal("99.00")) == Decimal("0")


def test_shipping_is_charged_below_threshold() -> None:
    """SPEC-SHIPPING-201：折后小计小于 99 时收取运费。"""

    assert shipping_fee(_order(), Decimal("98.99")) == Decimal("10")


def test_shipping_is_free_above_threshold() -> None:
    """SPEC-SHIPPING-201 / SPEC-SHIPPING-202：高于门槛时运费为 0。"""

    assert shipping_fee(_order(), Decimal("1000.00")) == Decimal("0")
