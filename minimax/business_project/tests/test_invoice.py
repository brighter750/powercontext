"""发票回归测试 —— SPEC-INVOICE-301 / SPEC-INVOICE-302。"""

from __future__ import annotations

from decimal import Decimal

from order_pricing.invoice import invoice_total
from order_pricing.models import Order, OrderLine


def _order(unit_price: str = "200", member_tier: str = "none") -> Order:
    return Order(
        order_id="I-1",
        lines=(OrderLine("SKU-1", Decimal(unit_price), 1),),
        member_tier=member_tier,
    )


def test_invoice_contains_subtotal_shipping_tax_and_total() -> None:
    """SPEC-INVOICE-302：发票给出折后小计、运费、税额与总额。"""

    invoice = invoice_total(_order())
    assert invoice.discounted_subtotal == Decimal("200")
    assert invoice.shipping == Decimal("0")
    assert invoice.tax == Decimal("12.00")
    assert invoice.total == Decimal("212.00")


def test_invoice_charges_shipping_below_free_threshold() -> None:
    """SPEC-INVOICE-301 / SPEC-SHIPPING-202：低于免运费门槛时计运费并计税。"""

    invoice = invoice_total(_order(unit_price="50"))
    assert invoice.shipping == Decimal("10")
    assert invoice.tax == Decimal("3.60")
    assert invoice.total == Decimal("63.60")


def test_invoice_applies_member_discount() -> None:
    """SPEC-PRICING-101 / SPEC-INVOICE-301：会员折扣会影响计税基数。"""

    invoice = invoice_total(_order(unit_price="200", member_tier="gold"))
    assert invoice.discounted_subtotal == Decimal("190.00")
    assert invoice.tax == Decimal("11.40")
    assert invoice.total == Decimal("201.40")
