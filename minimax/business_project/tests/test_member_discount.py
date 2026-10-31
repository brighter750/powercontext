"""会员折扣回归测试 —— SPEC-PRICING-101 / SPEC-COMPAT-403。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from order_pricing.discounts import (
    apply_discounts,
    member_discount_rate,
    member_discounted_subtotal,
    subtotal,
)
from order_pricing.models import Order, OrderLine


def test_no_member_has_no_discount() -> None:
    """SPEC-PRICING-101：none 等级没有会员折扣。"""

    order = Order(order_id="O-1", lines=(OrderLine("SKU-1", Decimal("200"), 1),))
    assert member_discount_rate(order) == Decimal("0")
    assert member_discounted_subtotal(order) == Decimal("200")


def test_silver_member_discount_is_three_percent() -> None:
    """SPEC-PRICING-101：silver 折扣率为 0.03。"""

    order = Order(
        order_id="O-2",
        lines=(OrderLine("SKU-1", Decimal("200"), 1),),
        member_tier="silver",
    )
    assert member_discount_rate(order) == Decimal("0.03")
    assert member_discounted_subtotal(order) == Decimal("194.00")


def test_gold_member_discount_is_five_percent() -> None:
    """SPEC-PRICING-101：gold 折扣率为 0.05。"""

    order = Order(
        order_id="O-3",
        lines=(OrderLine("SKU-1", Decimal("200"), 1),),
        member_tier="gold",
    )
    assert member_discount_rate(order) == Decimal("0.05")
    assert member_discounted_subtotal(order) == Decimal("190.00")


def test_unknown_member_tier_falls_back_to_none() -> None:
    """SPEC-PRICING-101 / SPEC-COMPAT-403：未知等级按 none 处理，不抛异常。"""

    order = Order(
        order_id="O-4",
        lines=(OrderLine("SKU-1", Decimal("200"), 1),),
        member_tier="platinum",
    )
    assert member_discount_rate(order) == Decimal("0")


def test_subtotal_multiplies_price_by_quantity() -> None:
    """SPEC-PRICING-101：小计 = 单价 × 数量。"""

    order = Order(
        order_id="O-5",
        lines=(OrderLine("SKU-1", Decimal("12.50"), 4),),
    )
    assert subtotal(order) == Decimal("50.00")


def test_empty_order_subtotal_is_zero() -> None:
    """SPEC-COMPAT-403：空订单小计为 0，不抛异常。"""

    assert subtotal(Order(order_id="O-6")) == Decimal("0")


def test_zero_quantity_line_contributes_nothing() -> None:
    """SPEC-COMPAT-403：数量为 0 的订单行对小计无贡献。"""

    order = Order(
        order_id="O-7",
        lines=(
            OrderLine("SKU-1", Decimal("99"), 0),
            OrderLine("SKU-2", Decimal("10"), 2),
        ),
    )
    assert subtotal(order) == Decimal("20")


def test_negative_unit_price_is_rejected() -> None:
    """SPEC-COMPAT-403：负单价属于非法输入，必须抛出 ValueError。"""

    with pytest.raises(ValueError):
        OrderLine("SKU-1", Decimal("-1"), 1)


def test_apply_discounts_exposes_intermediate_values() -> None:
    """SPEC-PRICING-105：结果必须暴露小计、会员折扣率与折后小计。"""

    order = Order(
        order_id="O-8",
        lines=(OrderLine("SKU-1", Decimal("200"), 1),),
        member_tier="gold",
    )
    result = apply_discounts(order)
    assert result.base_subtotal == Decimal("200")
    assert result.member_rate == Decimal("0.05")
    assert result.discounted_subtotal == Decimal("190.00")
